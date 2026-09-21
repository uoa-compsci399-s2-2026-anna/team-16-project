import pytest
from httpx import ASGITransport, AsyncClient
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
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


def _add_destination(engine, code, *, is_prevention):
    """A taxonomy row added to the running app's database mid-test.

    The `app` fixture seeds through the same engine, so a row written here is
    one `GET /taxonomy` and `POST /calculate` both see.
    """
    from sqlalchemy.orm import sessionmaker

    from db.models import Destination, DestinationGroup

    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        group = db.scalar(select(DestinationGroup).where(DestinationGroup.code == "reuse"))
        db.add(Destination(group_id=group.id, code=code, name=code.title(),
                           is_prevention=is_prevention, sort_order=999))
        db.commit()


def _set_prevention_flag(engine, code, value):
    from sqlalchemy.orm import sessionmaker

    from db.models import Destination

    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        db.execute(
            update(Destination).where(Destination.code == code)
            .values(is_prevention=value)
        )
        db.commit()


async def test_taxonomy_contract(app):
    async with await _client(app) as client:
        response = await client.get("/api/v1/taxonomy")
    assert response.status_code == 200
    body = response.json()
    #: v1.58 added the third key. Asserted as a whole dict rather than by
    #: membership: `item_level_enabled` is what releases step 2.5 on the front
    #: end, and a key quietly dropped from this block is a feature switch that
    #: stops reaching the browser with nothing failing.
    assert body["factor_set"] == {
        "version_label": "MOCK-v0",
        "is_mock": True,
        "item_level_enabled": False,
    }
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


async def test_the_endpoint_offers_only_what_the_published_set_prices(app):
    """§6.1 as HTTP, not as a repository call (v1.21).

    `db/tests/test_taxonomy_coverage.py` is where the rule is argued; this is
    the assertion that it survives serialisation and reaches the browser. The
    seed's `MOCK-v0` prices `processing`/`dairy` and two destinations, and
    every other seeded row is an option that would have returned a silent zero.
    """
    async with await _client(app) as client:
        body = (await client.get("/api/v1/taxonomy")).json()
    assert {row["code"] for row in body["destinations"]} == {"prevention", "landfill"}
    assert {row["code"] for row in body["sectors"]} == {"processing"}
    assert {row["code"] for row in body["food_categories"]} == {"standard_mix", "dairy"}
    assert {row["code"] for row in body["destination_groups"]} == {"reuse", "disposal"}


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


