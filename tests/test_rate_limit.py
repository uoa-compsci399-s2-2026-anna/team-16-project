from api.rate_limit import FixedWindowRateLimiter


def test_fixed_window_enforces_limit_and_returns_retry_after():
    limiter = FixedWindowRateLimiter(window_seconds=60)
    assert limiter.allow("calculate:127.0.0.1", 2, now=100.0) == (True, 0)
    assert limiter.allow("calculate:127.0.0.1", 2, now=101.0) == (True, 0)
    allowed, retry_after = limiter.allow("calculate:127.0.0.1", 2, now=102.0)
    assert allowed is False
    assert retry_after == 58
    assert limiter.allow("calculate:127.0.0.1", 2, now=160.0) == (True, 0)


def test_fixed_window_keeps_clients_and_endpoint_groups_separate():
    limiter = FixedWindowRateLimiter(window_seconds=60)
    assert limiter.allow("get:127.0.0.1", 1, now=1.0)[0]
    assert limiter.allow("get:127.0.0.2", 1, now=1.0)[0]
    assert limiter.allow("post-calculate:127.0.0.1", 1, now=1.0)[0]
