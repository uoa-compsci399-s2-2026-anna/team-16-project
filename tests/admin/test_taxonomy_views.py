"""Contract §8.1: the taxonomy views, audited by inheritance.

Deviates from task-3-brief.md's Step 1 listing in two ways, both forced by
how this suite's fixtures actually behave rather than by choice - see
task-3-report.md for the full account:

1. The end-to-end tests are ``async def`` and ``await`` every
   ``admin_client`` call. ``admin_client`` is httpx's ``AsyncClient`` (the
   ``client`` fixture in tests/conftest.py) - the same object
   tests/admin/test_accounts_view.py drives - and calling one of its methods
   without ``await`` returns an un-awaited coroutine, not a response.
2. ``session`` here wraps tests/admin/conftest.py's hard-committing
   ``_committed_session``, not the rolled-back ``session``
   tests/conftest.py defines at the top level. That one binds its
   sessionmaker to a Connection already holding an open transaction, so -
   per SQLAlchemy's ``join_transaction_mode`` default - ``session.commit()``
   never issues a real COMMIT visible outside that one Connection.
   ``admin_client`` drives real HTTP requests that reach the taxonomy views
   through the running app's *own*, separate sessionmaker (a different
   connection entirely), so a test that seeds a row via the rolled-back
   fixture and then expects ``admin_client`` to see it would fail for a
   reason that has nothing to do with the invariant under test.
   ``_committed_session`` opens a real session against ``admin_app``'s own
   sessionmaker instead and commits for real (see its own docstring for why
   it is not itself named ``session``); this file's own autouse cleanup
   fixture below removes the exact rows its tests touch afterwards.
"""

import pytest
from sqlalchemy import bindparam as sa_bindparam
from sqlalchemy import select, text

from admin.taxonomy_models import Destination, DestinationGroup, FoodCategory, Sector
from admin.taxonomy_views import (
    DestinationAdmin, DestinationGroupAdmin, FoodCategoryAdmin, MetricAdmin,
    SectorAdmin, UnitPresetAdmin,
)
from tests.admin.conftest import _resync

pytestmark = pytest.mark.db


@pytest.fixture
def session(_committed_session):
    """This file's own name for tests/admin/conftest.py's hard-committing
    session - see this module's own docstring."""
    return _committed_session


ALL_VIEWS = [
    DestinationGroupAdmin, DestinationAdmin, SectorAdmin,
    FoodCategoryAdmin, MetricAdmin, UnitPresetAdmin,
]


# --- Metadata-only tests: no database access, no client -------------------


@pytest.mark.parametrize("view", ALL_VIEWS)
def test_no_taxonomy_row_can_be_deleted(view):
    """A destination named by a historical submission must stay resolvable.

    Deleting it would leave stored results pointing at nothing - and every
    one of these tables carries `active` precisely so deactivation is the
    way a row leaves service.
    """
    assert view.can_delete is False


@pytest.mark.parametrize("view", ALL_VIEWS)
def test_every_taxonomy_view_is_audited(view):
    """Contract §8.1. Auditing comes from the base class, and a subclass that
    overrode __init__ without calling super() would silently lose it."""
    from admin.modelviews import AuditedModelView

    assert issubclass(view, AuditedModelView)


@pytest.mark.parametrize("view", ALL_VIEWS)
def test_the_details_page_shows_no_more_than_the_list(view):
    """column_details_list defaults to every mapped column, which leaked a
    password hash one stage earlier in this project. None of these tables
    holds a secret, but the habit is what keeps that true for E-5's factor
    views, which are added by the same hand.

    sqladmin's own unset default is `[]` (sqladmin/models.py), not `None` -
    ModelView.get_details_columns() falls back to every mapped column
    (`self._prop_names`) precisely when the list is falsy. `is not None` is
    therefore a tautology: it passes for a view that never set the attribute
    at all, which is the exact state that rendered a bcrypt hash and a
    Fernet-encrypted TOTP secret on a details page one click from a list
    that correctly redacted them. A real guard has to assert the list is
    non-empty *and* that everything in it is actually a column or
    relationship this model declares - not just "some list-like object
    exists here", which `[]` already satisfies.
    """
    details = view.column_details_list
    assert details, (
        "column_details_list must not fall back to sqladmin's empty "
        "default - an empty list renders every mapped column"
    )

    mapper = view.model.__mapper__
    declared = {attr.key for attr in mapper.column_attrs} | {
        rel.key for rel in mapper.relationships
    }
    shown = {col.key if hasattr(col, "key") else col for col in details}
    assert shown <= declared, (
        f"{view.__name__}.column_details_list names {shown - declared}, "
        f"which {view.model.__name__} does not declare as a column or "
        "relationship"
    )


