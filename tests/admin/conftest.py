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
session below. The one exception is the "e6-"/"e6_"-prefixed rows the
factor-set fixtures below commit - `_committed_session` cleans those up
itself, in its own teardown, so a new file that uses `two_sets` and friends
gets that part for free. See `_committed_session`'s own docstring for why
that is folded into its teardown rather than a second, `admin_app`-level
autouse fixture.

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
from sqlalchemy import bindparam as sa_bindparam
from sqlalchemy import select, text

from admin.accounts import (
    begin_mfa_enrolment, complete_mfa_enrolment, create_staff, get_staff, set_password,
)
from admin.models import Staff, StaffRole
from admin.totp import TOTP_INTERVAL
from admin.views import time as views_time

SECRET_KEY = "test-secret-key-not-used-anywhere-real"

#: admin/protection.py (Task 3) refuses a request with no session cookie yet
#: whose headers look scripted (admin.detection.looks_automated) - and
#: httpx.AsyncClient's own default User-Agent ("python-httpx/x.y.z") is
#: itself one of the markers that check flags. ProtectionMiddleware is off
#: by default for every app this fixture file builds (see
#: tests/conftest.py's `_protection_default_for_tests`), so most callers of
#: `_login` never touch this check at all - but
#: tests/admin/test_protection.py overrides that default to "true" for its
#: own app instances, and its two `admin_client`-based tests still have to
#: get through `_login`'s password and TOTP steps before any session cookie
#: exists to exempt them. Sending browser-shaped headers here is what keeps
#: `_login` working under both configurations rather than only the common
#: one - it is not a workaround for a problem every file has, only for the
#: one file that turns protection on. Once `_login` returns, the session
#: cookie makes every further request through the same client exempt (the
#: anti-lockout rule) regardless of headers, so nothing past this point
#: needs them.
_BROWSER_HEADERS = {
    "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0 Safari/537.36",
    "accept": "text/html,application/xhtml+xml",
    "sec-fetch-mode": "navigate",
}


@pytest.fixture
def settings(admin_app):
    """The running app's own Settings.

    Lets a test read `secret_key`, `protection_max_requests_per_minute` and
    friends from the exact object admin/protection.py and the rest of the
    app were built against, rather than a second, hardcoded copy that could
    silently drift from admin/config.py's own defaults.
    """
    return admin_app.state.settings


# --- Account / login plumbing ----------------------------------------------


