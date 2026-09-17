"""Reads the worker's own compatibility_version.py constants back out of
Redis, so this Flask app (a separate process/container from the worker, with
no shared memory) reflects a worker-only deploy without needing its own
restart.

Matches server.py's WORKER_VERSION_KEY pattern exactly: the worker publishes
these two values to Redis once, on startup (server.py's init()), and this
just reads them back. Falls back to this process's own (possibly stale)
compatibility_version.py import when Redis is unreachable or the worker
hasn't published yet -- the same behavior this had before this Redis channel
existed, so a Redis hiccup only ever costs freshness, never breaks anything.
"""
import os

from aiserver_utils.pf_redis import pf_redis_client
import compatibility_version as _local

# Namespaced per deployment (like REDIS_USERS_SET_KEY in account.py) since
# dev/poc share the same Redis cluster and would otherwise clobber each
# other's published values.
CURRENT_VERSION_KEY = os.getenv("REDIS_COMPATIBILITY_VERSION_KEY", "photoagent:compatibility:current_version")
MIN_COMPATIBLE_VERSION_KEY = os.getenv("REDIS_COMPATIBILITY_MIN_VERSION_KEY", "photoagent:compatibility:min_version")


def _read_int(key, default):
    try:
        value = pf_redis_client.get(key)
    except Exception:
        return default
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_current_version() -> int:
    return _read_int(CURRENT_VERSION_KEY, _local.CURRENT_COMPATIBILITY_VERSION)


def get_min_compatible_version() -> int:
    return _read_int(MIN_COMPATIBLE_VERSION_KEY, _local.MIN_COMPATIBLE_VERSION)
