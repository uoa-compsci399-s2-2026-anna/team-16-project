"""Middleware that refuses blocked and scripted traffic to the panel.

Contract §2.3: nothing here is stored. This module reads the blocklist
(``db.blocklist.is_blocked``) and the stateless header/rate signals
(``db.detection``, re-exported as ``admin.detection``) and refuses the one
request in front of it; it never writes a block itself. Turning a pattern into
a lasting block is a staff decision made on the blocklist screen (Task 4),
which is what keeps the blocklist's only write path behind an audited human
action.

**The refusal body here is a bare ``PlainTextResponse``, and that is right for
this app only.** ``/admin`` is a browser surface; a refused caller gets a page
that says nothing. The public API refuses with §9.2's ``BLOCKED`` envelope
instead (``api/app.py``), because a JSON client that got plain text back would
have to special-case one error out of the twelve in §9. Same blocklist, same
reticence about *why*, two bodies — one per surface, deliberately.

**Deployment note - single process only.** ``RequestRate`` (db/detection.py)
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
from db.blocklist import ip_fingerprint, is_blocked
from db.detection import RequestRate, client_ip, looks_automated

#: ``_client_ip`` moved to ``db/detection.py`` along with ``RequestRate`` and
#: ``looks_automated`` so that ``api/`` could apply the same blocklist to public
#: traffic without a second copy of any of them (§8.3). Kept under its original
#: private name here because that is the name §2.3 quotes and the name
#: ``tests/admin/test_client_address.py`` imports — and because it genuinely is
#: the same object, which ``tests/db/test_detection_shared.py`` asserts.
_client_ip = client_ip

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

#: The two pages that mint the session every other exemption in this file is
#: built on. **Exempt from the rate limit only** - never from the blocklist,
#: never from the header check.
#:
#: Why this exemption exists, and why it is not optional. The shipped
#: deployment terminates TLS upstream (.env.example, SESSION_HTTPS_ONLY), so a
#: reverse proxy is in front of this panel - while ``PROTECTION_TRUSTED_PROXY``
#: correctly defaults to False, because trusting ``X-Forwarded-For`` with no
#: proxy that overwrites it lets any caller claim any address. The combination
#: is that every caller arrives as the proxy's own address and therefore shares
#: **one** rate-limit bucket. ``RequestRate.record`` is called on refused
#: requests too, so a caller sending one request a second keeps that single
#: bucket permanently over ``PROTECTION_MAX_REQUESTS_PER_MINUTE`` and every
#: unauthenticated request in the deployment answers 429 - including
#: ``/admin/login`` and ``/admin/verify``. The authenticated-staff exemption
#: below structurally cannot rescue that: it needs ``SESSION_KEY``, which only
#: these two pages can issue. Recovery would be an env var plus a restart, for
#: an attack any unauthenticated caller can mount from anywhere. Exempting
#: these two paths removes the weapon: a flood against them now costs the
#: attacker nothing and gains them nothing, staff can always reach the login
#: handshake, and once they are through it the authenticated-staff exemption
#: covers the rest of the panel.
#:
#: What is given up, and why it is little. ``admin/throttle.py`` already
#: throttles login attempts **per account** (``LOGIN_MAX_FAILURES`` /
#: ``LOGIN_LOCKOUT_MINUTES``, with password and TOTP failures sharing one
#: counter), which is the check that actually defends a credential-stuffing
#: run - a per-address request rate never was. The header check still applies
#: here too, so a bare ``curl``/``requests`` loop against ``/admin/login`` is
#: still refused with 403 before it can attempt anything.
#:
#: The blocklist deliberately still applies to these paths (see ``dispatch``):
#: it is a per-address lookup rather than a shared counter, so it has none of
#: this failure mode, and a block is an administrator's deliberate act that
#: outranks reaching the login page. That is exactly why
#: ``python -m admin.cli unblock`` exists (docs/architecture.md 9.1.1).
_RATE_EXEMPT_PATHS = frozenset({"/admin/login", "/admin/verify"})


def _decode_session_cookie(raw: str, *, secret_key: str, max_age: int) -> dict:
    """Decode a SessionMiddleware-format cookie without SessionMiddleware.

    See the module docstring for why this exists instead of
    ``request.session``. **This is the most security-sensitive function in
    this file** - it is what decides whether a request gets the anti-lockout
    exemption, so it must accept exactly the cookies AdminAuth's own
    ``SessionMiddleware`` would accept, and refuse everything else. Matched
    against ``starlette.middleware.sessions.SessionMiddleware.__call__``
    (installed by ``AdminAuth.__init__``, admin/backend.py) parameter by
    parameter:

    * **Signing algorithm** - ``itsdangerous.TimestampSigner``, the same
      class SessionMiddleware constructs (``self.signer =
      itsdangerous.TimestampSigner(str(secret_key))``). Same ``unsign``
      call, same argument order.
    * **Secret** - the caller passes ``settings.secret_key``, the identical
      value ``AdminAuth.__init__`` passes as ``secret_key=settings.secret_key``
      to ``super().__init__()``, which is what reaches ``SessionMiddleware``.
      One ``Settings`` instance, read in both places - there is no second
      copy of this value anywhere that could drift from it.
    * **Cookie name** - hardcoded here as ``"session"``
      (``_SESSION_COOKIE_NAME``), matching ``SessionMiddleware``'s own
      default. ``AdminAuth`` never passes ``session_cookie=`` to override it
      (see its ``super().__init__()`` call), so the default is what is
      actually in force.
    * **max_age handling** - the caller passes
      ``settings.session_max_age_minutes * 60``, the identical conversion
      ``AdminAuth.__init__`` performs (``max_age=settings.session_max_age_minutes
      * 60``) before it reaches ``SessionMiddleware``. ``TimestampSigner.unsign``
      raises ``SignatureExpired`` (a ``BadSignature`` subclass) once the
      embedded timestamp is older than this many seconds - caught below,
      same as any other bad signature.
    * **https_only / same_site** - response-side cookie-attribute settings
      only (what ``Set-Cookie`` carries when a session is written). They
      play no part in reading an already-issued cookie back, so there is
      nothing to match here.

    **Failure handling, and why it cannot be more permissive than
    SessionMiddleware.** A missing/absent cookie, a bad signature, an
    expired timestamp, or (after a valid signature) a payload that is not
    the JSON object SessionMiddleware writes - every one of these returns
    ``{}``, i.e. "no session data", which downstream means no ``SESSION_KEY``
    and therefore no exemption. This is a strict subset of what
    SessionMiddleware accepts, never a superset: SessionMiddleware itself
    falls back to an *empty* ``Session()`` on ``BadSignature`` (starlette's
    own source, same file), so any cookie this function refuses,
    SessionMiddleware would treat as no session too - there is no cookie
    this function accepts that SessionMiddleware would reject, or vice
    versa. The one difference is defensive rather than permissive: this
    function also catches ``ValueError`` (a malformed base64/JSON payload
    *after* a valid signature) and treats it the same as a bad signature;
    SessionMiddleware has no equivalent guard and would raise instead - but
    a validly-signed payload that isn't well-formed JSON cannot arise from a
    cookie either of these two code paths ever issued (the signature
    guarantees the payload is byte-for-byte what was originally signed), so
    this only matters for a forged cookie, and forging one requires the
    secret key regardless of which of these two error paths it hits.
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


