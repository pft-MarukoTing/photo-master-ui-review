import random
from datetime import datetime

from .common import get_hostname_with_process_id, get_hostname
from .pf_redis import pf_redis_client, pf_cache_redis_client
import redis
import json
import os
import time
from .logger import logger
import traceback

class Config(object):
    def __init__(self, env):
        self.CONFIGS_ALLOWED_KEYS = (
            'REDIS_MSG_QUEUE',
            'REDIS_SCHEDULER_QUEUE',
            'REDIS_MSG_INFO_FORMAT',
            'REDIS_PROGRESS_QUEUE',
            'REDIS_CACHE_FORMAT',
            'STOP_PATH',
            'HOSTNAME'
        )
        env = self.cleanup_configs_kwargs(**env)
        try:
            self.redisMsgQueue = env['REDIS_MSG_QUEUE'].split(",")
            self.redisMsgInfoFormat = env['REDIS_MSG_INFO_FORMAT']
            self.redisProgressQueue = env['REDIS_PROGRESS_QUEUE']
            self.redisSpecificProgressQueue = "yce_ai:{tskSrc}_msg:ack"
            if 'REDIS_CACHE_FORMAT' in env:
                self.redisCacheFormat = env['REDIS_CACHE_FORMAT']
            else:  # Backward compatible
                self.redisCacheFormat = None
                logger.warning("Environment variable 'REDIS_CACHE_FORMAT' not found. Cannot use cache methods.")
        except Exception as e:
            raise RuntimeError("please set environment variable 'REDIS_MSG_QUEUE', 'REDIS_MSG_INFO_FORMAT', 'REDIS_PROGRESS_QUEUE'! They are needed.: %s" % str(e))

        try:
            if 'REDIS_SCHEDULER_QUEUE' in env and env['REDIS_SCHEDULER_QUEUE'] != "":
                self.useSchedulerQueue = True
                self.redisSchedulerQueue = env['REDIS_SCHEDULER_QUEUE']
                _ = env["HOSTNAME"]  # Ensure that 'HOSTNAME' is set
            else:
                self.useSchedulerQueue = False
        except:
            raise RuntimeError("Fail to use 'REDIS_SCHEDULER_QUEUE' because there is no env var: 'HOSTNAME'.")

        self.stopPath = env['STOP_PATH'] if 'STOP_PATH' in env else None

        # self.redisHbKey = env['REDIS_HB_KEY'] # if 'REDIS_HB_KEY' in env else None

    def cleanup_configs_kwargs(self, **kwargs):
        connection_kwargs = {
            k: v
            for k, v in kwargs.items()
            if k in self.CONFIGS_ALLOWED_KEYS
        }
        return connection_kwargs

pf_job_fetcher_config = Config(os.environ)

end_of_service_flag = False
def fetch_job(timeout=10, keyToGet = ['srcKeys', 'dstKeys', 'acts', 'tskId', 'tskSrc'], aug_keys=[]):
    _check_end_of_service()
    try:
        res = _fetch_job_id(pf_job_fetcher_config.redisMsgQueue, timeout)
        if res is None:
            return None, None

        resId = res[1].decode()
        logger.log("fetch job", status="running", job_id=resId)

        job_info = get_job_info(resId, keyToGet, aug_keys, sup_url_file_keys=True)

        task_id = job_info.get("tskId")
        _set_fetch_job_ts(resId, task_id)

        return resId, job_info
    except redis.exceptions.ConnectionError as e:
        logger.log(f"{e}", "Error", status="running")
    except redis.exceptions.RedisClusterException as e:
        logger.log(f"{e}", "Error", status="running")
    except (Exception,):
        logger.log(traceback.format_exc(), "Error", status="running")

    return None, None


