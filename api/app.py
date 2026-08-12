"""Standalone composition root for Part B.

**This module said, until v1.18, that E's app could "include ``router`` and pass
``admin.auth.require_staff`` as the staff authenticator instead". Do not.** That
instruction predates the layering rule and cannot be followed: ``api/`` may not
import ``admin/``, and the two run as separate services with separate images in
any case (``docker/compose.yaml``). ``admin.auth.require_staff`` additionally
reads ``request.state.db``, placed there by middleware its own docstring says
"the next plan installs" and which was never installed - so it has never been
callable from a live request from either side.

Following it was impossible, so nobody did, and the argument was left with no
authenticator at all: every dry run in every deployment answered
``UNAUTHORIZED`` while ``/admin/try`` rendered that refusal inside a 200 page.
That is open item O-9. The staff gate is now ``db/staff_proof.py``, verified by
``_default_staff_authenticator`` below and minted by the panel - shared through
``db/``, which both layers may import, rather than across a boundary neither
may cross.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.engine_adapter import DefaultEngineAdapter, EngineAdapter
from api.errors import (
    ApiProblem,
    api_problem_handler,
    blocked_problem,
    http_error_handler,
    internal_error_handler,
    problem_response,
    validation_handler,
)
from api.rate_limit import SlidingWindowRateLimiter
from api.router import router
from db.blocklist import InvalidAddressError, is_blocked
from db.detection import client_ip
from db.session import create_session_factory
from db.staff_proof import STAFF_PROOF_HEADER, verify_staff_proof

load_dotenv()

logger = logging.getLogger(__name__)

#: Logged once at start-up when the caller's address comes from the raw TCP
#: connection while a reverse proxy is the shipped arrangement (.env.example
#: terminates TLS upstream). Both consequences are named because each is a
#: different kind of failure and an operator who has only heard of one will
#: not recognise the other.
_UNTRUSTED_PROXY_WARNING = (
    "PROTECTION_TRUSTED_PROXY is false, so every caller is measured by the "
    "address of the TCP connection this process accepts. If a reverse proxy "
    "sits in front (the shipped arrangement - TLS is terminated upstream), "
    "that is the proxy's own address for every visitor, with two "
    "consequences: section 6.5's per-caller rate limit collapses into ONE "
    "shared bucket for all public traffic, and ONE blocklist entry denies "
    "every visitor at once. Set PROTECTION_TRUSTED_PROXY=true only once the "
    "proxy is confirmed to overwrite X-Forwarded-For itself - with no such "
    "proxy, trusting that header lets any caller claim any address, which is "
    "the worse failure of the two."
)

#: Logged at most once per process, the first time a request arrives with no
#: client address at all. Not per request: a line per request would be a log
#: that grows with traffic for a fact that does not change, and this is the
#: one condition where there is nothing to say about the caller anyway.
_NO_CLIENT_ADDRESS_WARNING = (
    "A request arrived with no client address (ASGI scope['client'] is None), "
    "so the blocklist and the rate limit were both skipped for it - neither "
    "can key on an address that is not there. If this is every request rather "
    "than an occasional one, the deployment is hiding the address from this "
    "process: `uvicorn --uds` behind nginx does exactly that. Both protections "
    "are then silently doing nothing. Bind a TCP socket, or put the address in "
    "X-Forwarded-For and set PROTECTION_TRUSTED_PROXY=true. Logged once per "
    "process; no address is recorded, because there is none."
)

_BOOL_TRUE_VALUES = ("1", "true", "yes", "on")
_BOOL_FALSE_VALUES = ("0", "false", "no", "off")


def _env_bool(name: str, default: bool) -> bool:
    """Parse a boolean setting, refusing to guess at an unrecognised value.

    Mirrors ``admin.config._bool`` deliberately, including the refusal:
    ``api/`` may not import ``admin/``, and a setting that means the same thing
    in both layers must not be parsed leniently in one of them.
    ``PROTECTION_TRUSTED_PROXY=ture`` silently becoming False in the API while
    the panel refuses to start is exactly the kind of split this whole task
    exists to close.
    """
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in _BOOL_TRUE_VALUES:
        return True
    if raw in _BOOL_FALSE_VALUES:
        return False
    raise ValueError(
        f"{name}={raw!r} is not a recognised boolean. Use one of "
        f"{_BOOL_TRUE_VALUES + _BOOL_FALSE_VALUES}."
    )


def _blocklist_check(request: Request) -> bool:
    """§2.3's blocklist, read on the request path.

    Reads and never writes: turning a pattern into a lasting block is a staff
    decision made on ``/admin/ip-block/block``, which is what keeps the
    blocklist's only write path behind an audited human action. One indexed
    lookup on ``ip_hmac``, on the session the request already has - §9.2 costs
    one query on purpose, and opening a second connection per request to spend
    it would be a strange way to honour that.

    A caller with no usable address is not blocked: the blocklist is keyed on
    ``HMAC(address)``, so with no address there is no fingerprint to compute
    and no row to look up. Inventing a stand-in key is what gives every such
    caller one shared blocklist entry.
    """
    ip = client_ip(request, trusted_proxy=request.app.state.trusted_proxy)
    if ip is None:
        return False
    return is_blocked(request.state.db, ip, secret_key=request.app.state.secret_key)


def _default_staff_authenticator(request: Request) -> str:
    """§6.2's staff gate on the dry-run path. Contract §8.2, open item O-9.

    Raises rather than returning a falsy value on refusal: ``router.py``'s
    ``_authenticate_dry_run`` treats any exception, and any non-string or empty
    return, as `UNAUTHORIZED` - so both shapes are already safe. Raising is the
    clearer of the two here, because there is no username to return and an
    empty string is not one.

    The secret comes off ``app.state``, which ``create_app`` has already refused
    to start without. It must be the *same* ``SECRET_KEY`` the panel signs with,
    which is the deployment fact ``docker/compose.yaml``'s shared ``secret``
    volume exists to guarantee - a second value here means every proof fails to
    verify and every dry run 401s, which is O-9 again wearing a different cause.
    """
    username = verify_staff_proof(
        request.headers.get(STAFF_PROOF_HEADER),
        secret_key=request.app.state.secret_key,
    )
    if username is None:
        raise StaffProofRejected("no valid staff proof on the request")
    return username


class StaffProofRejected(Exception):
    """No usable ``X-Staff-Proof``. Mapped to `UNAUTHORIZED` (401) by the router."""


def create_app(
    *,
    database_url: str | None = None,
    secret_key: str | None = None,
    trusted_proxy: bool | None = None,
    engine_adapter: EngineAdapter | None = None,
    staff_authenticator: Callable[[Request], str] | None = None,
    blocklist_check: Callable[[Request], bool] | None = None,
) -> FastAPI:
    database_url = database_url or os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    #: Required, and required for the same reason ``DATABASE_URL`` is: without
    #: it neither §2.3's fingerprint nor §6.5's key can be derived, so the app
    #: would start with the blocklist silently doing nothing. That is precisely
    #: the failure §2.3 names - "a block applied in the panel silently fails to
    #: hold at the API" - and a deployment that cannot enforce a block should
    #: refuse to start rather than start not enforcing it. It must be the
    #: *same* value the panel uses (one ``SECRET_KEY`` per deployment): the two
    #: sides derive the fingerprint key independently, and a different secret
    #: means a different fingerprint for the same address, with nothing raised
    #: on either side.
    secret_key = secret_key or os.getenv("SECRET_KEY")
    if not secret_key:
        raise RuntimeError("SECRET_KEY is required")
    app = FastAPI(title="Kai Commitment Impact Calculator")
    app.state.session_factory = create_session_factory(database_url)
    app.state.engine_adapter = engine_adapter or DefaultEngineAdapter()
    #: §6.2's "requires an authenticated staff session", and O-9's close.
    #:
    #: **``staff_authenticator=None`` here means "use the default", not "no
    #: authenticator"** - the same correction ``blocklist_check`` below already
    #: carries, arrived at the same way. It meant the latter, and nothing in any
    #: deployment ever passed one: the only callers that did were two test
    #: files. So `/admin/try` answered `UNAUTHORIZED` on every dry run in every
    #: real stack while rendering it inside a 200 page, and §8.2 was a feature
    #: that had never worked outside a test.
    #:
    #: The default verifies `db/staff_proof.py`'s header - a short-lived signed
    #: assertion the panel mints once per call, after its own authentication has
    #: passed. Read that module for why the panel's session cookie is
    #: deliberately not what is checked here.
    #:
    #: Assigning ``app.state.staff_authenticator = None`` *after* construction
    #: still disables it, exactly as for the blocklist: a deliberate act on a
    #: built app is a thing a test may want and a deployment may not.
    app.state.staff_authenticator = (
        _default_staff_authenticator if staff_authenticator is None
        else staff_authenticator
    )
    app.state.secret_key = secret_key
    #: One deployment fact, one setting: the same ``PROTECTION_TRUSTED_PROXY``
    #: the panel reads (``admin/config.py``). False by default, because
    #: trusting ``X-Forwarded-For`` with no proxy in front that overwrites it
    #: lets any caller claim to be any address - and therefore claim not to be
    #: the blocked one.
    app.state.trusted_proxy = (
        _env_bool("PROTECTION_TRUSTED_PROXY", False)
        if trusted_proxy is None
        else trusted_proxy
    )
    #: §9.2's blocklist lookup. Injectable so that E's app can pass its own
    #: (it already holds a ``Settings``), and so that a test can drive the
    #: refusal path without a table.
    #:
    #: **``blocklist_check=None`` here means "use the default", not "no
    #: blocklist".** It meant the latter before this app had a default to fall
    #: back on, and an API that ships with the blocklist off unless someone
    #: remembers to wire it is the failure §2.3 names. Assigning
    #: ``app.state.blocklist_check = None`` *after* construction still disables
    #: it - that is a deliberate act on a built app, which is a thing a test
    #: may want and a deployment may not.
    app.state.blocklist_check = _blocklist_check if blocklist_check is None else blocklist_check
    app.state.rate_limiter = SlidingWindowRateLimiter()
    #: Set the first time a request arrives with no client address, so that
    #: `_NO_CLIENT_ADDRESS_WARNING` is logged once per process rather than once
    #: per request. On app state rather than in a module global because two
    #: apps in one process (a test suite builds dozens) are two deployments as
    #: far as this warning is concerned.
    app.state.warned_no_client_address = False

    #: Start-up warning, not a refusal. There is no safe value this could be
    #: defaulted to instead: `true` would let any caller forge any address, so
    #: `false` is correct and the hazard it carries is operational. The panel
    #: survives the same chain only because `_RATE_EXEMPT_PATHS` keeps its
    #: login handshake reachable (§8.3); a public API has no login handshake,
    #: so there is no equivalent mitigation to build here - only a warning an
    #: operator cannot miss. Unconditional on the blocklist, because a built
    #: app always has one (see just above) and §6.5's rate limit is keyed on
    #: the same value regardless.
    if not app.state.trusted_proxy:
        logger.warning(_UNTRUSTED_PROXY_WARNING)

    @app.middleware("http")
    async def blocklist(request: Request, call_next):
        """§9.2, ahead of routing and ahead of validation.

        **Middleware rather than a router dependency.** FastAPI solves a
        router's dependencies only once a request has matched a route, so a
        dead path under ``/api/v1/`` answered 404 while every live one answered
        403 - which tells a blocked caller exactly which routes exist. Running
        here covers the dead paths, the 405s and the documentation routes too.

        **The body is §9's envelope, built by ``api/errors.py`` like every
        other error.** ``admin/protection.py`` answers a blocked caller with a
        bare ``PlainTextResponse("Refused.")``, which is right for a browser
        hitting the panel and wrong here: a JSON client that got plain text
        back for one error out of twelve would have to special-case it, and C
        and D would each have to do so separately. Same blocklist, same silence
        about *why*, one body per surface. Built directly rather than raised,
        because an exception raised in middleware never reaches
        ``add_exception_handler`` - Starlette's ``ExceptionMiddleware`` sits
        inside this one.

        **``InvalidAddressError`` must not escape here** (§2.3). The default
        check cannot raise it - ``client_ip`` already normalises and returns
        ``None`` for anything unparseable - but an injected one is not this
        module's code, and a 500 on every request from one malformed address is
        not a failure worth risking to keep a narrow ``except`` narrower. Only
        that one exception is caught; a database error still surfaces as a 500
        rather than silently failing the blocklist open.

        **``db.detection.looks_automated`` is deliberately not applied here.**
        The panel runs it because ``/admin`` is a browser-only surface, so a
        caller there that is plainly a script is refused. ``/api/v1/`` is a
        public JSON API: scripting it is a legitimate way to use it, and §6.3's
        CSV export exists precisely to be fetched by a tool. Refusing ``curl``
        here would refuse a use the contract invites. The API applies the
        blocklist and §6.5's rate limit and nothing else - an asymmetry, and a
        decided one.

        **A caller with no address is skipped by both checks and warned about
        once.** There is no key to look up and none to count under; inventing
        one is what gives every such caller a single shared bucket and a single
        shared blocklist entry. That is the right answer per request and a bad
        situation in aggregate - if *every* request arrives this way (``uvicorn
        --uds`` behind nginx does this) both protections are silently doing
        nothing, which is the same failure the required ``SECRET_KEY`` above
        exists to prevent. Hence one warning per process, naming the cause.
        """
        if client_ip(request, trusted_proxy=request.app.state.trusted_proxy) is None:
            if not request.app.state.warned_no_client_address:
                request.app.state.warned_no_client_address = True
                logger.warning(_NO_CLIENT_ADDRESS_WARNING)

        check = request.app.state.blocklist_check
        if check is not None:
            try:
                blocked = check(request)
            except InvalidAddressError:
                blocked = False
            if blocked:
                return problem_response(blocked_problem())
        return await call_next(request)

    # Registered after `blocklist` and therefore *outside* it (Starlette builds
    # its stack so that the last middleware added is the outermost), which is
    # what puts `request.state.db` in place before the blocklist check needs it.
    # One session per request, blocked callers included - the alternative, a
    # blocklist middleware outside this one opening its own connection, is the
    # same number of connections with two places to get the lifecycle wrong.
    @app.middleware("http")
    async def database_session(request: Request, call_next):
        with app.state.session_factory() as db:
            request.state.db = db
            try:
                response = await call_next(request)
                if response.status_code < 400:
                    db.commit()
                else:
                    db.rollback()
                return response
            except Exception:
                db.rollback()
                return problem_response(
                    ApiProblem(500, "INTERNAL_ERROR", "An internal error occurred")
                )

    app.add_exception_handler(ApiProblem, api_problem_handler)
    app.add_exception_handler(RequestValidationError, validation_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(Exception, internal_error_handler)
    app.include_router(router)
    return app


def app_from_environment() -> FastAPI:
    return create_app()