def _admin_object(admin_app):
    """The live sqladmin `Admin` instance behind a running `admin_app`.

    `create_app` (admin/app.py) does not keep or expose one - confirmed by
    tests/admin/test_dryrun_view.py's own `fake_calc_client` fixture, which
    reaches the *inner* mounted Starlette sub-app (`Admin.__init__`'s own
    `self.admin`) the same way, off the outer app's routes, for the same
    reason. That sub-app's own `"index"` route is `Route("/", endpoint=
    self.index, name="index")` in sqladmin's own application.py -
    `self.index` is a bound method of the `Admin` instance itself, so
    `route.endpoint.__self__` recovers it. Verified directly: calling this
    against a freshly built `admin_app` and then `._find_model_view(...)`
    (sqladmin's own lookup, application.py) returns the exact
    `DestinationGroupAdmin` instance `Admin.add_view` constructed and
    registered.
    """
    inner_app = next(
        route.app for route in admin_app.routes
        if getattr(route, "name", None) == "admin"
    )
    index_route = next(r for r in inner_app.router.routes if r.name == "index")
    return index_route.endpoint.__self__


@pytest.mark.parametrize("view", ALL_VIEWS)
def test_the_actions_button_is_live(view, admin_app):
    """The defect this task fixes: sqladmin's own templates/sqladmin/list.html
    disables the Actions dropdown unless `model_view.can_delete` or
    `model_view._custom_actions_in_list` is truthy -

        <button {% if not model_view.can_delete and not
        model_view._custom_actions_in_list %} disabled {% endif %}

    Every one of these six views sets `can_delete = False` deliberately
    (test_no_taxonomy_row_can_be_deleted above) and, before this task,
    defined no custom action either - so the button was correctly disabled
    and permanently useless. `_custom_actions_in_list` is populated by
    `Admin._handle_action_decorated_func` at *registration* time
    (sqladmin/application.py), not by the `@action` decorator itself, which
    only stamps attributes onto the function - so this has to be checked on
    the actual instance an `Admin` registered, via `_admin_object` above,
    not on a bare `view()` instantiated outside of one.
    """
    admin_obj = _admin_object(admin_app)
    registered = admin_obj._find_model_view(view.identity)

    assert registered._custom_actions_in_list, (
        f"{view.__name__}'s Actions button is disabled: can_delete is False "
        "and no custom action is registered for its list page"
    )
    assert {"deactivate", "activate"} <= set(registered._custom_actions_in_list)


# --- Fixtures ---------------------------------------------------------
#
# admin_client, staff_client, session and _resync now come from
# tests/admin/conftest.py. This file keeps only what names its own fixed
# codes: the cleanup helper below and the autouse fixture that runs it,
# since tests/admin/conftest.py's own `session` fixture cannot know what a
# given file's tests committed through it.


_TAXONOMY_CODES = {
    "food_category": ["standard_mix", "fruit"],
    "destination": ["prevention", "e4_landfill"],
    "destination_group": ["reuse"],
    "sector": ["e4_north", "e4_south"],
}