_fetch_job_v2_got_job_before = True
def fetch_job_v2(timeout=10, keyToGet = ['srcKeys', 'dstKeys', 'acts', 'tskId', 'tskSrc', 'retryCount'], aug_keys=[],
                 effect_cost=None):
    _check_end_of_service()
    try:
        global _fetch_job_v2_got_job_before

        if pf_job_fetcher_config.useSchedulerQueue:
            res = _fetch_job_with_job_request(pf_job_fetcher_config.redisSchedulerQueue,
                                              pf_job_fetcher_config.redisMsgQueue,
                                              effect_cost,  # Might be None
                                              timeout)
        else:
            res = _fetch_job_id(pf_job_fetcher_config.redisMsgQueue, timeout)

        if res is None:
            _fetch_job_v2_got_job_before = False
            return None
        _fetch_job_v2_got_job_before = True

        q_src = res[0].decode()
        resId = res[1].decode()
        priority = res[2]
        logger.log("fetch job v2", status="running", q_src=q_src, job_id=resId, effect_cost=effect_cost,
                   use_scheduler_queue=pf_job_fetcher_config.useSchedulerQueue)

        job_info = get_job_info(resId, keyToGet, aug_keys, sup_url_file_keys=True)

        task_id = job_info.get("tskId")
        _set_fetch_job_ts(resId, task_id)

        return {"job_id": resId, "job_info": job_info, "queue_name": q_src, "priority": priority}
    except redis.exceptions.ConnectionError as e:
        logger.log(f"{e}", "Error", status="running")
    except redis.exceptions.RedisClusterException as e:
        logger.log(f"{e}", "Error", status="running")
    except (Exception,):
        logger.log(traceback.format_exc(), "Error", status="running")

    return None

def _fetch_job_with_job_request(scheduler_queue, msgQueues, effect_cost, timeout):
    wait_queue_name = "yce_ai:{hostname}:queue".format(hostname=get_hostname_with_process_id())

    # send job request
    if _fetch_job_v2_got_job_before:
        job_request = {
            "waitQueueName": wait_queue_name,
            "toDoQueues": msgQueues,
            "cost": effect_cost
        }
        job_request_str = json.dumps(job_request)

        pf_redis_client.lpush(scheduler_queue, job_request_str, task_id=None)

        logger.log("push job request", status="running", scheduler_queue_name=scheduler_queue, job_request=job_request)

    # fetch job from waitQueueName
    return _fetch_job_id([wait_queue_name], timeout)

def _fetch_job_id(queues, timeout):
    random.shuffle(queues)
    return pf_redis_client.bzpopmin(queues, timeout=timeout)

def _set_fetch_job_ts(job_id, task_id=None):
    # Set the time that engine get job
    ts_now = int(time.time() * 1000)  # current time in milliseconds

    pf_redis_client.hmset(pf_job_fetcher_config.redisMsgInfoFormat.replace("{msg_id}", f"{job_id}"), {"ts": ts_now}, task_id=task_id)


def requeue_job(job_id, queue_name, retry_count=None, effect=None, task_id=None):
    try:
        start_time = datetime.utcnow()

        retry_cnt = 0 if retry_count is None else retry_count
        retry_cnt += 1

        # update retry_count to job_info first
        set_job_info(job_id, task_id=task_id, results={"retryCount": retry_cnt})

        # requeue job
        pf_redis_client.zadd(queue_name, {job_id: 0}, task_id=task_id)  # 0 score for immediate retry

        end_time = datetime.utcnow()
        logger.log("requeue job", status="running", start_time=start_time, end_time=end_time, effect=effect,
                   task_id=task_id, job_id=job_id, queue_name=queue_name, retry_count=retry_cnt)
    except Exception as e:
        logger.log(f"{e}", "Error", status="running")
        logger.log(traceback.format_exc(), "Error", status="running")