async def test_the_language_cookie_reaches_the_api_and_is_ignored(app):
    """The chooser's cookie rides along here, and must land nowhere.

    `kaicalc_lang` is set at `path=/` because the calculator at `/` and the
    panel at `/admin` both have to read it, and that path cannot be scoped any
    narrower - so the browser attaches it to every `POST /api/v1/calculate`.
    Contract §7.7.3 says the API receives it and ignores it.

    **Asserted rather than assumed.** `docker/nginx.conf`'s access log was
    found writing four §2.3-forbidden fields on 2026-08-12, which is what
    "obviously we do not store that" is worth here.

    The comparison against a request with no cookie is what makes this
    evidence: an endpoint that had stopped persisting anything at all would
    satisfy a bare "the column is empty" check.
    """
    payload = json.loads((FIXTURES / "calculate_request.json").read_text())
    async with await _client(app) as client:
        with_cookie = await client.post(
            "/api/v1/calculate",
            json=payload,
            headers={"Cookie": "kaicalc_lang=ta"},
        )
        without = await client.post("/api/v1/calculate", json=payload)

    assert with_cookie.status_code == 200, with_cookie.text
    assert without.status_code == 200, without.text

    # Nothing about the language comes back out, and no cookie is echoed.
    assert "set-cookie" not in {k.lower() for k in with_cookie.headers}
    assert "kaicalc_lang" not in with_cookie.text
    assert "ta" not in {
        str(v) for v in with_cookie.json().items() if not isinstance(v, (dict, list))
    }

    # And nothing about it reached the row. Every persisted column is compared
    # against the cookie-less request's row, so a language leaking into ANY
    # field fails here rather than only into a field this test thought to name.
    with app.state.session_factory() as db:
        rows = db.scalars(select(Submission).order_by(Submission.id)).all()
        assert len(rows) == 2
        # Clocks and identity, not content. Listed explicitly rather than
        # skipped by type so that a future column carrying something real
        # cannot slip past by happening to be a datetime.
        volatile = {"id", "token", "created_at", "updated_at", "token_expires_at"}
        for column in Submission.__table__.columns.keys():
            if column in volatile:
                continue
            assert getattr(rows[0], column) == getattr(rows[1], column), (
                f"submission.{column} differs when a language cookie is sent; "
                "the chooser's preference has reached the database"
            )

    # And a sweep of the whole schema rather than of the one table this test
    # thought to name. `zh-Hant` is used as the sentinel because it is a real
    # language the chooser can emit and appears nowhere else in a calculation -
    # unlike `ta`, whose two letters occur inside ordinary words.
    async with await _client(app) as client:
        marked = await client.post(
            "/api/v1/calculate",
            json=payload,
            headers={"Cookie": "kaicalc_lang=zh-Hant"},
        )
    assert marked.status_code == 200, marked.text

    from sqlalchemy import text as _text

    with app.state.session_factory() as db:
        tables = [
            name
            for (name,) in db.execute(
                _text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        ]
        assert tables, "no tables to sweep; this test would pass vacuously"
        for table in tables:
            for row in db.execute(_text(f'SELECT * FROM "{table}"')):
                for value in row:
                    assert "zh-Hant" not in str(value), (
                        f"the language cookie reached {table}: {row!r}"
                    )


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


async def test_a_prevention_destination_is_refused_in_a_current_scenario(app):
    """§6.2, v1.5. Nothing but C's UI enforced this before.

    A current-scenario prevention line persists as an ordinary
    `submission_line` with `scenario = 'current'`, which is exactly what §5.4
    selects — so it becomes a `by_destination` bucket in the public
    statistics, and a prevention destination is by construction where waste
    that did not happen goes. §5.4's scenario predicate is the *other* half of
    this problem and cannot catch it: it excludes the alternative scenario, and
    this line is not in the alternative scenario.

    The two halves of the tree that looked like coverage were not:
    `_check_scenario` had no such rule, and
    `test_fixture_consistency.py::test_prevention_is_only_ever_an_alternative
    _destination` asserts it of the fixture. A fixture cannot constrain a
    caller who is not using it.
    """
    async with await _client(app) as client:
        refused = await client.post(
            "/api/v1/calculate",
            json=_body(
                [
                    {"destination": "landfill", "qty_kg": "100.000"},
                    {"destination": "prevention", "qty_kg": "900.000"},
                ]
            ),
        )
        # The same destination in the alternative is the whole point of it.
        allowed = await client.post(
            "/api/v1/calculate",
            json=_body(
                [{"destination": "landfill", "qty_kg": "1000.000"}],
                alternative=[{"destination": "prevention", "qty_kg": "1000.000"}],
            ),
        )

    assert refused.status_code == 400, refused.text
    body = refused.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    _assert_shape(body, _fixture("errors/validation_error.json"))
    # §9: the path is located at the scenario that carried the line, not at
    # the entry, so C can highlight the right half of the form.
    assert [detail["field"] for detail in body["error"]["details"]] == [
        "entries[0].current"
    ]
    assert body["error"]["details"][0]["issue"] == "prevention_in_current"
    assert "prevention" in body["error"]["details"][0]["message"]

    assert allowed.status_code == 200, allowed.text


async def test_a_second_vocabularys_prevention_is_refused_too(app, sqlite_engine):
    """The defect the flag closes.

    §10.3's ReFED fixture brings `refed_prevention`, whose factors are all
    zero and which is therefore a prevention destination by every property
    that matters. While this rule compared against the literal `prevention`,
    that row could be entered as **current**-scenario waste, persisted, and
    became a public `by_destination` bucket — waste that by construction did
    not happen, counted as real waste, on the page whose whole design problem
    is not overclaiming. Reproducible against the deployed stack, which has
    the ReFED set published and offers `refed_prevention` on the form.
    """
    _add_destination(sqlite_engine, "refed_prevention", is_prevention=True)

    async with await _client(app) as client:
        refused = await client.post(
            "/api/v1/calculate",
            json=_body(
                [
                    {"destination": "landfill", "qty_kg": "100.000"},
                    {"destination": "refed_prevention", "qty_kg": "900.000"},
                ]
            ),
        )

    assert refused.status_code == 400, refused.text
    body = refused.json()
    assert [detail["field"] for detail in body["error"]["details"]] == [
        "entries[0].current"
    ]
    assert "refed_prevention" in body["error"]["details"][0]["message"]


async def test_a_destination_named_prevention_but_unflagged_is_not_special(
    app, sqlite_engine
):
    """What proves the string is really gone.

    Clear the tick on the row *called* `prevention` and give the role to
    another row. A current-scenario line to `prevention` must now be an
    ordinary accepted line: if this answers 400, something is still reading
    the code.
    """
    _add_destination(sqlite_engine, "waste_avoided", is_prevention=True)
    _set_prevention_flag(sqlite_engine, "prevention", False)

    async with await _client(app) as client:
        allowed = await client.post(
            "/api/v1/calculate",
            json=_body([{"destination": "prevention", "qty_kg": "1000.000"}]),
        )
        refused = await client.post(
            "/api/v1/calculate",
            json=_body([{"destination": "waste_avoided", "qty_kg": "1000.000"}]),
        )

    assert allowed.status_code == 200, allowed.text
    assert refused.status_code == 400, refused.text
    assert "waste_avoided" in refused.json()["error"]["details"][0]["message"]


async def test_details_has_exactly_the_two_shapes_section_9_defines(app):
    """§9, v1.5. Two shapes, and `code` alone decides which.

    The inline-bundle check used to emit a third: `{field, issue}` with no
    `message`, carrying `FactorBundle.validate()`'s human-readable prose
    (§4.1) in `issue`. That inverts the two keys — a consumer told to branch
    on `issue` gets a sentence that changes whenever the engine's wording
    does, and finds no `message` to display. It now emits the field shape
    with a stable slug, so a front end needs a branch on `code` and no
    presence checks at all.
    """
    app.state.staff_authenticator = lambda request: "alice"
    body = _body([{"destination": "landfill", "qty_kg": "1"}])
    bad_bundle = {
        "version_label": "INLINE-BAD",
        "is_mock": True,
        "_problems": ["upstream row 3 names metric 'co2' which is not in this bundle"],
    }
    async with await _client(app) as client:
        response = await client.post(
            "/api/v1/calculate",
            headers={"X-Dry-Run": "true"},
            json={**body, "dry_run": {"bundle": bad_bundle}},
        )

    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"] == [
        {
            "field": "dry_run.bundle",
            "issue": "bundle_invalid",
            "message": "upstream row 3 names metric 'co2' which is not in this bundle",
        }
    ]
    # Every VALIDATION_ERROR entry carries all three keys, whichever check
    # produced it -- Pydantic's, §6.2's post-parse rules, or this one.
    async with await _client(app) as client:
        pydantic_error = await client.post(
            "/api/v1/calculate",
            json=_body([{"destination": "landfill", "qty_kg": 1.0}]),
        )
    for detail in pydantic_error.json()["error"]["details"]:
        assert set(detail) == {"field", "issue", "message"}, detail


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
    """One submission publishes no buckets at all (§5.4, v1.5).

    It used to publish `[{"code": "other", "count": 1, "total_kg": "3.000"}]`
    — the single calculation republished under a new label, with its exact
    tonnage, which is a public statement that exactly one calculation exists
    and what it weighed. `other` is now subject to the threshold like any
    other bucket, and with nothing left to absorb the breakdown is empty.
    `total_calculations` still reports, because a count of submissions
    identifies nobody.
    """
    async with await _client(app) as client:
        calculated = await client.post(
            "/api/v1/calculate",
            json=_body(
                [{"destination": "landfill", "qty_kg": "3.000"}],
                alternative=[{"destination": "landfill", "qty_kg": "3.000"}],
            ),
        )
        # v1.48: a calculation is not itself public any more (§5.3) -- this
        # test is proving the *threshold*, not consent, so the fixture opts
        # in on the visitor's behalf, the same way the calculator's own
        # checkbox would.
        contributed = await client.post(
            "/api/v1/contribute", json={"token": calculated.json()["token"]}
        )
        assert contributed.status_code == 204
        response = await client.get("/api/v1/stats")
    assert response.status_code == 200
    body = response.json()
    assert body["total_calculations"] == 1
    assert body["by_destination"] == []
    assert body["by_sector"] == []
    assert body["by_food_category"] == []


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


