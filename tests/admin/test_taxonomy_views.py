"""Contract §8.1: the taxonomy views, audited by inheritance.

Deviates from task-3-brief.md's Step 1 listing in two ways, both forced by
how this suite's fixtures actually behave rather than by choice - see
task-3-report.md for the full account:

1. The end-to-end tests are ``async def`` and ``await`` every
   ``admin_client`` call. ``admin_client`` is httpx's ``AsyncClient`` (the
   ``client`` fixture in tests/conftest.py) - the same object
   tests/admin/test_accounts_view.py drives - and calling one of its methods
   without ``await`` returns an un-awaited coroutine, not a response.
2. ``session`` here is a *local* fixture, not the rolled-back one
   tests/conftest.py defines. That one binds its sessionmaker to a Connection
   already holding an open transaction, so - per SQLAlchemy's
   ``join_transaction_mode`` default - ``session.commit()`` never issues a
   real COMMIT visible outside that one Connection. ``admin_client`` drives
   real HTTP requests that reach the taxonomy views through the running
   app's *own*, separate sessionmaker (a different connection entirely), so
   a test that seeds a row via the rolled-back fixture and then expects
   ``admin_client`` to see it would fail for a reason that has nothing to do
   with the invariant under test. The local ``session`` fixture below opens
   a real session against ``admin_app``'s own sessionmaker instead, commits
   for real, and cleans up the exact rows it touches afterwards - the same
   shape as this file's own ``admin_client``/``_cleanup_staff`` and
   tests/admin/test_accounts_view.py's, and for the same reason (see that
   file's ``_cleanup_staff`` docstring): nothing else undoes a commit made
   through ``admin_app.state.session_factory``.
"""

import re
import time
import uuid

import pyotp
import pytest
import pytest_asyncio
from sqlalchemy import bindparam as sa_bindparam
from sqlalchemy import select, text

from admin.accounts import (
    begin_mfa_enrolment, complete_mfa_enrolment, create_staff, get_staff, set_password,
)
from admin.models import StaffRole
from admin.taxonomy_models import Destination, DestinationGroup, FoodCategory
from admin.taxonomy_views import (
    DestinationAdmin, DestinationGroupAdmin, FoodCategoryAdmin, MetricAdmin,
    SectorAdmin, UnitPresetAdmin,
)
from admin.totp import TOTP_INTERVAL
from admin.views import time as views_time

pytestmark = pytest.mark.db

SECRET_KEY = "test-secret-key-not-used-anywhere-real"

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


# --- Fixtures ---------------------------------------------------------
#
# No tests/admin/conftest.py exists (per Task 6's own note), so - matching
# every other file under tests/admin/ - these are file-local. admin_client
# and its helpers are the same shape as tests/admin/test_accounts_view.py's;
# duplicated rather than imported to keep this file self-contained the way
# every sibling file in this directory already is.


def _create_onboarded_account(admin_app, *, role=StaffRole.admin):
    """Create and commit a fully onboarded account of the given role."""
    username = f"u{uuid.uuid4().hex[:10]}"
    password = "a-long-enough-password"
    factory = admin_app.state.session_factory
    with factory() as db:
        create_staff(db, username=username, display_name="Test User", role=role)
        db.flush()
        set_password(db, username, password)
        secret, _ = begin_mfa_enrolment(db, username, secret_key=SECRET_KEY)
        # Backdated so the login step below, which mints a fresh TOTP code
        # for "now", cannot collide with the counter enrolment just spent.
        enrol_now = int(time.time()) - 4 * TOTP_INTERVAL
        complete_mfa_enrolment(
            db, username,
            pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(enrol_now),
            secret_key=SECRET_KEY, now=enrol_now,
        )
        db.commit()
        staff = get_staff(db, username)
    return staff, password, secret


async def _login(client, monkeypatch, *, username, password, secret):
    """Drive the real HTTP login flow for an already-created, onboarded account."""
    now = int(time.time())
    monkeypatch.setattr(views_time, "time", lambda: now)

    login = await client.post(
        "/admin/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert login.status_code == 302, "password step should have succeeded"

    verify_page = await client.get("/admin/verify")
    match = re.search(r'name="csrf_token" value="([^"]+)"', verify_page.text)
    assert match, "no CSRF token rendered on /admin/verify"
    code = pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(now)
    verify = await client.post(
        "/admin/verify",
        data={"code": code, "csrf_token": match.group(1)},
        follow_redirects=False,
    )
    assert verify.status_code == 302, "TOTP step should have completed the login"


def _cleanup_staff(admin_app, *staff_rows):
    """Remove the staff row (and any audit_log entry against it) this
    fixture created - see tests/admin/test_accounts_view.py's function of
    the same name for the full rationale (that module's cleanup is the
    established pattern; this is the same thing, scoped to this file).
    """
    factory = admin_app.state.session_factory
    with factory() as db:
        for staff in staff_rows:
            db.execute(
                text("DELETE FROM audit_log WHERE table_name = 'staff' AND row_id = :id"),
                {"id": staff.id},
            )
            db.execute(text("DELETE FROM staff WHERE id = :id"), {"id": staff.id})
        db.commit()


@pytest_asyncio.fixture
async def admin_client(admin_app, client, monkeypatch):
    """A client logged in as a fully onboarded administrator.

    See tests/admin/test_accounts_view.py's fixture of the same name: cleanup
    is registered before login is attempted, not after, so a failure partway
    through _login does not leave a committed staff row behind.
    """
    staff, password, secret = _create_onboarded_account(admin_app, role=StaffRole.admin)
    try:
        await _login(client, monkeypatch, username=staff.username, password=password, secret=secret)
    except BaseException:
        _cleanup_staff(admin_app, staff)
        raise
    yield client
    _cleanup_staff(admin_app, staff)


_TAXONOMY_CODES = {
    "food_category": ["standard_mix", "fruit"],
    "destination": ["prevention"],
    "destination_group": ["reuse"],
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
        db.commit()


def _resync(session):
    """End this session's own transaction and clear its identity map, so the
    next query reflects what `admin_client`'s HTTP request - a different
    connection - just committed.

    `session.expire_all()` alone is not enough: MySQL's default REPEATABLE
    READ isolation gives a transaction one consistent snapshot from its first
    statement onward, so a query re-issued on the *same still-open*
    transaction keeps seeing the pre-request snapshot no matter how many
    Python-side attributes were expired. Verified directly against this
    MySQL - test_a_valid_edit_still_goes_through failed on the assertion
    (stale "Fruit" instead of the freshly written "Fruit and berries") with
    only `expire_all()`, while a raw connection queried in parallel already
    showed the update. `session.commit()` here has nothing pending to write;
    its effect is to close out the stale transaction and open a fresh one for
    the query that follows.
    """
    session.commit()
    session.expire_all()


@pytest.fixture
def session(admin_app):
    """A real, hard-committing session against the running admin app's own
    database. See this module's docstring for why the rolled-back `session`
    fixture in tests/conftest.py cannot stand in here: `admin_client` reaches
    the taxonomy views through admin_app's own sessionmaker, a different
    connection, and MySQL will not let a write that was never really
    committed be visible there.

    Cleanup is unconditional (`finally`), not merely on success, and it is
    the *whole point* of this fixture rather than an afterthought - a test
    that fails partway through, after `session.commit()` has already run,
    would otherwise leave a row behind that collides with the fixed codes
    the next test in this file inserts.
    """
    factory = admin_app.state.session_factory
    db = factory()
    try:
        yield db
    finally:
        db.close()
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
    route to breaking this invariant - see check_prevention_intact."""
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
