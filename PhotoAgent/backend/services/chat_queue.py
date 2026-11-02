"""Asking the model through the worker: queue a chat job, wait for its answer.

The chat routes used to call photo_agent.chat_assistant here, in the request.
Now the worker runs it (photo_agent/chat_jobs.py) and this backend only
relays: the job's params go in through the job's info hash, the answer comes
back through it, and the request waits in between, so the routes -- and the
page -- see the same synchronous answer they always did.

Text that need not hold anything up (a photo's brief, the note under a
version) goes through submit and poll instead: the photo moves on, and the
answer is picked up whenever it is in.
"""

import json
import logging
import time
from contextvars import ContextVar

from aiserver_utils.queue import get_job_info
from backend.services.put_job import MSG_DETAIL_QUEUE, enqueue, get_redis_conn
from backend.services.template_params import get_engine_params_template
from backend.services.timing import job_spans, timeline
from photo_agent import chat_assistant
from photo_agent.chat_jobs import CHAT_QUEUE

# The model gets chat_assistant.TIMEOUT_SECONDS; on top of that, the queue
# and the trip there and back.
WAIT_SECONDS = chat_assistant.TIMEOUT_SECONDS + 15
POLL_SECONDS = 0.2
# Nothing reads a chat job once its answer is in: the info hash goes away on
# its own.
JOB_TTL_SECONDS = 24 * 3600
_current_task_id = ContextVar('chat_queue_task_id', default=None)
logger = logging.getLogger(__name__)


def current_task_id():
    return _current_task_id.get()


class ChatJobError(RuntimeError):
    """The worker failed the job, or never answered in time."""


def submit(params):
    """Queue one chat job and return its id, without waiting; poll reads
    its answer."""
    engine_params = get_engine_params_template()
    engine_params["srcKeys"] = []
    engine_params["dstKeys"] = []
    engine_params["acts"][0]["params"] = params
    job_id = enqueue(engine_params, queue=CHAT_QUEUE)
    get_redis_conn().expire(MSG_DETAIL_QUEUE.replace("{msg_id}", job_id), JOB_TTL_SECONDS)
    return job_id


def poll(job_id, since, stage=None):
    """(done, answer) for a job submit queued at `since` (time.time()):
    done is False while it still runs. A job that failed, or got no answer
    within WAIT_SECONDS, is done with answer None."""
    info = get_job_info(job_id, keyToGet=["status", "error", "0_res0"]) or {}
    status = info.get("status")
    if status == "success":
        result = info.get("0_res0")
        if isinstance(result, str):
            result = json.loads(result)
        timeline(job=job_id, event=f"chat_{stage}", wait=round(time.time() - since, 2), **job_spans("chat", job_id))
        return True, (result or {}).get("answer")
    if status == "error":
        logger.warning("chat job %s failed: %s", job_id, info.get("error"))
        return True, None
    if time.time() - since >= WAIT_SECONDS:
        get_redis_conn().zrem(CHAT_QUEUE, job_id)
        logger.warning("chat job %s not answered in %ss (status %s)", job_id, WAIT_SECONDS, status)
        return True, None
    return False, None


def run(params, *, wait_seconds=None):
    """The answer to one chat job (photo_agent.chat_jobs.run's), or raises
    ChatJobError."""
    _current_task_id.set(None)
    started = time.monotonic()
    deadline = None if wait_seconds is None else started + wait_seconds
    job_id = submit(params)
    _current_task_id.set(job_id)
    redis = get_redis_conn()

    if deadline is None:
        deadline = time.monotonic() + WAIT_SECONDS
    while True:
        info = get_job_info(job_id, keyToGet=["status", "error", "0_res0"]) or {}
        status = info.get("status")
        if wait_seconds is not None and time.monotonic() >= deadline:
            redis.zrem(CHAT_QUEUE, job_id)
            raise ChatJobError(f"chat job {job_id} deadline exceeded")
        if status == "success":
            result = info.get("0_res0")
            if isinstance(result, str):
                result = json.loads(result)
            timeline(job=job_id, event=f"chat_{params.get('stage')}", wait=round(time.monotonic() - started, 2),
                     **job_spans("chat", job_id))
            return (result or {}).get("answer")
        if status == "error":
            raise ChatJobError(f"chat job {job_id} failed: {info.get('error')}")
        if time.monotonic() >= deadline:
            # Not picked up yet: take it back, so no worker spends a model
            # call on an answer nobody reads.
            redis.zrem(CHAT_QUEUE, job_id)
            raise ChatJobError(f"chat job {job_id} not answered in {WAIT_SECONDS}s (status {status})")
        time.sleep(POLL_SECONDS)
