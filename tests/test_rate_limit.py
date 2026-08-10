"""§6.5's limiter, after the three counters were reconciled to one.

The two tests below are B's, unchanged apart from the class name: the fixed
window and the sliding window agree on every value they assert, which is why
the reconciliation was safe to make. Where they differ is pinned separately
below.
"""

from api.rate_limit import SlidingWindowRateLimiter
from db.detection import RequestRate


def test_the_limiter_counts_with_the_shared_counter():
    """Not a fourth implementation. `db.detection.RequestRate` is the same
    object `admin/protection.py` counts with."""
    assert SlidingWindowRateLimiter(window_seconds=60)._rate.__class__ is RequestRate


def test_the_window_slides_rather_than_resetting_on_a_boundary():
    """The behaviour that changed, and the reason it did.

    A fixed window lets a caller spend a whole hour's allowance in its last
    second and another in the first second of the next — 4 calls inside 2
    seconds against a limit of 2 here, 240 calculations against §6.5's "120 /
    hour" in production. Under a sliding window the second burst is refused,
    and each slot comes back exactly one window after the request that used it.
    """
    limiter = SlidingWindowRateLimiter(window_seconds=60)
    assert limiter.allow("k", 2, now=59.0) == (True, 0)
    assert limiter.allow("k", 2, now=59.5) == (True, 0)

    # A fixed window starting at 0 would have reset here and allowed both.
    assert limiter.allow("k", 2, now=60.5)[0] is False
    assert limiter.allow("k", 2, now=61.0)[0] is False

    # 59.0 leaves the window at 119.0, and one slot comes back with it - one,
    # not both: 59.5 is still inside the window at 119.2.
    assert limiter.allow("k", 2, now=119.2) == (True, 0)
    assert limiter.allow("k", 2, now=119.3)[0] is False


def test_a_refused_request_is_not_counted_against_the_caller():
    """Otherwise a caller who overshoots once keeps their own bucket full by
    retrying, and `Retry-After` is a guess rather than a promise. This is the
    one place this limiter deliberately differs from `admin/protection.py`,
    which counts refusals to make a flood cost the flooder — see the module
    docstring for why that reasoning does not carry over to a public API."""
    limiter = SlidingWindowRateLimiter(window_seconds=60)
    limiter.allow("k", 1, now=0.0)
    for tick in range(1, 20):
        assert limiter.allow("k", 1, now=float(tick))[0] is False

    # Served again one window after the single *allowed* request, not one
    # window after the last retry.
    assert limiter.allow("k", 1, now=60.5) == (True, 0)


def test_retry_after_is_the_wait_until_the_oldest_hit_leaves_the_window():
    limiter = SlidingWindowRateLimiter(window_seconds=60)
    limiter.allow("k", 1, now=10.0)
    allowed, retry_after = limiter.allow("k", 1, now=10.4)

    assert allowed is False
    # 10.0 + 60 - 10.4 = 59.6, rounded up: a client obeying this never retries
    # early.
    assert retry_after == 60


def test_sliding_window_enforces_limit_and_returns_retry_after():
    limiter = SlidingWindowRateLimiter(window_seconds=60)
    assert limiter.allow("calculate:127.0.0.1", 2, now=100.0) == (True, 0)
    assert limiter.allow("calculate:127.0.0.1", 2, now=101.0) == (True, 0)
    allowed, retry_after = limiter.allow("calculate:127.0.0.1", 2, now=102.0)
    assert allowed is False
    assert retry_after == 58
    assert limiter.allow("calculate:127.0.0.1", 2, now=160.0) == (True, 0)


def test_the_limiter_keeps_clients_and_endpoint_groups_separate():
    limiter = SlidingWindowRateLimiter(window_seconds=60)
    assert limiter.allow("get:127.0.0.1", 1, now=1.0)[0]
    assert limiter.allow("get:127.0.0.2", 1, now=1.0)[0]
    assert limiter.allow("post-calculate:127.0.0.1", 1, now=1.0)[0]
