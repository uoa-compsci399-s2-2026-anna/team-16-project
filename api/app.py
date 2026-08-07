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
    http_error_handler,
    internal_error_handler,
    problem_response,
    validation_handler,
)
from api.rate_limit import FixedWindowRateLimiter
from api.router import router
from db.session import create_session_factory

load_dotenv()


def create_app(
    *,
    database_url: str | None = None,
    engine_adapter: EngineAdapter | None = None,
    staff_authenticator: Callable[[Request], str] | None = None,
) -> FastAPI:
    database_url = database_url or os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    app = FastAPI(title="Kai Commitment Impact Calculator")
    app.state.session_factory = create_session_factory(database_url)
    app.state.engine_adapter = engine_adapter or DefaultEngineAdapter()
    app.state.staff_authenticator = staff_authenticator
    app.state.rate_limiter = FixedWindowRateLimiter()

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
