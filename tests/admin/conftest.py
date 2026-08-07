"""Shared HTTP-test fixtures for tests/admin/.

Extracted from tests/admin/test_factor_views.py, test_factor_set_view.py and
test_taxonomy_views.py, which each carried a verbatim copy of roughly 120
lines: `_create_onboarded_account`, `_login`, `_cleanup_staff`, the
`admin_client` fixture, a hard-committing `session`, and `_resync`.

The copy kept here is test_factor_views.py's: its `_cleanup_staff` deletes
audit rows by `actor`, which the other two copies did not. That difference is
load-bearing, not cosmetic - see `_cleanup_staff`'s own docstring below.

Each file's own `_cleanup_*_rows` helper stays local (it names the specific
labels and codes that file's own tests are written against) and each file
still needs its own autouse fixture that calls it, since this module has no
way to know what a given test file committed through the hard-committing
session below.

The hard-committing session itself is deliberately *not* exported under the
name `session` - see `_committed_session`'s own docstring for why naming it
that here broke nine passing tests in three unrelated files the first time
this was tried. Each file that needs it (test_factor_views.py,
test_factor_set_view.py, test_taxonomy_views.py, and any file added later
that drives `admin_client`) requests `_committed_session` and re-exposes it
locally as `session`.

`staff_client` (a plain, non-admin staff session) is included even though no
file under tests/admin/ used it before this module existed: Task 4's
test_factor_set_actions.py needs it, per docs/superpowers/plans/
2026-08-07-admin-e6-lifecycle.md's own Interfaces list for this file, and
tests/admin/test_accounts_view.py already establishes its shape.
"""

import re
import time
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pyotp
import pytest
import pytest_asyncio
from sqlalchemy import text

from admin.accounts import (
    begin_mfa_enrolment, complete_mfa_enrolment, create_staff, get_staff, set_password,
)
from admin.models import StaffRole
from admin.totp import TOTP_INTERVAL
from admin.views import time as views_time

SECRET_KEY = "test-secret-key-not-used-anywhere-real"


# --- Account / login plumbing ----------------------------------------------


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
    """Remove the staff row and *every* audit_log entry this account caused,
    keyed on the acting username.

    Keying on the actor rather than on the changed row is deliberate. The
    obvious cleanup - delete audit rows whose row_id is still present in the
    table they name - cannot reach a *delete* entry, because the row it
    describes is precisely the one that no longer exists. A stray delete
    entry left this way once poisoned an unrelated test that reads the first
    row of `audit_log` (test_modelviews.py, whose `select(AuditLog)` takes
    the first row in the table and asserts its action is "create" - it read
    "delete" instead, and only when the whole directory ran; the file passes
    alone).

    Every audit row a test in this directory produces carries the acting
    fixture's own throwaway username as its actor, so one predicate covers
    all of them regardless of which table or row was touched.
    """
    factory = admin_app.state.session_factory
    with factory() as db:
        for staff in staff_rows:
            db.execute(
                text("DELETE FROM audit_log WHERE actor = :username"),
                {"username": staff.username},
            )
            db.execute(
                text("DELETE FROM audit_log WHERE table_name = 'staff' AND row_id = :id"),
                {"id": staff.id},
            )
            db.execute(text("DELETE FROM staff WHERE id = :id"), {"id": staff.id})
        db.commit()


@pytest_asyncio.fixture
async def admin_client(admin_app, client, monkeypatch):
    """A client logged in as a fully onboarded administrator.

    Cleanup is registered before login is attempted, not after, so a failure
    partway through _login does not leave a committed staff row behind.
    """
    staff, password, secret = _create_onboarded_account(admin_app, role=StaffRole.admin)
    try:
        await _login(client, monkeypatch, username=staff.username, password=password, secret=secret)
    except BaseException:
        _cleanup_staff(admin_app, staff)
        raise
    client.staff = staff
    yield client
    _cleanup_staff(admin_app, staff)


