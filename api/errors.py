"""Uniform non-2xx error envelope and exception translation."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiProblem(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.code = code
        self.message = message
        self.details = details or []
        self.headers = headers


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


async def api_problem_handler(_request: Request, exc: ApiProblem) -> JSONResponse:
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


async def validation_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    details = []
    for error in exc.errors():
        loc = [str(item) for item in error.get("loc", ()) if item != "body"]
        details.append(
            {
                "field": ".".join(loc) or "body",
                "issue": error.get("type", "invalid"),
                "message": error.get("msg", "Invalid value"),
            }
        )
    return problem_response(
        ApiProblem(400, "VALIDATION_ERROR", "Request validation failed", details)
    )


def engine_problem(exc: Exception, *, authenticated_dry_run: bool) -> ApiProblem:
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
