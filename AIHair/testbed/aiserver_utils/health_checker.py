from datetime import datetime

from . import common
from .pf_redis import pf_redis_client
import time
import json
import os
from .logger import logger, hb_logger
import traceback
import signal
import threading
import sys
from .common import get_property, current_milli_timestamp, get_hostname_with_process_id
from .queue import requeue_job


class Config(object):
    def __init__(self, env):
        self.CONFIGS_ALLOWED_KEYS = (
            'READY_PATH',
            'STOP_PATH',
            'REDIS_HB_KEY',
            'REDIS_HB_INTERVAL',
            'REDIS_HB_BUSY_TIMEOUT_SECS',
            'REDIS_HB_BUSY_TIMEOUT_PATH',
            'HB_BUSY_TIMEOUT_PATHS',
            'HB_TIMEOUT_TOLERANCE'
        )
        env = self.cleanup_configs_kwargs(**env)

        self.readyPath = env['READY_PATH'] if 'READY_PATH' in env else None
        self.stopPath = env['STOP_PATH'] if 'STOP_PATH' in env else None
        self.redisHbKey = env['REDIS_HB_KEY'] if 'REDIS_HB_KEY' in env else None
        self.redisHbInterval = int(env['REDIS_HB_INTERVAL']) if 'REDIS_HB_INTERVAL' in env else 60

        ''' For timeout '''
        # for v2 timeout
        hbTimeoutToleranceStr = get_property(env, "HB_TIMEOUT_TOLERANCE")
        self.hbTimeoutTolerance = int(hbTimeoutToleranceStr) if hbTimeoutToleranceStr is not None else 1
        # for v1 timeout
        busyTimeoutSecondsStr = get_property(env, "REDIS_HB_BUSY_TIMEOUT_SECS")
        self.busyTimeoutSeconds = int(busyTimeoutSecondsStr) if busyTimeoutSecondsStr is not None else None
        if self.busyTimeoutSeconds is not None and self.busyTimeoutSeconds <= 0:
            self.busyTimeoutSeconds = None
        self.busyTimeoutPath = self._get_hb_timeout_path(env)
        self.busyTimeoutEnabled = self.busyTimeoutSeconds is not None and self.busyTimeoutPath is not None


    def cleanup_configs_kwargs(self, **kwargs):
        connection_kwargs = {
            k: v
            for k, v in kwargs.items()
            if k in self.CONFIGS_ALLOWED_KEYS
        }
        return connection_kwargs

    @staticmethod
    def _get_hb_timeout_path(env):
        busy_timeout_path_format = get_property(env, "HB_BUSY_TIMEOUT_PATHS")
        if busy_timeout_path_format is not None:
            busy_timeout_path = busy_timeout_path_format.format(processId=os.getpid())
        else:
            busy_timeout_path = get_property(env, "REDIS_HB_BUSY_TIMEOUT_PATH")

        if busy_timeout_path is not None:
            os.makedirs(os.path.dirname(busy_timeout_path), exist_ok=True)
        return busy_timeout_path

pf_health_checker_config = Config(os.environ)


def set_engine_ready():
    path = pf_health_checker_config.readyPath
    if not path:
        return

    try:
        _create_empty_file(path)
    except (Exception,):
        traceback.print_exc()
        pass


def setHbInfo(status=None, task_id=None):
    try:
        if not pf_health_checker_config.redisHbKey:
            logger.log("environment variable of REDIS_HB_KEY is not found or empty", status="running")
            return
        value = {"ts": int(time.time() * 1000)}
        value["status"] = status
        if task_id is not None:
            value["task_id"] = task_id

        hostname = get_hostname_with_process_id()

        input_dict = {hostname: json.dumps(value)}
        # print("[setHbInfo] status: ", input_dict, flush=True)

        pf_redis_client.hmset(pf_health_checker_config.redisHbKey, input_dict, task_id=task_id)

        logger.log("heart beat status:" + status, status="running", task_id=task_id, task_status=status)
    except Exception as e:
        logger.log("%s" % str(e), "Error", status="running")
        logger.log(traceback.format_exc(), "Error", status="running")
        print("[setHbInfo] EXCEPTION")
        print(traceback.format_exc())


