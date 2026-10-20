"""One retry policy and error shape for every provider HTTP call.

Retried: timeouts, connection errors, and HTTP 408 / 429 / 5xx -- the failures
that are about the moment (network, overload, rate limit), not the request.
Any other 4xx (bad parameters, auth, a safety rejection) fails identically on
retry, so it is raised at once. When the response names a wait (Retry-After,
usually on 429/503) that wait is used instead of the jittered backoff.
"""

import email.utils
import logging
import random
import time
from functools import wraps

import requests

logger = logging.getLogger(__name__)

MAX_TRIES = 3
# A Retry-After longer than this is clamped: the caller is a user waiting on
# one photo, and a minutes-long server hint is better answered by failing.
MAX_RETRY_AFTER_SECONDS = 30.0
RETRYABLE_STATUS_CODES = frozenset({408, 429})


class RetryableApiError(RuntimeError):
    """A transient provider failure; `retry_after` is the server's requested
    wait in seconds, when it gave one."""

    def __init__(self, message, *, status=None, retry_after=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


RETRYABLE_EXCEPTIONS = (
    requests.exceptions.Timeout,
    requests.exceptions.ConnectionError,
    RetryableApiError,
)


def is_retryable_status(status_code):
    return status_code in RETRYABLE_STATUS_CODES or status_code >= 500


def retry_after_seconds(response):
    """Retry-After as seconds (delta-seconds or HTTP-date form), else None."""
    headers = getattr(response, "headers", None) or {}
    value = str(headers.get("Retry-After") or "").strip()
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = email.utils.parsedate_to_datetime(value).timestamp() - time.time()
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
    return max(0.0, seconds)


def safe_json(response):
    try:
        return response.json()
    except Exception:
        return {}


def error_message(data, status_code):
    """The provider's own error text: error.message, then error.code, then a
    bare error string, else "HTTP <status>"."""
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or f"HTTP {status_code}")
    if error:
        return str(error)
    return f"HTTP {status_code}"


def api_error(response, message):
    """The exception for a non-OK response: RetryableApiError when the status
    is worth retrying, a plain RuntimeError otherwise."""
    status = response.status_code
    if is_retryable_status(status):
        return RetryableApiError(message, status=status, retry_after=retry_after_seconds(response))
    return RuntimeError(message)


def checked_json(response, label):
    """The response's JSON body, or raises api_error with "<label>: <reason>"."""
    data = safe_json(response)
    if not response.ok:
        raise api_error(response, f"{label}: {error_message(data, response.status_code)}")
    return data


def _backoff_wait(attempt):
    # Same curve as backoff.expo + backoff.full_jitter, which every client
    # used before: uniform(0, 2 ** attempt) for attempt 0, 1, ...
    return random.uniform(0, 2 ** attempt)


def retry_transient(func=None, *, max_tries=MAX_TRIES, sleep=None):
    """Decorator: re-run `func` on RETRYABLE_EXCEPTIONS, up to max_tries calls
    in total, then re-raise the last one."""

    def decorate(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            for attempt in range(max_tries):
                try:
                    return fn(*args, **kwargs)
                except RETRYABLE_EXCEPTIONS as exc:
                    if attempt + 1 >= max_tries:
                        raise
                    hinted = getattr(exc, "retry_after", None)
                    wait = min(hinted, MAX_RETRY_AFTER_SECONDS) if hinted is not None else _backoff_wait(attempt)
                    logger.warning(
                        "%s failed (try %d/%d, retrying in %.1fs): %s",
                        fn.__qualname__, attempt + 1, max_tries, wait, exc,
                    )
                    (sleep or time.sleep)(wait)
            raise AssertionError("unreachable")

        return wrapper

    return decorate(func) if func is not None else decorate
