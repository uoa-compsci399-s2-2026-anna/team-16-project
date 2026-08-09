"""Contract v1.2 §6.2 and §9 at the wire.

Every rule here is enforced nowhere else: the engine (§10.1) tests a fixed
request and cannot see a property *of* a request, and the database rejects
only what its unique indexes happen to cover — and it does so as a 500.
"""

import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from db.models import Submission

pytestmark = pytest.mark.asyncio


async def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


def _entry(qty="10.000", *, food_category="dairy", alternative=None):
    entry = {
        "sector": "processing",
        "food_category": food_category,
        "current": [{"destination": "landfill", "qty_kg": qty}],
    }
    if alternative is not None:
        entry["alternative"] = alternative
    return entry


async def _post(app, body, **kwargs):
    async with await _client(app) as client:
        return await client.post("/api/v1/calculate", json=body, **kwargs)


# --------------------------------------------------------------------------
# §6.2: a submission carries one or more entries
# --------------------------------------------------------------------------


async def test_a_submission_carries_several_entries_in_request_order(app):
    response = await _post(
        app,
        {
            "entries": [
                _entry("10.000", food_category="dairy"),
                _entry("5.000", food_category=None),
            ]
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [entry["food_category"] for entry in body["entries"]] == ["dairy", None]
    assert body["totals"]["total_kg"] == "15.000"


async def test_more_than_twenty_entries_are_rejected(app):
    entries = [_entry(food_category=f"cat_{index}") for index in range(21)]
    response = await _post(app, {"entries": entries})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["details"][0]["field"] == "entries"


async def test_a_request_with_no_entries_is_rejected(app):
    response = await _post(app, {"entries": []})
    assert response.status_code == 400
    assert response.json()["error"]["details"][0]["field"] == "entries"


async def test_two_entries_with_the_same_sector_and_food_category_are_rejected(app):
    """§6.2: "No duplicate `(sector, food_category)` across entries".

    Before this rule lived in the API the only thing rejecting the pair was
    `uq_submission_entry`, which surfaces as a 500 the user cannot act on.
    """
    response = await _post(app, {"entries": [_entry("10.000"), _entry("2.000")]})
    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"][0]["field"] == "entries[1]"
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Submission)) == 0


async def test_a_scenario_may_not_carry_more_than_twenty_lines(app):
    entry = {
        "sector": "processing",
        "food_category": "dairy",
        "current": [
            {"destination": f"landfill_{index}", "qty_kg": "1.000"}
            for index in range(21)
        ],
    }
    response = await _post(app, {"entries": [entry]})
    assert response.status_code == 400
    assert response.json()["error"]["details"][0]["field"] == "entries[0].current"


async def test_duplicate_destinations_within_one_scenario_are_rejected(app):
    entry = {
        "sector": "processing",
        "food_category": "dairy",
        "current": [
            {"destination": "landfill", "qty_kg": "1.000"},
            {"destination": "landfill", "qty_kg": "2.000"},
        ],
    }
    response = await _post(app, {"entries": [entry]})
    assert response.status_code == 400
    assert response.json()["error"]["details"][0]["field"] == "entries[0].current"


# --------------------------------------------------------------------------
# §6.2: the two scenarios of an entry describe the same mass
# --------------------------------------------------------------------------


async def test_an_alternative_that_simply_loses_mass_is_rejected(app):
    """The defect `prevention` exists to prevent (architecture.md §4.1).

    Dropping the landfill line rather than moving it to `prevention` produces
    a large and entirely fictitious `net_benefit`, and no field of the
    response exposes it: `totals.total_kg` is the *current* scenario's mass.
    """
    response = await _post(
        app,
        {
            "entries": [
                _entry(
                    "1200.000",
                    alternative=[{"destination": "landfill", "qty_kg": "300.000"}],
                )
            ]
        },
    )
    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"][0]["field"] == "entries[0].alternative"
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Submission)) == 0


async def test_the_mass_tolerance_is_inclusive_at_exactly_ten_grams(app):
    """20 lines x 0.0005 kg of independent rounding = 0.010 kg exactly.

    §6.2 derives the tolerance from that worst case, so the worst case sits
    *on* the boundary: a strict `<` would reject a request the front end
    unavoidably produces and the user cannot fix.
    """
    response = await _post(
        app,
        {
            "entries": [
                _entry(
                    "1000.000",
                    alternative=[{"destination": "landfill", "qty_kg": "1000.010"}],
                )
            ]
        },
    )
    assert response.status_code == 200, response.text


