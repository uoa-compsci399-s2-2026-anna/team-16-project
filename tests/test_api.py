import pytest
from httpx import ASGITransport, AsyncClient
import io
import json
import zipfile
from pathlib import Path

from sqlalchemy import func, select, update

from db.models import FactorSet, FactorSetStatus, Submission

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text())


def _assert_shape(actual, expected):
    if isinstance(expected, dict):
        assert isinstance(actual, dict)
        assert actual.keys() == expected.keys()
        for key in expected:
            _assert_shape(actual[key], expected[key])
    elif isinstance(expected, list):
        assert isinstance(actual, list)
        if expected:
            assert actual
            _assert_shape(actual[0], expected[0])

pytestmark = pytest.mark.asyncio


async def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def test_taxonomy_contract(app):
    async with await _client(app) as client:
        response = await client.get("/api/v1/taxonomy")
    assert response.status_code == 200
    assert response.json()["factor_set"] == {"version_label": "MOCK-v0", "is_mock": True}
    assert response.json()["destinations"][0]["code"] == "landfill"


async def test_public_calculation_persists_and_returns_token(app):
    payload = json.loads((FIXTURES / "calculate_request.json").read_text())
    async with await _client(app) as client:
        response = await client.post("/api/v1/calculate", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["factor_source"] == "published"
    assert body["token"]
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Submission)) == 1


async def test_dry_run_requires_staff_and_does_not_persist(app):
    async with await _client(app) as client:
        denied = await client.post(
            "/api/v1/calculate",
            headers={"X-Dry-Run": "true"},
            json={"sector": "processing", "current": [{"destination": "landfill", "qty_kg": "1"}]},
        )
        assert denied.status_code == 401
        _assert_shape(denied.json(), _fixture("errors/unauthorized.json"))
        app.state.staff_authenticator = lambda request: "alice"
        allowed = await client.post(
            "/api/v1/calculate",
            headers={"X-Dry-Run": "true"},
            json={"sector": "processing", "current": [{"destination": "landfill", "qty_kg": "1"}]},
        )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["token"] is None
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Submission)) == 0


async def test_validation_uses_400_envelope(app):
    async with await _client(app) as client:
        response = await client.post(
            "/api/v1/calculate",
            json={"sector": "processing", "current": [{"destination": "landfill", "qty_kg": 1.0}]},
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    _assert_shape(response.json(), _fixture("errors/validation_error.json"))


async def test_public_factors_hide_drafts(app):
    async with await _client(app) as client:
        response = await client.get("/api/v1/factors", params={"version": "DRAFT-v1"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "UNKNOWN_CODE"


async def test_factors_support_json_and_csv_zip(app):
    async with await _client(app) as client:
        json_response = await client.get("/api/v1/factors")
        zip_response = await client.get("/api/v1/factors", params={"format": "csv"})
    assert json_response.status_code == 200
    assert json_response.json()["factor_set"]["version_label"] == "MOCK-v0"
    assert zip_response.status_code == 200
    assert zip_response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(zip_response.content)) as archive:
        assert set(archive.namelist()) == {
            "factor_set.csv",
            "constants.csv",
            "formulas.csv",
            "upstream.csv",
            "downstream.csv",
            "equivalences.csv",
        }
        assert "value_per_kg" in archive.read("upstream.csv").decode()


async def test_stats_only_expose_persisted_public_calculations(app):
    async with await _client(app) as client:
        await client.post(
            "/api/v1/calculate",
            json={
                "sector": "processing",
                "current": [{"destination": "landfill", "qty_kg": "3.000"}],
                "alternative": [{"destination": "landfill", "qty_kg": "999.000"}],
            },
        )
        response = await client.get("/api/v1/stats")
    assert response.status_code == 200
    body = response.json()
    assert body["total_calculations"] == 1
    assert body["by_destination"] == [
        {
            "code": "other",
            "label": "Other (sample too small)",
            "count": 1,
            "share": "1.0000",
            "total_kg": "3.000",
        }
    ]


async def test_dry_run_supports_persisted_and_inline_sources(app):
    app.state.staff_authenticator = lambda request: "alice"
    common = {
        "sector": "processing",
        "current": [{"destination": "landfill", "qty_kg": "1"}],
    }
    inline = {
        "version_label": "INLINE-v1",
        "is_mock": True,
        "sectors": [{"code": "processing"}],
        "food_categories": [
            {"code": "standard_mix", "is_standard_mix": True}
        ],
        "destination_groups": [{"code": "disposal"}],
        "destinations": [{"code": "landfill", "group": "disposal"}],
        "metrics": [],
    }
    async with await _client(app) as client:
        persisted = await client.post(
            "/api/v1/calculate",
            headers={"X-Dry-Run": "true"},
            json={**common, "dry_run": {"factor_set_version": "DRAFT-v1"}},
        )
        inline_response = await client.post(
            "/api/v1/calculate",
            headers={"X-Dry-Run": "true"},
            json={**common, "dry_run": {"bundle": inline}},
        )
    assert persisted.status_code == 200, persisted.text
    assert persisted.json()["factor_source"] == "version:DRAFT-v1"
    assert inline_response.status_code == 200, inline_response.text
    assert inline_response.json()["factor_source"] == "inline"
    assert inline_response.json()["factor_set"]["version_label"] == "INLINE-v1"
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Submission)) == 0


