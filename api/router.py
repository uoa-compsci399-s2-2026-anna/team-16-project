"""Part B HTTP routes and contract orchestration."""

from __future__ import annotations

import csv
import io
import zipfile
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from api.engine_adapter import EngineAdapter
from api.errors import ApiProblem, ContractJSONResponse, engine_problem
from api.schemas import CalculatePayload, bundle_row_count
from api.serialization import wire
from db.errors import (
    FactorSetNotFoundError,
    FactorSetStateError,
    NoPublishedFactorSetError,
    TaxonomyInvariantError,
)
from db.repository import (
    get_factor_export,
    get_factor_set_by_version,
    get_public_stats,
    get_published_factor_set_id,
    get_taxonomy,
    load_factor_bundle,
    upsert_submission,
)

router = APIRouter(prefix="/api/v1")


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _limit(request: Request, group: str, limit: int) -> None:
    allowed, retry_after = request.app.state.rate_limiter.allow(
        f"{group}:{_client_ip(request)}", limit
    )
    if not allowed:
        raise ApiProblem(
            429,
            "RATE_LIMITED",
            "Rate limit exceeded; try again later",
            headers={"Retry-After": str(retry_after)},
        )


def _engine(request: Request) -> EngineAdapter:
    return request.app.state.engine_adapter


def _repository_problem(exc: Exception) -> ApiProblem:
    if isinstance(exc, NoPublishedFactorSetError):
        return ApiProblem(503, "NO_PUBLISHED_FACTOR_SET", "Calculator under maintenance")
    if isinstance(exc, FactorSetNotFoundError):
        return ApiProblem(400, "UNKNOWN_CODE", str(exc))
    if isinstance(exc, (FactorSetStateError, TaxonomyInvariantError)):
        return ApiProblem(500, "INTERNAL_ERROR", "Factor configuration is inconsistent")
    return ApiProblem(500, "INTERNAL_ERROR", "An internal error occurred")


@router.get("/taxonomy")
def taxonomy(request: Request) -> ContractJSONResponse:
    _limit(request, "get", 600)
    try:
        snapshot = get_taxonomy(request.state.db)
    except Exception as exc:
        raise _repository_problem(exc) from exc
    data = wire(snapshot)
    data["factor_set"] = {
        "version_label": data.pop("factor_set_version"),
        "is_mock": data.pop("factor_set_is_mock"),
    }
    return ContractJSONResponse(data)


def _authenticate_dry_run(request: Request) -> str:
    authenticator = request.app.state.staff_authenticator
    if authenticator is None:
        raise ApiProblem(401, "UNAUTHORIZED", "A valid staff session is required")
    try:
        actor = authenticator(request)
    except Exception as exc:
        raise ApiProblem(401, "UNAUTHORIZED", "A valid staff session is required") from exc
    if not isinstance(actor, str) or not actor:
        raise ApiProblem(401, "UNAUTHORIZED", "A valid staff session is required")
    return actor


@router.post("/calculate")
def calculate(payload: CalculatePayload, request: Request) -> ContractJSONResponse:
    _limit(request, "post-calculate", 120)
    header = request.headers.get("X-Dry-Run")
    if header is not None and header != "true":
        raise ApiProblem(400, "VALIDATION_ERROR", "X-Dry-Run must be true when supplied")
    dry_run = header is not None
    if payload.dry_run is not None and not dry_run:
        raise ApiProblem(400, "VALIDATION_ERROR", "dry_run requires X-Dry-Run: true")
    if dry_run:
        _authenticate_dry_run(request)

    adapter = _engine(request)
    factor_source = "published"
    factor_set_id: int | None = None
    try:
        if payload.dry_run and payload.dry_run.bundle is not None:
            if bundle_row_count(payload.dry_run.bundle) > 5000:
                raise ApiProblem(400, "VALIDATION_ERROR", "dry_run bundle exceeds 5000 rows")
            bundle = adapter.bundle_from_json(payload.dry_run.bundle)
            problems = list(bundle.validate())
            if problems:
                raise ApiProblem(
                    400,
                    "VALIDATION_ERROR",
                    "dry_run bundle is internally inconsistent",
                    [{"field": "dry_run.bundle", "issue": problem} for problem in problems],
                )
            factor_source = "inline"
        elif payload.dry_run and payload.dry_run.factor_set_version:
            factor_set = get_factor_set_by_version(
                request.state.db, payload.dry_run.factor_set_version
            )
            factor_set_id = factor_set.id
            bundle = load_factor_bundle(
                request.state.db,
                factor_set.id,
                bundle_factory=adapter.bundle_from_json,
            )
            factor_source = f"version:{factor_set.version_label}"
        else:
            factor_set_id = get_published_factor_set_id(request.state.db)
            bundle = load_factor_bundle(
                request.state.db,
                factor_set_id,
                bundle_factory=adapter.bundle_from_json,
            )
        engine_request = adapter.make_request(payload)
        result = adapter.calculate(engine_request, bundle)
    except ApiProblem:
        raise
    except (NoPublishedFactorSetError, FactorSetNotFoundError, FactorSetStateError) as exc:
        raise _repository_problem(exc) from exc
    except Exception as exc:
        raise engine_problem(exc, authenticated_dry_run=dry_run) from exc

    token: str | None = None
    if not dry_run:
        if factor_set_id is None:
            raise ApiProblem(500, "INTERNAL_ERROR", "Published factor set was not resolved")
        try:
            _, token = upsert_submission(
                request.state.db,
                str(payload.token) if payload.token else None,
                engine_request,
                factor_set_id,
            )
        except FactorSetNotFoundError as exc:
            raise _repository_problem(exc) from exc
        except Exception as exc:
            raise ApiProblem(
                500, "INTERNAL_ERROR", "An internal error occurred"
            ) from exc
    response = adapter.serialize_result(result)
    response["factor_source"] = factor_source
    response["token"] = token
    return ContractJSONResponse(wire(response))


@router.get("/factors")
def factors(
    request: Request, version: str | None = None, format: str = "json"
) -> Response:
    _limit(request, "get", 600)
    if format not in {"json", "csv"}:
        raise ApiProblem(400, "VALIDATION_ERROR", "format must be json or csv")
    try:
        data = get_factor_export(request.state.db, version, public_only=True)
    except Exception as exc:
        raise _repository_problem(exc) from exc
    if format == "json":
        return ContractJSONResponse(wire(data))
    return _csv_zip(data)


def _csv_zip(data: dict[str, Any]) -> Response:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, rows in data.items():
            records = [rows] if isinstance(rows, dict) else list(rows)
            fields = list(records[0].keys()) if records else []
            stream = io.StringIO(newline="")
            writer = csv.DictWriter(stream, fieldnames=fields)
            if fields:
                writer.writeheader()
                writer.writerows(wire(records))
            archive.writestr(f"{name}.csv", stream.getvalue())
    return Response(
        output.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="factors.zip"'},
    )


@router.get("/stats")
def stats(request: Request) -> ContractJSONResponse:
    _limit(request, "get", 600)
    try:
        result = get_public_stats(request.state.db)
    except Exception as exc:
        raise _repository_problem(exc) from exc
    return ContractJSONResponse(wire(result))
