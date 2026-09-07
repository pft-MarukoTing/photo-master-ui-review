import load_env

import gc
import os
import threading
import time
import traceback

import requests
from PIL import Image as PILImage

from server_logger import ServerLogger

from aiserver_utils.queue import fetch_job, set_job_info, set_job_error, set_job_progress
from aiserver_utils.pf_redis import pf_redis_client
from aiserver_utils.s3 import S3AccessV2
from aiserver_utils.logger import logger as aiutils_logger
import aiserver_utils.health_checker as health_checker
from aiserver_utils.health_checker import HeartBeatV2

from photo_agent import EFFECT_NAME
from artifact_store import S3ArtifactStore
from engine_result_code import RESULT_UNKNOWN_INTERNAL_ERROR, RESULT_AI_SERVICE_TIMEOUT, RESULT_EXCEED_MAX_RESOLUTION
from job_processor import PhotoAgentJobProcessor


ENGINE_VERSION = 'photoAgent-backbone-001'
ENGINE_WORKER_NUM = max(int(os.getenv('ENGINE_WORKER_NUM', '1')), 1)

MSG_ID_QUEUE = os.environ['REDIS_MSG_QUEUE'].split(",")[0]

# Read by backend/routes/static.py's /api/version so the browser can detect a
# worker deploy even though this process and the Flask app are separate
# containers with no shared memory -- Redis is the only channel between them.
# Falls back to a per-process-start value so a plain container restart (no
# code change) still surfaces as "updated" without requiring a manual bump.
WORKER_VERSION_KEY = os.getenv('REDIS_WORKER_VERSION_KEY', 'photoagent:version:worker')
WORKER_VERSION = os.getenv('WORKER_VERSION') or '%s-%d' % (ENGINE_VERSION, int(time.time()))

MONITOR_DST_DIR = './tmp'
os.makedirs(MONITOR_DST_DIR, exist_ok=True)

artifact_store = None
s3_accessor = None
redis_conn = None
logger = None
heart_beat = None
heart_beat_timeout = None
job_processor = None
heart_beat_lock = threading.Lock()
active_workers = 0
active_workers_lock = threading.Lock()


class Logger:
    def __init__(self) -> None:
        self.server_logger = ServerLogger()
        self.server_logger.initialize()

    def info(self, task_id, message):
        self.server_logger.info(task_id, message)
        aiutils_logger.log('[%s][%s] %s' %
                           (ENGINE_VERSION, task_id, message), "Info", status="running")

    def error(self, task_id, message):
        self.server_logger.error(task_id, message)
        aiutils_logger.log('[%s][%s] ERROR!! %s' %
                           (ENGINE_VERSION, task_id, message), "Error", status="running")


def init():
    """Set up server dependencies and shared resources."""
    global logger, heart_beat, heart_beat_timeout, artifact_store, s3_accessor, redis_conn, job_processor

    s3_accessor = S3AccessV2()
    logger = Logger()
    artifact_store = S3ArtifactStore(s3_accessor, logger, MONITOR_DST_DIR)
    job_processor = PhotoAgentJobProcessor(artifact_store, logger, MONITOR_DST_DIR)
    redis_conn = pf_redis_client

    try:
        redis_conn.ping()
        logger.info('', "Redis connection successful %s" %(MSG_ID_QUEUE))
    except Exception as e:
        logger.error('', "Fail to connect to redis cluster: %s" % str(e))
        raise e

    try:
        redis_conn.set(WORKER_VERSION_KEY, WORKER_VERSION)
        logger.info('', "Published worker version: %s" % WORKER_VERSION)
    except Exception as e:
        logger.error('', "Failed to publish worker version: %s" % str(e))

    heart_beat_timeout = 60000
    heart_beat = HeartBeatV2(timeout_after_ms=heart_beat_timeout)

    def thread_exception_hook(*args):
        msg = ''.join(traceback.format_exception(
            args[0].exc_type, args[0].exc_value, args[0].exc_traceback))
        logger.error('', msg)

    threading.excepthook = thread_exception_hook
    logger.info('', "photoAgent server initialization completed")


