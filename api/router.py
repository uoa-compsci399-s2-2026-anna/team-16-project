"""Part B HTTP routes and contract orchestration."""

from __future__ import annotations

import csv
import io
import zipfile
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from api.engine_adapter import EngineAdapter
from api.errors import (
    ApiProblem,
    ContractJSONResponse,
    engine_problem,
)
from api.export import EXPORT_FILENAME, ExportPayload, render_export_pdf
from api.schemas import (
    CalculatePayload,
    ContributePayload,
    bundle_row_count,
    entry_rule_problems,
)
from api.serialization import wire
from db.blocklist import ip_fingerprint
from db.detection import client_ip
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
    prevention_destination_codes,
    set_public_contribution,
    upsert_submission,
)

#: §9.2's blocklist check is **not** here. It was a router-level `Depends`,
#: which FastAPI solves only once a request has matched a route - so
#: `/api/v1/does-not-exist` answered 404 while every live path answered 403,
#: handing a blocked caller a working route scanner. It is now middleware in
#: `api/app.py`, which runs ahead of routing and therefore covers dead paths
#: too. Re-adding a dependency here would also mean two `is_blocked` lookups
#: per request, and §9.2 costs one query on purpose.
router = APIRouter(prefix="/api/v1")


def _limit(request: Request, group: str, limit: int) -> None:
    """§6.5, keyed on the §2.3 fingerprint and never on an address.

    The counter's dict outlives the request that filled it, so a raw address in
    a key is an address this system holds in memory - which §2.3 has always
    been read as covering, and which `admin/protection.py` already honoured
    while this layer did not (§6.5's own open item). Both layers now key the
    same way, on the same HMAC, derived from the same `SECRET_KEY`.

    **A caller with no address is skipped, not bucketed.** This used to build
    its key from the literal string `"unknown"`, which is a live dict key: every
    client-less caller shared one bucket, so the first of them to exceed the
    limit rate-limited all the rest. That is the same defect the empty-string
    key was in `admin/protection.py`, and it is fixed the same way - there is no
    address here to measure, so there is nothing to measure.
    """
    ip = client_ip(request, trusted_proxy=request.app.state.trusted_proxy)
    if ip is None:
        return
    fingerprint = ip_fingerprint(ip, secret_key=request.app.state.secret_key)
    allowed, retry_after = request.app.state.rate_limiter.allow(
        f"{group}:{fingerprint}", limit
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


def _dry_run_header(request: Request) -> bool:
    """§6.2's `X-Dry-Run`, read as a boolean rather than as one magic string.

    `false` means "not a dry run" and must not be a 400: it is the value a
    client sends when it templates the header unconditionally, and refusing
    it rejects a request that asked for exactly the default behaviour. The
    match is case-insensitive because HTTP header *values* have no case
    convention and `TRUE` is not a different instruction. Anything else is
    still refused, so a typo does not silently persist a staff dry run.
    """
    header = request.headers.get("X-Dry-Run")
    if header is None:
        return False
    value = header.strip().lower()
    if value in {"true", "false"}:
        return value == "true"
    raise ApiProblem(400, "VALIDATION_ERROR", "X-Dry-Run must be true or false")


@router.post("/calculate")
def calculate(payload: CalculatePayload, request: Request) -> ContractJSONResponse:
    _limit(request, "post-calculate", 120)
    dry_run = _dry_run_header(request)
    if payload.dry_run is not None and not dry_run:
        raise ApiProblem(400, "VALIDATION_ERROR", "dry_run requires X-Dry-Run: true")
    if dry_run:
        _authenticate_dry_run(request)

    #: §6.2's "no prevention destination in a current scenario". Read from the
    #: taxonomy on every request rather than from a constant, because which
    #: destinations carry the role is data (§2.1) — the literal this replaces
    #: did not cover §10.3's `refed_prevention`, which could therefore be
    #: entered as current-scenario waste and reach the public statistics.
    #:
    #: `destination` carries no `factor_set_id`, so this is one small read of a
    #: global table (tens of rows) and is the same answer for a dry run as for a
    #: public request. A failure here is a taxonomy failure, not a request one.
    try:
        prevention_codes = prevention_destination_codes(request.state.db)
    except Exception as exc:
        raise _repository_problem(exc) from exc

    problems = entry_rule_problems(payload, prevention_codes=prevention_codes)
    if problems:
        raise ApiProblem(400, "VALIDATION_ERROR", "Request validation failed", problems)

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
                    # §9's field shape: `issue` is a stable slug and `message`
                    # carries the prose. `bundle.validate()` returns
                    # human-readable problems (§4.1), and putting one of those
                    # in `issue` -- as this did until v1.5 -- inverts the two
                    # keys: a consumer told to branch on `issue` gets a
                    # sentence that changes whenever the engine's wording does,
                    # and finds no `message` to display.
                    [
                        {
                            "field": "dry_run.bundle",
                            "issue": "bundle_invalid",
                            "message": problem,
                        }
                        for problem in problems
                    ],
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
            # A token that resolves to nothing is treated as absent by
            # `upsert_submission` and a new one is minted (§6.2), which is
            # why the field is a plain string: a stale `sessionStorage`
            # value from an earlier deployment is not a request the user
            # can act on.
            _, token = upsert_submission(
                request.state.db,
                payload.token or None,
                engine_request,
                factor_set_id,
                time_frame=payload.time_frame,
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


@router.post("/export/pdf")
def export_pdf(payload: ExportPayload, request: Request) -> Response:
    """The document endpoint. See `api/export.py`'s module docstring for why
    it exists, why its payload cannot carry a figure of its own, and what its
    renderer does and does not do yet.

    Rate-limited on the same group and limit as `/calculate`: this route runs
    the engine on every call exactly as `/calculate` does, so a caller cannot
    dodge §6.5's budget for that cost by asking for a PDF instead of a JSON
    body.

    **No `X-Dry-Run`, no staff proof, no token.** Those all belong to
    `/calculate`'s persisted, staff-rehearsable path; this route persists
    nothing and always prices the published factor set, so none of the three
    has anything to attach to here. `upsert_submission` is never called - a
    download is not a calculation (§2.3).
    """
    _limit(request, "post-calculate", 120)

    try:
        prevention_codes = prevention_destination_codes(request.state.db)
    except Exception as exc:
        raise _repository_problem(exc) from exc

    problems = entry_rule_problems(payload, prevention_codes=prevention_codes)
    if problems:
        raise ApiProblem(400, "VALIDATION_ERROR", "Request validation failed", problems)

    adapter = _engine(request)
    try:
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
        raise engine_problem(exc, authenticated_dry_run=False) from exc

    pdf_bytes = render_export_pdf(result, payload)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{EXPORT_FILENAME}"'},
    )


@router.post("/contribute", status_code=204)
def contribute(payload: ContributePayload, request: Request) -> Response:
    """§5.3, v1.48. The visitor's own opt-in, keyed on the same session token
    `/calculate` mints -- no new identifier, per §2.3.

    Rate-limited on the same group and limit as `/calculate`: a button a
    caller can click once can be scripted into a loop, and this route writes
    just as `/calculate` does.

    Always 204, contributed or not: a token that does not resolve to a live
    submission -- unknown, or already expired and nulled by `expire_tokens`
    -- is treated as absent everywhere else it appears (§6.2), and a 404 here
    would turn a stale `sessionStorage` value into an error the visitor has
    no way to act on. `set_public_contribution` reports whether a row moved
    only to keep that distinction available to a caller that wants it; the
    route itself does not branch on it, so it also gives nothing away about
    whether the token exists.

    A dry run must persist nothing, exactly as `/calculate`'s own dry run
    must (§6.2). Today that is true by coincidence rather than by guard:
    `/calculate` never mints a token under `X-Dry-Run: true`, so a dry-run
    caller here has no live token to flip a flag with. `_dry_run_header`
    still reads the header and skips the write when it is `true`, so the
    guard holds even if a future dry-run path ever does hand out a real
    token -- this route must not become a live consent write just because
    nothing exercises that case yet.
    """
    _limit(request, "post-calculate", 120)
    if not _dry_run_header(request):
        set_public_contribution(request.state.db, payload.token)
    return Response(status_code=204)


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
