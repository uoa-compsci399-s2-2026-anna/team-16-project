import pytest
from httpx import ASGITransport, AsyncClient
import io
import json
import zipfile
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select, update

from db.models import FactorSet, FactorSetStatus, Submission

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _fixture(name):
    #: Explicitly UTF-8. `read_text()` uses the process's locale encoding,
    #: which is GBK on a Chinese Windows install and cp1252 on a Western one,
    #: and the fixtures carry em dashes throughout the taxonomy prose. The
    #: files are UTF-8 on every branch; only the reader was ambiguous.
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _body(current, alternative=None, **extra):
    """A §6.2 request body carrying one entry.

    Every payload in this file was written against the pre-v1.2 single-entry
    shape (`sector` and `current` at the top level). The rules those tests
    prove are unchanged; only the envelope moved.
    """
    entry = {"sector": "processing", "food_category": "dairy", "current": current}
    if alternative is not None:
        entry["alternative"] = alternative
    return {"entries": [entry], **extra}


#: Response objects whose *keys* are taxonomy codes rather than contract
#: field names. §6.2's `metrics` is keyed by `metric.code` and `net_benefit`
#: by the same, and "metrics are data, not code" means the key set is a
#: property of the deployment's `metric` table, not of the contract: a fixture
#: published from a five-metric factor set and a test app seeded with one
#: metric are both valid §6.2 bodies. Comparing key sets here would assert
#: that every deployment configures the same metrics.
_CODE_KEYED = {"metrics", "net_benefit"}


def _assert_shape(actual, expected, path="$", *, in_list=False):
    """Assert a live response has the fixture's shape.

    Three things this checks that the key-set comparison it replaces did not,
    each of which let a real defect through:

    * **Every** array element is checked, not element zero alone.
    * Scalars must agree on JSON type, so a decimal that arrives as a number
      instead of a string (§1.2) fails here rather than in a browser.
    * Null and not-null must agree outside arrays, so §9.2's `details: null`
      cannot silently become the `[]` every other error fixture carries.

    Nullability is tolerated *inside* arrays because one element cannot stand
    for the nullability of the rest: `factor_downstream.food_category` is null
    on the generic rows and set on the specific ones, in one array (§2.2).
    """
    key = path.rsplit(".", 1)[-1]
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{path}: expected an object, got {actual!r}"
        if key in _CODE_KEYED:
            assert actual, f"{path}: code-keyed map is empty"
            if expected:
                template = next(iter(expected.values()))
                for code, value in actual.items():
                    _assert_shape(value, template, f"{path}.{code}", in_list=in_list)
            return
        assert actual.keys() == expected.keys(), (
            f"{path}: keys differ. "
            f"missing={sorted(expected.keys() - actual.keys())} "
            f"unexpected={sorted(actual.keys() - expected.keys())}"
        )
        for name in expected:
            _assert_shape(actual[name], expected[name], f"{path}.{name}", in_list=in_list)
    elif isinstance(expected, list):
        assert isinstance(actual, list), f"{path}: expected an array, got {actual!r}"
        if expected:
            assert actual, f"{path}: fixture carries rows, response carries none"
            for index, item in enumerate(actual):
                _assert_shape(item, expected[0], f"{path}[{index}]", in_list=True)
    elif expected is None or actual is None:
        if not in_list:
            assert (actual is None) == (expected is None), (
                f"{path}: fixture has {expected!r}, response has {actual!r} — "
                "null and not-null are different contracts here (§9.2)"
            )
    else:
        assert type(actual) is type(expected), (
            f"{path}: expected {type(expected).__name__}, "
            f"got {type(actual).__name__} ({actual!r})"
        )

pytestmark = pytest.mark.asyncio