async def test_one_gram_past_the_tolerance_is_rejected(app):
    response = await _post(
        app,
        {
            "entries": [
                _entry(
                    "1000.000",
                    alternative=[{"destination": "landfill", "qty_kg": "1000.011"}],
                )
            ]
        },
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["details"][0]["field"] == "entries[0].alternative"


async def test_the_tolerance_is_absolute_and_not_relative(app):
    """At 5,000 tonnes even 0.01% is 500 kg — looser than the defect."""
    response = await _post(
        app,
        {
            "entries": [
                _entry(
                    "5000000.000",
                    alternative=[{"destination": "landfill", "qty_kg": "4999999.000"}],
                )
            ]
        },
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["details"][0]["field"] == "entries[0].alternative"


async def test_an_entry_without_an_alternative_is_not_mass_checked(app):
    response = await _post(app, {"entries": [_entry("10.000")]})
    assert response.status_code == 200, response.text
    assert response.json()["entries"][0]["alternative"] is None


# --------------------------------------------------------------------------
# §9: the error envelope
# --------------------------------------------------------------------------


async def test_a_details_field_is_a_bracket_indexed_path(app):
    """§9: `entries[0].current[1].qty_kg`, never Pydantic's `entries.0.current.1`.

    The failure is silent — a front end building the same key from its own
    render loop simply never matches, and the user sees only the banner.
    """
    entry = {
        "sector": "processing",
        "food_category": "dairy",
        "current": [
            {"destination": "landfill", "qty_kg": "1.000"},
            {"destination": "compost", "qty_kg": 2.0},
        ],
    }
    response = await _post(app, {"entries": [entry]})
    assert response.status_code == 400
    fields = [detail["field"] for detail in response.json()["error"]["details"]]
    assert "entries[0].current[1].qty_kg" in fields


async def test_a_blocked_caller_gets_a_fixed_message_and_null_details(app):
    """§9.2. `details` is `null`, not `[]` — and the message never varies."""
    app.state.blocklist_check = lambda request: True
    async with await _client(app) as client:
        calculated = await client.post("/api/v1/calculate", json={"entries": []})
        fetched = await client.get("/api/v1/taxonomy")
    for response in (calculated, fetched):
        assert response.status_code == 403, response.text
        assert response.json() == {
            "error": {
                "code": "BLOCKED",
                "message": (
                    "This request was refused. If you believe this is an error, "
                    "contact the Kai Commitment team."
                ),
                "details": None,
            }
        }


async def test_the_block_is_checked_before_request_validation(app):
    """§9.2: a blocked caller must not be able to probe the taxonomy through
    the API's own validation messages."""
    app.state.blocklist_check = lambda request: True
    response = await _post(app, {"nonsense": True})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "BLOCKED"


# --------------------------------------------------------------------------
# §6.2: token and X-Dry-Run
# --------------------------------------------------------------------------


async def test_a_stale_session_token_is_treated_as_absent(app):
    """§6.2: "Any value that does not resolve to a live submission is treated
    as absent and a new one is minted — a stale `sessionStorage` value must
    not produce an error"."""
    response = await _post(
        app, {"token": "not-a-uuid-at-all", "entries": [_entry("1.000")]}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token"] and body["token"] != "not-a-uuid-at-all"


async def test_dry_run_false_is_not_a_dry_run(app):
    response = await _post(
        app, {"entries": [_entry("1.000")]}, headers={"X-Dry-Run": "false"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["token"]
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Submission)) == 1


async def test_the_dry_run_header_match_is_case_insensitive(app):
    app.state.staff_authenticator = lambda request: "alice"
    response = await _post(
        app, {"entries": [_entry("1.000")]}, headers={"X-Dry-Run": "TRUE"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["token"] is None
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Submission)) == 0


async def test_an_uninterpretable_dry_run_header_is_still_rejected(app):
    response = await _post(
        app, {"entries": [_entry("1.000")]}, headers={"X-Dry-Run": "yes"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# --------------------------------------------------------------------------
# §6.2 / §3: what the response carries
# --------------------------------------------------------------------------


async def test_the_totals_carry_no_destination_breakdown(app):
    """§3 rule 2: the same destination can appear under several entries with
    different upstream factors, so there is no correct cross-entry
    aggregation. The serialiser omits the key."""
    response = await _post(
        app, {"entries": [_entry("10.000"), _entry("5.000", food_category=None)]}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    for metric in body["totals"]["current"]["metrics"].values():
        assert "by_destination" not in metric
    for entry in body["entries"]:
        for metric in entry["current"]["metrics"].values():
            assert metric["by_destination"]


async def test_every_number_on_the_wire_is_a_string(app):
    response = await _post(app, {"entries": [_entry("10.000")]})
    body = response.json()
    assert isinstance(body["totals"]["total_kg"], str)
    assert isinstance(body["entries"][0]["current"]["total_kg"], str)
    raw = json.loads(response.text)
    assert raw["entries"][0]["current"]["metrics"]["co2e"]["total"] == "10.000"
