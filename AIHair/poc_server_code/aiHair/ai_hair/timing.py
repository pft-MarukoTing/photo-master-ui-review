"""Per-task timing for aiHair: how long each feature spends generating
(Feature.process: its OpenAI calls and its own steps round them) and
post-processing (Feature.finish: the colour match, the lining up and the
blend, as HairFeatures' defaults switch them on), and of the generation how
long an OpenAI call was in flight. Each task's timing goes to the server log
and, as one JSON line, to TIMING_LOG_PATH.
"""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone

TIMING_LOG_PATH = os.getenv("AIHAIR_TIMING_LOG", "./log/aihair_timing.jsonl")
_WRITE_LOCK = threading.Lock()

# The client methods TimedClient times: images.edit (gpt-image's renders) and
# responses.parse/create (the analyst's checks).
_TIMED = {"images": {"edit", "generate"}, "responses": {"parse", "create"}}


class CallTimer:
    """The OpenAI calls one task made: (what, start, end), thread-safe, as an
    effect may make several at once."""

    def __init__(self):
        self._lock = threading.Lock()
        self.calls: list[tuple[str, float, float]] = []

    def record(self, what: str, start: float, end: float) -> None:
        with self._lock:
            self.calls.append((what, start, end))

    def busy_seconds(self) -> float:
        """Wall time with at least one call in flight: the calls' intervals
        merged, so calls made at once count once."""
        with self._lock:
            spans = sorted((s, e) for _, s, e in self.calls)
        total, cur_start, cur_end = 0.0, None, None
        for start, end in spans:
            if cur_end is None or start > cur_end:
                if cur_end is not None:
                    total += cur_end - cur_start
                cur_start, cur_end = start, end
            else:
                cur_end = max(cur_end, end)
        if cur_end is not None:
            total += cur_end - cur_start
        return total

    def summary(self) -> dict:
        with self._lock:
            calls = list(self.calls)
        by_kind: dict[str, dict] = {}
        for what, start, end in calls:
            entry = by_kind.setdefault(what, {"count": 0, "sec": 0.0})
            entry["count"] += 1
            entry["sec"] += end - start
        return {what: {"count": v["count"], "sec": round(v["sec"], 3)}
                for what, v in by_kind.items()}


class _TimedGroup:
    def __init__(self, group, name: str, timer: CallTimer, cpu=None):
        self._group, self._name, self._timer, self._cpu = group, name, timer, cpu

    def __getattr__(self, attr):
        target = getattr(self._group, attr)
        if attr not in _TIMED[self._name] or not callable(target):
            return target

        def timed(*args, **kwargs):
            if self._cpu is not None:
                self._cpu.call_started()
            start = time.perf_counter()
            try:
                return target(*args, **kwargs)
            finally:
                self._timer.record(f"{self._name}.{attr}", start, time.perf_counter())
                if self._cpu is not None:
                    self._cpu.call_ended()

        return timed


class TimedClient:
    """An OpenAI client whose images.edit/generate and responses.parse/create
    calls are timed into `timer`; with_options (core.Context.analyst's)
    keeps them timed. With `cpu` (a cpu_slots.TaskCpu), the task's CPU slot
    goes back while each such call is in flight. Everything else passes
    through."""

    def __init__(self, client, timer: CallTimer, cpu=None):
        self._client, self._timer, self._cpu = client, timer, cpu

    def with_options(self, **kwargs):
        return TimedClient(self._client.with_options(**kwargs), self._timer, self._cpu)

    def __getattr__(self, attr):
        target = getattr(self._client, attr)
        if attr in _TIMED:
            return _TimedGroup(target, attr, self._timer, self._cpu)
        return target


class TaskTiming(dict):
    """One task's timing record: sections timed with `section`, other fields
    set as on a dict; seconds rounded to the millisecond."""

    def __init__(self, **fields):
        super().__init__(**fields)
        self._start = time.perf_counter()

    @contextmanager
    def section(self, name: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            self[f"{name}_sec"] = round(self.get(f"{name}_sec", 0.0)
                                        + time.perf_counter() - start, 3)

    def close(self) -> "TaskTiming":
        self["total_sec"] = round(time.perf_counter() - self._start, 3)
        self["time"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        return self


def export_timing(record: dict, path: str | None = None) -> None:
    """Append `record` to the timing log, one JSON line per task."""
    path = path or TIMING_LOG_PATH
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, default=str)
    with _WRITE_LOCK:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