async def test_dry_run_contract_mismatches_are_rejected(app):
    app.state.staff_authenticator = lambda request: "alice"
    body = {
        "sector": "processing",
        "current": [{"destination": "landfill", "qty_kg": "1"}],
    }
    async with await _client(app) as client:
        body_without_header = await client.post(
            "/api/v1/calculate", json={**body, "dry_run": {}}
        )
        invalid_header = await client.post(
            "/api/v1/calculate", headers={"X-Dry-Run": "TRUE"}, json=body
        )
        mutually_exclusive = await client.post(
            "/api/v1/calculate",
            headers={"X-Dry-Run": "true"},
            json={
                **body,
                "dry_run": {
                    "factor_set_version": "DRAFT-v1",
                    "bundle": {"version_label": "INLINE-v1"},
                },
            },
        )
        too_large = await client.post(
            "/api/v1/calculate",
            headers={"X-Dry-Run": "true"},
            json={
                **body,
                "dry_run": {
                    "bundle": {
                        "version_label": "INLINE-v1",
                        "is_mock": True,
                        "sectors": [{}] * 5001,
                    }
                },
            },
        )
    for response in (body_without_header, invalid_header, mutually_exclusive, too_large):
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_formula_error_only_discloses_details_to_authenticated_staff(app):
    app.state.staff_authenticator = lambda request: "alice"
    bundle = {
        "version_label": "BROKEN-v1",
        "is_mock": True,
        "destinations": [{"code": "landfill"}],
        "_raise_formula": True,
    }
    payload = {
        "sector": "processing",
        "current": [{"destination": "landfill", "qty_kg": "1"}],
        "dry_run": {"bundle": bundle},
    }
    async with await _client(app) as client:
        response = await client.post(
            "/api/v1/calculate", headers={"X-Dry-Run": "true"}, json=payload
        )
    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "FORMULA_ERROR"
    assert error["details"] == [
        {
            "expression": "qty_kg / zero",
            "line": 1,
            "column": 8,
            "reason": "division by zero",
        }
    ]


async def test_public_formula_error_does_not_disclose_expression(app):
    class FormulaError(Exception):
        expression = "secret_expression"
        line = 4
        column = 2
        reason = "secret reason"

    def fail(_request, _bundle):
        raise FormulaError("do not return this")

    app.state.engine_adapter.calculate = fail
    async with await _client(app) as client:
        response = await client.post(
            "/api/v1/calculate",
            json={
                "sector": "processing",
                "current": [{"destination": "landfill", "qty_kg": "1"}],
            },
        )
    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "FORMULA_ERROR",
        "message": "The configured formula failed",
        "details": [],
    }
    _assert_shape(response.json(), _fixture("errors/formula_error.json"))


async def test_rate_limit_response_has_envelope_and_retry_after_and_ignores_forwarded_for(app):
    class CapturingLimiter:
        key = None

        def allow(self, key, limit):
            self.key = key
            return False, 37

    limiter = CapturingLimiter()
    app.state.rate_limiter = limiter
    async with await _client(app) as client:
        response = await client.get(
            "/api/v1/taxonomy", headers={"X-Forwarded-For": "203.0.113.9"}
        )
    assert response.status_code == 429
    assert response.headers["retry-after"] == "37"
    assert response.json()["error"]["code"] == "RATE_LIMITED"
    _assert_shape(response.json(), _fixture("errors/rate_limited.json"))
    assert limiter.key == "get:127.0.0.1"


async def test_framework_http_errors_also_use_the_contract_envelope(app):
    async with await _client(app) as client:
        missing = await client.get("/api/v1/not-an-endpoint")
        wrong_method = await client.delete("/api/v1/taxonomy")
    assert missing.status_code == 404
    assert missing.json() == {
        "error": {
            "code": "NOT_FOUND",
            "message": "Resource not found",
            "details": [],
        }
    }
    assert wrong_method.status_code == 405
    assert wrong_method.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


async def test_unknown_code_and_missing_published_have_contract_errors(app):
    async with await _client(app) as client:
        unknown = await client.post(
            "/api/v1/calculate",
            json={
                "sector": "processing",
                "current": [{"destination": "does_not_exist", "qty_kg": "1"}],
            },
        )
    assert unknown.status_code == 400
    assert unknown.json()["error"]["code"] == "UNKNOWN_CODE"
    _assert_shape(unknown.json(), _fixture("errors/unknown_code.json"))

    with app.state.session_factory() as db:
        db.execute(
            update(FactorSet)
            .where(FactorSet.status == FactorSetStatus.published)
            .values(status=FactorSetStatus.archived)
        )
        db.commit()
    async with await _client(app) as client:
        unavailable = await client.get("/api/v1/taxonomy")
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "NO_PUBLISHED_FACTOR_SET"
    _assert_shape(
        unavailable.json(), _fixture("errors/no_published_factor_set.json")
    )


async def test_contract_fixtures_have_the_same_top_level_shapes(app):
    cases = (
        ("taxonomy.json", "get", "/api/v1/taxonomy", None),
        ("factors.json", "get", "/api/v1/factors", None),
        ("stats.json", "get", "/api/v1/stats", None),
        (
            "calculate_response.json",
            "post",
            "/api/v1/calculate",
            {
                "sector": "processing",
                "current": [{"destination": "landfill", "qty_kg": "10"}],
                "alternative": [
                    {"destination": "landfill", "qty_kg": "5"}
                ],
            },
        ),
        (
            "calculate_response_single.json",
            "post",
            "/api/v1/calculate",
            {
                "sector": "processing",
                "current": [{"destination": "landfill", "qty_kg": "1"}],
            },
        ),
    )
    async with await _client(app) as client:
        for fixture_name, method, path, payload in cases:
            expected = _fixture(fixture_name)
            response = await client.request(method, path, json=payload)
            assert response.status_code == 200, response.text
            _assert_shape(response.json(), expected)