async def test_factors_fixture_matches_the_published_export(app):
    """§6.3, including the provenance v1.1 added the columns for.

    This carried a strict xfail until contract v1.4: `build_bundle_data` did
    not select `source_note` or `data_quality`, so the export published every
    value with its provenance stripped — the one combination v1.1's rationale
    rules out, since it removes the defence and keeps the exposure. The
    fixture was written to the contract rather than to the code, so removing
    the marker is what proves the repository caught up.

    v1.4 also settled the smaller question in the same direction: the
    equivalence rows' `name` and `sort_order` are now listed in §6.3 rather
    than removed from the export, because §10.2's bundle shape requires both
    and this dictionary is the projection both surfaces are built from.
    """
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

    Eleven entries is a small enough population that v1.5's absorb-until-clear
    rule fires: the suppressed sector is one entry, so `other` is one, so the
    smallest visible sector is absorbed as well and `other` reaches six. That
    is the behaviour worth having under test at this scale — a real deployment
    sits well above it, and this is the shape the statistics take on the day
    the calculator opens.
    """
    dual = _fixture("calculate_request.json")
    single = _request_from("calculate_response_single.json")
    async with await _client(app) as client:
        # v1.48: none of these six is public until its own visitor says so
        # (§5.3) -- this test is proving the aggregation and the threshold,
        # not consent, so every submission here opts in.
        for _ in range(5):
            posted = await client.post("/api/v1/calculate", json=dual)
            assert posted.status_code == 200, posted.text
            contributed = await client.post(
                "/api/v1/contribute", json={"token": posted.json()["token"]}
            )
            assert contributed.status_code == 204
        posted = await client.post("/api/v1/calculate", json=single)
        assert posted.status_code == 200, posted.text
        contributed = await client.post(
            "/api/v1/contribute", json={"token": posted.json()["token"]}
        )
        assert contributed.status_code == 204
        response = await client.get("/api/v1/stats")

    assert response.status_code == 200, response.text
    body = response.json()
    _assert_shape(body, _fixture("stats.json"), "stats.json$")

    assert body["total_calculations"] == 6
    # §5.4: buckets count entries, not submissions, so the two figures differ
    # by design and the fixture must not present them as if they did not.
    assert sum(bucket["count"] for bucket in body["by_sector"]) == 11
    codes = {bucket["code"]: bucket for bucket in body["by_sector"]}
    assert codes["other"]["count"] == 6, (
        "the below-threshold sector merges, and `other` then absorbs the "
        "smallest visible sector to clear the threshold itself"
    )
    assert "consumer_hospitality" not in codes, "a suppressed bucket must not be named"
    # No published bucket may sit below the threshold -- including `other`,
    # which is the rule v1.5 added. This is the assertion that fails if the
    # absorb loop is removed, whatever the counts happen to be.
    threshold = body["suppression_threshold"]
    for bucket in body["by_sector"]:
        assert bucket["count"] >= threshold, bucket
    # §6.4 says the shares "sum to 1", and both the fixture and the API deliver
    # exactly that; without `_bucketise`'s residue step a legend built on
    # `stats.json` reads 100% and the same legend reads 99.99% live.
    assert sum(Decimal(bucket["share"]) for bucket in body["by_sector"]) == Decimal("1")
    # `prevention` is an alternative-scenario destination. It must never reach
    # a public statistic, because it is by construction waste that did not
    # happen (§5.4).
    assert "prevention" not in {b["code"] for b in body["by_destination"]}


@pytest.mark.asyncio
async def test_the_new_context_fields_are_accepted_and_stored(app):
    """v1.48's four fields, end to end: sent, validated, persisted.

    Driven through the real endpoint rather than by constructing a payload,
    because the question is whether `extra="forbid"` lets them through and
    whether `upsert_submission` writes them - two places a field can be
    accepted and then quietly dropped.
    """
    body = {
        "gwp_horizon": 100,
        "time_frame": "one_month",
        "entries": [
            {
                "sector": "processing",
                "food_category": "dairy",
                "total_input_kg": "50000.000",
                "total_value_nzd": "120000.00",
                "wasted_value_nzd": "4500.00",
                "current": [{"destination": "landfill", "qty_kg": "1200.500"}],
                "alternative": None,
            }
        ],
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        response = await client.post("/api/v1/calculate", json=body)

    assert response.status_code == 200, response.text

    with app.state.session_factory() as session:
        row = session.scalars(
            select(Submission).order_by(Submission.id.desc())
        ).first()
        assert row.time_frame == "one_month"
        #: The default, not something the request set - v1.48 gives the
        #: visitor a separate action for this and `POST /calculate` never
        #: opts anybody in.
        assert row.is_public_contributed is False
        entry = row.entries[0]
        assert entry.total_input_kg == Decimal("50000.000")
        assert entry.total_value_nzd == Decimal("120000.00")
        assert entry.wasted_value_nzd == Decimal("4500.00")


@pytest.mark.asyncio
async def test_the_new_fields_are_optional_and_absent_is_not_zero(app):
    """A visitor who does not know their production total is the common case,
    and `None` has to survive as `None`.

    Zero would be a different claim - "this stage put nothing through" - and
    it would make the waste share infinite rather than absent.
    """
    body = {
        "gwp_horizon": 100,
        "entries": [
            {
                "sector": "processing",
                "food_category": "dairy",
                "current": [{"destination": "landfill", "qty_kg": "1200.500"}],
                "alternative": None,
            }
        ],
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        response = await client.post("/api/v1/calculate", json=body)

    assert response.status_code == 200, response.text
    with app.state.session_factory() as session:
        row = session.scalars(
            select(Submission).order_by(Submission.id.desc())
        ).first()
        assert row.time_frame is None
        entry = row.entries[0]
        assert entry.total_input_kg is None
        assert entry.total_value_nzd is None
        assert entry.wasted_value_nzd is None


#: v1.67. A shift, which is the case the client asked for by name, and it is
#: in the past so the `PERIOD_CEILING_HOURS` bound can never make this pair
#: expire the way a hard-coded future date would.
_SHIFT_START = "2026-09-14T08:10:00"
_SHIFT_END = "2026-09-14T16:20:00"


def _period_body(**extra):
    return _body([{"destination": "landfill", "qty_kg": "1200.500"}], **extra)


async def _post(app, body):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.post("/api/v1/calculate", json=body)


@pytest.mark.asyncio
async def test_a_custom_period_is_accepted_and_stored(app):
    """§6.2, v1.67, end to end: sent, validated, persisted, verbatim.

    Driven through the real endpoint rather than by constructing a payload,
    for the reason the v1.48 test above gives: `extra="forbid"` and
    `upsert_submission` are two separate places a new field can be accepted
    and then quietly dropped, and only a round trip crosses both.

    **Verbatim is the assertion.** The stored instants are the visitor's
    local wall clock and carry no zone (§2.3); a response that came back
    shifted by twelve hours would mean something on the path had decided
    which zone this was, which is precisely what this field does not know.
    """
    response = await _post(app, _period_body(
        time_frame="custom", period_start=_SHIFT_START, period_end=_SHIFT_END,
    ))
    assert response.status_code == 200, response.text

    with app.state.session_factory() as session:
        row = session.scalars(select(Submission).order_by(Submission.id.desc())).first()
        assert row.time_frame == "custom"
        assert row.period_start == datetime(2026, 9, 14, 8, 10)
        assert row.period_end == datetime(2026, 9, 14, 16, 20)


@pytest.mark.asyncio
async def test_recalculating_on_the_same_token_replaces_the_whole_period(app):
    """§5.3, v1.67. The upsert's **update** path, which is where a new column
    is most easily written on insert and forgotten.

    A visitor changing one number and pressing Calculate again is the
    commonest thing that happens on this screen, and it reuses the token. A
    period written only on insert would leave the row saying whatever the
    first calculation said, forever.

    **All three fields move together, and this asserts all three.** Carrying
    `time_frame` across while leaving the interval behind would leave the row
    in exactly the state `ck_submission_period` exists to forbid -- here,
    `one_week` against a shift the visitor has since replaced.
    """
    first = await _post(app, _period_body(
        time_frame="custom", period_start=_SHIFT_START, period_end=_SHIFT_END,
    ))
    assert first.status_code == 200, first.text
    token = first.json()["token"]
    assert token

    second = await _post(app, _period_body(
        token=token, time_frame="one_week",
        period_start="2026-09-07T00:00:00", period_end="2026-09-14T00:00:00",
    ))
    assert second.status_code == 200, second.text
    assert second.json()["token"] == token

    with app.state.session_factory() as session:
        rows = session.scalars(select(Submission)).all()
        row = [r for r in rows if r.token == token][0]
        assert row.time_frame == "one_week"
        assert row.period_start == datetime(2026, 9, 7)
        assert row.period_end == datetime(2026, 9, 14)


@pytest.mark.asyncio
async def test_recalculating_can_take_the_period_away_again(app):
    """The other half of the update path, and the one a `if period_start:`
    guard would quietly break.

    A visitor who set a period and then moved step 5 back to "Not stated"
    must end with a row carrying neither instant -- not with the old shift
    still attached to a `time_frame` that no longer mentions it, which is
    both wrong and a `ck_submission_period` violation waiting for the next
    write.
    """
    first = await _post(app, _period_body(
        time_frame="custom", period_start=_SHIFT_START, period_end=_SHIFT_END,
    ))
    token = first.json()["token"]

    second = await _post(app, _period_body(token=token))
    assert second.status_code == 200, second.text

    with app.state.session_factory() as session:
        rows = session.scalars(select(Submission)).all()
        row = [r for r in rows if r.token == token][0]
        assert row.time_frame is None
        assert row.period_start is None and row.period_end is None


@pytest.mark.asyncio
async def test_a_preset_carries_its_interval_too(app):
    """v1.67's designed normal case, and the reason `time_frame` was not
    collapsed to `custom`.

    The four presets are templates that fill the picker; `time_frame` goes on
    recording which button was pressed. So a row can and should say both, and
    the question *did they mean a standard week, or did they choose those
    dates?* stays answerable -- which an interval on its own cannot answer.
    """
    response = await _post(app, _period_body(
        time_frame="one_week", period_start=_SHIFT_START, period_end=_SHIFT_END,
    ))
    assert response.status_code == 200, response.text

    with app.state.session_factory() as session:
        row = session.scalars(select(Submission).order_by(Submission.id.desc())).first()
        assert row.time_frame == "one_week"
        assert row.period_start == datetime(2026, 9, 14, 8, 10)


@pytest.mark.asyncio
async def test_a_preset_with_no_interval_is_still_accepted(app):
    """Every row written before v1.67 has this shape, and the form that
    produced them is still the form until WP3 lands. A revision that made the
    old shape a 400 would break the deployed front end on the day it shipped.
    """
    response = await _post(app, _period_body(time_frame="one_month"))
    assert response.status_code == 200, response.text

    with app.state.session_factory() as session:
        row = session.scalars(select(Submission).order_by(Submission.id.desc())).first()
        assert row.time_frame == "one_month"
        assert row.period_start is None and row.period_end is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra, issue",
    [
        (dict(time_frame="custom", period_start=_SHIFT_START),
         "period_half_interval"),
        (dict(time_frame="custom", period_end=_SHIFT_END),
         "period_half_interval"),
        (dict(time_frame="custom"),
         "period_custom_without_interval"),
        (dict(period_start=_SHIFT_START, period_end=_SHIFT_END),
         "period_without_time_frame"),
        (dict(time_frame="custom", period_start=_SHIFT_END, period_end=_SHIFT_START),
         "period_ends_before_it_starts"),
        (dict(time_frame="custom", period_start="1969-12-31T08:10:00",
              period_end="1969-12-31T16:20:00"),
         "period_before_1970"),
        (dict(time_frame="custom", period_start="2026-09-14T08:10:00+13:00",
              period_end="2026-09-14T16:20:00+13:00"),
         "period_carries_a_zone"),
    ],
)
async def test_the_period_contradictions_and_bounds_are_refused(app, extra, issue):
    """§6.2's v1.67 rules, on the wire, each with its own `issue`.

    **`issue` and not just the status code**, because §9's `details[].issue`
    is what the front end binds a message to. The four cross-field rules all
    report `field: "body"` -- a `model_validator` reports against the
    location of the model, which is this module's own standing complaint --
    so `issue` is the only thing distinguishing them, and a validator that
    refused the right payloads with one indistinguishable code would pass a
    test that checked the status alone.
    """
    response = await _post(app, _period_body(**extra))

    assert response.status_code == 400, response.text
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    issues = {detail["issue"] for detail in body["error"]["details"]}
    assert issue in issues, (
        f"expected issue {issue!r} for {extra!r}; got {body['error']['details']}"
    )


@pytest.mark.asyncio
async def test_a_period_far_enough_ahead_is_refused_and_38_hours_is_not(app):
    """The ceiling, and **why it is 38 hours rather than 24.**

    The stored instants are zoneless local wall-clock time, so this server
    cannot tell which side of the date line a value was typed on. A visitor
    in New Zealand is at UTC+12 or +13, and the widest civil offset anywhere
    is UTC+14; their honest "now + 24 hours" therefore reads up to 24 + 14 =
    38 hours ahead of this server's UTC clock. The exact 24-hour rule lives
    in the form, where "now" is the visitor's own now.

    Both halves are asserted deliberately. A test that only proved the
    refusal would pass just as happily against a bound tightened to 24 --
    which would refuse a shift somebody in Auckland entered correctly, at the
    time it actually happened, with nothing in the payload they could change
    to make it pass.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)

    inside = await _post(app, _period_body(
        time_frame="custom",
        period_start=now.isoformat(),
        period_end=(now + timedelta(hours=30)).isoformat(),
    ))
    assert inside.status_code == 200, (
        "30 hours ahead is inside the window a UTC+13 visitor can honestly "
        f"reach: {inside.text}"
    )

    outside = await _post(app, _period_body(
        time_frame="custom",
        period_start=now.isoformat(),
        period_end=(now + timedelta(hours=40)).isoformat(),
    ))
    assert outside.status_code == 400, outside.text
    details = outside.json()["error"]["details"]
    assert {d["issue"] for d in details} == {"period_too_far_ahead"}
    assert [d["field"] for d in details] == ["period_end"]
    assert "38" in details[0]["message"], (
        "the message must say the number out loud, so that a reader meeting "
        "this text first does not read 38 as a typo for 24: "
        f"{details[0]['message']!r}"
    )