def get_job_info(job_id, keyToGet = ['srcKeys', 'dstKeys', 'acts', 'tskId', 'tskSrc', 'retryCount'], aug_keys=[], retry=3, sup_url_file_keys=False):
    start_time = datetime.utcnow()

    output_dict = None
    to_try = 0
    exception = []

    keyToGet = keyToGet + aug_keys
    if sup_url_file_keys:
        keyToGet = _augment_url_keys(keyToGet)

    tskIdToGet = True
    while to_try < retry and output_dict is None:
        to_try += 1
        try:
            noneValCount = 0
            if 'tskId' not in keyToGet:
                keyToGet.append('tskId')
                tskIdToGet = False

            msgInfoKey = pf_job_fetcher_config.redisMsgInfoFormat.replace("{msg_id}", f"{job_id}")
            msgInfoRes = pf_redis_client.hmget(msgInfoKey, keyToGet)

            output_dict = {}
            for idx, key in enumerate(keyToGet):
                try:
                    if msgInfoRes[idx] is None: 
                        noneValCount += 1
                        continue
                    output_dict[key] = json.loads(msgInfoRes[idx])
                    if key in ["dstKeys"]:
                        if output_dict[key] is None:
                            continue
                        for dstKeyIdx in range(len(output_dict[key])):
                            if type(output_dict[key][dstKeyIdx]) == dict:
                                output_dict[key][dstKeyIdx] = output_dict[key][dstKeyIdx]["dstKey"]
                except Exception:
                    output_dict[key] = (
                        msgInfoRes[idx].decode() if msgInfoRes[idx] != None else msgInfoRes[idx]
                    )
            if noneValCount == len(keyToGet):
                exception.append({"message": "Message info all none", "traceback": None})
                output_dict = None
                time.sleep(0.5)

        except Exception as e:
            output_dict = None
            exception.append({"message": f"{e}", "traceback": traceback.format_exc()})

    if output_dict is not None and sup_url_file_keys:
        output_dict = _replace_file_keys_with_url(output_dict)

    end_time = datetime.utcnow()
    task_id = output_dict["tskId"] if output_dict is not None and "tskId" in output_dict else None
    if task_id is None:
        logger.log("info no task_id", status="running", start_time=start_time, end_time=end_time, job_id=job_id,
               tried_time=to_try, job_info=output_dict, exception=exception)
        return None
    if not tskIdToGet:
        del output_dict['tskId']
    logger.log("get job info", status="running", start_time=start_time, end_time=end_time, task_id=task_id,
               tried_time=to_try, job_info=output_dict, exception=exception)
    return output_dict

def set_job_info(job_id, task_id=None, status=None, estDueTs=None, ts=None, endTs=None, results=None):
    try:
        # Ignore ts

        start_time = datetime.utcnow()

        input_dict = {}
        if status is not None:
            input_dict["status"] = status
        if estDueTs is not None:
            input_dict["estDueTs"] = estDueTs
        if endTs is not None:
            input_dict["endTs"] = endTs
        if results is not None:
            for key in results.keys():
                value = results[key]
                if key not in ['tskId', 'session_id', 'effect', 'status', 'tskSrc']:
                    value = json.dumps(value)
                input_dict[key] = value

        # Auto-set updatedAt for frontend heartbeat-based polling
        input_dict["updatedAt"] = int(time.time() * 1000)

        pf_redis_client.hmset(pf_job_fetcher_config.redisMsgInfoFormat.replace("{msg_id}", f"{job_id}"), input_dict, task_id=task_id)

        end_time = datetime.utcnow()
        logger.log("set job info", task_id=task_id, status="running", start_time=start_time, end_time=end_time, job_info=input_dict)
    except Exception as e:
        logger.log(f"{e}", "Error", status="running")
        logger.log(traceback.format_exc(), "Error", status="running")

