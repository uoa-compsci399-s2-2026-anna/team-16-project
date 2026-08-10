"""Behavioural signals for scripted-traffic detection. Contract §2.3.

Two pure, stateless-per-call checks over what a request already carries in
its headers, plus an in-memory counter over how often a key has been seen.

**What this stops.** The least sophisticated automated traffic: tools that
identify themselves honestly in `User-Agent` (`curl`, `python-requests`,
`python-httpx`, `wget`, `go-http-client`), requests missing a `User-Agent`
altogether, and HTML navigations missing the `Sec-Fetch-*` headers every
browser has sent since 2020. It also gives Task 3 a sliding-window request
counter to rate-limit by.

**What this does not stop.** Anything that bothers to look like a browser —
a real `User-Agent` string, a full header set, human-like pacing. This module
does not attempt to defeat a motivated scraper; it raises the floor above
"one line of `requests.get()`" and no further. It is not a fingerprint: no
signal computed here is stored, logged, or combined with any other request
to build an identity. A header is read, judged, and forgotten within the one
call that reads it — that is what keeps this inside §2.3, which forbids
storing any IP address, user agent or browser fingerprint.

`looks_automated` is a whitelist of *suspicious* signals, not a whitelist of
permitted browsers: anything not named here returns `None`. A check that
tries to guess which browsers are legitimate will eventually lock out a
staff member using something this list never anticipated — the false
positive is the expensive failure mode, because it gets the check turned
off entirely.
"""

from collections import OrderedDict, deque
from collections.abc import Mapping

#: Case-insensitive substrings of `User-Agent` that identify a scripting
#: tool honestly. Matching on a substring rather than the whole value is
#: deliberate: version numbers and platform suffixes vary
#: (`python-requests/2.31.0`, `Go-http-client/2.0`) but the tool name does
#: not.
_SCRIPTING_TOOL_MARKERS = ("curl/", "python-requests", "python-httpx", "wget", "go-http-client")


def _get(headers: Mapping[str, str], name: str) -> str | None:
    """Case-insensitive header lookup.

    Starlette hands this function an already-lowercased mapping; a raw
    dict built in a test, or by some other caller, may not be.
    """
    name = name.lower()
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None


def looks_automated(headers: Mapping[str, str]) -> str | None:
    """Return a short human-readable reason the request looks scripted, or
    `None` if none of the checks fire.

    The reason is shown to staff on the blocklist screen and written into
    the audit entry, so it names the signal rather than just saying
    "automated" — "no User-Agent header" is actionable, "automated" is not.

    Checked in order:

    1. A missing `User-Agent` — every browser sends one.
    2. A `User-Agent` naming a known scripting tool.
    3. An HTML navigation (`Accept` contains `text/html`) with no
       `Sec-Fetch-Mode` — every browser released since 2020 sends
       `Sec-Fetch-*` on a navigation, so a caller that has an HTML Accept
       header but no Sec-Fetch-Mode is imitating a browser rather than
       being one.
    """
    user_agent = _get(headers, "user-agent")

    if not user_agent:
        return "no User-Agent header"

    lowered = user_agent.lower()
    for marker in _SCRIPTING_TOOL_MARKERS:
        if marker in lowered:
            return f"User-Agent identifies a scripting tool ({marker.rstrip('/')})"

    accept = _get(headers, "accept") or ""
    if "text/html" in accept.lower() and _get(headers, "sec-fetch-mode") is None:
        return "HTML navigation with no Sec-Fetch-Mode header"

    return None


class RequestRate:
    """An in-memory, sliding-window request counter, keyed by whatever the
    caller decides identifies a source (Task 3's decision, not this
    module's — this class stores only the key strings it is given).

    A deque of timestamps is kept per key; entries outside the window are
    dropped on read, so the window slides rather than resetting on a fixed
    boundary — a bucket that resets on the hour lets a caller send a full
    allowance right before the boundary and another right after. Keys are
    evicted least-recently-touched-first once there are more than
    `max_keys` of them, so an attacker who cycles through keys cannot grow
    this structure without bound; it lives in process memory for the life
    of the deployment.
    """

    def __init__(self, *, window_seconds: float, max_keys: int = 10_000) -> None:
        self._window_seconds = window_seconds
        self._max_keys = max_keys
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()

    def _prune(self, key: str, *, now: float) -> deque[float]:
        timestamps = self._hits.setdefault(key, deque())
        cutoff = now - self._window_seconds
        while timestamps and timestamps[0] <= cutoff:
            timestamps.popleft()
        return timestamps

    def record(self, key: str, *, now: float) -> int:
        """Record one hit for `key` at time `now` and return the count
        within the window afterwards."""
        timestamps = self._prune(key, now=now)
        timestamps.append(now)
        # Touching the key marks it most-recently-used for eviction.
        self._hits.move_to_end(key)
        self._evict_if_over_capacity()
        return len(timestamps)

    def count(self, key: str, *, now: float) -> int:
        """The number of hits recorded for `key` within the window ending
        at `now`, without recording a new one."""
        if key not in self._hits:
            return 0
        timestamps = self._prune(key, now=now)
        self._hits.move_to_end(key)
        return len(timestamps)

    def size(self) -> int:
        """The number of distinct keys currently tracked."""
        return len(self._hits)

    def _evict_if_over_capacity(self) -> None:
        while len(self._hits) > self._max_keys:
            self._hits.popitem(last=False)
