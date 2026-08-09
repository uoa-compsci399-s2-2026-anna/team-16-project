"""Standalone composition root for Part B.

When E's branch is merged, its existing FastAPI app can include ``router`` and
pass ``admin.auth.require_staff`` as the staff authenticator instead.
"""

from __future__ import annotations

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

load_dotenv()

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
    app.state.staff_authenticator = staff_authenticator
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
    #: refusal path without a table; the default is the real one above.
    #: Setting it to ``None`` disables the blocklist entirely and every caller
    #: passes - which is a thing a test may want and a deployment may not.
    app.state.blocklist_check = _blocklist_check if blocklist_check is None else blocklist_check
    app.state.rate_limiter = SlidingWindowRateLimiter()

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
        """
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