def _cleanup_taxonomy_rows(admin_app):
    """Remove every row this file's tests may have hard-committed via the
    local `session` fixture below, by the fixed set of codes those tests are
    written against - deleting `destination` before `destination_group`
    respects the foreign key between them. See this module's own docstring
    for why these rows are committed for real in the first place, and why
    nothing else undoes it.
    """
    factory = admin_app.state.session_factory
    with factory() as db:
        food_category_ids = db.execute(
            text("SELECT id FROM food_category WHERE code IN :codes")
            .bindparams(sa_bindparam("codes", expanding=True)),
            {"codes": _TAXONOMY_CODES["food_category"]},
        ).scalars().all()
        destination_ids = db.execute(
            text("SELECT id FROM destination WHERE code IN :codes")
            .bindparams(sa_bindparam("codes", expanding=True)),
            {"codes": _TAXONOMY_CODES["destination"]},
        ).scalars().all()
        group_ids = db.execute(
            text("SELECT id FROM destination_group WHERE code IN :codes")
            .bindparams(sa_bindparam("codes", expanding=True)),
            {"codes": _TAXONOMY_CODES["destination_group"]},
        ).scalars().all()
        sector_ids = db.execute(
            text("SELECT id FROM sector WHERE code IN :codes")
            .bindparams(sa_bindparam("codes", expanding=True)),
            {"codes": _TAXONOMY_CODES["sector"]},
        ).scalars().all()

        if food_category_ids:
            db.execute(
                text("DELETE FROM audit_log WHERE table_name = 'food_category' "
                     "AND row_id IN :ids").bindparams(sa_bindparam("ids", expanding=True)),
                {"ids": food_category_ids},
            )
            db.execute(
                text("DELETE FROM food_category WHERE id IN :ids")
                .bindparams(sa_bindparam("ids", expanding=True)),
                {"ids": food_category_ids},
            )
        if destination_ids:
            db.execute(
                text("DELETE FROM audit_log WHERE table_name = 'destination' "
                     "AND row_id IN :ids").bindparams(sa_bindparam("ids", expanding=True)),
                {"ids": destination_ids},
            )
            db.execute(
                text("DELETE FROM destination WHERE id IN :ids")
                .bindparams(sa_bindparam("ids", expanding=True)),
                {"ids": destination_ids},
            )
        if group_ids:
            db.execute(
                text("DELETE FROM audit_log WHERE table_name = 'destination_group' "
                     "AND row_id IN :ids").bindparams(sa_bindparam("ids", expanding=True)),
                {"ids": group_ids},
            )
            db.execute(
                text("DELETE FROM destination_group WHERE id IN :ids")
                .bindparams(sa_bindparam("ids", expanding=True)),
                {"ids": group_ids},
            )
        if sector_ids:
            db.execute(
                text("DELETE FROM audit_log WHERE table_name = 'sector' "
                     "AND row_id IN :ids").bindparams(sa_bindparam("ids", expanding=True)),
                {"ids": sector_ids},
            )
            db.execute(
                text("DELETE FROM sector WHERE id IN :ids")
                .bindparams(sa_bindparam("ids", expanding=True)),
                {"ids": sector_ids},
            )
        db.commit()


@pytest.fixture(autouse=True)
def _cleanup_this_files_rows(admin_app):
    """Runs after every test in this file, regardless of whether it used
    `session` - matching the unconditional (`finally`) cleanup the old
    file-local `session` fixture used to perform itself. This is the *whole
    point* of the cleanup rather than an afterthought - a test that fails
    partway through, after `session.commit()` has already run, would
    otherwise leave a row behind that collides with the fixed codes the next
    test in this file inserts.
    """
    yield
    _cleanup_taxonomy_rows(admin_app)


# --- End-to-end tests: the invariant holds through the real edit path -----


@pytest.mark.asyncio
async def test_marking_a_second_standard_mix_is_refused(session, admin_client):
    """The end-to-end proof: the invariant holds through the real edit path,
    not merely when the rule function is called directly."""
    session.add(FoodCategory(code="standard_mix", name="Mixed", is_standard_mix=True))
    session.add(FoodCategory(code="fruit", name="Fruit", is_standard_mix=False))
    session.commit()
    fruit = session.scalar(select(FoodCategory).where(FoodCategory.code == "fruit"))

    response = await admin_client.post(
        f"/admin/food-category/edit/{fruit.id}",
        data={"code": "fruit", "name": "Fruit", "is_standard_mix": "on",
              "sort_order": "0", "active": "on"},
    )

    _resync(session)
    assert session.scalar(
        select(FoodCategory).where(FoodCategory.code == "fruit")
    ).is_standard_mix is False, "the edit was committed despite breaking the invariant"
    assert "standard mix" in response.text.lower()


@pytest.mark.asyncio
async def test_a_refused_edit_writes_no_audit_entry(session, admin_client):
    """A rolled-back change must leave no record claiming it happened
    (contract §5.5). The hook runs before the audit rows are added, so this
    is a property of the ordering, not a coincidence."""
    from admin.models import AuditLog

    session.add(FoodCategory(code="standard_mix", name="Mixed", is_standard_mix=True))
    session.add(FoodCategory(code="fruit", name="Fruit", is_standard_mix=False))
    session.commit()
    fruit = session.scalar(select(FoodCategory).where(FoodCategory.code == "fruit"))
    before = session.scalar(select(AuditLog).order_by(AuditLog.id.desc()))

    await admin_client.post(
        f"/admin/food-category/edit/{fruit.id}",
        data={"code": "fruit", "name": "Fruit", "is_standard_mix": "on",
              "sort_order": "0", "active": "on"},
    )

    _resync(session)
    after = session.scalar(select(AuditLog).order_by(AuditLog.id.desc()))
    assert (after.id if after else None) == (before.id if before else None)