def _refuse(status_code: int) -> Response:
    return PlainTextResponse(_REFUSED_BODY, status_code=status_code)


class ProtectionMiddleware(BaseHTTPMiddleware):
    """Refuses blocked and scripted traffic ahead of the panel.

    Checked in order, each one's reason given in the module and method
    docstrings:

    1. Static paths pass immediately.
    2. Blocklist - no exemption, not even for staff.
    3. An authenticated staff session is exempt from the checks below it.
    4. Header signals (``db.detection.looks_automated``). Applied here and
       **not** to ``/api/v1/`` - see that module's docstring for why a public
       JSON API must not refuse a caller for looking like a script.
    5. Rate limit, keyed on the HMAC fingerprint of the address - never the
       raw address itself, which contract §2.3 forbids holding anywhere,
       including in this process's own memory. ``/admin/login`` and
       ``/admin/verify`` are exempt from this check and this check only -
       see ``_RATE_EXEMPT_PATHS`` for the remote-lockout chain that exemption
       closes, and why the blocklist above it still applies to them.

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

    @staticmethod
    def _rate_exempt(path: str) -> bool:
        """Whether ``path`` is one of the two login-handshake pages.

        Trailing slash tolerated: Starlette's own ``redirect_slashes`` means
        ``/admin/login/`` reaches the same page, and an exemption that a single
        extra character defeats is not an exemption - a caller would simply
        flood the slashed form instead.
        """
        return (path.rstrip("/") or "/") in _RATE_EXEMPT_PATHS

    async def dispatch(self, request: Request, call_next) -> Response:
        settings = self._settings
        if not settings.protection_enabled:
            return await call_next(request)

        if request.url.path.startswith(_STATIC_PREFIX):
            return await call_next(request)

        ip = client_ip(request, trusted_proxy=settings.protection_trusted_proxy)

        # Blocklist first, and with no exemption: a block is a deliberate
        # act by another administrator and outranks everything below,
        # including a live staff session. Skipped when ip is None - see
        # _client_ip's own docstring for why there is no address here to
        # check in the first place, rather than a stand-in to check with.
        if ip is not None:
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

        # Same reasoning as the blocklist check above: skipped, not
        # bucketed, when there is no real address to key the counter on.
        # Also skipped - and deliberately not even counted - on the two login
        # paths: see _RATE_EXEMPT_PATHS for the remote-lockout chain this
        # closes. Not counting rather than counting-but-not-refusing is the
        # stronger form: a flood against /admin/login must not fill the bucket
        # that every *other* unauthenticated path shares with it either.
        if ip is not None and not self._rate_exempt(request.url.path):
            fingerprint = ip_fingerprint(ip, secret_key=settings.secret_key)
            count = self._rate.record(fingerprint, now=time.time())
            if count > settings.protection_max_requests_per_minute:
                return _refuse(429)

        return await call_next(request)
