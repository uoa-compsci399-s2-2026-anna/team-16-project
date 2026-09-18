"""Authoring the item level: the screens, the flag and the two publish guards.

The schema landed in #95 and the engine's lookup is #100. Between the two there
was a state nothing failed on and nobody could use: `food_item` existed as a
table, `factor_upstream.food_item_id` existed as a column, and
`FactorUpstreamAdmin` hand-writes `column_list`, `column_details_list` and
`form_columns` as explicit lists — so the column was invisible on all three and
**no staff member could author a single item-level factor.** A test on the
model would have been green throughout.

So the assertions here are about the *panel*, and they are deliberately made
against the live view classes and the rendered pages rather than against the
model: the failure being guarded is a column that exists everywhere except on
the form.
"""

from decimal import Decimal

import pytest
from sqlalchemy import select

from admin.factor_lifecycle import (
    LifecycleError, import_published_into, publish_factor_set,
)
from admin.factor_models import FactorSet, FactorSetStatus, FactorUpstream
from admin.factor_views import FactorSetAdmin, FactorUpstreamAdmin
from admin.taxonomy_models import FoodItem
from admin.taxonomy_views import FoodItemAdmin
#: Re-used rather than rebuilt, the same way tests/admin/test_compare_view.py
#: re-uses it: the fake is the only CalculateClient in this suite, and a second
#: copy would be a second thing to keep in step with `HttpCalculateClient`.
from tests.admin.test_dryrun_view import fake_calc_client  # noqa: F401

#: `asyncio` is applied per test rather than at module level: this file mixes
#: synchronous assertions about the view classes with async ones that drive real
#: HTTP, and pytest-asyncio warns on every synchronous test carrying the mark.
pytestmark = pytest.mark.db


def _item(session, taxonomy, code="e6_cheese", name="Cheese", **kwargs):
    item = FoodItem(
        code=code, name=name,
        food_category_id=taxonomy.category.id,
        **kwargs,
    )
    session.add(item)
    session.flush()
    return item


def _item_factor(session, factor_set, taxonomy, item, *, destination=None):
    row = FactorUpstream(
        factor_set_id=factor_set.id,
        sector_id=taxonomy.sector.id,
        food_category_id=taxonomy.category.id,
        food_item_id=item.id,
        destination_id=destination,
        metric_id=taxonomy.metric.id,
        value_per_kg=Decimal("3.4000000000"),
    )
    session.add(row)
    session.flush()
    return row


# --- the upstream form, which is what was actually unusable ---------------


def test_the_upstream_screen_offers_the_food_item_on_all_three_lists():
    """One list is not enough, and neither is two.

    `form_columns` decides whether a factor can be *written* at all;
    `column_list` decides whether the list page can tell an item row from the
    category row it refines; `column_details_list` decides the same for the
    details page. A column present on the form and missing from the list makes
    two rows that price differently render identically — the same defect
    `FactorUpstream.__str__` already carries the item code for.
    """
    for attribute in ("column_list", "column_details_list", "form_columns"):
        names = [
            getattr(column, "key", column)
            for column in getattr(FactorUpstreamAdmin, attribute)
        ]
        assert "food_item" in names, (
            f"FactorUpstreamAdmin.{attribute} does not carry food_item, so "
            "the column exists in the database and not on this screen"
        )


def test_the_food_item_field_is_optional_like_the_destination_field():
    """Blank means "every food in this category" — the category average, and
    every row in every factor set today. A required select would make the
    normal row unwritable, which is the mistake `destination` is documented
    against on this same view."""
    assert FactorUpstream.food_item_id.nullable is True
    description = FactorUpstreamAdmin.form_args["food_item"]["description"]
    assert "Leave blank" in description


# --- the new taxonomy screen ---------------------------------------------


def test_the_food_item_screen_is_a_taxonomy_screen():
    """Audited by inheritance, and no delete: a food a stored submission names
    has to stay resolvable, so `active` is how it leaves service."""
    from admin.modelviews import AuditedModelView
    from admin.taxonomy_views import _TaxonomyAdmin

    assert issubclass(FoodItemAdmin, _TaxonomyAdmin)
    assert issubclass(FoodItemAdmin, AuditedModelView)
    assert FoodItemAdmin.can_delete is False


def test_the_food_item_form_requires_a_category():
    """`food_category_id` is NOT NULL and the parent is not decoration: it is
    the row a food with no factors of its own is priced at."""
    names = [
        getattr(column, "key", column) for column in FoodItemAdmin.form_columns
    ]
    assert names[0] == "food_category", (
        "the category a food belongs to is the first thing to choose, because "
        "everything else about the row depends on it"
    )
    assert FoodItem.food_category_id.nullable is False


