"""The item dimension at the wire. Contract §6.1, §6.2, §5.3, §9.

Landing step 6 of the food-granularity design, and the first of its landings
where the wire itself changes rather than a column being added behind it.
Three things arrive together and each fails on its own:

* **§6.1 carries the vocabulary.** `food_items[]`, filtered by the
  parent-covered rule `_covered_by` has computed since v1.57 and nothing has
  read, plus `factor_set.item_level_enabled` — which is how the front end
  learns whether to render step 2.5 at all. The flag reaches the browser and
  never the engine (design §3, `tests/test_item_level_inertness.py`).
* **§6.2 accepts and returns `entries[].food_item`**, and the duplicate rule
  becomes a triple. That last one *changes* an existing answer rather than
  adding to it: two entries naming `dairy/cheese` and `dairy/butter` are what
  a forked chain produces, `uq_submission_entry` has permitted them since
  v1.54, and the API refused them.
* **§5.3 stores it.** Nothing wrote `submission_entry.food_item_id` before
  this landing, so a visitor who named a food stored nothing about it and
  §5.4's statistics and §5.3's reproducibility both lost the answer.

**Nothing here seeds an item into a shared fixture**, for the reason
`tests/db/test_food_item_projection.py` gives: no deployment holds an item row
(`admin/seed.py` seeds none, and which foods exist is an open question with the
client), so the suite's default state must go on being the one production is
in. Every test that needs a food writes it into the app's own engine first.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from db.models import FoodCategory, FoodItem, SubmissionEntry

pytestmark = pytest.mark.asyncio

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


async def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


def _add_item(engine, code, name, category_code, *, sort_order=0):
    """One `food_item` row written into the running app's own database.

    The `app` fixture seeds through `sqlite_engine`, so a row written here is
    one `GET /taxonomy` and `POST /calculate` both see — the same trick
    `tests/api/test_api.py::_add_destination` uses.
    """
    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        parent = db.scalar(select(FoodCategory).where(FoodCategory.code == category_code))
        assert parent is not None, f"the seed has no {category_code!r} to hang {code!r} on"
        db.add(
            FoodItem(
                code=code,
                name=name,
                food_category_id=parent.id,
                sort_order=sort_order,
            )
        )
        db.commit()


def _entry(qty="10.000", *, food_category="dairy", food_item=None, omit_item=True):
    entry = {
        "sector": "processing",
        "food_category": food_category,
        "current": [{"destination": "landfill", "qty_kg": qty}],
    }
    if food_item is not None or not omit_item:
        entry["food_item"] = food_item
    return entry


async def _post(app, body, **kwargs):
    async with await _client(app) as client:
        return await client.post("/api/v1/calculate", json=body, **kwargs)


# --------------------------------------------------------------------------
# §6.1 — the vocabulary and the switch
# --------------------------------------------------------------------------


async def test_the_taxonomy_carries_the_item_vocabulary_and_each_items_parent(
    app, sqlite_engine
):
    """§6.1. Without the parent the front end cannot group step 2.5 under the
    categories step 2 offered, and a flat list of forty-seven foods is not the
    question the design asks."""
    _add_item(sqlite_engine, "cheese", "Cheese", "dairy", sort_order=20)
    #: Both under `dairy`, because `vegetables` is not priced by the seed and
    #: a food under it would be filtered out by the parent-covered rule the
    #: test below is about. Ordering has to be asserted on rows that survive.
    _add_item(sqlite_engine, "butter", "Butter", "dairy", sort_order=10)

    async with await _client(app) as client:
        response = await client.get("/api/v1/taxonomy")

    assert response.status_code == 200, response.text
    body = response.json()
    assert "food_items" in body, (
        "GET /taxonomy carries no food_items, so the front end has no "
        "vocabulary to render step 2.5 from"
    )
    rows = {row["code"]: row for row in body["food_items"]}
    assert rows["cheese"] == {
        "code": "cheese",
        "name": "Cheese",
        "food_category": "dairy",
        "sort_order": 20,
    }
    #: `sort_order` then `code`, the order every other taxonomy section uses.
    assert [row["code"] for row in body["food_items"]] == ["butter", "cheese"]
    #: §1.1: `code` is the cross-layer identifier and no primary key travels.
    assert all("id" not in row for row in body["food_items"])


async def test_the_taxonomy_tells_the_front_end_whether_step_two_point_five_is_released(
    app,
):
    """§6.1. `factor_set.item_level_enabled` is the *only* thing that decides
    whether the front end renders step 2.5. Leave it off the response and the
    browser has to guess — which in practice means rendering the step whenever
    `food_items` is non-empty, and the vocabulary is non-empty long before any
    set prices a food individually."""
    async with await _client(app) as client:
        response = await client.get("/api/v1/taxonomy")

    factor_set = response.json()["factor_set"]
    assert "item_level_enabled" in factor_set, (
        "the switch that releases step 2.5 never reaches the browser"
    )
    assert factor_set["item_level_enabled"] is False
    assert set(factor_set) == {"version_label", "is_mock", "item_level_enabled"}


async def test_an_item_is_offered_because_its_parent_is_priced_not_because_it_is(
    app, sqlite_engine
):
    """§6.1's one row whose coverage rule is not "it has factors of its own".

    A food with no row of its own falls through to its category's average — a
    defined number, and §2.1's categories *are* the averages of these same
    foods — so filtering items the way destinations are filtered would hide
    almost the whole vocabulary to prevent something that cannot happen.
    `dairy` is priced by the seed and `vegetables` is not.
    """
    _add_item(sqlite_engine, "cheese", "Cheese", "dairy")
    _add_item(sqlite_engine, "carrots", "Carrots", "vegetables")

    async with await _client(app) as client:
        body = (await client.get("/api/v1/taxonomy")).json()

    offered = {row["code"] for row in body["food_items"]}
    priced = {row["code"] for row in body["food_categories"]}
    assert "cheese" in offered, (
        "cheese has no factor row of its own and its parent is priced, which "
        "is the state nearly every food will be in"
    )
    assert "vegetables" not in priced, "the seed's coverage changed; this test is moot"
    assert "carrots" not in offered, (
        "carrots was offered under a food category the published set does not "
        "price, so step 2.5 would offer a food step 2 does not"
    )
    #: Every offered food names a category the same response carries, or the
    #: front end cannot group it and renders an orphan.
    for row in body["food_items"]:
        assert row["food_category"] in priced, row


# --------------------------------------------------------------------------
# §6.2 — the request and the response
# --------------------------------------------------------------------------


async def test_a_named_food_is_accepted_and_echoed(app, sqlite_engine):
    _add_item(sqlite_engine, "cheese", "Cheese", "dairy")

    response = await _post(app, {"entries": [_entry(food_item="cheese")]})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["entries"][0]["food_item"] == "cheese"


@pytest.mark.parametrize("omit", [True, False])
async def test_absent_and_null_both_mean_the_visitor_named_no_food(app, omit):
    """Which is every request that exists today. `null` is present-and-null
    on the way back, on the terms §6.2 already gives `alternative`."""
    entry = _entry(omit_item=omit)
    if not omit:
        assert entry["food_item"] is None

    response = await _post(app, {"entries": [entry]})

    assert response.status_code == 200, response.text
    assert response.json()["entries"][0]["food_item"] is None


async def test_a_food_may_not_arrive_without_its_category(app, sqlite_engine):
    """Design §3.4 and `ck_submission_entry_item_has_category`.

    `food_category_id IS NULL` already means *did not break it down by type*
    and §5.4 forbids conflating that with a finer answer, so the one state the
    schema refuses is refused here too — with a field, rather than as the 500
    an IntegrityError would surface as.
    """
    _add_item(sqlite_engine, "cheese", "Cheese", "dairy")

    response = await _post(
        app, {"entries": [_entry(food_category=None, food_item="cheese")]}
    )

    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    #: The slug is asserted, not merely the field: before this landing the
    #: request was refused too — by `extra="forbid"`, which refuses *every*
    #: request that names a food. A test that only looked for the field name
    #: would have passed then and would go on passing if the dimension were
    #: removed again.
    assert error["details"][0] == {
        "field": "entries[0].food_item",
        "issue": "item_without_category",
        "message": error["details"][0]["message"],
    }, error
    assert "food_category" in error["details"][0]["message"]


# --------------------------------------------------------------------------
# §6.2 — the duplicate rule, which changes an existing answer
# --------------------------------------------------------------------------


async def test_two_foods_of_one_category_are_a_forked_chain_and_not_a_duplicate(
    app, sqlite_engine
):
    """The whole point of step 2.5, and the answer the API gave before this
    landing was `VALIDATION_ERROR`. `uq_submission_entry` has been four
    columns since v1.54 and has permitted this pair the entire time."""
    _add_item(sqlite_engine, "cheese", "Cheese", "dairy")
    _add_item(sqlite_engine, "butter", "Butter", "dairy")

    response = await _post(
        app,
        {
            "entries": [
                _entry("10.000", food_item="cheese"),
                _entry("4.000", food_item="butter"),
            ]
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [entry["food_item"] for entry in body["entries"]] == ["cheese", "butter"]
    assert body["totals"]["total_kg"] == "14.000"


async def test_the_same_food_twice_is_still_a_duplicate(app, sqlite_engine):
    _add_item(sqlite_engine, "cheese", "Cheese", "dairy")

    response = await _post(
        app,
        {
            "entries": [
                _entry("10.000", food_item="cheese"),
                _entry("2.000", food_item="cheese"),
            ]
        },
    )

    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"][0]["field"] == "entries[1]"
    assert error["details"][0]["issue"] == "duplicate_entry"


async def test_a_category_with_no_food_twice_is_still_a_duplicate(app):
    """The NULL half, and the one `uq_submission_entry` cannot state: MySQL
    treats NULLs as distinct inside a UNIQUE key, which is why
    `uq_submission_entry_generic` exists as a functional index over
    `COALESCE(food_item_id, 0)`. The API's rule has to agree with the index
    that actually enforces it, and a Python tuple carrying `None` collapses
    exactly the way `COALESCE(..., 0)` does.

    **This test passes on both sides of v1.58, deliberately, and it is the
    only one in this file that does.** It is the regression half of the
    rule: widening the key to a triple had to go on refusing what the pair
    refused, and a check that only ran green after the change could not say
    so. Measured rather than assumed -- the whole file was run against the
    pre-v1.58 tree and this is one of the two that passed there. Do not
    'fix' it by giving one of its entries a food; that is the test below.
    """
    response = await _post(app, {"entries": [_entry("10.000"), _entry("2.000")]})

    assert response.status_code == 400, response.text
    detail = response.json()["error"]["details"][0]
    assert detail["field"] == "entries[1]"
    #: Pinned to the slug and not just to the status, because a 400 is
    #: what every other refusal in §6.2 returns too, and this test's
    #: subject is *which* rule fires on a pair the index collapses.
    assert detail["issue"] == "duplicate_entry"


async def test_a_category_with_no_food_beside_one_of_its_foods_is_accepted(
    app, sqlite_engine
):
    """The case the database permits and somebody has to decide about.

    `COALESCE(food_item_id, 0)` makes `(processing, dairy, NULL)` and
    `(processing, dairy, cheese)` two different keys, so the index admits the
    pair — and the API's rule must agree with the index rather than with the
    constraint that does not enforce it. It is also the right answer on its
    own terms: §5.4 gives a NULL its own bucket meaning *the user did not
    break this down by type*, so "300 kg of dairy I did not itemise, and 40 kg
    of cheese I did" is two different answers about two different masses, not
    one answer sent twice. Refusing it would make the API enforce something
    the schema does not, which is the divergence this landing exists to close
    in the other direction.
    """
    _add_item(sqlite_engine, "cheese", "Cheese", "dairy")

    response = await _post(
        app,
        {
            "entries": [
                _entry("300.000"),
                _entry("40.000", food_item="cheese"),
            ]
        },
    )

    assert response.status_code == 200, response.text
    assert [e["food_item"] for e in response.json()["entries"]] == [None, "cheese"]


async def test_the_same_food_under_two_sectors_is_not_a_duplicate(app, sqlite_engine):
    """The sector is still the first member of the key."""
    _add_item(sqlite_engine, "cheese", "Cheese", "dairy")

    entry = _entry("10.000", food_item="cheese")
    other = dict(entry, sector="consumer_hospitality")

    response = await _post(app, {"entries": [entry, other]})

    assert response.status_code == 200, response.text


# --------------------------------------------------------------------------
# §5.3 — persistence
# --------------------------------------------------------------------------


async def test_a_named_food_is_stored_with_its_parent_category(app, sqlite_engine):
    """Design §3.4: **both** columns, never NULL in the category.

    Nothing wrote `submission_entry.food_item_id` before this landing, so a
    submission that named a food stored nothing about it — §5.4's statistics
    and §5.3's reproducibility both lost the answer the visitor gave, with no
    error anywhere.
    """
    _add_item(sqlite_engine, "cheese", "Cheese", "dairy")

    response = await _post(app, {"entries": [_entry("40.000", food_item="cheese")]})
    assert response.status_code == 200, response.text

    with sessionmaker(bind=sqlite_engine, expire_on_commit=False)() as db:
        entry = db.scalars(select(SubmissionEntry)).one()
        item = db.get(FoodItem, entry.food_item_id) if entry.food_item_id else None
        assert item is not None, (
            "the visitor named a food and the submission stored nothing about it"
        )
        assert item.code == "cheese"
        assert entry.food_category_id == item.food_category_id, (
            "an entry naming a food must name its category too; NULL there "
            "already means 'did not break it down by type' (§5.4)"
        )


async def test_a_submission_that_names_no_food_stores_null(app, sqlite_engine):
    """The absent case, stored as absent. A column that defaulted to anything
    else would make "did not itemise" indistinguishable from an itemised
    answer, which is the conflation §5.4 forbids from the other side."""
    response = await _post(app, {"entries": [_entry("40.000")]})
    assert response.status_code == 200, response.text

    with sessionmaker(bind=sqlite_engine, expire_on_commit=False)() as db:
        entry = db.scalars(select(SubmissionEntry)).one()
        assert entry.food_item_id is None
        assert entry.food_category_id is not None


async def test_a_dry_run_that_names_a_food_still_persists_nothing(app, sqlite_engine):
    """§6.2's `X-Dry-Run`. A new column is a new way for a staff dry run to
    reach the public statistics."""
    from db.staff_proof import mint_staff_proof
    from tests.support.sqlite import API_TEST_SECRET_KEY

    _add_item(sqlite_engine, "cheese", "Cheese", "dairy")
    proof = mint_staff_proof("tester", secret_key=API_TEST_SECRET_KEY)

    response = await _post(
        app,
        {"entries": [_entry("40.000", food_item="cheese")]},
        headers={"X-Dry-Run": "true", "X-Staff-Proof": proof},
    )

    assert response.status_code == 200, response.text
    with sessionmaker(bind=sqlite_engine, expire_on_commit=False)() as db:
        assert db.scalars(select(SubmissionEntry)).all() == []


async def test_the_statistics_roll_a_named_food_up_into_its_category(app, sqlite_engine):
    """§5.4's `by_food_category` keeps working unchanged, which is the reason
    design §3.4 stores the parent alongside the item rather than instead of
    it. A bucket per food would also weaken the suppression threshold, which
    §5 calls a privacy regression rather than a cosmetic one."""
    _add_item(sqlite_engine, "cheese", "Cheese", "dairy")
    _add_item(sqlite_engine, "butter", "Butter", "dairy")

    async with await _client(app) as client:
        for index in range(6):
            body = {
                "entries": [
                    _entry("10.000", food_item="cheese" if index % 2 else "butter")
                ]
            }
            created = await client.post("/api/v1/calculate", json=body)
            assert created.status_code == 200, created.text
            await client.post(
                "/api/v1/contribute", json={"token": created.json()["token"]}
            )
        stats = (await client.get("/api/v1/stats")).json()

    buckets = {row["code"]: row for row in stats["by_food_category"]}
    assert "dairy" in buckets, (
        "six itemised dairy submissions did not reach the dairy bucket, so "
        "the item column has replaced the category rather than refining it"
    )
    assert "cheese" not in buckets and "butter" not in buckets, (
        "§5.4 buckets by food category; a bucket per food reaches the "
        "threshold with fewer real submissions behind it"
    )
    assert buckets["dairy"]["count"] == 6


# --------------------------------------------------------------------------
# §9 — the engine's two refusals, with a field
# --------------------------------------------------------------------------


async def test_a_food_the_factor_set_never_heard_of_is_a_validation_error(app):
    """`FactorBundle.resolve_food_item` raises for it, and the engine raising
    is not enough on its own: `UnknownCodeError` maps to a bare `UNKNOWN_CODE`
    that names no field, so a front end cannot put the message next to the
    control the visitor used."""
    response = await _post(app, {"entries": [_entry(food_item="unicorn_steak")]})

    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"][0]["field"] == "entries[0].food_item"
    assert error["details"][0]["issue"] == "unknown_food_item"


async def test_a_food_from_another_category_is_a_validation_error(app, sqlite_engine):
    """The pair `submission_entry` permits and the engine is what refuses.

    `(fruit, cheese)` is storable — the CHECK constraint says only that an
    item may not arrive *without* a category — and a lookup for it would fall
    quietly through the chain and price cheese at the vegetables average. A
    wrong answer that looks right is what this dimension is built to avoid.
    """
    _add_item(sqlite_engine, "cheese", "Cheese", "dairy")

    response = await _post(
        app, {"entries": [_entry(food_category="vegetables", food_item="cheese")]}
    )

    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"][0]["field"] == "entries[0].food_item"
    assert error["details"][0]["issue"] == "food_item_category_mismatch"
    assert "dairy" in error["details"][0]["message"]


async def test_the_second_bad_entry_is_reported_as_well_as_the_first(
    app, sqlite_engine
):
    """§9: one `details` entry per problem, in `entries` order — a caller with
    two bad entries is told about both."""
    _add_item(sqlite_engine, "cheese", "Cheese", "dairy")

    response = await _post(
        app,
        {
            "entries": [
                _entry("1.000", food_item="unicorn_steak"),
                _entry("2.000", food_category="vegetables", food_item="cheese"),
            ]
        },
    )

    assert response.status_code == 400, response.text
    details = response.json()["error"]["details"]
    assert [detail["field"] for detail in details] == [
        "entries[0].food_item",
        "entries[1].food_item",
    ]
    assert [detail["issue"] for detail in details] == [
        "unknown_food_item",
        "food_item_category_mismatch",
    ]


async def test_a_refused_food_persists_nothing(app, sqlite_engine):
    """A refusal that had already written a submission would put an entry in
    §5.4's statistics for a calculation that never returned a figure.

    **The refusal is asserted by its slug, not by its status, and that is
    the whole difference between this test and a green light.** Against
    the pre-v1.58 tree the same request is refused as well -- `EntryPayload`
    was `extra="forbid"` with no `food_item` field, so `unicorn_steak`
    came back as `extra_forbidden` -- and a check that stopped at `400`
    passed there too, saying nothing about the rule it is named for.
    Measured: it did.
    """
    response = await _post(app, {"entries": [_entry(food_item="unicorn_steak")]})

    assert response.status_code == 400, response.text
    detail = response.json()["error"]["details"][0]
    assert detail["field"] == "entries[0].food_item"
    assert detail["issue"] == "unknown_food_item"

    with sessionmaker(bind=sqlite_engine, expire_on_commit=False)() as db:
        assert db.scalars(select(SubmissionEntry)).all() == []


# --------------------------------------------------------------------------
# §6.2.3 — the export route takes the same entries
# --------------------------------------------------------------------------


async def test_the_export_payload_accepts_the_same_entry_shape():
    """`ExportPayload` reuses `EntryPayload`, so the dimension reaches the
    export the moment it reaches `/calculate`. Asserted on the model rather
    than over HTTP because the renderer needs WeasyPrint's native libraries,
    which CI has and a developer's Windows box may not."""
    from api.export import ExportPayload

    payload = ExportPayload(
        locale="en",
        entries=[
            {
                "sector": "processing",
                "food_category": "dairy",
                "food_item": "cheese",
                "current": [{"destination": "landfill", "qty_kg": "10.000"}],
            }
        ],
    )

    assert payload.entries[0].food_item == "cheese"