def set_job_progress(job_id, progress, external_task_id=None, task_id=None, task_src=None, status=None, results=None, effect=None,
                     error_type=None, **kwargs):
    # external_task_id: the external-facing tskId (preferred). task_id kept for backward compat.
    _task_id = external_task_id or task_id
    try:
        start_time = datetime.utcnow()

        input_dict = {
            "id": job_id,
            "progress": progress,
        }
        if status is not None:
            input_dict["status"] = status
        if effect is not None:
            input_dict["effect"] = effect
        input_dict["results"] = results if results is not None else {}
        input_dict["hostname"] = get_hostname()
        if error_type is not None:
            input_dict["errorType"] = error_type
        input_dict.update(kwargs)

        if task_src is not None:
            progress_queue_key = pf_job_fetcher_config.redisSpecificProgressQueue.replace("{tskSrc}", f"{task_src}")
        else:
            progress_queue_key = pf_job_fetcher_config.redisProgressQueue
        pf_redis_client.lpush(progress_queue_key, json.dumps(input_dict), task_id=_task_id)

        # Refresh updatedAt in job info hash so frontend polling can detect stale jobs
        try:
            set_job_info(job_id, task_id=_task_id)
        except Exception:
            pass

        end_time = datetime.utcnow()
        logger.log("set job progress", task_id=_task_id, status="running", start_time=start_time, end_time=end_time,
                   effect=effect, task_status=status, progress=progress, error_type=error_type, results=results,
                   **kwargs)
    except Exception as e:
        logger.log(f"{e}", "Error", status="running")
        logger.log(traceback.format_exc(), "Error", status="running")

def set_job_error(job_id, error, error_code=None, task_id=None, task_src=None, error_type=None, effect=None, endTs=None,
                  progress_kargs=None, error_message=None):
    try:
        start_time = datetime.utcnow()

        input_dict = {
            "status": "error",
            "error": f"{error}"
        }

        if error_code is not None:
            input_dict["error_code"] = error_code
        if error_type is not None:
            input_dict["error_type"] = error_type
        if endTs is not None:
            input_dict["endTs"] = endTs
        if error_message is not None:
            input_dict["error_message"] = error_message

        pf_redis_client.hmset(pf_job_fetcher_config.redisMsgInfoFormat.replace("{msg_id}", f"{job_id}"), input_dict, task_id=task_id)

        end_time = datetime.utcnow()
        logger.log(f"{error}", "Error", start_time=start_time, end_time=end_time, effect=effect, task_id=task_id,
                   error_code=error_code, error_type=error_type, endTs=endTs, error_message=error_message)

        # if error occuring, also set error status into progressInfo
        if progress_kargs is None:
            progress_kargs = {}
        set_job_progress(job_id, progress=-1, task_id=task_id, task_src=task_src, status="error", effect=effect,
                         error_type=error_type, **progress_kargs)
    except Exception as e:
        logger.log(f"{e}", "Error", status="running")
        logger.log(traceback.format_exc(), "Error", status="running")

def get_extend_job_info(info_key, task_id=None):
    try:
        start_time = datetime.utcnow()
        result = pf_redis_client.hgetall(info_key)
        end_time = datetime.utcnow()
        logger.log("get extend job info", task_id=task_id, status="running", start_time=start_time, end_time=end_time)

        extend_info = {}
        for key in result:
            extend_info[key.decode()] = result[key].decode()
        return extend_info
    except Exception as e:
        logger.log(f"{e}", "Error", status="running")
        logger.log(traceback.format_exc(), "Error", status="running")

def add_cache(session_id: str, cache_name: str, ttl: int, cache: any, task_id:str=None):
    """Add cache to redis

    Args:
        session_id (str): Session id.
        cache_name (str): Cache name.
        ttl (int): Expiration time of the cache, in seconds, with maximum value 1800 (30 min.).
        cache (any): Cache content. Be aware that this will be transfered to string by cache.
            If the transformation is not expected,
            please transfer the content to string by yourself then pass to this method.
        task_id (str): Task id.
    """
    start_time = datetime.utcnow()

    _check_cache_config()
    try:
        cache_key = pf_job_fetcher_config.redisCacheFormat.replace("{session_id}", f"{session_id}") \
            .replace("{cache_name}", f"{cache_name}")
        ttl_tmp = min(ttl, 1800)
        pf_cache_redis_client.set(cache_key, cache, ttl_tmp)

        end_time = datetime.utcnow()
        logger.log("add cache", status="running", task_id=task_id, start_time=start_time, end_time=end_time)
    except Exception as e:
        logger.log(f"{e}", "Error")
        logger.log(traceback.format_exc(), "Error")

