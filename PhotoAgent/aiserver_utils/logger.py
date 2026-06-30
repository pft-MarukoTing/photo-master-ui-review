import inspect
import os
# import cv2
import json
import datetime
import traceback
import logging
from logging import handlers

from .common import get_hostname_with_process_id, get_property, get_hostname
from .version import get_version
from inspect import signature

def get_usage_info():
    import subprocess
    import psutil

    def _output_to_list(x):
        return x.decode('ascii').split('\n')[:-1]

    try:
        command = "nvidia-smi --query-gpu=memory.used --format=csv"
        memory_usage_info = _output_to_list(subprocess.check_output(command.split()))[1:]
        gpu_mem_usage = int(memory_usage_info[0].split()[0])

        command = "nvidia-smi --query-gpu=utilization.gpu --format=csv"
        cpu_usage_info = _output_to_list(subprocess.check_output(command.split()))[1:]
        gpu_usage = cpu_usage_info[0]

        tot_m, used_m, free_m = map(int, os.popen('free -t -m').readlines()[-1].split()[1:])
        cpu_mem_usage = used_m

        cpu_usage = psutil.cpu_percent(interval=0.1)

        return {"CPU": f"{cpu_usage} %", "GPU": f"{gpu_usage}", "CPU Mem": f"{cpu_mem_usage}", "GPU Mem": f"{gpu_mem_usage}"}
    except (Exception, ):
        pass


class Logger(object):
    _instance = {}

    def __new__(cls, log_dst="default"):
        if cls._instance.get(log_dst) is not None:
            return cls._instance[log_dst]

        self = super().__new__(cls)
        cls._instance[log_dst] = self

        self.log_dst = log_dst
    
        # self.log_path = os.environ["LOG_PATH"] if "LOG_PATH" in os.environ else None
        self.dump_path = os.environ["DUMP_PATH"] if "DUMP_PATH" in os.environ else None
        self.logging_path = None
        self.logging_fp = None
        self.logging_queue = None
        git_ver = get_version()

        if self.dump_path:
            os.makedirs(self.dump_path, exist_ok=True)

        self.hostname = get_hostname()

        self.my_log = None
        if self.log_dst == "default":
            self.log_path = self._get_log_path()
        elif self.log_dst == "hb":
            self.log_path = self._get_hb_log_path()
        else:
            raise ValueError(f"Unknown log_dst: {self.log_dst}")

        self.my_log = self._logging(self.log_path)

        self.log(ver=git_ver, status="init")

        return cls._instance[log_dst]

    def _logging(self, filename):
        rotate_time = datetime.time(hour=9, minute=0, second=0)
        log = logging.getLogger(filename)
        formatter = logging.Formatter('%(message)s')

        # th = handlers.TimedRotatingFileHandler(filename=filename, when='midnight', backupCount=5, encoding='utf-8', atTime=rotate_time)
        kwargs = {
            'mode': 'a',
            'maxBytes': 5*1024*1024,
            'backupCount': 5,
            'encoding': 'utf-8',
            'delay': False,
        }
        if signature(handlers.RotatingFileHandler).parameters.get('errors'):
            kwargs['errors'] = None
        th = handlers.RotatingFileHandler(filename, **kwargs)

        th.setFormatter(formatter)
        th.setLevel(logging.INFO)
        log.addHandler(th)
        log.setLevel(logging.INFO)
        return log

    def set_logging_path(self, logging_path):
        if logging_path is not None:
            os.makedirs(os.path.dirname(logging_path), exist_ok=True)
            self.logging_path = logging_path
            self.logging_fp = open(logging_path, "a+")
        else:
            self.logging_path = None

    def set_logging_queue(self, logging_queue):
        self.logging_queue = logging_queue

    def terminate_logging_queue(self):
        self.p.join()
        self.logging_queue.put('kill')

    def log(self, msg="", action="Debug", task_id=None, task_status=None, error_code=None, error_type=None, status=None,
            enqueue_time=None, start_time=None, end_time=None, ver=None, effect=None, handled_file_size=None, proc_time=None,
            **kwargs):
        def _info():
            src = self._get_log_caller_src()["src"]
            info = {"date": self._time_to_iso(datetime.datetime.utcnow()),
                    "action": action,
                    "msg": msg,
                    "log_src": src,
                    "pid": os.getpid()
            }
            if ver:
                info["ver"] = ver
            if error_code:
                info["error_code"] = error_code
            if error_type:
                info["error_type"] = error_type
            if task_id:
                info["task_id"] = task_id
            if task_status:
                info["task_status"] = task_status
            if status:
                info["status"] = status
            if effect:
                info["effect"] = effect
            if kwargs:
                info["params"] = kwargs
            if self.hostname:
                info["hostname"] = self.hostname
            if enqueue_time:
                info["enqueue_time"] = self._time_to_iso(enqueue_time)
            if start_time:
                info["start_time"] = self._time_to_iso(start_time)
            if end_time:
                info["end_time"] = self._time_to_iso(end_time)
            if handled_file_size is not None:  # Allow 0
                info["handled_file_size"] = handled_file_size
            if proc_time:
                info["proc_time"] = proc_time
            return info

        if self.logging_fp is not None:
            info = _info()
            for i in range(0, 3):
                try:
                    self.logging_fp.write(f"{json.dumps(info)}\n")
                    break
                except (Exception, ):
                    self.logging_fp = open(self.logging_path, "a+")
                    traceback.print_exc()
                    pass

        elif self.my_log is not None:
            try:
                self.my_log.info(f"{json.dumps(_info())}")
            except (Exception, ):
                self.my_log = self._logging(filename=self.log_path)

        elif self.logging_queue is not None:
            self.logging_queue.put(f"{json.dumps(_info())}")
        else:
            print(f"{datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%s')} [{action}] {msg} {kwargs if len(kwargs) > 0 else ''}")

    def debug(self, msg="", task_id=None):
        self.log(msg, action="Debug", task_id=task_id, error_code=None)

    def warning(self, msg="", task_id=None):
        self.log(msg, action="Warning", task_id=task_id, error_code=None)

    def log_usage(self, msg="", task_id=None):
        usage_info = get_usage_info()
        if usage_info is not None:
            self.log(msg, task_id=task_id, **usage_info)

    def _time_to_iso(self, datetime):
        return datetime.isoformat(timespec='milliseconds')+'Z'

    @staticmethod
    def _get_log_caller_src():
        try:
            frame = inspect.currentframe().f_back.f_back.f_back # `_get_log_caller_src` -> `_info` -> `log` -> caller
            caller_module = frame.f_globals.get('__name__')
            src_module = caller_module
            curr_module = __name__.split('.')[0]
            if caller_module.startswith(curr_module):
                src = "lib"
            else:
                src = "eng"
            return {"src": src, "src_module": src_module}
        except (Exception, ):
            return {"src": None, "src_module": None}

    @staticmethod
    def _get_log_path():
        log_paths_format = get_property(os.environ, "LOG_PATHS")
        if log_paths_format is not None:
            log_path = log_paths_format.format(processId=os.getpid())
        else:
            log_path = get_property(os.environ, "LOG_PATH")
        if log_path is not None:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
        return log_path

    @staticmethod
    def _get_hb_log_path():
        ori_log_path = Logger._get_log_path()
        hb_log_path, ext = os.path.splitext(ori_log_path)
        hb_log_path = f"{hb_log_path}_hb{ext}"
        return hb_log_path


logger = Logger()
hb_logger = Logger(log_dst="hb")

