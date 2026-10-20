"""The shared provider retry policy (photo_agent/providers/http.py)."""

import unittest
from unittest.mock import MagicMock

import requests

from photo_agent.providers import http


def _response(status, body=None, headers=None):
    response = MagicMock()
    response.status_code = status
    response.ok = status < 400
    response.headers = headers or {}
    if body is None:
        response.json.side_effect = ValueError("no json")
    else:
        response.json.return_value = body
    return response


class RetryTransientTests(unittest.TestCase):
    def _call(self, *outcomes):
        """Runs a retry_transient-wrapped function that yields `outcomes` in turn
        (exceptions are raised); returns (result or exception, calls, sleeps)."""
        sleeps, calls = [], []
        pending = list(outcomes)

        @http.retry_transient(sleep=sleeps.append)
        def fn():
            calls.append(1)
            outcome = pending.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        try:
            return fn(), len(calls), sleeps
        except Exception as exc:
            return exc, len(calls), sleeps

    def _status_error(self, status, headers=None):
        return http.api_error(_response(status, headers=headers), f"HTTP {status}")

    def test_retries_408_429_and_5xx(self):
        for status in (408, 429, 500, 503):
            with self.subTest(status):
                result, calls, _ = self._call(self._status_error(status), "ok")
                self.assertEqual((result, calls), ("ok", 2))

    def test_retries_timeouts_and_connection_errors(self):
        result, calls, _ = self._call(requests.exceptions.Timeout(), requests.exceptions.ConnectionError(), "ok")
        self.assertEqual((result, calls), ("ok", 3))

    def test_other_4xx_raised_at_once(self):
        for status in (400, 401, 403, 404):
            with self.subTest(status):
                result, calls, sleeps = self._call(self._status_error(status), "ok")
                self.assertIsInstance(result, RuntimeError)
                self.assertNotIsInstance(result, http.RetryableApiError)
                self.assertEqual((calls, sleeps), (1, []))

    def test_gives_up_after_max_tries(self):
        errors = [self._status_error(503) for _ in range(http.MAX_TRIES + 1)]
        result, calls, sleeps = self._call(*errors)
        self.assertIs(result, errors[http.MAX_TRIES - 1])
        self.assertEqual((calls, len(sleeps)), (http.MAX_TRIES, http.MAX_TRIES - 1))

    def test_backoff_without_hint_is_jittered_exponential(self):
        _, _, sleeps = self._call(self._status_error(503), self._status_error(503), "ok")
        self.assertTrue(0 <= sleeps[0] <= 1)
        self.assertTrue(0 <= sleeps[1] <= 2)

    def test_retry_after_honored_and_capped(self):
        _, _, sleeps = self._call(
            self._status_error(429, {"Retry-After": "7"}),
            self._status_error(429, {"Retry-After": "600"}),
            "ok",
        )
        self.assertEqual(sleeps, [7.0, http.MAX_RETRY_AFTER_SECONDS])


class RetryAfterTests(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(http.retry_after_seconds(_response(429, headers={"Retry-After": "3"})), 3.0)
        self.assertIsNone(http.retry_after_seconds(_response(429)))
        self.assertIsNone(http.retry_after_seconds(_response(429, headers={"Retry-After": "soon"})))
        past = "Wed, 21 Oct 2015 07:28:00 GMT"
        self.assertEqual(http.retry_after_seconds(_response(429, headers={"Retry-After": past})), 0.0)


class CheckedJsonTests(unittest.TestCase):
    def test_ok_returns_body(self):
        self.assertEqual(http.checked_json(_response(200, {"a": 1}), "X"), {"a": 1})

    def test_error_message_fallbacks(self):
        cases = [
            ({"error": {"message": "bad size"}}, "X: bad size"),
            ({"error": {"code": "moderation_blocked"}}, "X: moderation_blocked"),
            ({"error": "quota"}, "X: quota"),
            (None, "X: HTTP 400"),
        ]
        for body, expected in cases:
            with self.subTest(body):
                with self.assertRaises(RuntimeError) as ctx:
                    http.checked_json(_response(400, body), "X")
                self.assertEqual(str(ctx.exception), expected)

    def test_retryable_status_carries_status_and_hint(self):
        with self.assertRaises(http.RetryableApiError) as ctx:
            http.checked_json(_response(429, {}, {"Retry-After": "2"}), "X")
        self.assertEqual((ctx.exception.status, ctx.exception.retry_after), (429, 2.0))


if __name__ == "__main__":
    unittest.main()
