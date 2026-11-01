"""A circuit breaker for the ML service (M10 §6, handbook 08 §8.3).

On the free host the service sleeps after 15 idle minutes and takes about a minute to wake, so
every customer-path call in that time would wait out its whole timeout before the fallback
answered. After `threshold` consecutive failures the breaker opens: calls are not made for
`cooldown_s` and the fallback answers at once. Then one call is let through as a probe
(half-open); its success closes the breaker, its failure opens it for another cool-down. Calls
made while the probe is in flight are short-circuited.

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

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._probe_started_at = None

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


# The gateway's one breaker: the ML service is one dependency.
ml_breaker = CircuitBreaker()
