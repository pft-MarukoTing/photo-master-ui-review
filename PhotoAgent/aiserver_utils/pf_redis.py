import inspect
import queue
import threading
from datetime import datetime

from redis import Redis
from redis.cluster import RedisCluster, ClusterNode
import os
from .aescipher import AESCipher
from .async_redis_thread import AsyncRedisWorker
from .common import get_property, is_empty_str
from .logger import logger

cipher = AESCipher()

def _cleanup_configs_kwargs(config_allowed_keys, **kwargs):
    connection_kwargs = {
        k: v
        for k, v in kwargs.items()
        if k in config_allowed_keys
    }
    return connection_kwargs


class Config(object):
    def __init__(self, env, prop_prefix):
        self.configs_allowed_keys = [f"{prop_prefix}{key}" for key in (
            'URL', 'USER', 'PASS', 'NODES', 'SSL', 'MODE'
        )]
        env = _cleanup_configs_kwargs(self.configs_allowed_keys, **env)

        # Optional
        self.redisSSL = get_property(env, prop_prefix + 'SSL')
        self.redisURL = get_property(env, prop_prefix + 'URL')
        self.redisMode = get_property(env, prop_prefix + 'MODE', 'cluster')

        # Required
        self.redisUser = get_property(env, prop_prefix + 'USER', allow_empty=True)
        pass_encrypted = get_property(env, prop_prefix + 'PASS', allow_empty=True)
        self.redisPass = cipher.decrypt(pass_encrypted) if pass_encrypted else pass_encrypted
        redis_nodes_str = get_property(env, prop_prefix + 'NODES')
        self.redisNodes = redis_nodes_str.split(',') if redis_nodes_str else None

        if self.redisUser is None or self.redisPass is None or self.redisNodes is None:
            raise RuntimeError(
                f"please set environment variable '{prop_prefix}USER', '{prop_prefix}PASS', '{prop_prefix}NODES'! They are needed."
            )


def is_redis_share_same_client(base_prop_prefix, extend_prop_prefix):  # extend can be cache or region
    """
    Check whether the redis connection should be shared between base redis client and extended one,
    according to '{prefix}NODES' environment variables.
    If same, should share the same redis client.
    """
    base_nodes = get_property(os.environ, base_prop_prefix + 'NODES')
    extend_nodes = get_property(os.environ, extend_prop_prefix + 'NODES')
    if extend_nodes is None or base_nodes == extend_nodes:
        return True
    return False

# def initRedisClient_simple(config):
#     from urllib.parse import urlparse
#     redis_url = urlparse(config.redisURL)
#     REDIS_HOST = redis_url.hostname
#     REDIS_PORT = redis_url.port
#     REDIS_DB = 0
#     redis_conn = Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=False)
#     return redis_conn


def initRedisClient(config):
    if config.redisMode != "cluster":
        return initRedisStandAloneClient(config)
    
    if config.redisSSL == "true":
        redisSSL = True
    else:
        redisSSL = False

    redis_connection_params = {
        'max_connections': 10,
        'username': config.redisUser,
        'password': config.redisPass,
        'encoding': 'utf-8',
        # 'decode_responses': True,
        'ssl': redisSSL
    }
    # logger.log("create redis client", action="Init", **redis_connection_params)
    nodes = list()

    for n in config.redisNodes:
        host, port = n.split(':')
        # print("host, port! %s", n, flush=True)
        nodes.append(ClusterNode(host=host, port=port))

    client = RedisCluster(startup_nodes=nodes, read_from_replicas=True, **redis_connection_params)
    # print("initRedisClient:", client, flush=True)
    return client

# {"<given name>": {"used_nodes": "<nodes original str format>", "client_instance": <The root client instance>}}
_existing_redis_client_name_to_info = {}
def _init_redis_client_with_fallback(init_name, prop_prefix, fallback_sequence_names = None):
    global _existing_redis_client_name_to_info
    if fallback_sequence_names is None:
        fallback_sequence_names = []

    # Not set redis nodes, try to fallback to other existing redis client
    target_redis_nodes = get_property(os.environ, prop_prefix + 'NODES')
    nodes_not_set = is_empty_str(target_redis_nodes)
    if nodes_not_set and len(fallback_sequence_names) == 0:
        # Should not happen
        raise RuntimeError(f"Cannot init redis client '{init_name}'")
    if nodes_not_set:
        fallback_name = fallback_sequence_names[0] # Try to fallback to the first one
        if fallback_name not in _existing_redis_client_name_to_info:
            raise RuntimeError(f"Cannot init redis client '{init_name}', try to fallback to '{fallback_name}' client but it does not exist")
        _existing_redis_client_name_to_info[init_name] = _existing_redis_client_name_to_info[fallback_name]
        return _existing_redis_client_name_to_info[init_name]['client_instance']

    # Set redis nodes. Fallback if same nodes, otherwise create a new client
    for fallback_name in fallback_sequence_names:
        if fallback_name not in _existing_redis_client_name_to_info:
            raise RuntimeError(f"Cannot init redis client '{init_name}', try to fallback to '{fallback_name}' client but it does not exist")
        fallback_nodes = _existing_redis_client_name_to_info[fallback_name]['used_nodes']
        if fallback_nodes == target_redis_nodes:
            _existing_redis_client_name_to_info[init_name] = _existing_redis_client_name_to_info[fallback_name]
            return _existing_redis_client_name_to_info[init_name]['client_instance']

    # Create a new client
    init_config = Config(os.environ, prop_prefix)
    new_client = initRedisClient(init_config)
    _existing_redis_client_name_to_info[init_name] = {
        "used_nodes": target_redis_nodes,
        "client_instance": new_client,
    }
    return new_client