def _set_hb_info_v2(msg, hb_state):
    try:
        if pf_health_checker_config.redisHbKey is None:
            _hb_log("environment variable of REDIS_HB_KEY is not found", status="running")
            return
        value = {"ts": current_milli_timestamp()}
        value["status"] = hb_state["hb_status"]
        value["task_id"] = hb_state["task_id"]

        hostname = get_hostname_with_process_id()

        input_dict = {hostname: json.dumps(value)}

        pf_redis_client.hmset(pf_health_checker_config.redisHbKey, input_dict, task_id=None)

        _hb_log(msg, status="running", task_id=hb_state["task_id"], task_status=hb_state["hb_status"],
                   effect=hb_state["effect"], job_id=hb_state["job_id"], queue_name=hb_state["q_name"],
                   retry_count=hb_state["retry_cnt"], timeout_ts=hb_state["timeout_ts"])
    except Exception as e:
        _hb_log(f"{e}", "Error", status="running")
        _hb_log(traceback.format_exc(), "Error", status="running")

def _hb_log(*args, **kwargs):
    """
    heartbeat log should put log to hb_logger & logger at the same time.
    Only logger will import into Opeansearch. For debugging purpose, need to have log on OpenSearch.
    hb_logger will be used for engine alive detection.
    """
    hb_logger.log(*args, **kwargs)
    logger.log(*args, **kwargs)


class HeartBeat(object):
    msg_prefix = "[heart beat v1]"
    def __init__(self):
        self.heart_beat_status = "idle"
        self.setHbInfo = setHbInfo
        self.stop_signal = threading.Event()

        ''' For timeout '''
        self.busy_task_id = None
        self.busyTimeoutEnabled = pf_health_checker_config.busyTimeoutEnabled
        logger.log("HeartBeat busy timeout is " + ("enabled" if self.busyTimeoutEnabled else "disabled"),
                   status="running")
        self.log_timeout = False
        self.latest_busy_ts = None
        self.already_timeout = False  # This will be set in both heart beat thread and engine main thread. Need lock.
        self.lock = threading.Lock()  # Prevent race condition for timeout related variables

        self.t = threading.Thread(target=self.run_heart_beat)
        self.t.start()

    def run_heart_beat(self):
        logger.log(f"{self.msg_prefix} run_heart_beat start")
        if pf_health_checker_config.redisHbKey is None:
            logger.log("There is no REDIS_HB_KEY! We won't provide heartbeat function!", status="running")
            return

        while not self.stop_signal.wait(timeout=pf_health_checker_config.redisHbInterval):
            if self.heart_beat_status == "stopped":
                break
            self.setHbInfo(status=self.heart_beat_status)

            ''' If busy timeout, add timeout log '''
            with self.lock:
                if self.log_timeout and self.heart_beat_status == 'busy' and not self.already_timeout \
                        and self.busyTimeoutEnabled and self.latest_busy_ts is not None:
                    if common.current_milli_timestamp() - self.latest_busy_ts > pf_health_checker_config.busyTimeoutSeconds*1000:
                        self.already_timeout = True
                        _append_timeout_log(self.latest_busy_ts, self.busy_task_id)
                        logger.log(f"task timeout", status="running", task_id=self.busy_task_id, busy_ts=self.latest_busy_ts)

        logger.log(f"{self.msg_prefix} run_heart_beat end")

    def change_status(self, status, task_id=None, log_timeout=False):
        self.heart_beat_status = status
        self.busy_task_id = task_id if status == "busy" else None

        ''' If not successive busy timeout. Delete timeout log when status changed to "idle" '''
        with self.lock:
            if log_timeout and self.busyTimeoutEnabled and self.heart_beat_status == 'busy':
                self.latest_busy_ts = common.current_milli_timestamp()
                self.log_timeout = True
            elif self.log_timeout and self.busyTimeoutEnabled and self.heart_beat_status == 'idle':
                if not self.already_timeout:  # if not already timeout, means current task finished in time
                    _remove_timeout_file()
                self.latest_busy_ts = None
                self.already_timeout = False
                self.log_timeout = False

        start_time = datetime.utcnow()
        self.setHbInfo(status=self.heart_beat_status, task_id=task_id)
        end_time = datetime.utcnow()
        logger.log("change heart beat status to:" + status, status="running", start_time=start_time, end_time=end_time)

    def terminate(self):
        logger.log(f"{self.msg_prefix} terminate - start")

        self.stop_signal.set()
        self.t.join()

        logger.log(f"{self.msg_prefix} terminate - done")


def _append_timeout_log(latest_busy_ts, task_id=None):
    try:
        with open(pf_health_checker_config.busyTimeoutPath, "a+") as f:
            f.write(f"busy_ts:{latest_busy_ts}, task_id: {task_id}\n")
    except Exception as e:
        logger.log(f"{e}", "Error", status="running")
        logger.log(traceback.format_exc(), "Error", status="running")


def _remove_timeout_file():
    try:
        if os.path.exists(pf_health_checker_config.busyTimeoutPath):
            os.remove(pf_health_checker_config.busyTimeoutPath)
    except Exception as e:
        logger.log(f"{e}", "Error", status="running")
        logger.log(traceback.format_exc(), "Error", status="running")


