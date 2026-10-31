"""The sliding-window limiter on its own: who is let through, when, and what it remembers."""

from backend.app.ratelimit import SlidingWindowLimiter


class Ticker:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_requests_over_the_limit_are_refused_with_a_wait_until_the_oldest_ages_out() -> None:
    ticker = Ticker()
    limiter = SlidingWindowLimiter(limit=3, window_seconds=60, now=ticker)

    assert [limiter.hit("a") for _ in range(3)] == [None, None, None]
    ticker.now += 20
    wait = limiter.hit("a")

    assert wait == 40  # the first of the three leaves the window 60 s after it arrived


def test_each_key_has_its_own_allowance_and_it_returns_as_the_window_moves() -> None:
    ticker = Ticker()
    limiter = SlidingWindowLimiter(limit=1, window_seconds=60, now=ticker)

    assert limiter.hit("a") is None
    assert limiter.hit("b") is None
    assert limiter.hit("a") is not None
    ticker.now += 61
    assert limiter.hit("a") is None


def test_a_refused_request_does_not_extend_the_wait() -> None:
    ticker = Ticker()
    limiter = SlidingWindowLimiter(limit=1, window_seconds=60, now=ticker)
    limiter.hit("a")

    for _ in range(50):
        ticker.now += 1
        limiter.hit("a")  # hammering while refused
    ticker.now += 10  # 60 s after the one request that was let through

    assert limiter.hit("a") is None


def test_memory_stays_bounded_when_keys_never_repeat() -> None:
    ticker = Ticker()
    limiter = SlidingWindowLimiter(limit=1, window_seconds=60, now=ticker, max_keys=100)

    for n in range(1000):
        ticker.now += 0.01
        limiter.hit(f"ip-{n}")

    assert limiter.tracked_keys() <= 100


def test_failures_can_be_counted_separately_from_checking() -> None:
    ticker = Ticker()
    limiter = SlidingWindowLimiter(limit=2, window_seconds=60, now=ticker)

    assert limiter.blocked("a") is None
    limiter.record("a")
    assert limiter.blocked("a") is None
    limiter.record("a")
    assert limiter.blocked("a") == 60