def initRedisStandAloneClient(config):
    REDIS_HOST = None
    REDIS_PORT = None
    REDIS_DB = 0
    for n in config.redisNodes:
        REDIS_HOST, REDIS_PORT = n.split(":")
        break
    REDIS_USER = config.redisUser
    REDIS_PASS = config.redisPass
    redis_conn = Redis(
        host=REDIS_HOST, port=REDIS_PORT, username=REDIS_USER, password=REDIS_PASS, db=REDIS_DB, decode_responses=False
    )
    return redis_conn


class PfRedisClientService:
    """
    A class to manage PF customized redis behavior.
    This class is designed to handle asynchronous write operations using worker threads.
    Async feature is disabled when init, and can be started by engine.
    After starting the async feature and before terminating it, all write operations will be asynchronous.
    If set region redis config, write operations will be envoloped and send to region redis asynchronously.
    If not, write operations will be sent to base redis asynchronously.
    If async feature is not started, all write operations keep synchronous to base redis.
    """
    msg_prefix = "[PfRedisClientService]"

    def __init__(self, base_redis_client: RedisCluster, region_redis_client: RedisCluster, **kwargs):
        logger.log(f"{self.msg_prefix} init - start")

        self._base_redis_client = base_redis_client
        self._region_redis_client = region_redis_client
        # Share same client will do redis write operations async to base_redis_client. If use different client, will envolop command to _region_redis_client
        self._enable_region_redis = base_redis_client is not region_redis_client

        # Async feature is disabled when init.
        self._async_workers_lifecycle_lock = threading.RLock()
        self._async_lib_op_queue = None  # This queue is used for library specific operations not for specific tasks, liek heartbeat log
        self._async_op_queues = []
        self._async_workers = []
        self.async_worker_num = int(get_property(os.environ, 'REDIS_ASYNC_WORKER_NUM', '3'))
        if self._enable_region_redis:
            self.async_worker_num = int(get_property(os.environ, 'REGION_REDIS_ASYNC_WORKER_NUM', '3'))
        self.async_proxy_queue = get_property(os.environ, 'REGION_REDIS_ASYNC_PROXY_QUEUE')
        self.write_commands = AsyncRedisWorker.get_all_support_redis_write_commands()

        logger.log(f"{self.msg_prefix} init - success", async_proxy_queue=self.async_proxy_queue,
                   async_worker_num=self.async_worker_num, enable_region_redis=self._enable_region_redis,
                   write_commands=self.write_commands)

    def __getattr__(self, name):
        """
        Intercepts all method requests and check self first.
        If using write command:
        - If async feature is not started, delegate to base_redis_client.
        - If async feature is started, distribute the operation to one of the async queues.
        If using read command, delegate to base_redis_client
        """
        # Ignore attributes thant cause recursion
        if name in ['base_redis_client']:
            raise AttributeError(f"'PfRedisClientService' object has no attribute '{name}'")
        func = getattr(self._base_redis_client, name)
        if func is None:
            raise AttributeError(f"'PfRedisClientService' object has no attribute '{name}'")

        command = name.upper()
        if command not in self.write_commands or not self._is_async_workers_started():
            def sync_wrapper(*args, **kwargs):
                # Process additional logic or arguments here if needed
                task_id = kwargs.pop('task_id', None)  # task_id is only used for async write commands
                return func(*args, **kwargs)
            return sync_wrapper

        # Write command and async feature is started
        def async_wrapper(*args, **kwargs):
            task_id = kwargs.pop('task_id', None)
            return self._async_op(task_id, command, *args, **kwargs)
        return async_wrapper

    def _create_and_start_worker_threads(self):
        """
        Create worker threads for asynchronous operations.
        """
        # For library specific operations, like heartbeat log.
        with self._async_workers_lifecycle_lock:
            logger.log(f"{self.msg_prefix} init lib specific worker", async_proxy_queue=self.async_proxy_queue,
                       enable_region_redis=self._enable_region_redis)
            self._async_lib_op_queue = queue.Queue()
            t = AsyncRedisWorker(self._async_lib_op_queue, self._base_redis_client, self._region_redis_client,
                                 enable_region_redis=self._enable_region_redis, async_proxy_queue=self.async_proxy_queue)
            self._async_workers.append(t)
            t.start()

            # For task-specific operations
            logger.log(f"{self.msg_prefix} init task specific workers", async_proxy_queue=self.async_proxy_queue,
                       enable_region_redis=self._enable_region_redis, async_worker_for_task_num=self.async_worker_num)
            for _ in range(self.async_worker_num):
                op_queue = queue.Queue()
                self._async_op_queues.append(op_queue)
                t = AsyncRedisWorker(op_queue, self._base_redis_client, self._region_redis_client,
                                     enable_region_redis=self._enable_region_redis, async_proxy_queue=self.async_proxy_queue)
                self._async_workers.append(t)
                t.start()

    def _terminate_worker_threads(self):
        # Terminate all async worker threads.
        with self._async_workers_lifecycle_lock:
            logger.log(f"{self.msg_prefix} terminate all worker")
            for t in self._async_workers:
                t.stop()
            for t in self._async_workers:
                t.join()

            self._async_lib_op_queue = None
            self._async_op_queues = []
            self._async_workers = []

    def _is_async_workers_started(self):
        """
        Check if async workers are started.
        """
        with self._async_workers_lifecycle_lock:
            return bool(self._async_workers)

    def start_async_workers(self):
        """
        Start async workers by engine.
        If not started, redis client should use synchronous operations.
        """
        with self._async_workers_lifecycle_lock:
            logger.log(f"{self.msg_prefix} start async workers - start")
            if self._is_async_workers_started():
                logger.log(f"{self.msg_prefix} async workers already started, skipping.")
                return

            self._create_and_start_worker_threads()
            logger.log(f"{self.msg_prefix} start async workers - success")

    def terminate_async_workers(self):
        """
        Terminate async workers by engine
        """
        with self._async_workers_lifecycle_lock:
            logger.log(f"{self.msg_prefix} terminate async workers - start")
            if not self._is_async_workers_started():
                logger.log(f"{self.msg_prefix} async workers not started, skipping termination.")
                return

            self._terminate_worker_threads()
            logger.log(f"{self.msg_prefix} terminate async workers - success")

    def _get_q_by_task_id(self, task_id):
        """
        Get the index of the async queue based on the task_id.
        This is a simple hash function to distribute jobs across queues.
        """
        # library specific operations will have task_id=None
        if task_id is None:
            return self._async_lib_op_queue

        # For task-specific operations, distribute based on task_id
        q_idx = hash(task_id) % len(self._async_op_queues)
        return self._async_op_queues[q_idx]

    def _async_op(self, task_id, command, *args, **kwargs):
        op_src = self._get_curr_module_calling_methods()

        op_queue = self._get_q_by_task_id(task_id)
        enqueue_time = datetime.utcnow()
        async_op = {
            'cmd': command,
            'args': args,
            'kwargs': kwargs,
            'task_id': task_id,
            'enq_time': enqueue_time,
            'op_src': op_src,  # For logging
        }
        op_queue.put(async_op)

    @staticmethod
    def _get_curr_module_calling_methods():
        """
        Cascade the calling methos in this module.
        """
        try:
            calling_methods = []
            frame = inspect.currentframe().f_back.f_back.f_back # `_get_curr_module_calling_methods` -> `asyncable_op` -> async_wrapper -> caller
            while frame:
                code = frame.f_code
                caller_module = frame.f_globals.get('__name__')
                curr_module = __name__.split('.')[0]
                if not caller_module.startswith(curr_module):
                    break
                method_name = code.co_name
                if method_name == "__init__":  # Show class name if it's an init method
                    method_name = frame.f_locals.get('self', None).__class__.__name__ + ".__init__"
                calling_methods.append(method_name)

                frame = frame.f_back

            return ".".join(calling_methods[::-1])  # Reverse to get the correct order
        except (Exception, ):
            # This is for logging. If error, should not break original flow.
            return "<parsing failed>"


def start_pf_redis_clients_async():
    """
    *** Note: If started async workers, need to terminate them finally. ***
    Start pf_redis clients async feature.
    """
    pf_redis_client.start_async_workers()

def terminate_pf_redis_clients_async():
    """
    Terminate pf_redis clients async feature.
    """
    pf_redis_client.terminate_async_workers()


_base_prop_prefix = 'REDIS_'
_cache_prop_prefix = 'CACHE_REDIS_'
_region_prop_prefix = 'REGION_REDIS_'

""" root redis clients, dao level - start """
_base_redis_client = _init_redis_client_with_fallback("base", _base_prop_prefix)
_region_redis_client = _init_redis_client_with_fallback("region", _region_prop_prefix, fallback_sequence_names=["base"])
_cache_redis_client = _init_redis_client_with_fallback("cache", _cache_prop_prefix, fallback_sequence_names=["region", "base"])
""" root redis clients, dao level - end """

pf_redis_client = PfRedisClientService(_base_redis_client, _region_redis_client)
pf_cache_redis_client = _cache_redis_client  # No special behavior now, can be used directly for cache usage