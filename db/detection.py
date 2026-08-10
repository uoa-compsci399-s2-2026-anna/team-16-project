"""Behavioural signals for scripted-traffic detection, shared by both callers.
Contract §2.3, §8.3.

Two pure, stateless-per-call checks over what a request already carries in
its headers, an in-memory counter over how often a key has been seen, and the
one function that decides which address a caller is measured by.

**Why this is in `db/` and not in `admin/`.** It was built in `admin/` because
that is where E's stage lived, not because that is where it belongs: nothing
here needs the panel, and both the panel (`admin/protection.py`) and the public
API (`api/app.py`, `api/router.py`) need all of it. `db/` is the layer both may
import, which is the same reasoning that put `db/blocklist.py` where it is.
§8.3 recorded the alternative — duplicating the header markers and the
sliding-window counter into `api/` — and rejected it: two copies of a detection
rule drift, and the copy that stops matching is the one nobody notices.

`admin/detection.py` is a re-export of this module, so the panel's existing
imports keep working and there is still exactly one implementation.

**What this stops.** The least sophisticated automated traffic: tools that
identify themselves honestly in `User-Agent` (`curl`, `python-requests`,
`python-httpx`, `wget`, `go-http-client`), requests missing a `User-Agent`
altogether, and HTML navigations missing the `Sec-Fetch-*` headers every
browser has sent since 2020. It also gives the panel a sliding-window request
counter to rate-limit by, which `api/rate_limit.py` now counts with too.

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

> **`looks_automated` is deliberately applied to the panel only.** `admin/` is
> a browser-only surface, so a caller there that is plainly a script is
> refused. `/api/v1/` is a public JSON API: scripting it is a legitimate way to
> use it, and refusing `curl` there would refuse a use the contract invites
> (§6.3's CSV export exists to be fetched by tools). The API applies the
> blocklist and the rate limit from this module and not the header check —
> that asymmetry is a decision, not an omission.

Imports nothing outside the standard library at runtime except
``db.blocklist``, which owns address normalisation; the Starlette type below is
imported for annotations only, so this module stays usable — and testable —
without a web framework.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Mapping
from typing import TYPE_CHECKING

from db.blocklist import InvalidAddressError, normalise_ip

if TYPE_CHECKING:  # pragma: no cover - typing only
    from starlette.requests import Request

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
    caller decides identifies a source (the caller's decision, not this
    module's — this class stores only the key strings it is given, and both
    callers give it the §2.3 fingerprint rather than an address).

    A deque of timestamps is kept per key; entries outside the window are
    dropped on read, so the window slides rather than resetting on a fixed
    boundary — a bucket that resets on the hour lets a caller send a full
    allowance right before the boundary and another right after. Keys are
    evicted least-recently-touched-first once there are more than
    `max_keys` of them, so an attacker who cycles through keys cannot grow
    this structure without bound; it lives in process memory for the life
    of the deployment.

    **Not thread-safe on its own.** The panel drives it from a single event
    loop, so nothing there can interleave. `api/rate_limit.py` is called from
    FastAPI's sync route handlers, which run in a threadpool, and it therefore
    holds a lock around every call it makes here — see the note on
    `SlidingWindowRateLimiter`. A caller that shares one instance between
    threads must do the same.
    """

    def __init__(self, *, window_seconds: float, max_keys: int = 10_000) -> None:
        self._window_seconds = window_seconds
        self._max_keys = max_keys
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()

    @property
    def window_seconds(self) -> float:
        return self._window_seconds

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

    def oldest(self, key: str, *, now: float) -> float | None:
        """The earliest hit for `key` still inside the window, or `None` if
        there is none.

        Exists so a caller can answer "how long until this key is under its
        limit again" — a sliding window has no window-start to subtract from,
        which is the one thing a fixed window made easy. `api/rate_limit.py`
        turns this into §6.5's `Retry-After`. Records nothing.
        """
        if key not in self._hits:
            return None
        timestamps = self._prune(key, now=now)
        self._hits.move_to_end(key)
        return timestamps[0] if timestamps else None

    def size(self) -> int:
        """The number of distinct keys currently tracked."""
        return len(self._hits)

    def _evict_if_over_capacity(self) -> None:
        while len(self._hits) > self._max_keys:
            self._hits.popitem(last=False)


def client_ip(request: "Request", *, trusted_proxy: bool) -> str | None:
    """The caller's address, never trusting a caller-supplied header unless
    an operator has explicitly said a reverse proxy is in front of this.

    See ``Settings.protection_trusted_proxy``'s own docstring
    (admin/config.py) for why the default matters: with no proxy in front,
    trusting ``X-Forwarded-For`` lets any caller claim to be any address. The
    API layer reads the same ``PROTECTION_TRUSTED_PROXY`` variable, so one
    setting governs both — two switches for one deployment fact is how they end
    up disagreeing.

    Returns ``None`` when the ASGI connection carries no client address at
    all (``request.client is None``) - rare in a real deployment (the ASGI
    server populates this from the actual TCP connection; nothing here is
    attacker-controlled), but real for some transports and test harnesses.
    Earlier versions returned ``""`` (the panel) and the literal string
    ``"unknown"`` (the API) for this case, and both are wrong in the same way:
    each is itself a valid dict/lookup key, so every such caller silently
    collapsed into one shared bucket - both the in-memory rate counter and the
    blocklist's fingerprint lookup - rather than each being its own,
    unidentified caller. **Both callers skip their checks outright when this is
    ``None``**, rather than inventing an address to check against.

    Also returns ``None`` when the value that *is* present does not parse as
    an address. ``db.blocklist.ip_fingerprint`` raises
    ``InvalidAddressError`` on such a value by design - an unparseable
    address that still produced a fingerprint is a block that silently
    matches nobody - and this runs ahead of *every* request under ``/admin``
    and ``/api/v1/``, so it must not turn a malformed ``request.client.host``
    into a 500 on the login page or on a public endpoint. Normalising here and
    treating an unusable value exactly as ``None`` is what keeps those two
    requirements compatible.

    **An unparseable ``X-Forwarded-For`` falls back to the real connection
    address rather than to ``None``.** With ``trusted_proxy`` true that header
    is the one attacker-reachable input on this path, and returning ``None``
    for it would hand any caller a one-header bypass of both the blocklist
    and the rate limit (``X-Forwarded-For: nonsense``). Falling back means a
    forged header gains nothing: the caller is still measured against the
    address the ASGI server actually saw.
    """
    if trusted_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # The left-most entry is the original client; anything to its
            # right was appended by a hop this deployment's own proxy chain
            # controls.
            candidate = _normalise_or_none(forwarded.split(",")[0])
            if candidate is not None:
                return candidate
    client = request.client
    return _normalise_or_none(client.host) if client is not None else None


def _normalise_or_none(candidate: str) -> str | None:
    """``db.blocklist.normalise_ip``, with its refusal turned into ``None``.

    The whole of the difference between a request path and every other caller
    of ``normalise_ip``: the admin form and the CLI report an invalid address
    to the person who typed it, whereas this one runs on every request and has
    nobody to report to - see ``client_ip`` for why ``None`` is the right
    answer here and why it is not reachable from a forged header.
    """
    try:
        return normalise_ip(candidate)
    except InvalidAddressError:
        return None