@pytest.mark.asyncio
async def test_both_roles_can_open_the_food_item_screen(staff_client, admin_client):
    """§8.3's taxonomy row, driven at both roles over real HTTP.

    `tests/admin/test_role_matrix.py` is the file that refuses a screen with no
    role decided at all; this is the same decision asserted where the reader of
    this landing will look for it.
    """
    for client in (staff_client, admin_client):
        response = await client.get("/admin/food-item/list", follow_redirects=False)
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_a_staff_member_can_author_a_food_item_through_the_panel(
    admin_client, taxonomy_for_factors, _committed_session
):
    """End to end, because every intermediate assertion above is about a list.

    Committed rather than flushed: `admin_client` drives real HTTP and the app
    answers on its own session, which cannot see an uncommitted row — the
    category select would render empty and the POST would fail as "not a valid
    choice", for a reason that has nothing to do with this screen.
    """
    _committed_session.commit()
    response = await admin_client.post(
        "/admin/food-item/create",
        data={
            "food_category": str(taxonomy_for_factors.category.id),
            "code": "e6_cheese",
            "name": "Cheese",
            "sort_order": "10",
            "active": "on",
        },
        follow_redirects=False,
    )
    assert response.status_code in (302, 303), response.text

    written = _committed_session.scalar(
        select(FoodItem).where(FoodItem.code == "e6_cheese")
    )
    assert written is not None
    assert written.food_category_id == taxonomy_for_factors.category.id


# --- the flag, and the guard that keeps it honest -------------------------


def test_the_flag_is_on_the_factor_set_form():
    """It is a draft-only edit, so the form is where it belongs — unlike
    `is_mock`, whose dangerous direction is legal in every status and changes
    the public page the instant it is saved."""
    names = [
        getattr(column, "key", column) for column in FactorSetAdmin.form_columns
    ]
    assert "item_level_enabled" in names
    assert "is_mock" not in names, (
        "is_mock must stay off this form; it has two actions of its own"
    )
    assert "item_level_enabled" in FactorSetAdmin.form_args


