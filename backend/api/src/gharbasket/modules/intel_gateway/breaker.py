"""A circuit breaker for the ML service (M10 §6, handbook 08 §8.3).

On the free host the service sleeps after 15 idle minutes and takes about a minute to wake, so
every customer-path call in that time would wait out its whole timeout before the fallback
answered. After `threshold` consecutive failures the breaker opens: calls are not made for
`cooldown_s` and the fallback answers at once. Then one call is let through as a probe
(half-open); its success closes the breaker, its failure opens it for another cool-down. Calls
made while the probe is in flight are short-circuited.

There is one breaker per ML path (`BreakerRegistry`): on staging the planner never fits the
customer timeout, which must not stop the fast green-slot quotes. A scheduled job (`allow_job`)
ignores the cool-down only for one probe a cool-down; every other job call falls back at once.
`JobBudget` is the ML time one job run may spend on long (job) timeouts.

State is per process and in memory (a few fields under a lock; nothing awaits inside it, so it
is safe across threads and tasks). With several API instances each learns on its own.
"""

import threading
import time
from collections.abc import Callable
from typing import Literal

State = Literal["closed", "open", "half_open"]


class CircuitBreaker:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at: float | None = None  # set while open or half-open
        self._probe_started_at: float | None = None
        self._job_probe_at: float | None = None

    def state(self, cooldown_s: float) -> State:
        with self._lock:
            if self._opened_at is None:
                return "closed"
            return "half_open" if self._expired(self._opened_at, cooldown_s) else "open"

    def allow(self, threshold: int, cooldown_s: float) -> bool:
        """Whether a call may go to the ML service now. Once the cool-down is over exactly one
        caller gets True (the probe); a probe that never reports (its task was cancelled) is
        replaced after another cool-down. `threshold` 0 turns the breaker off."""
        if threshold <= 0:
            return True
        with self._lock:
            if self._opened_at is None:
                return True
            if not self._expired(self._opened_at, cooldown_s):
                return False
            probing = self._probe_started_at
            if probing is not None and not self._expired(probing, cooldown_s):
                return False
            self._probe_started_at = self._clock()
            return True

    def allow_job(self, threshold: int, cooldown_s: float) -> bool:
        """The same for a scheduled job, which can wait for the service to wake: while the breaker
        is open one job call a cool-down may probe, before the cool-down ends too; the others are
        short-circuited. (After the cool-down a customer's probe is possible as well.)"""
        if threshold <= 0:
            return True
        with self._lock:
            if self._opened_at is None:
                return True
            probing = self._job_probe_at
            if probing is not None and not self._expired(probing, cooldown_s):
                return False
            self._job_probe_at = self._clock()
            return True

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._probe_started_at = None
            self._job_probe_at = None

    def record_failure(self, threshold: int) -> None:
        if threshold <= 0:
            return
        with self._lock:
            self._failures += 1
            self._probe_started_at = None
            if self._failures >= threshold or self._opened_at is not None:
                self._opened_at = self._clock()  # (re)open for a full cool-down

    def reset(self) -> None:
        self.record_success()

    def _expired(self, since: float, cooldown_s: float) -> bool:
        return self._clock() - since >= cooldown_s


class BreakerRegistry:
    """The gateway's breakers, one per ML path, made on first use."""

    def __init__(self) -> None:
        self.clock: Callable[[], float] = time.monotonic  # tests replace it
        self._lock = threading.Lock()
        self._by_path: dict[str, CircuitBreaker] = {}

    def get(self, path: str) -> CircuitBreaker:
        with self._lock:
            if path not in self._by_path:
                self._by_path[path] = CircuitBreaker(lambda: self.clock())
            return self._by_path[path]

    def reset(self) -> None:
        with self._lock:
            self._by_path.clear()


ml_breakers = BreakerRegistry()


class JobBudget:
    """The ML time one job run may spend on job timeouts (`ML_JOB_BUDGET_S`). The job makes one
    and hands it down to the gateway calls; each job call is charged the time it took. Once spent
    the run's remaining calls are customer calls: the customer timeout, solver limit and breaker
    rules. Not shared between runs and not global."""

    def __init__(self, total_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.total_s = total_s
        self._clock = clock
        self._spent_s = 0.0

    @property
    def exhausted(self) -> bool:
        return self._spent_s >= self.total_s

    @property
    def spent_s(self) -> float:
        return self._spent_s

    def now(self) -> float:
        return self._clock()

    def charge(self, since: float) -> None:
        self._spent_s += max(0.0, self._clock() - since)