async def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def test_taxonomy_contract(app):
    async with await _client(app) as client:
        response = await client.get("/api/v1/taxonomy")
    assert response.status_code == 200
    body = response.json()
    assert body["factor_set"] == {"version_label": "MOCK-v0", "is_mock": True}
    #: Ordered by sort_order, so `prevention` (5) leads and `landfill` (110)
    #: trails. Asserted as a property rather than as `destinations[0]`, which
    #: only held while the seed carried a single destination.
    destinations = {row["code"]: row for row in body["destinations"]}
    assert destinations["landfill"]["group"] == "disposal"
    assert [row["code"] for row in body["destinations"]] == sorted(
        destinations, key=lambda code: destinations[code]["sort_order"]
    )
    #: §10: without a `prevention` destination in a non-waste group, neither
    #: the mass-conserving offset nor the non-waste half of the MfE taxonomy
    #: can be demonstrated at all.
    groups = {row["code"]: row for row in body["destination_groups"]}
    assert groups[destinations["prevention"]["group"]]["is_waste"] is False


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
            json=_body([{"destination": "landfill", "qty_kg": "1"}]),
        )
        assert denied.status_code == 401
        _assert_shape(denied.json(), _fixture("errors/unauthorized.json"))
        app.state.staff_authenticator = lambda request: "alice"
        allowed = await client.post(
            "/api/v1/calculate",
            headers={"X-Dry-Run": "true"},
            json=_body([{"destination": "landfill", "qty_kg": "1"}]),
        )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["token"] is None
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Submission)) == 0


async def test_validation_uses_400_envelope(app):
    async with await _client(app) as client:
        response = await client.post(
            "/api/v1/calculate",
            json=_body([{"destination": "landfill", "qty_kg": 1.0}]),
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
            json=_body(
                [{"destination": "landfill", "qty_kg": "3.000"}],
                alternative=[{"destination": "landfill", "qty_kg": "3.000"}],
            ),
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
    common = _body([{"destination": "landfill", "qty_kg": "1"}])
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
    body = _body([{"destination": "landfill", "qty_kg": "1"}])
    async with await _client(app) as client:
        body_without_header = await client.post(
            "/api/v1/calculate", json={**body, "dry_run": {}}
        )
        # Not "TRUE": §6.2's header match is case-insensitive, so that is a
        # dry run. An uninterpretable value is still refused rather than
        # silently persisting a staff calculation.
        invalid_header = await client.post(
            "/api/v1/calculate", headers={"X-Dry-Run": "maybe"}, json=body
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
    payload = _body([{"destination": "landfill", "qty_kg": "1"}])
    payload["dry_run"] = {"bundle": bundle}
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
            json=_body([{"destination": "landfill", "qty_kg": "1"}]),
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
    # The connection address, not the forwarded one - and as the §2.3
    # fingerprint of it rather than the address itself, which is what this
    # assertion read before the two layers were reconciled onto one counter.
    # The limiter's dict outlives the request that filled it, so a raw address
    # in a key is an address held in memory; `admin/protection.py` already
    # keyed on the fingerprint while this layer did not (§6.5's open item).
    from db.blocklist import ip_fingerprint

    from tests.support.sqlite import API_TEST_SECRET_KEY

    assert limiter.key == f"get:{ip_fingerprint('127.0.0.1', secret_key=API_TEST_SECRET_KEY)}"
    assert limiter.key != f"get:{ip_fingerprint('203.0.113.9', secret_key=API_TEST_SECRET_KEY)}"
    assert "127.0.0.1" not in limiter.key


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
            json=_body([{"destination": "does_not_exist", "qty_kg": "1"}]),
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


def _request_from(fixture_name):
    """Rebuild the request that produced a calculate response fixture.

    §10 names no `calculate_request_single.json`, so the single-scenario
    fixture's request is derived from the fixture itself: every line it was
    calculated from is in `by_destination`. Posting that back is what proves
    the fixture describes a request the API will actually accept.
    """
    fixture = _fixture(fixture_name)
    entries = []
    for entry in fixture["entries"]:
        body = {"sector": entry["sector"], "food_category": entry["food_category"]}
        for scenario in ("current", "alternative"):
            if entry[scenario] is None:
                body[scenario] = None
                continue
            metric = next(iter(entry[scenario]["metrics"].values()))
            body[scenario] = [
                {"destination": row["destination"], "qty_kg": row["qty_kg"]}
                for row in metric["by_destination"]
            ]
        entries.append(body)
    return {"gwp_horizon": fixture["gwp_horizon"], "entries": entries}


async def test_contract_fixtures_have_the_same_top_level_shapes(app):
    cases = (
        ("taxonomy.json", "get", "/api/v1/taxonomy", None),
        (
            "calculate_response.json",
            "post",
            "/api/v1/calculate",
            _fixture("calculate_request.json"),
        ),
        (
            "calculate_response_single.json",
            "post",
            "/api/v1/calculate",
            _request_from("calculate_response_single.json"),
        ),
    )
    async with await _client(app) as client:
        for fixture_name, method, path, payload in cases:
            expected = _fixture(fixture_name)
            response = await client.request(method, path, json=payload)
            assert response.status_code == 200, f"{fixture_name}: {response.text}"
            _assert_shape(response.json(), expected, f"{fixture_name}$")


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Contract v1.3 change 13 requires GET /factors to carry `source_note` "
        "and `data_quality` on every upstream and downstream row and "
        "`source_note` on every equivalence, present-and-null rather than "
        "omitted, because §6.3 is the only public surface where a factor can "
        "say whether it was measured or borrowed. The columns exist "
        "(admin/factor_models.py) and the admin panel edits them, but "
        "db/repository.py's build_bundle_data does not select them, so the "
        "export publishes the values with the provenance stripped. The fixture "
        "is written to the contract; this marker comes off when the repository "
        "catches up, and `strict` means it fails the moment it does. "
        "Second, smaller gap in the same direction, recorded here rather than "
        "left silent: the export's equivalence rows carry `name` and "
        "`sort_order`, which §6.3's sample response does not show. The fixture "
        "carries them because they are what the endpoint actually sends and "
        "`name` is useful to D, but §6.3 should either list them or the export "
        "should stop sending them. Contract question, not a code defect."
    ),
)
async def test_factors_fixture_matches_the_published_export(app):
    async with await _client(app) as client:
        response = await client.get("/api/v1/factors")
    assert response.status_code == 200, response.text
    _assert_shape(response.json(), _fixture("factors.json"), "factors.json$")