@pytest.mark.asyncio
async def test_an_unknown_time_frame_is_refused(app):
    """§6.2 fixes the vocabulary, for the reason `gwp_horizon` is fixed to
    20 and 100: a label the results page cannot render is a label that reaches
    a visitor as a raw string."""
    body = {
        "gwp_horizon": 100,
        "time_frame": "since_the_dawn_of_time",
        "entries": [
            {
                "sector": "processing",
                "food_category": "dairy",
                "current": [{"destination": "landfill", "qty_kg": "1200.500"}],
                "alternative": None,
            }
        ],
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        response = await client.post("/api/v1/calculate", json=body)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_a_negative_money_figure_is_refused(app):
    """The same guard `qty_kg` has. A negative wasted value would flow into
    the share in Task 5 and produce a negative percentage on the results
    page."""
    body = {
        "gwp_horizon": 100,
        "entries": [
            {
                "sector": "processing",
                "food_category": "dairy",
                "wasted_value_nzd": "-1.00",
                "current": [{"destination": "landfill", "qty_kg": "1200.500"}],
                "alternative": None,
            }
        ],
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        response = await client.post("/api/v1/calculate", json=body)

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_the_money_block_carries_the_right_numbers_over_http(app):
    """Defect 3: value, not merely shape.

    `test_contract_fixtures_have_the_same_top_level_shapes` (and every other
    shape check in this file) asserts `type(actual) is type(expected)` for a
    scalar and stops there - it would pass unchanged if every figure below
    came back as some other string of the same type. This is the one test in
    the suite that reads the actual numbers.

    Same two entries as `tests/fixtures/calculate_request.json` /
    `calculate_response.json`, hand-verified independently here. **This does
    not call the real engine** - the `app` fixture wires in
    `tests.support.sqlite.FakeEngineAdapter`, whose own `_money()` is a
    hand-kept copy of `engine.calculate._money`, not a call to it. What this
    test certifies is that a correct money figure survives the trip through
    `api/engine_adapter.py`'s serialisation onto the wire; that the fake's
    `_money()` agrees with the real one is `tests/support/
    test_fake_engine_agreement.py`'s job, not this test's:

    entry 1 (processing/dairy) prices its waste at $6750.00 / 1500 kg =
    $4.50/kg and diverts nothing to `prevention` - its alternative only moves
    mass between two non-prevention destinations - so it contributes $0.00.
    entry 2 (primary_production/vegetables) prices its waste at
    $4000.00 / 800 kg = $5.00/kg and diverts its whole 800 kg to
    `prevention`, contributing 5.00 x 800 = $4,000.00. A single blended rate
    over the whole form would instead answer (6750+4000)/(1500+800) x 800 =
    3739.13, not 4000.00 - the number this test would read back if the two
    entries' rates were blended instead of kept separate.
    """
    body = _fixture("calculate_request.json")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        response = await client.post("/api/v1/calculate", json=body)

    assert response.status_code == 200, response.text
    assert response.json()["totals"]["money"] == {
        "total_value_nzd": "50000.00",
        "wasted_value_nzd": "10750.00",
        "wasted_share_percent": "21.50",
        "saving_nzd": "4000.00",
    }


@pytest.mark.asyncio
async def test_a_calculation_does_not_reach_the_public_statistics_until_asked(app):
    """**v1.48 reverses §2.3's "there is no consent checkbox".**

    It said so deliberately - one calculation was one submission and nothing
    asked. The client asked for the opposite, and this is what the reversal
    has to mean: a submission is recorded, the panel sees it, and the public
    aggregate does not count it until the visitor says so.
    """
    #: `food_category` is `dairy`, not the brief's `bread_bakery`: this app
    #: fixture's taxonomy (`tests/support/sqlite.py`) seeds the codes
    #: `calculate_request.json` and the rest of this file already exercise --
    #: `standard_mix`, `vegetables`, `dairy` -- and `bread_bakery` is not
    #: among them, so it 400s as UNKNOWN_CODE before a submission ever exists.
    body = {
        "gwp_horizon": 100,
        "entries": [{
            "sector": "processing", "food_category": "dairy",
            "current": [{"destination": "landfill", "qty_kg": "1200.500"}],
            "alternative": None,
        }],
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        calculated = await client.post("/api/v1/calculate", json=body)
        assert calculated.status_code == 200, calculated.text
        token = calculated.json()["token"]

        before = (await client.get("/api/v1/stats")).json()["total_calculations"]

        contributed = await client.post("/api/v1/contribute", json={"token": token})
        assert contributed.status_code == 204

        after = (await client.get("/api/v1/stats")).json()["total_calculations"]

    assert after == before + 1, (
        "contributing did not move the public count, so either the flag is "
        "not written or the aggregate is not reading it"
    )


@pytest.mark.asyncio
async def test_an_unknown_token_is_not_an_error(app):
    """§6.2's rule for `token` everywhere else: a value that resolves to
    nothing is treated as absent. A stale `sessionStorage` value is not a
    request the visitor can fix, and a 400 here would surface as a broken
    button on a page whose calculation succeeded."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        response = await client.post(
            "/api/v1/contribute",
            json={"token": "3f2a91c4-77b5-4d1e-9c08-6b5e2a7d4419"},
        )

    assert response.status_code == 204


@pytest.mark.asyncio
async def test_a_dry_run_contribute_does_not_persist(app):
    """A dry run must persist nothing (§6.2), and this route writes.

    It is safe today only by coincidence: `/calculate` never mints a token
    under `X-Dry-Run: true`, so nothing has ever exercised a dry run here
    with a live token to flip. This test uses a real (non-dry-run) token so
    that a guard which merely happened to work because dry runs see no token
    cannot pass it -- the token is genuine, live, and would flip the flag on
    a non-dry-run call.
    """
    body = {
        "gwp_horizon": 100,
        "entries": [{
            "sector": "processing", "food_category": "dairy",
            "current": [{"destination": "landfill", "qty_kg": "1200.500"}],
            "alternative": None,
        }],
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        calculated = await client.post("/api/v1/calculate", json=body)
        assert calculated.status_code == 200, calculated.text
        token = calculated.json()["token"]

        response = await client.post(
            "/api/v1/contribute",
            json={"token": token},
            headers={"X-Dry-Run": "true"},
        )
        assert response.status_code == 204

    with app.state.session_factory() as db:
        row = db.scalar(select(Submission).where(Submission.token == token))
        assert row.is_public_contributed is False, (
            "a dry run flipped a real consent flag"
        )
