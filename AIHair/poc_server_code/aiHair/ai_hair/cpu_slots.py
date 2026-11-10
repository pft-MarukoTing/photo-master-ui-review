"""The CPU slots the server's tasks share: at most AIHAIR_CPU_SLOTS tasks
compute at once (the feature's own steps, the colour match, the lining up,
the blend), each letting its slot go while it waits on OpenAI. So
ENGINE_WORKER_NUM tasks can have their renders in flight while only a few
of them use the CPU (a 2 vCPU server: 5 workers, 1 slot).

A task takes its slot when its first OpenAI call ends, so what comes before
it (loading the photo, a short step or two) runs without one and a new
task's render goes out at once, not behind another task's post-processing.
It lets the slot go while any of its calls is in flight and takes one again
when the last of them ends (TimedClient calls call_started/call_ended
round each call), and before post-processing where it made no call (take).
"""

from __future__ import annotations

import threading
import time
from collections import deque


class CpuSlots:
    """`n` slots, handed out in the order they were asked for, so no task
    waits behind ones that asked after it."""

    def __init__(self, n: int):
        if n < 1:
            raise ValueError(f"CpuSlots needs at least one slot, not {n}")
        self.n = n
        self._free = n
        self._waiting: deque = deque()
        self._cond = threading.Condition()

    def acquire(self) -> float:
        """Wait for a slot, in turn; returns the seconds waited."""
        start = time.perf_counter()
        ticket = object()
        with self._cond:
            self._waiting.append(ticket)
            try:
                while self._waiting[0] is not ticket or self._free == 0:
                    self._cond.wait()
            except BaseException:
                self._waiting.remove(ticket)
                self._cond.notify_all()
                raise
            self._waiting.popleft()
            self._free -= 1
            if self._free and self._waiting:
                self._cond.notify_all()  # the next in line may take one too
        return time.perf_counter() - start

    def release(self) -> None:
        with self._cond:
            if self._free >= self.n:
                raise RuntimeError("released a CPU slot that wasn't taken")
            self._free += 1
            self._cond.notify_all()


class TaskCpu:
    """One task's hold on a slot of `slots`: held while the task computes,
    let go while any of its OpenAI calls is in flight (see the module's
    docstring), and at `close`. Thread-safe: the calls may be made from
    several threads at once. With no slots (None: AIHAIR_CPU_SLOTS=0) it
    does nothing. wait_sec: the seconds the task waited for a slot."""

    def __init__(self, slots: CpuSlots | None):
        self._slots = slots
        self._lock = threading.Lock()    # the state below
        self._taking = threading.Lock()  # one of the task's threads takes a slot at a time
        self._in_flight = 0
        self._wanted = False
        self._held = False
        self._closed = False
        self.wait_sec = 0.0

    def call_started(self) -> None:
        """An OpenAI call is going out: the slot goes back while it is in
        flight."""
        if self._slots is None:
            return
        with self._lock:
            self._in_flight += 1
            if self._held:
                self._held = False
                self._slots.release()

    def call_ended(self) -> None:
        """An OpenAI call has ended (answered or failed): where it was the
        last in flight, waits for a slot before the task computes on."""
        if self._slots is None:
            return
        with self._lock:
            self._in_flight -= 1
            self._wanted = True
        self._settle()

    def take(self) -> None:
        """Waits for a slot where the task hasn't one (it made no call, so
        far): before post-processing."""
        if self._slots is None:
            return
        with self._lock:
            self._wanted = True
        self._settle()

    def close(self) -> None:
        """The task is done computing: its slot, where it has one, goes
        back."""
        if self._slots is None:
            return
        with self._lock:
            self._closed = True
            if self._held:
                self._held = False
                self._slots.release()

    def _needs_slot(self) -> bool:
        return (self._wanted and not self._closed and not self._held
                and self._in_flight == 0)

    def _settle(self) -> None:
        with self._taking:
            with self._lock:
                if not self._needs_slot():
                    return
            waited = self._slots.acquire()
            with self._lock:
                self.wait_sec += waited
                if self._needs_slot():
                    self._held = True
                    return
            # A call went out (or the task closed) while it waited: not
            # needed now.
            self._slots.release()
