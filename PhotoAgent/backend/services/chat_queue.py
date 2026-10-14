"""Asking the model through the worker: queue a chat job, wait for its answer.

The chat routes used to call photo_agent.chat_assistant here, in the request.
Now the worker runs it (photo_agent/chat_jobs.py) and this backend only
relays: the job's params go in through the job's info hash, the answer comes
back through it, and the request waits in between, so the routes -- and the
page -- see the same synchronous answer they always did.
"""

import json
import time

from aiserver_utils.queue import get_job_info
from backend.services.put_job import MSG_DETAIL_QUEUE, enqueue, get_redis_conn
from backend.services.template_params import get_engine_params_template
from photo_agent import chat_assistant
from photo_agent.chat_jobs import CHAT_QUEUE

# The model gets chat_assistant.TIMEOUT_SECONDS; on top of that, the queue
# and the trip there and back.
WAIT_SECONDS = chat_assistant.TIMEOUT_SECONDS + 15
POLL_SECONDS = 0.2
# Nothing reads a chat job once its answer is in: the info hash goes away on
# its own.
JOB_TTL_SECONDS = 24 * 3600


class ChatJobError(RuntimeError):
    """The worker failed the job, or never answered in time."""


def run(params, *, wait_seconds=None):
    """The answer to one chat job (photo_agent.chat_jobs.run's), or raises
    ChatJobError."""
    deadline = None if wait_seconds is None else time.monotonic() + wait_seconds
    engine_params = get_engine_params_template()
    engine_params["srcKeys"] = []
    engine_params["dstKeys"] = []
    engine_params["acts"][0]["params"] = params
    job_id = enqueue(engine_params, queue=CHAT_QUEUE)
    redis = get_redis_conn()
    redis.expire(MSG_DETAIL_QUEUE.replace("{msg_id}", job_id), JOB_TTL_SECONDS)

    if deadline is None:
        deadline = time.monotonic() + WAIT_SECONDS
    while True:
        info = get_job_info(job_id, keyToGet=["status", "error", "0_res0"]) or {}
        status = info.get("status")
        if wait_seconds is not None and time.monotonic() >= deadline:
            redis.zrem(CHAT_QUEUE, job_id)
            raise ChatJobError("preview conversation deadline exceeded")
        if status == "success":
            result = info.get("0_res0")
            if isinstance(result, str):
                result = json.loads(result)
            return (result or {}).get("answer")
        if status == "error":
            raise ChatJobError(f"chat job {job_id} failed: {info.get('error')}")
        if time.monotonic() >= deadline:
            # Not picked up yet: take it back, so no worker spends a model
            # call on an answer nobody reads.
            redis.zrem(CHAT_QUEUE, job_id)
            raise ChatJobError(f"chat job {job_id} not answered in {WAIT_SECONDS}s (status {status})")
        time.sleep(POLL_SECONDS)
