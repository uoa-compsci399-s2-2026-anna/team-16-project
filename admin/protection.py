"""Middleware that refuses blocked and scripted traffic to the panel.

Contract §2.3: nothing here is stored. This module reads the blocklist
(``db.blocklist.is_blocked``) and the stateless header/rate signals
(``admin.detection``) and refuses the one request in front of it; it never
writes a block itself. Turning a pattern into a lasting block is a staff
decision made on the blocklist screen (Task 4), which is what keeps the
blocklist's only write path behind an audited human action.

**Deployment note - single process only.** ``RequestRate`` (admin/detection.py)
counts in one process's memory. Run this panel under more than one worker and
each worker gets its own counter, so the effective rate limit is the
configured number multiplied by the worker count - silently weaker than
``PROTECTION_MAX_REQUESTS_PER_MINUTE`` says. The blocklist and the header
check are unaffected (both are either stateless or backed by the database),
only the rate limit degrades. Sharing the counter across workers would need
an external store (e.g. Redis) and is not built here (YAGNI, matches the
same note in admin/throttle.py).

**Why this decodes the session cookie itself instead of using
``request.session``.** This middleware is installed on the *outer* FastAPI
app (see admin/app.py). sqladmin mounts its own Starlette sub-application at
``/admin`` and that sub-application owns the ``SessionMiddleware`` AdminAuth
configures (admin/backend.py) - it runs *inside* that inner app, not the
outer one. A Starlette ``Mount`` hands the same ASGI ``scope`` dict down to
the inner app, but only once the outer app's own middleware and routing have
already run; this middleware's pre-``call_next`` code therefore executes
*before* the inner app - and its ``SessionMiddleware`` - ever sees the
request. Confirmed directly: reading ``"session" in request.scope`` at this
point is always ``False``, and Starlette's ``Request.session`` property
asserts on exactly that. So the anti-lockout check below decodes the
``session`` cookie by hand, with the same algorithm ``SessionMiddleware``
itself uses (itsdangerous ``TimestampSigner`` + base64 + JSON) and the same
``secret_key``/``max_age``/cookie name AdminAuth's own ``SessionMiddleware``
is built with (admin/backend.py's ``AdminAuth.__init__``) - a session this
reads as valid is exactly one AdminAuth would also accept, and vice versa.
"""

import json
import time
from base64 import b64decode
from collections.abc import Callable

import itsdangerous
from itsdangerous.exc import BadSignature
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from admin.accounts import UnknownStaffError, get_staff
from admin.auth import SESSION_GENERATION_KEY, SESSION_KEY
from admin.config import Settings
from admin.detection import RequestRate, looks_automated
from db.blocklist import ip_fingerprint, is_blocked

#: starlette.middleware.sessions.SessionMiddleware's default cookie name.
#: AdminAuth (admin/backend.py) never overrides ``session_cookie``, so this
#: is the name the inner app's SessionMiddleware actually uses.
_SESSION_COOKIE_NAME = "session"

#: Static files must pass unconditionally and before every other check - see
#: ProtectionMiddleware.dispatch. Matches the prefix admin/app.py mounts
#: StaticFiles under ("/admin/static", see the mount-ordering comment there).
_STATIC_PREFIX = "/admin/static/"

#: Deliberately uninformative. Contract: a refused caller is not owed the
#: rule it broke - that is a free tuning signal for whoever is probing. Staff
#: read the actual reason on the blocklist screen and in the audit trail.
_REFUSED_BODY = "Refused."