async def test_stats_fixture_shape_holds_against_a_populated_database(app):
    """`stats.json` needs submissions behind it or it checks nothing.

    An empty database returns three empty arrays, which the shape check passes
    vacuously — that is how a fixture of all zeros survived long enough for D
    to have nothing to build the statistics page against. Six submissions are
    posted first: five of the two-entry canonical request, which puts every
    bucket it touches at or above the default threshold of 5, and one single
    -entry submission in a third sector, which lands below the threshold and
    is therefore merged into `other` (§5.4).
    """
    dual = _fixture("calculate_request.json")
    single = _request_from("calculate_response_single.json")
    async with await _client(app) as client:
        for _ in range(5):
            posted = await client.post("/api/v1/calculate", json=dual)
            assert posted.status_code == 200, posted.text
        posted = await client.post("/api/v1/calculate", json=single)
        assert posted.status_code == 200, posted.text
        response = await client.get("/api/v1/stats")

    assert response.status_code == 200, response.text
    body = response.json()
    _assert_shape(body, _fixture("stats.json"), "stats.json$")

    assert body["total_calculations"] == 6
    # §5.4: buckets count entries, not submissions, so the two figures differ
    # by design and the fixture must not present them as if they did not.
    assert sum(bucket["count"] for bucket in body["by_sector"]) == 11
    codes = {bucket["code"]: bucket for bucket in body["by_sector"]}
    assert codes["other"]["count"] == 1, "the below-threshold sector must merge"
    assert "consumer_hospitality" not in codes, "a suppressed bucket must not be named"
    # §6.4 says the shares "sum to 1", and both the fixture and the API now
    # deliver exactly that. Buckets of 5/11, 5/11 and 1/11 quantize to 0.9999,
    # so `_bucketise` assigns the residue; without that step a legend built on
    # `stats.json` reads 100% and the same legend reads 99.99% live.
    assert sum(Decimal(bucket["share"]) for bucket in body["by_sector"]) == Decimal("1")
    # `prevention` is an alternative-scenario destination. It must never reach
    # a public statistic, because it is by construction waste that did not
    # happen (§5.4).
    assert "prevention" not in {b["code"] for b in body["by_destination"]}