class HeartBeatV2(object):
    msg_prefix = "[heart beat v2]"
    def __init__(self, timeout_after_ms):
        self.state = {
            "hb_status": "idle",
            "task_id": None,
            "job_id": None,
            "q_name": None,
            "retry_cnt": None,
            "effect": None,
            "timeout_ts": current_milli_timestamp() + timeout_after_ms # For timeout
        }

        self.setHbInfo = _set_hb_info_v2
        self.stop_signal = threading.Event()

        self.setHbInfo(f"{self.msg_prefix} init - start", self.state)

        self.t = threading.Thread(target=self.run_heart_beat)
        self.t.start()

        self.setHbInfo(f"{self.msg_prefix} init - done", self.state)


    def run_heart_beat(self):
        _hb_log(f"{self.msg_prefix} run_heart_beat start")
        if pf_health_checker_config.redisHbKey is None:
            _hb_log(f"{self.msg_prefix} There is no REDIS_HB_KEY! We won't provide heartbeat function!", status="running")
            return

        while not self.stop_signal.wait(timeout=pf_health_checker_config.redisHbInterval):
            # stop by engine
            if self.state["hb_status"] == "stopped":
                break

            # Check timeout
            if self._is_hb_timeout(): # Is timeout. Log & requeue job if job_id and queue_name are provided
                self.state["hb_status"] = "stopped"
                # For backward compatibility, use same 'msg' when heartbeat as v1
                self.setHbInfo("task timeout", self.state)
                if self.state["job_id"] is not None and self.state["q_name"] is not None:
                    requeue_job(self.state["job_id"], self.state["q_name"], self.state["retry_cnt"],
                                self.state["effect"], self.state["task_id"])
                break # Stop heartbeat

            # Not timeout.
            # For backward compatibility, use same 'msg' when heartbeat as v1
            self.setHbInfo("heart beat status:" + self.state["hb_status"], self.state)

        _hb_log(f"{self.msg_prefix} run_heart_beat end")

    def _is_hb_timeout(self):
        curr_ts = current_milli_timestamp()
        hb_interval_ms = pf_health_checker_config.redisHbInterval*1000
        hb_timeout_tolerance = pf_health_checker_config.hbTimeoutTolerance
        tolerence_dur = hb_timeout_tolerance * hb_interval_ms
        return curr_ts > self.state["timeout_ts"] + tolerence_dur

    def extend_timeout(self, timeout_after_ms):
        self.state["timeout_ts"] = current_milli_timestamp() + timeout_after_ms
        self.setHbInfo(f"{self.msg_prefix} extend timeout ts", self.state)

    def change_status(self, task_status, task_id=None, job_id=None, queue_name=None, retry_count=None, effect=None):
        # Update status
        self.state["hb_status"] = task_status
        self.state["task_id"] = task_id
        self.state["job_id"] = job_id
        self.state["q_name"] = queue_name
        self.state["retry_cnt"] = retry_count
        self.state["effect"] = effect

        # For backward compatibility, use same 'msg' when heartbeat as v1
        self.setHbInfo("change heart beat status to:" + task_status, self.state)

    def terminate(self):
        self.state["hb_status"] = "stopped"

        self.setHbInfo(f"{self.msg_prefix} terminate - start", self.state)

        self.stop_signal.set()
        self.t.join()

        self.setHbInfo(f"{self.msg_prefix} terminate - done", self.state)


def set_engine_stop():  # Do not have to remove files because engine container restart will not keep the disk
    logger.log("job_fecher is stopping! If it is not stopped by stopfile, supervisor will restart job_fecher again! See you!", status="running")
    # if pf_health_checker_config.readyPath and os.path.isfile(pf_health_checker_config.readyPath):
    #     os.remove(pf_health_checker_config.readyPath)

    # if pf_health_checker_config.busyTimeoutPath and os.path.isfile(pf_health_checker_config.busyTimeoutPath):
    #     os.remove(pf_health_checker_config.busyTimeoutPath)

    if pf_health_checker_config.stopPath and os.path.isfile(pf_health_checker_config.stopPath):
        # logger.log("Stopped by stop file! Supervisord WON't restart job_fecher! Good bye!", status="stopping")
        # os.remove(pf_health_checker_config.stopPath)
        sys.exit(9)


def _create_empty_file(file_path):
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, 'a'):
        pass


def signal_handler(signum, frame):
    if "STOP_PATH" in os.environ:
        logger.log("set stop file!!!!!!!!!!!!")
        stop_path = os.environ["STOP_PATH"]
        _create_empty_file(stop_path)


signal.signal(signal.SIGTERM, signal_handler)