def upload_file(filepath, dstKey):
    return artifact_store.upload_file(filepath, dstKey)


def download_file(key, folder_name=None):
    return artifact_store.download_file(key, folder_name=folder_name)


def s3_create_presigned_url(object_name: str, action: str) -> str:
    return artifact_store.s3_create_presigned_url(object_name, action)


def process_for_api(task_id, task_info):
    return job_processor.process_for_api(task_id, task_info)


def check_queue():
    global active_workers
    logger.info('', '[CheckingQueue] start')
    while True:
        try:
            with heart_beat_lock:
                heart_beat.extend_timeout(heart_beat_timeout)
            task_id, task_info = fetch_job(aug_keys=['effect', 'app', 'uid', 'os', 'appVer', 'verType', 'uniqueId', 'umaId'])
        except Exception as e:
            logger.info('', "Got stop signal: %s" % str(e))
            with heart_beat_lock:
                heart_beat.terminate()
            return

        if not task_id or not task_info:
            continue

        tsk_id = task_info.get('tskId', 'N/A')
        effect = task_info.get('effect', EFFECT_NAME)

        try:
            with active_workers_lock:
                active_workers += 1
            with heart_beat_lock:
                heart_beat.change_status("busy", task_id=tsk_id, job_id=task_id, queue_name=MSG_ID_QUEUE)
                heart_beat.extend_timeout(heart_beat_timeout)

            logger.info(task_id, '[Worker] task_id: %s(%s)' % (task_id, tsk_id))
            results = process_for_api(task_id, task_info)

            ts_now = int(time.time() * 1000)
            set_job_info(job_id=task_id, status="success",
                        estDueTs=ts_now, ts=None, endTs=ts_now,
                        task_id=tsk_id,
                        results=results)
            set_job_progress(task_id, 100, status="success", results=results, task_id=tsk_id, effect=effect, fetch_job_ts=task_info.get('ts'))
        except Exception as e:
            error_message = str(e)
            if len(e.args) > 1:
                error_code = e.args[1]
            elif isinstance(e, requests.exceptions.Timeout):
                error_code = RESULT_AI_SERVICE_TIMEOUT
            elif isinstance(e, PILImage.DecompressionBombError):
                error_code = RESULT_EXCEED_MAX_RESOLUTION
                error_message = "Image resolution is too high, please use a smaller image."
            else:
                error_code = RESULT_UNKNOWN_INTERNAL_ERROR
            logger.error(task_id, f'[Worker] process with error: {error_message}')
            set_job_error(task_id, error=error_code, error_code=error_code, task_id=tsk_id, effect=effect, fetch_job_ts=task_info.get('ts'), error_message=error_message)
            set_job_progress(task_id, 100, status="error", task_id=tsk_id, effect=effect, fetch_job_ts=task_info.get('ts'))
        finally:
            with active_workers_lock:
                active_workers -= 1
                should_set_idle = (active_workers == 0)
            if should_set_idle:
                with heart_beat_lock:
                    heart_beat.change_status("idle", task_id=tsk_id, job_id=task_id, queue_name=MSG_ID_QUEUE)
            gc.collect()


def launch_checking_queue_thread():
    threads = []
    for i in range(ENGINE_WORKER_NUM):
        thread = threading.Thread(target=check_queue, name=f'worker-{i}')
        thread.daemon = True
        thread.start()
        threads.append(thread)
    logger.info('', f'Launched {ENGINE_WORKER_NUM} worker thread(s)')
    return threads


if __name__ == "__main__":
    try:
        init()

        threads = launch_checking_queue_thread()
        health_checker.set_engine_ready()
        for t in threads:
            t.join()

    except Exception as e:
        print(traceback.format_exc())
        if logger is not None:
            logger.error('', "exception from main: %s" % str(e))

    finally:
        if heart_beat is not None:
            with heart_beat_lock:
                heart_beat.terminate()
        if logger is not None:
            logger.info('', "job_fetcher is stopping!")
        health_checker.set_engine_stop()