def get_cache(session_id: str, cache_name: str, ttl: int, task_id:str=None) -> str:
    """Get cache from redis and update the expiration time

    Args:
        session_id (str): Session id.
        cache_name (str): Cache name.
        ttl (int): Expiration time of the cache, in seconds, with maximum value 1800 (30 min.).
        task_id (str): Task id.

    Returns:
        str: Cache content if set, otherwise None.
    """
    start_time = datetime.utcnow()

    _check_cache_config()
    try:
        cache_key = pf_job_fetcher_config.redisCacheFormat.replace("{session_id}", f"{session_id}") \
            .replace("{cache_name}", f"{cache_name}")
        ttl_tmp = min(ttl, 1800)
        cache_content = pf_cache_redis_client.getex(cache_key, ttl_tmp)

        end_time = datetime.utcnow()
        logger.log("get cache", status="running", task_id=task_id, start_time=start_time, end_time=end_time)

        return cache_content
    except Exception as e:
        logger.log(f"{e}", "Error")
        logger.log(traceback.format_exc(), "Error")

def delete_cache(session_id: str, cache_name: str, task_id:str=None):
    """Delete cache on redis

    Args:
        session_id (str): Session id.
        cache_name (str): Cache name.
        task_id (str): Task id.
    """
    start_time = datetime.utcnow()

    _check_cache_config()
    try:
        cache_key = pf_job_fetcher_config.redisCacheFormat.replace("{session_id}", f"{session_id}") \
            .replace("{cache_name}", f"{cache_name}")
        pf_cache_redis_client.delete(cache_key)

        end_time = datetime.utcnow()
        logger.log("delete cache", status="running", task_id=task_id, start_time=start_time, end_time=end_time)
    except Exception as e:
        logger.log(f"{e}", "Error")
        logger.log(traceback.format_exc(), "Error")

def _check_cache_config():
    if pf_job_fetcher_config.redisCacheFormat is None:
        raise RuntimeError("please set environment variable 'REDIS_CACHE_FORMAT'! It is needed.")


def _check_end_of_service():
    if pf_job_fetcher_config.stopPath and os.path.isfile(pf_job_fetcher_config.stopPath):
        global end_of_service_flag
        end_of_service_flag = True
    if end_of_service_flag:
        logger.log("[Check end of service] Detect stop file. End of service")
        raise RuntimeError('End of Service')

def _augment_url_keys(in_keys):
    url_keys = []
    for key in in_keys:
        if key.endswith("Keys"):
            url_keys.append(key[:-4]+"Urls")
        elif key.endswith("Key"):
            url_keys.append(key[:-3]+"Url")
    return in_keys+url_keys

def _replace_file_keys_with_url(in_job_info):
    job_info = in_job_info.copy()
    for key in list(job_info.keys()):
        if not key.endswith("Urls") and not key.endswith("Url"):
            continue

        to_replace_key = None
        if key.endswith("Url"):
            to_replace_key = key[:-3]+"Key"
            if to_replace_key in job_info and job_info[to_replace_key] is not None:
                del job_info[key]
                continue
        elif key.endswith("Urls"):
            to_replace_key = key[:-4]+"Keys"
            if to_replace_key in job_info and job_info[to_replace_key] is not None and len(job_info[to_replace_key]) != 0:
                del job_info[key]
                continue
        job_info[to_replace_key] = job_info[key]
        del job_info[key]

    return job_info
