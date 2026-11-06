import json
import queue
import threading
import traceback
from datetime import datetime
from queue import Queue

from redis import RedisCluster

from aiserver_utils.logger import logger


class AsyncRedisWorker(threading.Thread):
    REDIS_ORI_METHOD_PREFIX = "_redis_ori_"

    def __init__(self, op_queue: Queue, base_redis_cluster: RedisCluster, region_redis_client: RedisCluster,
                 enable_region_redis=False, async_proxy_queue=None):
        threading.Thread.__init__(self)
        self.op_queue : Queue = op_queue
        self.stop_event = threading.Event()
        self.base_redis_cluster : RedisCluster = base_redis_cluster
        self.region_redis_client : RedisCluster = region_redis_client
        self.enable_region_redis = enable_region_redis
        self.async_proxy_queue = async_proxy_queue

    def stop(self):
        self.stop_event.set()

    def run(self):
        task_id = None
        data = None
        command = None
        msg_prefix = "[AsyncRedisWorker run async op]"
        while True:  # avoid infinite blocking
            try:
                data = None
                data = self.op_queue.get(timeout=0.01)

                # got data
                before_time = datetime.utcnow()
                command = data.get("cmd")
                args = data.get("args", [])
                kwargs = data.get("kwargs", {})
                task_id = data.get("task_id")  # None for library specific operations
                enqueue_time = data.get("enq_time")
                op_src = data.get("op_src", None)

                if not self.enable_region_redis:
                    func = getattr(self.base_redis_cluster, command.lower())  # Should exist because of validation in AsyncableRedisCluster.async_op
                    func(*args, **kwargs)
                else:  # Pass command to the proxy queue
                    data_for_proxy = self._get_str_format_command(task_id, command, *args, **kwargs)
                    # print(f"{data_for_proxy['cmd_str']}")
                    self.region_redis_client.lpush(self.async_proxy_queue, json.dumps(data_for_proxy, ensure_ascii=False))

                after_time = datetime.utcnow()
                logger.log(f"{msg_prefix} success", task_id=task_id, command=command, enqueue_time=enqueue_time, op_src=op_src,
                           start_time=before_time, end_time=after_time, status="running", thread_id=threading.current_thread().name,)

            except queue.Empty:
                # print(f"{msg_prefix} Waiting data at {datetime.utcnow()} on thread {threading.current_thread().name}")
                pass

            except Exception as e:
                logger.log(f"{msg_prefix} error", "Error", status="running", task_id=task_id, command=command,
                           error=f"{e}", error_traceback=traceback.format_exc(), thread_id=threading.current_thread().name)
            finally:
                if data is not None:
                    self.op_queue.task_done()
                if self.op_queue.empty() and self.stop_event.is_set():
                    # Break the loop and stop this worker if stop event is set and queue is empty
                    break

    def _get_str_format_command(self, task_id, command, *args, **kwargs):
        cmd_lower = command.lower()
        copy_func = getattr(self, f"{self.REDIS_ORI_METHOD_PREFIX}{cmd_lower}", None)
        if copy_func is None:
            # Should not happen
            raise NotImplementedError(f"Command '{command}' not supported")

        list_format_command = copy_func(*args, **kwargs)
        str_format_command = self._list_format_command_to_str_format_command(list_format_command)

        return { "cmd_str": str_format_command, "task_id": task_id }

    @staticmethod
    def _redis_sdk_format_to_list_format_command(command, *args, **options):
        list_format_command = [command]
        list_format_command.extend(args)
        if options:
            for k, v in options.items():
                list_format_command.append(k)
                if v is not True:
                    list_format_command.append(v)
        return list_format_command

    @staticmethod
    def _list_format_command_to_str_format_command(list_format_command):
        def _to_str(item):
            if isinstance(item, bytes):
                return item.decode('utf-8', 'ignore')
            elif isinstance(item, str):
                return json.dumps(item, ensure_ascii=False)
            else:
                return str(item)

        return (list_format_command[0] + ' ' +
                ' '.join([_to_str(item) for item in list_format_command][1:]))

    """
    Copied methods from redis-py to convert args to list format command - Start
    To support a new command:
    1. Copy the implementation from redis-py
    2. Modify the method name to start with REDIS_ORI_METHOD_PREFIX
    3. Replace 'DataError' with 'RuntimeError'
    4. Remove warnings + remove type hints so that we do not need to import them
    5. Modify the return statement to use 'return self._redis_sdk_format_to_list_format_command("COMMAND", ...)'
    """
    def _redis_ori_zadd(
        self,
        name,
        mapping,
        nx: bool = False,
        xx: bool = False,
        ch: bool = False,
        incr: bool = False,
        gt: bool = False,
        lt: bool = False,
    ):
        if not mapping:
            raise RuntimeError("ZADD requires at least one element/score pair")
        if nx and xx:
            raise RuntimeError("ZADD allows either 'nx' or 'xx', not both")
        if gt and lt:
            raise RuntimeError("ZADD allows either 'gt' or 'lt', not both")
        if incr and len(mapping) != 1:
            raise RuntimeError(
                "ZADD option 'incr' only works when passing a "
                "single element/score pair"
            )
        if nx and (gt or lt):
            raise RuntimeError("Only one of 'nx', 'lt', or 'gr' may be defined.")

        pieces: list = []
        options = {}
        if nx:
            pieces.append(b"NX")
        if xx:
            pieces.append(b"XX")
        if ch:
            pieces.append(b"CH")
        if incr:
            pieces.append(b"INCR")
            options["as_score"] = True
        if gt:
            pieces.append(b"GT")
        if lt:
            pieces.append(b"LT")
        for pair in mapping.items():
            pieces.append(pair[1])
            pieces.append(pair[0])
        return self._redis_sdk_format_to_list_format_command("ZADD", name, *pieces, **options)


    def _redis_ori_hmset(self, name: str, mapping: dict):
        if not mapping:
            raise RuntimeError("'hmset' with 'mapping' of length 0")
        items = []
        for pair in mapping.items():
            items.extend(pair)
        return self._redis_sdk_format_to_list_format_command("HMSET", name, *items)

    def _redis_ori_lpush(self, name: str, *values):
        return self._redis_sdk_format_to_list_format_command("LPUSH", name, *values)

    """ Copy methods from redis-py to convert args to list format command - End """

    @staticmethod
    def get_all_support_redis_write_commands():
        return [func_name[len(AsyncRedisWorker.REDIS_ORI_METHOD_PREFIX):].upper() for func_name in dir(AsyncRedisWorker)
                if func_name.startswith(AsyncRedisWorker.REDIS_ORI_METHOD_PREFIX)]