@pytest_asyncio.fixture
async def staff_client(admin_app, client, monkeypatch):
    """A client logged in as a fully onboarded, plain (non-admin) staff member.

    See admin_client's docstring for why cleanup is registered before login
    is attempted rather than after.
    """
    staff, password, secret = _create_onboarded_account(admin_app, role=StaffRole.staff)
    try:
        await _login(client, monkeypatch, username=staff.username, password=password, secret=secret)
    except BaseException:
        _cleanup_staff(admin_app, staff)
        raise
    client.staff = staff
    yield client
    _cleanup_staff(admin_app, staff)


def _resync(session):
    """End this session's own transaction and clear its identity map, so the
    next query reflects what `admin_client`'s HTTP request - a different
    connection - just committed.

    MySQL's default REPEATABLE READ isolation gives a transaction one
    consistent snapshot from its first statement onward, so
    `session.expire_all()` alone is not enough - a query re-issued on the
    same still-open transaction keeps seeing the pre-request snapshot no
    matter how many Python-side attributes were expired. `session.commit()`
    here has nothing pending to write; its effect is to close out the stale
    transaction and open a fresh one for the query that follows.
    """
    session.commit()
    session.expire_all()


@pytest.fixture
def _committed_session(admin_app):
    """A real, hard-committing session against the running admin app's own
    database - not the rolled-back `session` fixture tests/conftest.py
    defines at the top level.

    `admin_client` drives real HTTP requests that reach every admin view
    through the running app's own, separate sessionmaker - a different
    connection entirely - so a test that seeded a row via the rolled-back
    fixture and then expected `admin_client` to see it would fail for a
    reason that has nothing to do with the invariant under test.

    Deliberately **not** named `session`. A conftest.py fixture shadows a
    same-named fixture from every ancestor conftest.py for the *whole*
    directory it sits in, not just the files that asked for this one -
    tried directly, naming this fixture `session` here silently broke nine
    passing tests in three unrelated files (test_modelviews.py,
    test_audit.py, test_taxonomy_rules.py) that request a plain `session`
    and were written against tests/conftest.py's version: bound to a shared
    Connection with SQLAlchemy's default autoflush, not
    `admin_app.state.session_factory`'s own Engine-bound, autoflush=False
    configuration (the setting sqladmin's `Admin.__init__` deliberately
    applies for production - see admin/modelviews.py's
    `_audited_session_maker` docstring). One of those files
    (test_modelviews.py's `audited_view` fixture) additionally opens a
    *second* sessionmaker bound to `session.get_bind()` specifically to
    share this fixture's own connection and transaction - which only works
    when that bind is the Connection the rolled-back fixture uses; bound to
    an Engine instead, the second sessionmaker opens an unrelated
    connection that cannot see this one's uncommitted rows at all.

    Every file that needs this fixture's hard-committing behaviour requests
    it under its own private name and re-exposes it locally as `session` -
    see test_factor_views.py's `session` fixture for the shape. That keeps
    the override scoped to the handful of files actually written against
    it, while every other file in this directory keeps getting
    tests/conftest.py's rolled-back one undisturbed.

    Cleanup here is unconditional (`finally`) but deliberately generic: it
    only closes the session. Which rows a given file's tests hard-committed
    - and so which rows need deleting afterwards - is specific to that file's
    own fixed labels and codes, so each file registers its own additional,
    autouse cleanup fixture that calls its local `_cleanup_*_rows` helper.
    """
    factory = admin_app.state.session_factory
    db = factory()
    try:
        yield db
    finally:
        db.close()


# --- Task 2 Step 1's factor-set fixtures ------------------------------------
#
# Added here (rather than in tests/admin/test_factor_lifecycle.py, which does
# not exist yet) because Task 1's own published-set guard tests need
# `two_sets`, and Tasks 2-4 need the same fixtures again - one definition
# rather than a fourth copy.