@pytest.mark.asyncio
async def test_deactivating_prevention_is_refused(session, admin_client):
    group = DestinationGroup(code="reuse", name="Reuse", is_waste=False)
    session.add(group)
    session.commit()
    session.add(Destination(group_id=group.id, code="prevention", name="Prevented"))
    session.commit()
    prevention = session.scalar(
        select(Destination).where(Destination.code == "prevention")
    )

    await admin_client.post(
        f"/admin/destination/edit/{prevention.id}",
        data={"group": str(group.id), "code": "prevention", "name": "Prevented",
              "sort_order": "0"},  # `active` omitted = unchecked
    )

    _resync(session)
    assert session.scalar(
        select(Destination).where(Destination.code == "prevention")
    ).active is True


@pytest.mark.asyncio
async def test_deactivating_preventions_group_is_refused(session, admin_client):
    """The end-to-end proof for DestinationGroupAdmin: a staff member tidying
    up the destination-group list, not the destination list, is the likelier
    route to breaking this invariant - see check_prevention_destination."""
    group = DestinationGroup(code="reuse", name="Reuse", is_waste=False)
    session.add(group)
    session.commit()
    session.add(Destination(group_id=group.id, code="prevention", name="Prevented"))
    session.commit()

    response = await admin_client.post(
        f"/admin/destination-group/edit/{group.id}",
        data={"code": "reuse", "name": "Reuse", "sort_order": "0"},
        # `is_waste` and `active` both omitted = unchecked
    )

    _resync(session)
    assert session.scalar(
        select(DestinationGroup).where(DestinationGroup.code == "reuse")
    ).active is True, "the edit was committed despite dropping prevention out of service"
    assert "prevention" in response.text.lower()


@pytest.mark.asyncio
async def test_a_valid_edit_still_goes_through(session, admin_client):
    """The invariants must refuse the specific broken states and nothing else.
    A hook that refused everything would pass every test above."""
    session.add(FoodCategory(code="standard_mix", name="Mixed", is_standard_mix=True))
    session.add(FoodCategory(code="fruit", name="Fruit", is_standard_mix=False))
    session.commit()
    fruit = session.scalar(select(FoodCategory).where(FoodCategory.code == "fruit"))

    await admin_client.post(
        f"/admin/food-category/edit/{fruit.id}",
        data={"code": "fruit", "name": "Fruit and berries", "sort_order": "3",
              "active": "on"},
    )

    _resync(session)
    assert session.scalar(
        select(FoodCategory).where(FoodCategory.code == "fruit")
    ).name == "Fruit and berries"


# --- Bulk deactivate / activate ---------------------------------------
#
# The fix for the defect this task exists to close: these six views set
# can_delete = False deliberately and, until now, defined no custom action
# either, so sqladmin's own Actions button (see test_the_actions_button_is_live
# above) was correctly, permanently disabled. See admin/taxonomy_views.py's
# _TaxonomyAdmin for what closes it and the trap doing so naively falls into.


@pytest.mark.asyncio
async def test_bulk_deactivate_deactivates_several_rows_and_audits_each(
    session, admin_client
):
    """SectorAdmin carries no cross-row invariant (unlike Destination,
    DestinationGroup and FoodCategory below), so this is the plain case:
    every selected row changes, and every change is audited - the two facts
    a naive `for pk in pks: row.active = False; session.commit()` would not
    get for free, since an `@action` inherits neither from AuditedModelView
    (admin/modelviews.py's own docstring)."""
    from admin.models import AuditLog

    session.add(Sector(code="e4_north", name="North Island"))
    session.add(Sector(code="e4_south", name="South Island"))
    session.commit()
    north = session.scalar(select(Sector).where(Sector.code == "e4_north"))
    south = session.scalar(select(Sector).where(Sector.code == "e4_south"))

    response = await admin_client.get(
        f"/admin/sector/action/deactivate?pks={north.id},{south.id}"
    )

    _resync(session)
    assert session.get(Sector, north.id).active is False
    assert session.get(Sector, south.id).active is False
    assert response.status_code in (200, 302)

    audit_rows = session.scalars(
        select(AuditLog).where(
            AuditLog.table_name == "sector",
            AuditLog.row_id.in_([north.id, south.id]),
        )
    ).all()
    assert {row.row_id for row in audit_rows} == {north.id, south.id}
    assert {row.action for row in audit_rows} == {"update"}


