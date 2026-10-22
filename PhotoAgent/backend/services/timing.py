"""Timing lines for where a photo's wait goes, one grep-able line each:

    timing step=opening_brief photo=p_1 ms=8421
    timeline photo=p_1 analyze_queue=1.2 analyze_run=24.8 ...

Spans inside this process use time.monotonic. Gaps across the backend and
the worker (queue wait, the lag before a poll notices a finished job) use the
job's info-hash timestamps: enq_ts is this backend's clock, ts and endTs the
worker's -- across two machines those carry their clock skew.
"""

import logging
import time
from contextlib import contextmanager

from aiserver_utils.queue import get_job_info

logger = logging.getLogger("photoagent.timing")
# The backend configures no logging, so info lines would go nowhere; these
# get a handler of their own instead of turning on everyone's info.
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def _fields(ids):
    return " ".join(f"{k}={v}" for k, v in ids.items() if v is not None)


@contextmanager
def timed(step, **ids):
    started = time.monotonic()
    try:
        yield
    finally:
        logger.info("timing step=%s %s ms=%d", step, _fields(ids), (time.monotonic() - started) * 1000)


def job_times(job_id):
    """(queued, picked up, finished) in seconds since the epoch, each None
    when the job's info hash lacks it."""
    info = get_job_info(job_id, keyToGet=["enq_ts", "ts", "endTs"]) or {}

    def sec(key):
        try:
            return int(info.get(key)) / 1000
        except (TypeError, ValueError):
            return None
    return sec("enq_ts"), sec("ts"), sec("endTs")


def job_spans(prefix, job_id, noticed=None):
    """{prefix_queue, prefix_run, prefix_notice} seconds for one job; noticed
    is when this backend saw it finish (default now)."""
    queued, picked, ended = job_times(job_id)
    noticed = time.time() if noticed is None else noticed

    def gap(a, b):
        return round(b - a, 2) if a is not None and b is not None else None
    return {f"{prefix}_queue": gap(queued, picked), f"{prefix}_run": gap(picked, ended),
            f"{prefix}_notice": gap(ended, noticed)}


def timeline(**fields):
    logger.info("timeline %s", _fields(fields))
