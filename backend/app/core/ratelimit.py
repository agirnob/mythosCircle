"""In-process sliding-window attempt limiter (AR29, spec-1.5).

One uvicorn worker is the single writer (AD-13/AR2), so an in-memory
limiter is correct — it enforces the login rate limit with lockout and
is deterministic-tested via an injectable clock. A restart clears
lockouts (acceptable in beta; persistence is deferred).

``allowed(key)`` reports whether the key may proceed WITHOUT consuming a
quota; ``record(key)`` consumes one attempt after a failure. Successes
never consume quota (review round 1). Locked attempts still get the
generic 401 from the caller — the limiter never leaks whether a lockout
is in effect.
"""

import time as _time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field

#: Cap on tracked keys — an attacker minting distinct keys (open
#: registration, arbitrary email casing) must not grow process memory
#: without bound (review round 1).
_MAX_KEYS = 10_000


@dataclass
class AttemptLimiter:
    """Sliding-window attempt limiter keyed by an arbitrary string.

    A key that reaches ``max_attempts`` *failures* within
    ``window_seconds`` is blocked until its oldest attempt falls out of
    the window. Calls ``clock()`` (injectable, default ``time.monotonic``)
    for deterministic tests.
    """

    max_attempts: int = 5
    window_seconds: float = 900.0
    clock: Callable[[], float] = field(default=lambda: _time.monotonic())

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        self._attempts: OrderedDict[str, deque[float]] = OrderedDict()
        self._next_prune = self.clock()

    def allowed(self, key: str) -> bool:
        """Report whether the key may try now; does NOT consume a quota."""
        now = self.clock()
        self._prune_expired(now)
        attempts = self._attempts.get(key, deque())
        self._prune(attempts, now)
        return len(attempts) < self.max_attempts

    def record(self, key: str, count: int = 1) -> None:
        """Consume ``count`` attempts (default: one failure)."""
        now = self.clock()
        self._prune_expired(now)
        attempts = self._attempts.get(key, deque())
        self._prune(attempts, now)
        # Keep the newest quota: older excess attempts cannot release a lockout.
        for _ in range(min(max(count, 1), self.max_attempts)):
            attempts.append(now)
        while len(attempts) > self.max_attempts:
            attempts.popleft()
        self._attempts[key] = attempts
        self._attempts.move_to_end(key)
        self._evict_if_oversized()

    def blocked_until(self, key: str) -> float | None:
        """The earliest time the key may try again, or None if it may now.

        This is the oldest attempt's expiry — the moment the sliding
        window releases the key (review round 1: reporting the newest
        attempt's expiry would overstate the lockout).
        """
        now = self.clock()
        self._prune_expired(now)
        attempts = self._attempts.get(key, deque())
        self._prune(attempts, now)
        if len(attempts) < self.max_attempts:
            return None
        return attempts[0] + self.window_seconds

    def _prune(self, attempts: deque[float], now: float) -> None:
        while attempts and attempts[0] <= now - self.window_seconds:
            attempts.popleft()

    def _prune_expired(self, now: float) -> None:
        # Sweep globally at most once per minute, including on read-only calls.
        if now < self._next_prune:
            return
        for key, attempts in list(self._attempts.items()):
            self._prune(attempts, now)
            if not attempts:
                del self._attempts[key]
        self._next_prune = now + min(self.window_seconds, 60.0)

    def _evict_if_oversized(self) -> None:
        # Under saturation, retain the most recently recorded failure keys.
        while len(self._attempts) > _MAX_KEYS:
            self._attempts.popitem(last=False)

    def reset(self) -> None:
        """Clear all attempts (test isolation)."""
        self._attempts.clear()
        self._next_prune = self.clock()
