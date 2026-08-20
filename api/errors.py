"""Uniform non-2xx error envelope and exception translation."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

#: `details` omitted and `details` explicitly null are different envelopes.
#: Every ordinary error carries `[]`; §9.2's `BLOCKED` carries `null`, and
#: with a plain `details or []` that value was unrepresentable, so the rule
#: could not be satisfied at all rather than merely being unimplemented.
_UNSET: Any = object()

#: §9.2. Fixed, and it never varies: the refusal must not say which rule
#: fired, when the block expires, or that a blocklist exists - that is a free
#: tuning signal for whoever is probing. Staff read the reason and the expiry
#: on /admin/ip-block/list and in `audit_log`.
BLOCKED_MESSAGE = (
    "This request was refused. If you believe this is an error, "
    "contact the Kai Commitment team."
)


class ApiProblem(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: list[dict[str, Any]] | None = _UNSET,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.code = code
        self.message = message
        self.details = [] if details is _UNSET else details
        self.headers = headers


def blocked_problem() -> ApiProblem:
    """§9.2's 403. Distinct from `RATE_LIMITED` on purpose: a retry will never
    succeed, and a front end that treated the two the same would poll a
    blocked caller against the API forever."""
    return ApiProblem(403, "BLOCKED", BLOCKED_MESSAGE, None)


class ContractJSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"


def problem_response(problem: ApiProblem) -> ContractJSONResponse:
    return ContractJSONResponse(
        status_code=problem.status,
        content={
            "error": {
                "code": problem.code,
                "message": problem.message,
                "details": problem.details,
            }
        },
        headers=problem.headers,
    )


async def api_problem_handler(request: Request, exc: ApiProblem) -> JSONResponse:
    # A 4xx is the system working: the caller was told what to fix and there is
    # nothing for an operator to do. A 5xx is the opposite, and it is the only
    # one of the two an operator can act on, so it is the only one that logs.
    #
    # `exc.__cause__` is where the real failure is. Every 5xx `ApiProblem` in
    # this codebase is raised with `from exc` — the seven sites in router.py,
    # `_repository_problem`'s fallback, `engine_problem`'s fallback — so the
    # original class and traceback are already attached and were, until now,
    # simply never read. Falling back to `exc` itself keeps a hand-raised
    # `ApiProblem(500, …)` from logging an empty traceback.
    if exc.status >= 500:
        logger.error(
            "%s %s -> %d %s",
            request.method,
            request.url.path,
            exc.status,
            exc.code,
            exc_info=exc.__cause__ or exc,
        )
    return problem_response(exc)


async def internal_error_handler(_request: Request, _exc: Exception) -> JSONResponse:
    return problem_response(
        ApiProblem(500, "INTERNAL_ERROR", "An internal error occurred")
    )


async def http_error_handler(
    _request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    code, message = {
        404: ("NOT_FOUND", "Resource not found"),
        405: ("METHOD_NOT_ALLOWED", "Method not allowed"),
    }.get(exc.status_code, ("HTTP_ERROR", "The request could not be completed"))
    return problem_response(
        ApiProblem(exc.status_code, code, message, headers=exc.headers)
    )


def bracket_path(loc: tuple[Any, ...]) -> str:
    """Pydantic's `loc` in the form §9 puts on the wire.

    `("body", "entries", 0, "current", 1, "qty_kg")` becomes
    `entries[0].current[1].qty_kg`, which is what a front end building a
    lookup key from its own render loop writes. Pydantic's own rendering is
    `entries.0.current.1.qty_kg`; neither is wrong, but if the API emits one
    and the client looks up the other, field-level highlighting simply never
    binds - no error, no console warning, and the user only ever sees the
    generic banner.
    """
    parts: list[str] = []
    for position, item in enumerate(loc):
        if position == 0 and item == "body":
            continue
        if isinstance(item, int):
            parts.append(f"[{item}]")
        elif parts:
            parts.append(f".{item}")
        else:
            parts.append(str(item))
    return "".join(parts) or "body"


async def validation_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    details = []
    for error in exc.errors():
        details.append(
            {
                "field": bracket_path(tuple(error.get("loc", ()))),
                "issue": error.get("type", "invalid"),
                "message": error.get("msg", "Invalid value"),
            }
        )
    return problem_response(
        ApiProblem(400, "VALIDATION_ERROR", "Request validation failed", details)
    )


def engine_problem(exc: Exception, *, authenticated_dry_run: bool) -> ApiProblem:
    """§4.4's four engine exceptions as §9 codes.

    Matched by class name rather than by importing `engine.errors`, because
    this module is imported at start-up and `engine/` may not be installed;
    the names are safe to match on because §4.4 fixes all four in the
    contract and gives each a mapped API error. Anything else the engine
    raises is a bug in the engine, not a documented condition, and lands on
    `INTERNAL_ERROR` deliberately - a code invented here would be one C could
    not have branched on.
    """
    name = type(exc).__name__
    if name == "UnknownCodeError":
        return ApiProblem(400, "UNKNOWN_CODE", "A requested code does not exist")
    if name in {"BundleFormatError", "ValueError"}:
        return ApiProblem(400, "VALIDATION_ERROR", str(exc) or "Invalid request")
    if name in {"FormulaError", "UnknownConstantError"}:
        details: list[dict[str, Any]] = []
        if authenticated_dry_run and name == "FormulaError":
            details.append(
                {
                    "expression": getattr(exc, "expression", ""),
                    "line": getattr(exc, "line", None),
                    "column": getattr(exc, "column", None),
                    "reason": getattr(exc, "reason", str(exc)),
                }
            )
        return ApiProblem(500, "FORMULA_ERROR", "The configured formula failed", details)
    if isinstance(exc, RuntimeError) and "engine" in str(exc).lower():
        return ApiProblem(503, "ENGINE_UNAVAILABLE", "Calculation engine is unavailable")
    return ApiProblem(500, "INTERNAL_ERROR", "An internal error occurred")