def _decode_session_cookie(raw: str, *, secret_key: str, max_age: int) -> dict:
    """Decode a SessionMiddleware-format cookie without SessionMiddleware.

    See the module docstring for why this exists instead of
    ``request.session``. Any failure - a bad signature, an expired
    timestamp, or a payload that is not the JSON object SessionMiddleware
    writes - is treated as "no session data", the same way SessionMiddleware
    itself falls back to an empty session on ``BadSignature``.
    """
    signer = itsdangerous.TimestampSigner(secret_key)
    try:
        unsigned = signer.unsign(raw.encode("utf-8"), max_age=max_age)
        data = json.loads(b64decode(unsigned))
    except (BadSignature, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _is_authenticated_staff(
    request: Request, *, session_factory: Callable, settings: Settings
) -> bool:
    """Mirror the core of ``AdminAuth.authenticate()`` - the anti-lockout
    exemption.

    Deliberately narrower than a full ``authenticate()`` call: it checks
    only what "already passed a password, a TOTP code and a session-
    generation check" (the brief's own words) requires - ``SESSION_KEY`` is
    set (only ``stamp_session``, called after a second factor completes,
    ever sets it - see admin/auth.py), the account is still active, and the
    session's generation still matches the account's current one (an
    evicted cookie, e.g. from an MFA reset, fails this and is not exempt).
    It does not re-check ``must_change_password``/``mfa_enrolled``: a
    session that lacks ``SESSION_KEY`` because it never got that far is
    already excluded by the first check, and one that carries it is by
    construction past both of those steps already.
    """
    raw_cookie = request.cookies.get(_SESSION_COOKIE_NAME)
    if not raw_cookie:
        return False

    session_data = _decode_session_cookie(
        raw_cookie,
        secret_key=settings.secret_key,
        max_age=settings.session_max_age_minutes * 60,
    )
    username = session_data.get(SESSION_KEY)
    if not username:
        return False

    with session_factory() as db:
        try:
            staff = get_staff(db, username)
        except UnknownStaffError:
            return False
        if not staff.is_active:
            return False
        if session_data.get(SESSION_GENERATION_KEY) != staff.session_generation:
            return False

    return True


def _client_ip(request: Request, *, trusted_proxy: bool) -> str:
    """The caller's address, never trusting a caller-supplied header unless
    an operator has explicitly said a reverse proxy is in front of this.

    See ``Settings.protection_trusted_proxy``'s own docstring
    (admin/config.py) for why the default matters: with no proxy in front,
    trusting ``X-Forwarded-For`` lets any caller claim to be any address.
    """
    if trusted_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # The left-most entry is the original client; anything to its
            # right was appended by a hop this deployment's own proxy chain
            # controls.
            return forwarded.split(",")[0].strip()
    client = request.client
    return client.host if client is not None else ""


def _refuse(status_code: int) -> Response:
    return PlainTextResponse(_REFUSED_BODY, status_code=status_code)


class ProtectionMiddleware(BaseHTTPMiddleware):
    """Refuses blocked and scripted traffic ahead of the panel.

    Checked in order, each one's reason given in the module and method
    docstrings:

    1. Static paths pass immediately.
    2. Blocklist - no exemption, not even for staff.
    3. An authenticated staff session is exempt from the checks below it.
    4. Header signals (``admin.detection.looks_automated``).
    5. Rate limit, keyed on the HMAC fingerprint of the address - never the
       raw address itself, which contract §2.3 forbids holding anywhere,
       including in this process's own memory.

    Reads the blocklist and never writes to it - see the module docstring.
    """

    def __init__(self, app, *, session_factory: Callable, settings: Settings) -> None:
        super().__init__(app)
        self._session_factory = session_factory
        self._settings = settings
        # One counter for the life of this middleware instance - Starlette
        # constructs it once when the app's middleware stack is built, not
        # per request, so this is already the "one throttle for the
        # process" admin/throttle.py's build_throttle documents for the
        # login throttle. A per-request RequestRate would start empty every
        # time and rate-limit nothing.
        self._rate = RequestRate(window_seconds=60.0)

    async def dispatch(self, request: Request, call_next) -> Response:
        settings = self._settings
        if not settings.protection_enabled:
            return await call_next(request)

        if request.url.path.startswith(_STATIC_PREFIX):
            return await call_next(request)

        ip = _client_ip(request, trusted_proxy=settings.protection_trusted_proxy)

        # Blocklist first, and with no exemption: a block is a deliberate
        # act by another administrator and outranks everything below,
        # including a live staff session.
        with self._session_factory() as db:
            blocked = is_blocked(db, ip, secret_key=settings.secret_key)
        if blocked:
            return _refuse(403)

        # Anti-lockout: staff who already passed a password, a TOTP code and
        # a session-generation check are exempt from the header and rate
        # checks below. See _is_authenticated_staff and the module docstring
        # for why this cannot simply read request.session.
        if _is_authenticated_staff(
            request, session_factory=self._session_factory, settings=settings
        ):
            return await call_next(request)

        if looks_automated(request.headers) is not None:
            return _refuse(403)

        fingerprint = ip_fingerprint(ip, secret_key=settings.secret_key)
        count = self._rate.record(fingerprint, now=time.time())
        if count > settings.protection_max_requests_per_minute:
            return _refuse(429)

        return await call_next(request)