def _create_onboarded_account(admin_app, *, role=StaffRole.admin):
    """Create and commit a fully onboarded account of the given role."""
    username = f"u{uuid.uuid4().hex[:10]}"
    password = "a-long-enough-password"
    factory = admin_app.state.session_factory
    with factory() as db:
        create_staff(db, username=username, display_name="Test User", role=role, actor="test", secret_key=SECRET_KEY)
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
        headers=_BROWSER_HEADERS,
        follow_redirects=False,
    )
    assert login.status_code == 302, "password step should have succeeded"

    verify_page = await client.get("/admin/verify", headers=_BROWSER_HEADERS)
    match = re.search(r'name="csrf_token" value="([^"]+)"', verify_page.text)
    assert match, "no CSRF token rendered on /admin/verify"
    code = pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(now)
    verify = await client.post(
        "/admin/verify",
        data={"code": code, "csrf_token": match.group(1)},
        headers=_BROWSER_HEADERS,
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


def _cleanup_staff_named(admin_app, *usernames):
    """``_cleanup_staff`` for a fixture that holds a username, not a row.

    Several files build their accounts with ``create_staff`` and keep only the
    name. Each grew its own teardown, and every one of those copies kept the
    ``actor`` predicate and dropped the ``table_name``/``row_id`` one - which
    is the predicate that catches an entry written **against** these staff rows
    by a *different* actor, the shape ``admin/accounts.py::issue_password``
    writes when one administrator acts on another. Five weaker copies of the
    block that exists to stop audit rows leaking between files is how the
    leak comes back, so the deletion itself stays written once, above, and
    this only resolves names to rows.

    A name with no row is not an error: these teardowns also run *before* a
    walk, to clear anything an interrupted earlier run left behind, and a
    bootstrap account may legitimately not exist yet. There is no id to key
    the second predicate on in that case, and no staff row to delete, so the
    actor predicate is all there is - and all there can be.
    """
    factory = admin_app.state.session_factory
    with factory() as db:
        rows = {
            username: db.scalar(
                select(Staff).where(Staff.username == username)
            )
            for username in usernames
        }

    found = [row for row in rows.values() if row is not None]
    if found:
        _cleanup_staff(admin_app, *found)

    missing = [username for username, row in rows.items() if row is None]
    if missing:
        with factory() as db:
            for username in missing:
                db.execute(
                    text("DELETE FROM audit_log WHERE actor = :username"),
                    {"username": username},
                )
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
    # Both factors are hung off the client alongside the row. Any screen that
    # re-authenticates on top of an established session - /admin/security, and
    # /admin/staff/new - needs one of them to drive its permitted case, and a
    # test that could only drive the *refused* case would be asserting that a
    # system which refuses everything works correctly.
    client.password = password
    client.secret = secret
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
    client.password = password
    client.secret = secret
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

    Cleanup here covers the one thing every consumer of this fixture shares
    - the "e6-"/"e6_"-prefixed rows `taxonomy_for_factors`, `populated_set`,
    `one_draft` and `two_sets` (below) commit through it, via
    `_cleanup_e6_rows` - unconditionally (`finally`), so a test that fails
    partway through still leaves the database clean for the next one.

    Deliberately *not* a separate `autouse=True` fixture depending on
    `admin_app` directly, which was tried first: `admin_app` builds a real
    FastAPI app and SQLAlchemy engine, and an autouse fixture naming it as a
    parameter forces that construction for *every* test in this directory,
    whether or not the test has anything to do with factor sets -
    including test_expressions.py's 64 pure-function tests, which touch no
    database at all. Measured directly: adding such a fixture and running
    64 otherwise-trivial tests through it took just under 8 seconds where
    the same 64 tests take 0.05s without it - roughly 125ms each, which
    across this directory's several hundred tests is not a rounding error.
    Folding the cleanup into this fixture's own teardown instead means it
    only ever runs for a test that already needed `_committed_session` (or
    something built on it), which is exactly the set of tests that could
    have committed one of these rows in the first place - the same
    guarantee, at the cost of nothing for everything else.

    Which rows a given file's *own* fixed labels and codes touch (as
    opposed to these shared "e6-" ones) is still specific to that file, so
    each file that needs its own cleanup still registers its own additional
    autouse fixture calling its local `_cleanup_*_rows` helper, same as
    before.
    """
    factory = admin_app.state.session_factory
    db = factory()
    try:
        yield db
    finally:
        db.close()
        # e7- rows first: `comparison_scenario_line` holds foreign keys into
        # the taxonomy rows (`destination`, `sector`, `food_category`) that
        # `taxonomy_for_factors` commits with the "e6_"-prefixed codes
        # `_cleanup_e6_rows` deletes below - deleting those out from under a
        # still-referencing comparison_scenario_line fails with a foreign
        # key constraint error, the same way it would for any other child
        # row still pointing at a taxonomy id about to disappear.
        _cleanup_e7_rows(admin_app)
        _cleanup_e6_rows(admin_app)


def _cleanup_e6_rows(admin_app) -> None:
    """Remove every row any test in this directory may have hard-committed
    through `taxonomy_for_factors`, `populated_set`, `one_draft`, `two_sets`
    or `_add_formula` - or built directly with the same "e6-"/"e6_" prefix,
    as tests/admin/test_factor_views.py's published-set guard tests do for
    extra factor sets beyond what those fixtures themselves create.

    Matched by prefix (`LIKE 'e6-%'` / `LIKE 'e6\\_%' ESCAPE '\\\\'`) rather
    than a fixed list of labels/codes: a fixed list has to be extended by
    hand every time a new consumer adds another ad hoc "e6-..." row, which
    is exactly the kind of thing a second consumer forgets - the whole
    reason this lives here once rather than being copied into every file
    that uses these fixtures.

    `factor_set_id` cascades (`ondelete="CASCADE"` on
    FactorUpstream/FactorDownstream/Constant/Formula/Equivalence, see
    admin/factor_models.py) so deleting the factor_set rows themselves is
    enough to take their children with it; the taxonomy rows underneath
    them (sector, food_category, metric, destination, destination_group) do
    not belong to any factor_set and are cleaned up explicitly,
    destination_group last because destination carries a foreign key to it.
    """
    factory = admin_app.state.session_factory
    with factory() as db:
        factor_set_ids = db.execute(
            text("SELECT id FROM factor_set WHERE version_label LIKE 'e6-%'")
        ).scalars().all()

        if factor_set_ids:
            for table in ("formula", "constant", "factor_upstream",
                          "factor_downstream", "equivalence"):
                db.execute(
                    text(f"DELETE FROM audit_log WHERE table_name = '{table}' "
                         "AND row_id IN (SELECT id FROM " + table +
                         " WHERE factor_set_id IN :ids)")
                    .bindparams(sa_bindparam("ids", expanding=True)),
                    {"ids": factor_set_ids},
                )
            db.execute(
                text("DELETE FROM audit_log WHERE table_name = 'factor_set' "
                     "AND row_id IN :ids").bindparams(sa_bindparam("ids", expanding=True)),
                {"ids": factor_set_ids},
            )
            db.execute(
                text("DELETE FROM factor_set WHERE id IN :ids")
                .bindparams(sa_bindparam("ids", expanding=True)),
                {"ids": factor_set_ids},
            )

        #: `destination` is matched by its *group* as well as by its own code.
        #: A test needing the reserved `prevention` row (contract §2.1 fixes
        #: that literal, so it cannot carry an "e6_" prefix) hangs it off an
        #: "e6_" group — tests/admin/test_factor_lifecycle.py's O-7 publish
        #: guard test does exactly that. Without this clause the destination
        #: survives the sweep and the *group* delete below then fails on its
        #: foreign key, leaving both behind for every later run.
        _EXTRA_MATCH = {
            "destination": (
                " OR group_id IN (SELECT id FROM destination_group "
                "WHERE code LIKE 'e6\\_%' ESCAPE '\\\\')"
            ),
        }
        for table in ("metric", "destination", "sector", "food_category"):
            ids = db.execute(
                text(f"SELECT id FROM {table} WHERE code LIKE 'e6\\_%' ESCAPE '\\\\'"
                     + _EXTRA_MATCH.get(table, ""))
            ).scalars().all()
            if ids:
                db.execute(
                    text(f"DELETE FROM audit_log WHERE table_name = '{table}' "
                         "AND row_id IN :ids").bindparams(sa_bindparam("ids", expanding=True)),
                    {"ids": ids},
                )
                db.execute(
                    text(f"DELETE FROM {table} WHERE id IN :ids")
                    .bindparams(sa_bindparam("ids", expanding=True)),
                    {"ids": ids},
                )

        group_ids = db.execute(
            text("SELECT id FROM destination_group WHERE code LIKE 'e6\\_%' ESCAPE '\\\\'")
        ).scalars().all()
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


def _cleanup_e7_rows(admin_app) -> None:
    """Remove every `comparison_scenario` (and, by `ondelete="CASCADE"`,
    every `comparison_scenario_line`) row a test in this directory
    hard-committed with an "e7-"-prefixed `code` - `seeded_scenario`,
    `seeded_scenario_pair` and `inactive_scenario` in
    tests/admin/test_compare_view.py.

    Same reasoning as `_cleanup_e6_rows` just above: folded into
    `_committed_session`'s own teardown so every consumer of `two_sets` /
    `one_draft` gets this for free, rather than each new file that adds an
    "e7-..." scenario needing to remember its own cleanup. Matched by
    prefix, not a fixed list, for the same reason - a fixed list is exactly
    the kind of thing a second consumer forgets to extend.
    """
    factory = admin_app.state.session_factory
    with factory() as db:
        scenario_ids = db.execute(
            text("SELECT id FROM comparison_scenario WHERE code LIKE 'e7-%'")
        ).scalars().all()
        if scenario_ids:
            db.execute(
                text("DELETE FROM audit_log WHERE table_name = 'comparison_scenario_line' "
                     "AND row_id IN (SELECT id FROM comparison_scenario_line "
                     "WHERE scenario_id IN :ids)")
                .bindparams(sa_bindparam("ids", expanding=True)),
                {"ids": scenario_ids},
            )
            db.execute(
                text("DELETE FROM audit_log WHERE table_name = 'comparison_scenario' "
                     "AND row_id IN :ids").bindparams(sa_bindparam("ids", expanding=True)),
                {"ids": scenario_ids},
            )
            db.execute(
                text("DELETE FROM comparison_scenario WHERE id IN :ids")
                .bindparams(sa_bindparam("ids", expanding=True)),
                {"ids": scenario_ids},
            )
        db.commit()


# --- Task 2 Step 1's factor-set fixtures ------------------------------------
#
# Added here (rather than in tests/admin/test_factor_lifecycle.py, which does
# not exist yet) because Task 1's own published-set guard tests need
# `two_sets`, and Tasks 2-4 need the same fixtures again - one definition
# rather than a fourth copy.
#
# These depend on `_committed_session` *by that name*, not on `session`.
# `session` is not defined at this conftest level (see `_committed_session`'s
# own docstring for why), so a fixture here that asked for `session` would
# resolve it per the *requesting test's own file* - the local hard-committing
# wrapper in test_factor_views.py, but tests/conftest.py's rolled-back one in
# any file that has not added that same wrapper. That is exactly the trap a
# new consumer (Task 4's test_factor_set_actions.py) would fall into
# silently: `admin_client` would see none of the rows these fixtures commit,
# and the resulting test failure - a row missing over HTTP - reads like a
# broken guard rather than a fixture resolving to the wrong session.
# Depending on `_committed_session` directly removes the ambiguity: every
# caller gets the same, real, hard-committing session no matter which file
# it is defined in.


@pytest.fixture()
def taxonomy_for_factors(_committed_session):
    """The minimum taxonomy a factor row can point at.

    Built through the real models rather than raw inserts: a factor row's
    foreign keys are the whole reason these tables exist, and a fixture that
    bypassed them could produce a combination the panel cannot.
    """
    from admin.taxonomy_models import (
        Destination, DestinationGroup, FoodCategory, Metric, Sector,
    )

    session = _committed_session
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
    """One factor set, optionally with a row of every child kind.

    Every optional column (`source_note`, `data_quality`, `unit`, `note`,
    `notes`, `sort_order`, `active`) is given a non-default value, not left
    to whatever the column default happens to be. A clone implementation
    that only copies NOT NULL columns is indistinguishable from a correct
    one when every optional column already sits at its default - this is
    what lets tests/admin/test_factor_lifecycle.py's full-column comparison
    actually catch that.
    """
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
                       value_per_kg=Decimal("1.9000000000"),
                       source_note="e6 fixture upstream source note",
                       data_quality="measured"),
        FactorDownstream(factor_set_id=fs.id, destination_id=taxonomy.destination.id,
                         food_category_id=None, metric_id=taxonomy.metric.id,
                         value_per_kg=Decimal("0.9900000000"),
                         source_note="e6 fixture downstream source note",
                         data_quality="estimated"),
        Constant(factor_set_id=fs.id, code="GWP_CH4_100", value=Decimal("29.8"),
                 unit="kg CO2e / kg CH4", note="e6 fixture constant note"),
        Formula(factor_set_id=fs.id, metric_id=taxonomy.metric.id,
                expression="qty_kg * (upstream + downstream)",
                notes="e6 fixture formula note"),
        Equivalence(factor_set_id=fs.id, code="km_driven", name="Kilometres driven",
                    source_metric_id=taxonomy.metric.id,
                    value_per_unit=Decimal("0.192"),
                    label_template="Equivalent to driving {value} km",
                    source_note="e6 fixture equivalence source note",
                    sort_order=3, active=False),
    ])
    session.flush()
    return fs


@pytest.fixture()
def populated_set(_committed_session, taxonomy_for_factors):
    """A draft carrying one row of each child kind, for clone tests."""
    from admin.factor_models import FactorSetStatus

    return _make_set(_committed_session, "e6-source", FactorSetStatus.draft,
                     taxonomy_for_factors, populated=True)


@pytest.fixture()
def one_draft(_committed_session, taxonomy_for_factors):
    """A single draft and nothing published - the fresh-deployment state."""
    from admin.factor_models import FactorSetStatus

    return _make_set(_committed_session, "e6-only-draft", FactorSetStatus.draft,
                     taxonomy_for_factors, populated=True)


@pytest.fixture()
def two_sets(_committed_session, taxonomy_for_factors):
    """A published set and a draft: the ordinary before-publish state."""
    from admin.factor_models import FactorSetStatus

    live = _make_set(_committed_session, "e6-live", FactorSetStatus.published,
                     taxonomy_for_factors, populated=True)
    draft = _make_set(_committed_session, "e6-next", FactorSetStatus.draft,
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


# --- Playwright, package-scoped -------------------------------------------
#
# The same shape as tests/web/conftest.py's own `_playwright`/`browser` pair,
# and for the same reason: Playwright's sync API keeps an asyncio loop
# "running" on this OS thread for as long as a `sync_playwright()` context
# stays open, so a `scope="session"` fixture here would go on poisoning
# `tests/web`'s own browser-driving files (or vice versa) the moment both
# packages' tests share one pytest session. `scope="package"` opens the
# driver on first use inside tests/admin/ and closes it as soon as the last
# tests/admin/ test finishes, independently of whichever package tests/web
# ties its own copy to. See tests/web/conftest.py's docstring for the
# measured failure this avoids.


@pytest.fixture(scope="package")
def _playwright():
    """The one `sync_playwright()` context for the whole tests/admin run.

    The import is deferred to fixture setup: a checkout without Playwright
    installed still needs tests/admin/ to *collect* cleanly, because every
    browser-driving file in this package does its own
    `pytest.importorskip("playwright.sync_api", ...)` and skips at module
    level before any test in it ever requests `browser` - collection must not
    fail here first.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="package")
def browser(_playwright):
    instance = _playwright.chromium.launch()
    yield instance
    instance.close()
