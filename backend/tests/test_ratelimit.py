"""AttemptLimiter unit tests (AR29, spec-1.5).

Deterministic — the clock is injected; the sliding window and its
release (RATE_WINDOW_RESET) are pinned.
"""

import pytest

from app.core.ratelimit import AttemptLimiter


class FakeClock:
    def __init__(self) -> None:
        self.t = 1_000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, delta: float) -> None:
        self.t += delta


def test_allowed_until_max_failures() -> None:
    clock = FakeClock()
    limiter = AttemptLimiter(max_attempts=3, window_seconds=60.0, clock=clock)
    assert limiter.allowed("k")
    limiter.record("k")
    limiter.record("k")
    assert limiter.allowed("k")
    limiter.record("k")
    assert not limiter.allowed("k")  # third failure locks


def test_window_release_after_elapse() -> None:
    """RATE_WINDOW_RESET: once the oldest attempt leaves the window the
    key is allowed again."""
    clock = FakeClock()
    limiter = AttemptLimiter(max_attempts=3, window_seconds=60.0, clock=clock)
    for _ in range(3):
        limiter.record("k")
    assert not limiter.allowed("k")
    blocked_until = limiter.blocked_until("k")
    assert blocked_until == pytest.approx(clock.t + 60.0)
    clock.advance(61.0)
    assert limiter.allowed("k")  # window passed
    assert limiter.blocked_until("k") is None


def test_blocked_until_is_oldest_attempt_expiry() -> None:
    """blocked_until reports when the OLDEST failure leaves the window —
    the actual release moment (review round 1)."""
    clock = FakeClock()
    limiter = AttemptLimiter(max_attempts=3, window_seconds=100.0, clock=clock)
    limiter.record("k")  # t=1000
    clock.advance(40.0)
    limiter.record("k")  # t=1040
    clock.advance(40.0)
    limiter.record("k")  # t=1080 -> locked
    assert limiter.blocked_until("k") == pytest.approx(1000.0 + 100.0)  # oldest, not newest


def test_allowed_does_not_consume() -> None:
    clock = FakeClock()
    limiter = AttemptLimiter(max_attempts=1, window_seconds=60.0, clock=clock)
    assert limiter.allowed("k")  # must not consume
    assert limiter.allowed("k")
    assert limiter.allowed("k")


def test_failures_are_independent_per_key() -> None:
    clock = FakeClock()
    limiter = AttemptLimiter(max_attempts=2, window_seconds=60.0, clock=clock)
    limiter.record("a")
    limiter.record("a")
    assert not limiter.allowed("a")
    assert limiter.allowed("b")  # another key is unaffected


def test_pruning_keeps_window_slide() -> None:
    clock = FakeClock()
    limiter = AttemptLimiter(max_attempts=3, window_seconds=60.0, clock=clock)
    limiter.record("k")  # t=1000
    clock.advance(59.0)
    limiter.record("k")  # t=1059 -> [1000, 1059]
    clock.advance(2.0)  # t=1061
    limiter.record("k")  # prune pops 1000 (out of the 60s window) -> [1059, 1061]
    assert limiter.allowed("k")  # 2 in window, budget left — prune worked
    limiter.record("k")  # -> [1059, 1061, 1061] locked
    assert not limiter.allowed("k")


def test_reset_clears() -> None:
    clock = FakeClock()
    limiter = AttemptLimiter(max_attempts=1, window_seconds=60.0, clock=clock)
    limiter.record("k")
    assert not limiter.allowed("k")
    limiter.reset()
    assert limiter.allowed("k")


def test_distinct_failures_have_bounded_retention(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.core.ratelimit._MAX_KEYS", 8)
    limiter = AttemptLimiter()
    for i in range(100):
        assert limiter.allowed(str(i))
        limiter.record(str(i))
        assert len(limiter._attempts) <= 8
    assert set(limiter._attempts) == {str(i) for i in range(92, 100)}


def test_read_only_keys_are_not_retained_and_global_expiry_is_pruned() -> None:
    clock = FakeClock()
    limiter = AttemptLimiter(clock=clock, window_seconds=60)
    for i in range(100):
        limiter.allowed(str(i))
        limiter.blocked_until(str(i))
    assert not limiter._attempts
    limiter.record("expired", count=1000000)
    assert len(limiter._attempts["expired"]) == limiter.max_attempts
    clock.advance(60)
    assert limiter.allowed("new")
    assert not limiter._attempts


def test_key_eviction_retains_recent_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.core.ratelimit._MAX_KEYS", 2)
    limiter = AttemptLimiter(max_attempts=2)
    limiter.record("active")
    limiter.record("older")
    limiter.record("active")
    limiter.record("new")
    assert not limiter.allowed("active")
    assert limiter.allowed("older")
    assert len(limiter._attempts) == 2


def test_global_pruning_preserves_unexpired_failures() -> None:
    clock = FakeClock()
    limiter = AttemptLimiter(max_attempts=1, window_seconds=60, clock=clock)
    limiter.record("expired")
    clock.advance(30)
    limiter.record("live")
    clock.advance(30)
    assert limiter.blocked_until("live") == 1090
    assert "expired" not in limiter._attempts
    assert not limiter.allowed("live")
