"""§6.5's per-caller limits, counted with the shared counter.

**This was a third implementation.** `admin/protection.py` had a sliding-window
`RequestRate`, `admin/detection.py` owned it, and this module had its own fixed
window — three pieces of code counting requests, two of them applying different
privacy standards to the same data (§6.5's own open item: this one keyed on
`"post-calculate:203.0.113.9"`, the panel on the §2.3 fingerprint). The counter
is now `db.detection.RequestRate` in both layers and the key is the fingerprint
in both layers. What stays here is the *policy* the API needs and the panel does
not: a per-call limit, and §6.5's `Retry-After`.

**Sliding, not fixed.** A fixed window resets on a boundary, which lets a caller
spend a full hour's allowance in the last second of one window and another in
the first second of the next — 240 calculations inside two seconds against a
"120 / hour" limit. The window now slides, so "120 in any hour" means what it
says. The observable change is at the *end* of a window rather than at the
limit: a refused caller is served again once their own oldest counted request
falls out, not when a shared clock ticks over.

**A refused request is not counted**, which is this layer's own rule and differs
from the panel's. `admin/protection.py` counts refusals deliberately (its note
on `_RATE_EXEMPT_PATHS` explains what that closes). Here it would mean a caller
who overshoots once keeps their own bucket permanently full by retrying — and
unlike the panel, there is no login page behind this and no staff exemption to
recover through. Not counting is what makes `Retry-After` a promise rather than
a guess.

**Thread-safety is this module's job, not `RequestRate`'s.** FastAPI runs `def`
route handlers in a threadpool, so two requests genuinely execute `allow()` at
the same time; the panel drives the same class from one event loop and cannot.
The lock is held around every call into the counter for that reason.
"""

from __future__ import annotations

import math
import threading
import time

from db.detection import RequestRate


class SlidingWindowRateLimiter:
    """§6.5's counter. Keys are opaque to this class — `api/router.py` builds
    them as `f"{group}:{fingerprint}"` and never with an address."""

    def __init__(self, window_seconds: int = 3600) -> None:
        self.window_seconds = window_seconds
        self._rate = RequestRate(window_seconds=float(window_seconds))
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, *, now: float | None = None) -> tuple[bool, int]:
        """Whether this request is within `limit` for `key`, and how many
        seconds to wait if it is not.

        `Retry-After` is derived from the oldest hit still inside the window:
        once it falls out, the count drops below the limit and the caller is
        served again. Rounded up and floored at one second, so a client that
        obeys it never retries early.
        """
        now = time.monotonic() if now is None else now
        with self._lock:
            if self._rate.count(key, now=now) < limit:
                self._rate.record(key, now=now)
                return True, 0
            oldest = self._rate.oldest(key, now=now)
        if oldest is None:  # pragma: no cover - only reachable with limit <= 0
            return False, self.window_seconds
        return False, max(1, math.ceil(oldest + self.window_seconds - now))