@pytest.mark.asyncio
async def test_bulk_activate_reactivates_several_rows(session, admin_client):
    session.add(Sector(code="e4_north", name="North Island", active=False))
    session.add(Sector(code="e4_south", name="South Island", active=False))
    session.commit()
    north = session.scalar(select(Sector).where(Sector.code == "e4_north"))
    south = session.scalar(select(Sector).where(Sector.code == "e4_south"))

    response = await admin_client.get(
        f"/admin/sector/action/activate?pks={north.id},{south.id}"
    )

    _resync(session)
    assert session.get(Sector, north.id).active is True
    assert session.get(Sector, south.id).active is True
    assert response.status_code in (200, 302)


@pytest.mark.asyncio
async def test_bulk_deactivating_prevention_is_refused_and_nothing_in_the_batch_applies(
    session, admin_client
):
    """`prevention` selected together with an innocuous destination: the
    whole batch is refused, not just prevention's own row - a naive
    implementation that applied changes one row at a time and refused only
    when it reached `prevention` would leave the innocuous row deactivated
    with nothing on the page saying so."""
    group = DestinationGroup(code="reuse", name="Reuse", is_waste=False)
    session.add(group)
    session.commit()
    session.add(Destination(group_id=group.id, code="prevention", name="Prevented"))
    session.add(Destination(group_id=group.id, code="e4_landfill", name="Landfill"))
    session.commit()
    prevention = session.scalar(
        select(Destination).where(Destination.code == "prevention")
    )
    landfill = session.scalar(
        select(Destination).where(Destination.code == "e4_landfill")
    )

    response = await admin_client.get(
        f"/admin/destination/action/deactivate?pks={prevention.id},{landfill.id}"
    )

    _resync(session)
    assert session.scalar(
        select(Destination).where(Destination.code == "prevention")
    ).active is True
    assert session.scalar(
        select(Destination).where(Destination.code == "e4_landfill")
    ).active is True
    assert response.status_code == 400
    assert "prevention" in response.text.lower()


@pytest.mark.asyncio
async def test_bulk_deactivating_preventions_group_is_refused(session, admin_client):
    group = DestinationGroup(code="reuse", name="Reuse", is_waste=False)
    session.add(group)
    session.commit()
    session.add(Destination(group_id=group.id, code="prevention", name="Prevented"))
    session.commit()

    response = await admin_client.get(
        f"/admin/destination-group/action/deactivate?pks={group.id}"
    )

    _resync(session)
    assert session.scalar(
        select(DestinationGroup).where(DestinationGroup.code == "reuse")
    ).active is True
    assert response.status_code == 400
    assert "prevention" in response.text.lower()


@pytest.mark.asyncio
async def test_bulk_deactivating_the_last_active_standard_mix_is_refused(
    session, admin_client
):
    """Selected together with an ordinary category - same "whole batch
    refused" proof as the prevention test above, for FoodCategoryAdmin's own
    invariant."""
    session.add(FoodCategory(code="standard_mix", name="Mixed", is_standard_mix=True))
    session.add(FoodCategory(code="fruit", name="Fruit", is_standard_mix=False))
    session.commit()
    standard = session.scalar(
        select(FoodCategory).where(FoodCategory.code == "standard_mix")
    )
    fruit = session.scalar(select(FoodCategory).where(FoodCategory.code == "fruit"))

    response = await admin_client.get(
        f"/admin/food-category/action/deactivate?pks={standard.id},{fruit.id}"
    )

    _resync(session)
    assert session.scalar(
        select(FoodCategory).where(FoodCategory.code == "standard_mix")
    ).active is True
    assert session.scalar(
        select(FoodCategory).where(FoodCategory.code == "fruit")
    ).active is True
    assert response.status_code == 400
    assert "standard mix" in response.text.lower()