@pytest.mark.asyncio
async def test_ticking_the_flag_on_a_set_with_no_item_factors_is_refused(
    admin_client, one_draft, _committed_session
):
    """§3.5's soft guard at the form: every food on the released screen would
    be priced at its category's average while the screen asked a more specific
    question. Driven over real HTTP, because the guard sits in
    `validate_before_commit` and a unit call on the predicate would pass for a
    guard wired into nothing."""
    _committed_session.commit()
    response = await admin_client.post(
        f"/admin/factor-set/edit/{one_draft.id}",
        data={
            "version_label": one_draft.version_label,
            "item_level_enabled": "on",
            "effective_from": "",
            "notes": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400, response.status_code
    _committed_session.expire_all()
    assert _committed_session.get(FactorSet, one_draft.id).item_level_enabled is False


@pytest.mark.asyncio
async def test_ticking_the_flag_is_allowed_once_one_food_is_priced(
    admin_client, one_draft, taxonomy_for_factors, _committed_session
):
    """The other half, and the half that stops the refusal above passing for a
    screen that refuses every edit."""
    item = _item(_committed_session, taxonomy_for_factors)
    _item_factor(_committed_session, one_draft, taxonomy_for_factors, item)
    _committed_session.commit()

    response = await admin_client.post(
        f"/admin/factor-set/edit/{one_draft.id}",
        data={
            "version_label": one_draft.version_label,
            "item_level_enabled": "on",
            "effective_from": "",
            "notes": "",
        },
        follow_redirects=False,
    )

    assert response.status_code in (302, 303), response.text
    _committed_session.expire_all()
    assert _committed_session.get(FactorSet, one_draft.id).item_level_enabled is True


@pytest.mark.asyncio
async def test_the_factor_set_list_shows_how_many_foods_are_priced(
    admin_client, one_draft, taxonomy_for_factors, _committed_session
):
    """"On" and "off" is a boolean; the decision behind it is not. A staff
    member releasing step 2.5 is weighing how much of the vocabulary carries
    numbers, and nothing else on this panel says."""
    item = _item(_committed_session, taxonomy_for_factors)
    _item(_committed_session, taxonomy_for_factors, code="e6_butter", name="Butter")
    _item_factor(_committed_session, one_draft, taxonomy_for_factors, item)
    _committed_session.commit()

    body = (await admin_client.get("/admin/factor-set/list")).text

    assert "item-level factors for 1 of 2 foods" in body, body[-3000:]


@pytest.mark.asyncio
async def test_the_coverage_panel_says_nothing_with_no_foods_at_all(
    admin_client, one_draft
):
    """The state of every deployment today. "0 of 0 foods" beside every set is
    a card that says nothing, in the space above the list where what is
    published belongs."""
    body = (await admin_client.get("/admin/factor-set/list")).text

    assert "item-level factors for" not in body


# --- the two publish guards, through the panel's own lifecycle module -----


def test_publishing_refuses_the_flag_without_item_rows(
    _committed_session, one_draft
):
    """The copy that cannot be walked past. The form guard is a courtesy; this
    is the single transactional choke point."""
    one_draft.item_level_enabled = True
    _committed_session.flush()

    with pytest.raises(LifecycleError) as excinfo:
        publish_factor_set(_committed_session, one_draft.id, actor="kim")

    assert "food item" in str(excinfo.value).lower()


def test_publishing_refuses_an_item_row_with_no_category_row_under_it(
    _committed_session, one_draft, taxonomy_for_factors
):
    """**The guard #100 could not enforce.** §2.2's chain has no silent zero
    only while the data carries a category row to fall back to.

    `one_draft`'s single upstream row is the category row for
    (sector, category, metric). Moving the item onto a *second* metric that has
    no category row of its own is the shape: that metric prices one food and
    nobody else, and every other food in the category — and the visitor who
    named no food at all — is charged zero upstream rather than an average.
    """
    from admin.taxonomy_models import Metric

    second = Metric(code="e6_water", name="Water", unit="L")
    _committed_session.add(second)
    _committed_session.flush()

    item = _item(_committed_session, taxonomy_for_factors)
    _committed_session.add(
        FactorUpstream(
            factor_set_id=one_draft.id,
            sector_id=taxonomy_for_factors.sector.id,
            food_category_id=taxonomy_for_factors.category.id,
            food_item_id=item.id,
            destination_id=None,
            metric_id=second.id,
            value_per_kg=Decimal("1200.0000000000"),
        )
    )
    _committed_session.flush()

    with pytest.raises(LifecycleError) as excinfo:
        publish_factor_set(_committed_session, one_draft.id, actor="kim")

    message = str(excinfo.value)
    assert "e6_processing/e6_dairy/e6_water" in message, message
    #: The refusal has to name the row to add, not only the row that is wrong.
    assert "leaving both the food item and the destination blank" in message


def test_publishing_an_item_row_beside_its_category_row_is_allowed(
    _committed_session, one_draft, taxonomy_for_factors
):
    """The healthy shape, and the assertion that stops the two refusals above
    passing against a publish that refuses everything."""
    item = _item(_committed_session, taxonomy_for_factors)
    _item_factor(_committed_session, one_draft, taxonomy_for_factors, item)
    one_draft.item_level_enabled = True
    _committed_session.flush()

    publish_factor_set(_committed_session, one_draft.id, actor="kim")

    assert one_draft.status is FactorSetStatus.published


def test_importing_the_published_set_carries_the_item_rows_with_the_flag(
    _committed_session, two_sets, taxonomy_for_factors
):
    """After an import the flag guard is checking something else, and something
    this landing is more worried about than a mis-ticked box: the target's rows
    were just deleted and re-copied by reflection, so a flag that survives with
    no item row under it means **the copy lost the item dimension**."""
    live, draft = two_sets
    item = _item(_committed_session, taxonomy_for_factors)
    _item_factor(_committed_session, live, taxonomy_for_factors, item)
    live.item_level_enabled = True
    _committed_session.flush()

    import_published_into(_committed_session, draft.id, actor="kim")
    _committed_session.flush()

    assert draft.item_level_enabled is True
    copied = _committed_session.scalars(
        select(FactorUpstream).where(
            FactorUpstream.factor_set_id == draft.id,
            FactorUpstream.food_item_id.is_not(None),
        )
    ).all()
    assert len(copied) == 1, (
        "the import carried the flag but not the item rows, which is the "
        "silent hybrid this guard exists to refuse"
    )
    assert copied[0].food_item_id == item.id


# --- the dry-run form, so the people releasing the step can test it -------
#
# Kept in this file rather than added to tests/admin/test_dryrun_view.py so
# that the landing's own assertions read together; that file's own six-control
# coverage test is what pins the help text on the rest of the form.


@pytest.mark.asyncio
async def test_the_dry_run_form_offers_no_food_item_while_none_exists(
    admin_client
):
    """The inert state. A select whose only option is "Whole category" is a
    control that cannot be used and a question a staff member has to work out
    the answer to — and every deployment is in that state today."""
    body = (await admin_client.get("/admin/try")).text

    assert 'name="food_item"' not in body


@pytest.mark.asyncio
async def test_the_dry_run_form_offers_the_food_once_one_exists(
    admin_client, taxonomy_for_factors, _committed_session
):
    """§8.2: staff releasing step 2.5 have to be able to exercise it here,
    against a draft, before it is live for the public."""
    _item(_committed_session, taxonomy_for_factors)
    _committed_session.commit()

    body = (await admin_client.get("/admin/try")).text

    assert 'name="food_item"' in body
    assert 'value="e6_cheese"' in body
    #: The parent travels with it: an item filed under another category is
    #: refused by the engine rather than priced, so the form has to be able to
    #: narrow the list to the category that was chosen.
    assert 'data-food-category="e6_dairy"' in body
    #: And the control explains itself, like the other six on this page.
    assert "Whole category" in body


@pytest.mark.asyncio
async def test_an_unchosen_food_item_is_omitted_from_the_request_body(
    admin_client, fake_calc_client, one_draft, taxonomy_for_factors, session
):
    """**Omitted, not sent as null.** `api/schemas.py` sets `extra="forbid"` on
    the entry model and it does not know `food_item` until contract v1.54 part
    two lands with the API, so a key sent unconditionally would turn every dry
    run on this panel into a 422. This assertion is the whole of why the body
    is built conditionally."""
    session.commit()

    await admin_client.post("/admin/try", data={
        "factor_set": one_draft.version_label,
        "sector": taxonomy_for_factors.sector.code,
        "food_category": taxonomy_for_factors.category.code,
        "food_item": "",
        "gwp_horizon": "100",
        "destination": taxonomy_for_factors.destination.code,
        "qty_kg": "1200.000",
    })

    entry = fake_calc_client.calls[0].body["entries"][0]
    assert "food_item" not in entry, entry


@pytest.mark.asyncio
async def test_a_chosen_food_item_reaches_the_calculate_client(
    admin_client, fake_calc_client, one_draft, taxonomy_for_factors,
    _committed_session
):
    """The other half. A form that renders the select and drops the answer on
    the way to the request is exactly as useless as no select, and looks
    identical on screen."""
    _item(_committed_session, taxonomy_for_factors)
    _committed_session.commit()

    await admin_client.post("/admin/try", data={
        "factor_set": one_draft.version_label,
        "sector": taxonomy_for_factors.sector.code,
        "food_category": taxonomy_for_factors.category.code,
        "food_item": "e6_cheese",
        "gwp_horizon": "100",
        "destination": taxonomy_for_factors.destination.code,
        "qty_kg": "1200.000",
    })

    entry = fake_calc_client.calls[0].body["entries"][0]
    assert entry["food_item"] == "e6_cheese"
    #: The category travels with it — the engine refuses a pair that disagrees.
    assert entry["food_category"] == taxonomy_for_factors.category.code


@pytest.mark.asyncio
async def test_the_dry_run_header_is_still_mandatory_with_an_item(
    admin_client, fake_calc_client, one_draft, taxonomy_for_factors,
    _committed_session
):
    """§8.2. Staff tuning a formula run dozens of these, and a persisted dry
    run pollutes the public statistics directly. The item dimension changes
    nothing about that, and this is the assertion that says so rather than
    assuming it.

    `dry_run` is the only method `FakeCalculateClient` exposes, and
    `HttpCalculateClient.dry_run` is what sets `X-Dry-Run: true` and fills the
    body's own `dry_run` field in — so a recorded call is a call that went
    through that path, and a body carrying a `dry_run` key of its own would
    mean this view had started building one itself."""
    _item(_committed_session, taxonomy_for_factors)
    _committed_session.commit()

    await admin_client.post("/admin/try", data={
        "factor_set": one_draft.version_label,
        "sector": taxonomy_for_factors.sector.code,
        "food_category": taxonomy_for_factors.category.code,
        "food_item": "e6_cheese",
        "gwp_horizon": "100",
        "destination": taxonomy_for_factors.destination.code,
        "qty_kg": "1200.000",
    })

    assert len(fake_calc_client.calls) == 1
    assert "dry_run" not in fake_calc_client.calls[0].body


@pytest.mark.asyncio
async def test_importing_a_set_that_claims_the_item_level_without_rows_is_refused(
    _committed_session, two_sets
):
    """The guard, as opposed to the happy path above — and the mutation that
    showed the happy path alone does not pin it.

    From the target's side the two ways of reaching this state are
    indistinguishable, which is the point: `item_level_enabled` true with no
    `factor_upstream` row naming a food means either that the copy dropped the
    item dimension on its way across, or that the source was already claiming a
    precision it does not have. Both are a set that says it prices foods
    individually and prices none, and the import is the moment to say so —
    after it, the draft is what a staff member will publish.

    The source is put in that state directly here. `publish_factor_set` would
    never let a set reach it, but `rollback_to` deliberately skips these
    completeness rules (an emergency rollback must not be refused over one), so
    it is not a state the guard may assume away.
    """
    live, draft = two_sets
    live.item_level_enabled = True
    _committed_session.flush()

    with pytest.raises(LifecycleError) as excinfo:
        import_published_into(_committed_session, draft.id, actor="kim")

    assert "food item" in str(excinfo.value).lower()