@pytest.fixture()
def taxonomy_for_factors(session):
    """The minimum taxonomy a factor row can point at.

    Built through the real models rather than raw inserts: a factor row's
    foreign keys are the whole reason these tables exist, and a fixture that
    bypassed them could produce a combination the panel cannot.
    """
    from admin.taxonomy_models import (
        Destination, DestinationGroup, FoodCategory, Metric, Sector,
    )

    group = DestinationGroup(code="e6_disposal", name="Disposal", is_waste=True)
    session.add(group)
    session.flush()
    dest = Destination(group_id=group.id, code="e6_landfill", name="Landfill")
    sector = Sector(code="e6_processing", name="Processing")
    category = FoodCategory(code="e6_dairy", name="Dairy")
    metric = Metric(code="e6_co2e", name="Greenhouse gases", unit="kg CO2e")
    session.add_all([dest, sector, category, metric])
    session.flush()
    return SimpleNamespace(destination=dest, sector=sector,
                           category=category, metric=metric)


def _make_set(session, label, status, taxonomy, *, populated):
    """One factor set, optionally with a row of every child kind."""
    from admin.factor_models import (
        Constant, Equivalence, FactorDownstream, FactorSet, FactorUpstream, Formula,
    )

    fs = FactorSet(version_label=label, status=status, is_mock=True)
    session.add(fs)
    session.flush()
    if not populated:
        return fs

    session.add_all([
        FactorUpstream(factor_set_id=fs.id, sector_id=taxonomy.sector.id,
                       food_category_id=taxonomy.category.id,
                       metric_id=taxonomy.metric.id,
                       value_per_kg=Decimal("1.9000000000")),
        FactorDownstream(factor_set_id=fs.id, destination_id=taxonomy.destination.id,
                         food_category_id=None, metric_id=taxonomy.metric.id,
                         value_per_kg=Decimal("0.9900000000")),
        Constant(factor_set_id=fs.id, code="GWP_CH4_100", value=Decimal("29.8")),
        Formula(factor_set_id=fs.id, metric_id=taxonomy.metric.id,
                expression="qty_kg * (upstream + downstream)"),
        Equivalence(factor_set_id=fs.id, code="km_driven", name="Kilometres driven",
                    source_metric_id=taxonomy.metric.id,
                    value_per_unit=Decimal("0.192"),
                    label_template="Equivalent to driving {value} km"),
    ])
    session.flush()
    return fs


@pytest.fixture()
def populated_set(session, taxonomy_for_factors):
    """A draft carrying one row of each child kind, for clone tests."""
    from admin.factor_models import FactorSetStatus

    return _make_set(session, "e6-source", FactorSetStatus.draft,
                     taxonomy_for_factors, populated=True)


@pytest.fixture()
def one_draft(session, taxonomy_for_factors):
    """A single draft and nothing published - the fresh-deployment state."""
    from admin.factor_models import FactorSetStatus

    return _make_set(session, "e6-only-draft", FactorSetStatus.draft,
                     taxonomy_for_factors, populated=True)


@pytest.fixture()
def two_sets(session, taxonomy_for_factors):
    """A published set and a draft: the ordinary before-publish state."""
    from admin.factor_models import FactorSetStatus

    live = _make_set(session, "e6-live", FactorSetStatus.published,
                     taxonomy_for_factors, populated=True)
    draft = _make_set(session, "e6-next", FactorSetStatus.draft,
                      taxonomy_for_factors, populated=True)
    return live, draft


def _add_formula(session, factor_set, expression):
    """A formula on a *second* metric, so it does not collide with the one
    _make_set already created (UNIQUE on factor_set_id + metric_id)."""
    from admin.factor_models import Formula
    from admin.taxonomy_models import Metric

    metric = Metric(code=f"e6_extra_{factor_set.id}", name="Extra", unit="x")
    session.add(metric)
    session.flush()
    formula = Formula(factor_set_id=factor_set.id, metric_id=metric.id,
                      expression=expression)
    session.add(formula)
    session.flush()
    return formula
