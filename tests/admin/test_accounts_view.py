"""Contract §8.3: an administrator can recover a colleague without email.

Every request here goes through the real HTTP login flow (password, then
TOTP) rather than a hand-built session dict - the same choice
tests/admin/test_app.py's ``logged_in_client`` and tests/admin/test_flow.py
make, and for the same reason: this proves each route is reachable the way a
real user reaches it, through ``AuthenticationBackend``, not merely that the
view class exists.

sqladmin 0.30's ``@action`` decorator registers a GET route at
``/{identity}/action/{slug}``, reading the selected rows off a ``pks`` query
parameter (``sqladmin/application.py``) - not a POST to a path carrying the
row id, which an earlier draft of ``admin/accounts_view.py`` assumed. Every
request below targets that shape.
"""

import re
import time
import uuid

import pyotp
import pytest
import pytest_asyncio
from sqlalchemy import bindparam as sa_bindparam
from sqlalchemy import func, select, text

from admin.accounts import (
    RECOVERY_CODE_COUNT,
    begin_mfa_enrolment,
    complete_mfa_enrolment,
    create_staff,
    get_staff,
    set_password,
)
from admin.audit import write_audit
from admin.models import AuditLog, Staff, StaffRole
from admin.security import verify_password
from admin.totp import TOTP_INTERVAL
from admin.views import time as views_time

pytestmark = [pytest.mark.db, pytest.mark.asyncio]

SECRET_KEY = "test-secret-key-not-used-anywhere-real"

#: The password every account these fixtures create is left holding.
#: A constant rather than a literal repeated at each call site: the
#: routes that re-authenticate (creating an account, deleting one) have
#: to send the same value the fixture set, and two copies of it would
#: drift into a test that fails for the wrong reason.
PASSWORD = "a-long-enough-password"


# --- Fixtures ----------------------------------------------------------
#
# No tests/admin/conftest.py exists (per Task 6's own note), so - matching
# every other file under tests/admin/ - these are file-local.


def _create_onboarded_account(admin_app, *, role):
    """Create and commit a fully onboarded account of the given role.

    Split out of what used to be a single `_provision_and_login` so that a
    failure in the login step below (see `_login`) can be told apart from a
    failure here - the fixtures that call both need to clean up a row that
    was successfully committed even when the *login* half raises.
    """
    username = f"u{uuid.uuid4().hex[:10]}"
    password = PASSWORD
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
    """Remove rows this test's fixtures created - the staff row and any
    audit_log entries an action wrote against it.

    admin_app's session factory commits against the same persistent test
    database every test in this module shares - unlike the rolled-back
    `session` fixture in tests/conftest.py, nothing here undoes a commit
    automatically. Two separate leaks follow from that, and this closes
    both:

    * An administrator account left behind by one test's admin_client
      fixture is still `is_active` when a later test's two_admins fixture
      counts administrators, inflating count_active_admins past
      MIN_ACTIVE_ADMINS and silently defeating the floor test - exactly
      what happened the first time this file ran (the deactivation the
      floor test performs succeeded instead of being refused, because two
      prior tests' admin accounts were still sitting in the table).
    * An audit_log row this module's HTTP-driven actions wrote (issue a
      password, reset an authenticator) is otherwise the only row this
      whole suite ever commits outside a rolled-back `session` fixture.
      tests/admin/test_audit.py and test_modelviews.py both read AuditLog
      with `session.scalar(select(AuditLog)...)` and no further filter,
      trusting they are the only writer - true against every other file,
      false once this one leaves rows behind. Running the full suite after
      this file without this cleanup reproduced exactly that: nine
      unrelated failures elsewhere, several of them
      `entry.before_json["..."]` raising TypeError because the row
      `.scalar()` happened to return was one of this file's, not the one
      the failing test had just written.

    Staff rows are deleted by id rather than username for the same reason -
    row_id on audit_log is numeric and has no username to join back through.
    """
    factory = admin_app.state.session_factory
    with factory() as db:
        for staff in staff_rows:
            db.execute(
                text("DELETE FROM audit_log WHERE table_name = 'staff' AND row_id = :id"),
                {"id": staff.id},
            )
            # ...and every entry this account *wrote*, whatever table it names.
            # tests/admin/conftest.py's copy of this helper has always done
            # this and its docstring calls the difference load-bearing. It
            # matters more here now: the deletion tests assert that an entry an
            # account wrote outlives the account, which means deliberately
            # committing one that row_id-based cleanup would never find.
            db.execute(
                text("DELETE FROM audit_log WHERE actor = :actor"),
                {"actor": staff.username},
            )
            db.execute(text("DELETE FROM staff WHERE id = :id"), {"id": staff.id})
        db.commit()


@pytest_asyncio.fixture
async def admin_client(admin_app, client, monkeypatch):
    """A client logged in as a fully onboarded administrator.

    The Staff row backing the session is stashed on the client as ``.staff``
    - httpx's AsyncClient takes arbitrary attributes - so fixtures built on
    top of this one (``two_admins``) can identify exactly which account is
    acting, rather than guessing from a fresh query.

    Cleanup is registered before the login is attempted, not after: the
    account row is already committed at that point (`_create_onboarded_account`
    commits), so a failure in `_login` - a broken CSRF scrape, a rejected
    TOTP code - must not leave a committed, active administrator row behind
    for a later test's `two_admins` fixture to silently count. An earlier
    version of this fixture called both steps as one function and only
    registered cleanup after both succeeded; a failure partway through
    would raise out of the fixture before `yield`, and teardown code after
    a `yield` never runs if the fixture never reaches it - exactly the kind
    of leak `_cleanup_staff`'s own docstring already describes for a
    different cause.
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


@pytest.fixture
def enrolled_staff(admin_app):
    """A fully onboarded, plain staff account for an administrator to act on.

    Built through the service functions rather than by hand-constructing a
    Staff row, so the account is in a state the login gate actually admits -
    the same reasoning as tests/admin/test_issue_password.py's fixture of the
    same name. Persisted through admin_app's own session factory, not the
    generic `session` fixture: the running app reads through a different
    connection than that fixture's rolled-back transaction, so a row created
    there would be invisible to it.
    """
    username = f"u{uuid.uuid4().hex[:10]}"
    factory = admin_app.state.session_factory
    with factory() as db:
        create_staff(db, username=username, display_name="Target User", actor="test", secret_key=SECRET_KEY)
        db.flush()
        set_password(db, username, "a-strong-initial-password")
        secret, _ = begin_mfa_enrolment(db, username, secret_key=SECRET_KEY)
        now = int(time.time()) - 8 * TOTP_INTERVAL
        complete_mfa_enrolment(
            db, username,
            pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(now),
            secret_key=SECRET_KEY, now=now,
        )
        db.commit()
        staff = get_staff(db, username)
    yield staff
    _cleanup_staff(admin_app, staff)


@pytest_asyncio.fixture
async def two_admins(admin_app, admin_client):
    """Exactly two administrators: the one behind admin_client, and a second.

    Built as a pair around admin_client's own account rather than as two
    independent admins. MIN_ACTIVE_ADMINS is 2 (admin/accounts.py); if
    admin_client's account were a third, unrelated administrator, deactivating
    one of the returned pair would go 3 -> 2 and succeed, never reaching the
    refusal this fixture exists to set up.

    Also purges any other `u<10 hex>`-named administrator left active by an
    earlier test in this module (or another admin/ test file using the same
    throwaway-username convention) before counting - admin_app's session
    factory commits against the same persistent test database every test
    shares, and count_active_admins reads the whole table, not just the rows
    this fixture created. This is exactly the failure this fixture's first
    version hit: two leftover admin accounts from earlier tests kept the
    real count above MIN_ACTIVE_ADMINS and the deactivation the floor test
    performs (which should be refused) went through instead.
    """
    factory = admin_app.state.session_factory
    with factory() as db:
        strays = db.execute(
            text(
                "SELECT id FROM staff WHERE username REGEXP '^u[0-9a-f]{10}$' "
                "AND username != :keep"
            ),
            {"keep": admin_client.staff.username},
        ).scalars().all()
        if strays:
            # audit_log.row_id carries no foreign key (admin/models.py), so
            # deleting the stray staff rows directly would leave their
            # audit_log entries behind as the same kind of orphan this
            # fixture's docstring already describes for its own accounts.
            db.execute(
                text("DELETE FROM audit_log WHERE table_name = 'staff' AND row_id IN :ids")
                .bindparams(sa_bindparam("ids", expanding=True)),
                {"ids": strays},
            )
            db.execute(
                text("DELETE FROM staff WHERE id IN :ids")
                .bindparams(sa_bindparam("ids", expanding=True)),
                {"ids": strays},
            )
        db.commit()
        first = get_staff(db, admin_client.staff.username)
        second_username = f"u{uuid.uuid4().hex[:10]}"
        create_staff(
            db, username=second_username, display_name="Second Admin",
            role=StaffRole.admin,
            actor="test",
            secret_key=SECRET_KEY,
        )
        db.flush()
        set_password(db, second_username, "a-long-enough-password")
        secret, _ = begin_mfa_enrolment(db, second_username, secret_key=SECRET_KEY)
        now = int(time.time()) - 8 * TOTP_INTERVAL
        complete_mfa_enrolment(
            db, second_username,
            pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(now),
            secret_key=SECRET_KEY, now=now,
        )
        db.commit()
        second = get_staff(db, second_username)
    yield first, second
    _cleanup_staff(admin_app, second)


@pytest.fixture
def db_session(admin_app):
    """A fresh session against the running app's own database.

    Opened lazily (SQLAlchemy autobegins on first statement), so a query
    issued after an HTTP action already committed sees that commit - it is a
    new transaction, not a snapshot taken at fixture setup.
    """
    factory = admin_app.state.session_factory
    with factory() as db:
        yield db


# --- Tests ---------------------------------------------------------------


async def test_a_staff_member_cannot_reach_the_account_screen(staff_client):
    """Only role=admin. A staff member who could issue passwords to
    administrators would hold administrator power by another route."""
    response = await staff_client.get("/admin/staff/list")

    assert response.status_code == 403


async def test_an_administrator_can_reach_it(admin_client):
    response = await admin_client.get("/admin/staff/list")

    assert response.status_code == 200


async def test_the_list_never_renders_a_password_hash(admin_client, enrolled_staff):
    """Contract §5.5's redaction covers the audit trail; this covers the
    screen it is displayed beside."""
    response = await admin_client.get("/admin/staff/list")

    assert "password_hash" not in response.text
    assert "mfa_secret_enc" not in response.text
    assert enrolled_staff.password_hash not in response.text


async def test_the_details_view_never_renders_a_password_hash(admin_client, enrolled_staff):
    """sqladmin's own default for column_details_list is "every mapped
    column" (get_details_columns falls back to self._prop_names) unless a
    view sets it explicitly. column_list narrowing the *list* page's columns
    says nothing about the details page - a details button sits on every
    list row, so an unset column_details_list is a one-click path to the
    hash and the encrypted TOTP secret this same account correctly hides
    from the list.
    """
    response = await admin_client.get(f"/admin/staff/details/{enrolled_staff.id}")

    assert response.status_code == 200
    assert "password_hash" not in response.text
    # Both names: `mfa_secret_enc` was the column before contract v1.13 and
    # `secret_enc` is the one it became. The old name is kept because a
    # details page that somehow rendered a pre-migration payload would still
    # be a leak, and because a test that only knows the new name would go
    # green against a view that had quietly reverted.
    assert "mfa_secret_enc" not in response.text
    assert "secret_enc" not in response.text
    assert "mfa_last_counter" not in response.text
    assert enrolled_staff.password_hash not in response.text


async def test_the_csv_export_never_renders_a_password_hash(admin_client, enrolled_staff):
    """column_export_list falls back to the *list* columns (get_export_columns's
    default is self._list_prop_names, not self._prop_names) so this is
    already safe without column_list narrowing it separately - covered here
    anyway, since export is a second unfiltered-by-default render path and
    the reasoning that made the details page unsafe doesn't obviously not
    apply to it too.
    """
    response = await admin_client.get("/admin/staff/export/csv")

    assert response.status_code == 200
    assert "password_hash" not in response.text
    assert "mfa_secret_enc" not in response.text
    assert enrolled_staff.password_hash not in response.text


async def test_issuing_a_password_shows_it_once_and_forces_a_change(
    admin_client, db_session, enrolled_staff
):
    response = await admin_client.get(
        "/admin/staff/action/issue-password", params={"pks": enrolled_staff.id}
    )

    assert response.status_code == 200
    match = re.search(r'<code class="key">([^<]+)</code>', response.text)
    assert match, "no issued password rendered in the response body"
    issued_password = match.group(1)

    staff = get_staff(db_session, enrolled_staff.username)
    assert staff.must_change_password is True
    # Not just "some text landed in the <code> tag" - the exact password the
    # account can now log in with. A template whose context key silently
    # didn't match (e.g. `password` vs. the view's `issued` tuple) would
    # still satisfy an emptiness-blind assertion; this project has shipped
    # that defect once already, on a different page.
    assert verify_password(issued_password, staff.password_hash)


async def test_resetting_mfa_sends_the_account_back_through_enrolment(
    admin_client, db_session, enrolled_staff
):
    response = await admin_client.get(
        "/admin/staff/action/reset-mfa", params={"pks": enrolled_staff.id}
    )

    assert response.status_code == 302
    staff = get_staff(db_session, enrolled_staff.username)
    assert staff.mfa_enrolled is False
    assert staff.recovery_codes == []


async def test_deactivating_the_second_to_last_administrator_is_refused(
    admin_client, db_session, two_admins
):
    """MIN_ACTIVE_ADMINS = 2, and there is no email system to recover through.

    The floor lives in accounts.py; this asserts the view surfaces the
    refusal rather than swallowing it into a 500.
    """
    first, second = two_admins

    response = await admin_client.get(
        "/admin/staff/action/deactivate", params={"pks": second.id}
    )

    assert response.status_code == 400
    assert "administrator" in response.text.lower()
    assert get_staff(db_session, second.username).is_active is True


async def test_every_action_is_audited(admin_client, db_session, enrolled_staff):
    await admin_client.get(
        "/admin/staff/action/reset-mfa", params={"pks": enrolled_staff.id}
    )

    entry = db_session.scalar(
        select(AuditLog).where(AuditLog.table_name == "staff").order_by(AuditLog.id.desc())
    )
    assert entry is not None
    assert entry.actor == admin_client.staff.username


async def test_a_staff_member_cannot_issue_a_password_via_the_action_url(
    staff_client, enrolled_staff
):
    """is_visible alone hides the menu entry and leaves the URL open -
    contract §8.3's whole point is that only an administrator holds this
    power, so the action route itself, not just the list page, has to
    refuse a plain staff session.
    """
    response = await staff_client.get(
        "/admin/staff/action/issue-password", params={"pks": enrolled_staff.id}
    )

    assert response.status_code == 403


# --- Nobody recovers their own account -----------------------------------
#
# Contract §8.3's recovery layer L2 is "another administrator". Both actions
# below exist because the account's owner *cannot* act: issuing a password
# replaces a password nobody knows any more, resetting MFA clears a binding
# to a device nobody holds any more. Aimed at the actor's own account they
# stop being recovery and become the two steps of a takeover - a stolen
# session issues itself a password, resets the authenticator, enrols its own,
# and holds the account outright, having needed neither the password nor the
# device at any point. The audit trail records that faithfully and prevents
# none of it.
#
# Every test here drives the @action URL rather than the list page. sqladmin
# 0.30 registers those routes through `Admin.add_route` with `login_required`
# alone (application.py's `_handle_action_decorated_func`), so a guard that
# only ever showed up on /admin/staff/list would leave the URL itself open -
# the same shape as the defect this file's
# `test_a_staff_member_cannot_issue_a_password_via_the_action_url` already
# pins for the role check.
#
# `admin_client.staff` is the pre-request snapshot every "unchanged"
# assertion below compares against. It is deliberately *not* re-read through
# `db_session` before the request: MySQL's REPEATABLE READ would then hand
# every later query in the same test that same pre-request snapshot, and an
# assertion that the row is unchanged would hold no matter what the request
# did (see conftest's `_resync` for the same trap).


async def test_an_administrator_cannot_issue_a_password_to_their_own_account(
    admin_client, db_session
):
    """Issuing yourself a password is a password change that never asks for
    the current one."""
    me = admin_client.staff

    response = await admin_client.get(
        "/admin/staff/action/issue-password", params={"pks": me.id}
    )

    assert response.status_code == 400
    # The refusal page, not the credential page. A refusal that still rendered
    # a password would have issued one.
    assert 'class="key"' not in response.text

    staff = get_staff(db_session, me.username)
    assert staff.password_hash == me.password_hash
    assert staff.must_change_password is False
    assert staff.session_generation == me.session_generation


async def test_an_administrator_cannot_reset_their_own_authenticator(
    admin_client, admin_app, db_session
):
    """Resetting your own MFA is a second factor that never asks for the
    device."""
    me = admin_client.staff

    # Read through a session of its own that is closed again before the
    # request goes out. `db_session` must not be touched here: it opens
    # lazily precisely so that a query issued *after* the request sees the
    # commit, and a read taken through it now would start a transaction whose
    # REPEATABLE READ snapshot predates the request - every "unchanged"
    # assertion below would then hold no matter what the request did.
    with admin_app.state.session_factory() as db:
        before = [d.secret_enc for d in get_staff(db, me.username).totp_devices]
    assert before, "the fixture account should have an enrolled device"

    response = await admin_client.get(
        "/admin/staff/action/reset-mfa", params={"pks": me.id}
    )

    assert response.status_code == 400

    staff = get_staff(db_session, me.username)
    # The devices themselves, not just the derived flag. reset_mfa clears
    # the whole collection, so a refusal that left mfa_enrolled_at set while
    # emptying staff_totp_device would pass a flag-only assertion and leave
    # an account that reports itself enrolled with nothing to verify against.
    assert [d.secret_enc for d in staff.totp_devices] == before
    assert staff.mfa_enrolled is True
    assert staff.session_generation == me.session_generation
    # The recovery codes are the other half of what reset_mfa destroys, and
    # the half a "the enrolment is still there" assertion would miss.
    assert len(staff.recovery_codes) == RECOVERY_CODE_COUNT


async def test_a_refused_self_recovery_is_recorded(admin_client, db_session):
    """A refusal is worth a row of its own. The successful case is audited,
    so an attempt that was turned away leaving no trace is the one thing the
    trail would not show - and a self-aimed recovery action is exactly what
    an administrator reading it later wants to see.
    """
    await admin_client.get(
        "/admin/staff/action/reset-mfa", params={"pks": admin_client.staff.id}
    )

    entry = db_session.scalar(
        select(AuditLog).where(AuditLog.table_name == "staff").order_by(AuditLog.id.desc())
    )
    assert entry is not None
    assert entry.action == "refuse"
    assert entry.actor == admin_client.staff.username
    assert entry.row_id == admin_client.staff.id
    assert entry.after_json["refused"] == "reset-mfa"


async def test_a_selection_containing_the_actor_refuses_the_whole_batch(
    admin_client, db_session, enrolled_staff
):
    """`pks` is a comma-separated list, so an administrator can select their
    own row alongside a colleague's. The colleague's password must not be
    issued either: it would have been shown on a page this request never
    renders, so the account would be left holding a credential nobody read
    out. Same choice `deactivate_action` already makes for the
    two-administrator floor - one refusal aborts the selection.
    """
    response = await admin_client.get(
        "/admin/staff/action/issue-password",
        params={"pks": f"{enrolled_staff.id},{admin_client.staff.id}"},
    )

    assert response.status_code == 400
    colleague = get_staff(db_session, enrolled_staff.username)
    assert colleague.password_hash == enrolled_staff.password_hash
    assert colleague.session_generation == enrolled_staff.session_generation


async def test_the_screen_offers_no_unaccounted_action(admin_client):
    """Six actions, all accounted for.

    ``issue-password``, ``reset-mfa`` and ``delete`` are guarded against
    self-application by ``admin.accounts``; ``deactivate`` and ``delete`` by
    the two-administrator floor in the same module; ``delete`` additionally
    requires the account to be deactivated already and re-proves the actor
    before it runs. ``show-initial-password`` (contract v1.15) performs
    nothing itself — like ``delete`` it carries the selection to a page that
    can take a proof, because an ``@action`` is GET-only and revealing a live
    credential cannot be a one-click ``confirm()``. ``reactivate`` is the only
    one that guards nothing, and it is the only one that takes nothing away —
    it restores an account to the credentials it already had. That asymmetry
    is the point of listing them here: an action that removes access, or hands
    out a working credential, and carries no guard is what this assertion
    exists to catch.

    Walks the MRO's own ``__dict__`` rather than ``inspect.getmembers`` so an
    action added to a *base* class is caught too - `AuditedModelView` is
    shared with every other screen in the panel, and an action added there
    would appear on this one without anyone editing this file.
    """
    from admin.accounts_view import StaffAdmin

    slugs = {
        member._slug
        for klass in StaffAdmin.__mro__
        for member in vars(klass).values()
        if hasattr(member, "_slug")
    }

    assert slugs == {
        "issue-password", "reset-mfa", "deactivate", "reactivate", "delete",
        "show-initial-password",
    }


async def test_an_administrator_cannot_weaken_their_own_row_through_the_edit_form(
    admin_client, db_session
):
    """The companion to the floor test below, aimed at the actor's own row.

    `can_edit = False` closes the generic form for every row, so this is
    already covered - but it is covered by a class attribute one line long
    that a later task could plausibly flip to make `display_name` editable,
    and the fields that would come with it (`role`, `is_active`) are exactly
    the two an account must not be able to change on itself. This pins the
    property rather than the attribute.
    """
    me = admin_client.staff

    get_form = await admin_client.get(f"/admin/staff/edit/{me.id}")
    post_form = await admin_client.post(
        f"/admin/staff/edit/{me.id}",
        data={"display_name": me.display_name, "role": StaffRole.staff.value},
        follow_redirects=False,
    )

    assert get_form.status_code == 403
    assert post_form.status_code == 403
    staff = get_staff(db_session, me.username)
    assert staff.role is StaffRole.admin
    assert staff.is_active is True


async def test_every_other_generic_write_route_is_closed(admin_client, db_session):
    """The edit form is not the only route sqladmin generates that writes.

    `Admin.init` (sqladmin/application.py) registers `create`, `delete` and -
    new in 0.30 - `import`, a CSV upload that runs rows through the same form
    machinery as `create`. Each is refused here, by `can_create = False`,
    `can_delete = False` and sqladmin's own `can_import = False` default
    respectively.

    The import route in particular is guarded by a library default this view
    never mentions, which is precisely the kind of thing that changes under
    you on an upgrade: a CSV of `staff` rows would set `role` and `is_active`
    through `Query.update`, the same path the edit-form test below shows
    walks straight past `_guard_admin_floor`. Pinned as behaviour rather than
    left resting on a default nobody chose.
    """
    me = admin_client.staff

    delete = await admin_client.request(
        "DELETE", "/admin/staff/delete", params={"pks": me.id}
    )
    create = await admin_client.get("/admin/staff/create")
    upload = await admin_client.post(
        "/admin/staff/import",
        files={"file": ("staff.csv", b"username,role\nmine,admin\n", "text/csv")},
    )

    assert delete.status_code == 403
    assert create.status_code == 403
    assert upload.status_code == 403
    staff = get_staff(db_session, me.username)
    assert staff.is_active is True
    assert staff.role is StaffRole.admin


async def test_the_generic_edit_form_cannot_bypass_the_admin_floor(
    admin_client, db_session, two_admins
):
    """Contract §8.3: "This must be enforced in the service layer, not only
    in the form — sqladmin's form validation can be bypassed." The guarded
    actions (deactivate_action, set_role) honour that by routing through
    admin.accounts, which runs _guard_admin_floor. sqladmin's own generic
    edit form reaches the very same two fields (role, is_active) through a
    completely different path — Query.update -> plain setattr -> commit —
    where _guard_admin_floor never runs at all.

    is_active is rendered as an unchecked-means-False checkbox (a
    non-nullable Boolean column, per sqladmin's forms.py conv_boolean), so
    simply omitting it from the POST body is what an administrator
    unticking the box in the browser sends. This drives that request
    directly, with exactly two active administrators, and asserts the
    second cannot be deactivated through it.
    """
    first, second = two_admins

    response = await admin_client.post(
        f"/admin/staff/edit/{second.id}",
        data={"display_name": second.display_name, "role": second.role.value},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert get_staff(db_session, second.username).is_active is True


# --- deleting an account through the panel ----------------------------------


async def _delete_page(client, *ids):
    """GET the confirmation page for a selection."""
    return await client.get(
        "/admin/staff/delete", params={"pks": ",".join(str(i) for i in ids)}
    )


async def _delete(client, *ids, proof=None, csrf=None):
    """POST the confirmation page. `proof` is the re-authentication field."""
    if csrf is None:
        page = await _delete_page(client, *ids)
        assert page.status_code == 200, page.status_code
        match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
        assert match, "no CSRF token rendered on the delete page"
        csrf = match.group(1)
    data = {"csrf_token": csrf, "pks": ",".join(str(i) for i in ids)}
    data.update(proof or {})
    return await client.post("/admin/staff/delete", data=data, follow_redirects=False)


def _deactivate_directly(admin_app, staff):
    """Put an account into the state deletion requires, without driving the
    action - the deactivate route has its own tests."""
    factory = admin_app.state.session_factory
    with factory() as db:
        db.execute(
            text("UPDATE staff SET is_active = 0 WHERE id = :id"), {"id": staff.id}
        )
        db.commit()


async def test_the_delete_action_hands_the_selection_to_the_confirmation_page(
    admin_client, enrolled_staff
):
    """The action itself deletes nothing: sqladmin registers @action as GET
    only, and deletion has to take a proof, which needs a form."""
    response = await admin_client.get(
        "/admin/staff/action/delete", params={"pks": enrolled_staff.id},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert "/admin/staff/delete" in response.headers["location"]
    assert str(enrolled_staff.id) in response.headers["location"]


async def test_a_plain_staff_member_cannot_reach_the_delete_page(
    staff_client, enrolled_staff
):
    """sqladmin registers an @expose route on a ModelView with login_required
    only and never calls is_accessible for it, so the check has to be in the
    handler - the same property /admin/staff/new needs."""
    page = await staff_client.get(
        "/admin/staff/delete", params={"pks": enrolled_staff.id}
    )
    posted = await staff_client.post(
        "/admin/staff/delete",
        data={"pks": str(enrolled_staff.id), "csrf_token": "irrelevant"},
        follow_redirects=False,
    )

    assert page.status_code == 403
    assert posted.status_code == 403


async def test_an_active_account_is_refused_with_the_step_that_unblocks_it(
    admin_client, admin_app, db_session, enrolled_staff
):
    """The account has not been deactivated, so the refusal has to name
    deactivation rather than the two-administrator floor - they are different
    problems with different next steps, which is why they are different
    exception classes."""
    response = await _delete(
        admin_client, enrolled_staff.id, proof={"current_password": PASSWORD}
    )

    assert response.status_code == 400
    assert "deactivate" in response.text.lower()
    assert get_staff(db_session, enrolled_staff.username) is not None


async def test_a_deactivated_account_is_deleted_and_the_trail_keeps_its_work(
    admin_client, admin_app, db_session, enrolled_staff
):
    """The whole feature, end to end, with the property that justified a hard
    delete asserted on the way through: an entry the account wrote is still
    there afterwards, still naming it."""
    factory = admin_app.state.session_factory
    with factory() as db:
        write_audit(
            db, actor=enrolled_staff.username, action="update",
            table_name="factor_set", row_id=4242,
            before={"status": "draft"}, after={"status": "published"},
        )
        db.commit()
    _deactivate_directly(admin_app, enrolled_staff)

    response = await _delete(
        admin_client, enrolled_staff.id, proof={"current_password": PASSWORD}
    )

    assert response.status_code == 200, response.text[:400]
    assert enrolled_staff.username in response.text

    db_session.commit()
    assert db_session.scalar(
        select(Staff).where(Staff.username == enrolled_staff.username)
    ) is None, "the account is gone"

    survived = db_session.scalar(
        select(AuditLog).where(
            AuditLog.actor == enrolled_staff.username,
            AuditLog.table_name == "factor_set",
        )
    )
    assert survived is not None, "what the account did outlives the account"
    assert survived.after_json == {"status": "published"}

    deletion = db_session.scalar(
        select(AuditLog)
        .where(AuditLog.table_name == "staff", AuditLog.action == "delete")
        .order_by(AuditLog.id.desc())
    )
    assert deletion is not None
    assert deletion.actor == admin_client.staff.username
    assert deletion.before_json["username"] == enrolled_staff.username


async def test_deleting_requires_the_password_or_a_code(
    admin_client, admin_app, db_session, enrolled_staff
):
    """A stolen session is the one thing that already has the cookie, so the
    cookie alone cannot be enough to empty the staff list.

    Both halves: refused with no proof, and permitted with it. A refusal test
    on its own passes against a route that refuses everything.
    """
    _deactivate_directly(admin_app, enrolled_staff)

    refused = await _delete(admin_client, enrolled_staff.id)

    assert refused.status_code == 400
    db_session.commit()
    assert db_session.scalar(
        select(Staff).where(Staff.username == enrolled_staff.username)
    ) is not None, "nothing was deleted without proof"

    allowed = await _delete(
        admin_client, enrolled_staff.id, proof={"current_password": PASSWORD}
    )

    assert allowed.status_code == 200, allowed.text[:400]
    db_session.commit()
    assert db_session.scalar(
        select(Staff).where(Staff.username == enrolled_staff.username)
    ) is None, "and the same request with proof succeeds"


async def test_deleting_your_own_account_is_refused_and_recorded(
    admin_client, db_session
):
    """Half of a takeover from a stolen session, and the second party removed
    from a procedure whose value is that there was one. Refused in the service
    layer so the CLI is refused identically; recorded here because a refusal
    that left no trace would be the one thing an administrator reading the
    trail could not see.

    The acting account is left active on purpose. ``_guard_not_self`` runs
    *before* ``delete_staff``'s deactivation check, so aiming this at yourself
    is refused for being yourself rather than for being active — which is the
    right message, and the ordering that produces it is what this asserts.
    Deactivating the acting account first would also make the request
    unauthenticated: ``AdminAuth.authenticate`` refuses an inactive account, so
    the page would never be reached at all and the test would pass for a
    reason that has nothing to do with the guard.
    """
    response = await _delete(
        admin_client, admin_client.staff.id, proof={"current_password": PASSWORD}
    )

    assert response.status_code == 400
    db_session.commit()
    assert db_session.scalar(
        select(Staff).where(Staff.username == admin_client.staff.username)
    ) is not None

    refusal = db_session.scalar(
        select(AuditLog)
        .where(AuditLog.table_name == "staff", AuditLog.action == "refuse")
        .order_by(AuditLog.id.desc())
    )
    assert refusal is not None
    assert refusal.after_json["refused"] == "delete"


async def test_the_delete_page_says_the_audit_trail_is_not_erased(
    admin_client, enrolled_staff
):
    """An administrator who believes deleting an account scrubs what it did
    will either avoid the button or press it hoping that it does. Both are
    worse than knowing."""
    page = await _delete_page(admin_client, enrolled_staff.id)

    assert page.status_code == 200
    readable = re.sub(r"<[^>]+>", " ", page.text)
    assert "audit log" in readable.lower()
    assert enrolled_staff.username in page.text


async def test_the_deleted_page_does_not_offer_a_selectionless_delete(admin_client):
    """No pks at all is a page that must not carry a submittable form - the
    handler refuses it, and the page should not invite the request."""
    page = await admin_client.get("/admin/staff/delete", params={"pks": ""})

    assert page.status_code == 200
    assert 'name="current_password"' not in page.text


# --- reactivation -----------------------------------------------------------


async def test_a_deactivated_account_can_be_reactivated_from_the_panel(
    admin_client, admin_app, db_session, enrolled_staff
):
    """Without this, requiring deactivation before deletion is a trap rather
    than a step."""
    _deactivate_directly(admin_app, enrolled_staff)

    response = await admin_client.get(
        "/admin/staff/action/reactivate", params={"pks": enrolled_staff.id},
        follow_redirects=False,
    )

    assert response.status_code == 302
    db_session.commit()
    assert get_staff(db_session, enrolled_staff.username).is_active is True


async def test_reactivation_is_audited(
    admin_client, admin_app, db_session, enrolled_staff
):
    _deactivate_directly(admin_app, enrolled_staff)

    await admin_client.get(
        "/admin/staff/action/reactivate", params={"pks": enrolled_staff.id},
        follow_redirects=False,
    )

    db_session.commit()
    entry = db_session.scalar(
        select(AuditLog)
        .where(AuditLog.table_name == "staff", AuditLog.row_id == enrolled_staff.id)
        .order_by(AuditLog.id.desc())
    )
    assert entry is not None
    assert entry.actor == admin_client.staff.username
    assert entry.after_json["is_active"] is True


async def test_reactivating_an_already_active_account_writes_nothing(
    admin_client, db_session, enrolled_staff
):
    """A bulk selection that happens to include an active account is a slip,
    not an error - but an audit entry claiming a change that did not happen is
    worse than no entry."""
    before = db_session.scalar(
        select(func.count()).select_from(AuditLog).where(
            AuditLog.table_name == "staff", AuditLog.row_id == enrolled_staff.id
        )
    )

    await admin_client.get(
        "/admin/staff/action/reactivate", params={"pks": enrolled_staff.id},
        follow_redirects=False,
    )

    db_session.commit()
    after = db_session.scalar(
        select(func.count()).select_from(AuditLog).where(
            AuditLog.table_name == "staff", AuditLog.row_id == enrolled_staff.id
        )
    )
    assert after == before


async def test_the_delete_confirmation_names_the_acting_administrator(
    admin_client, enrolled_staff
):
    """The third page carrying the proof field, and it was the one nobody
    would have thought to check.

    A form holding ``autocomplete="current-password"`` is read by Chrome as a
    credential form, and with no field declaring ``autocomplete="username"`` it
    guesses which input holds the account name. That guess is what made
    renaming an authenticator on /admin/security offer to change the stored
    username, and it is why this page - which has no visible text input at all,
    so today Chrome has nothing to guess *with* - is asserted anyway. Adding
    one field to this form later would otherwise reintroduce the defect in a
    place with no test looking.

    The value is the administrator doing the deleting, never an account in the
    selection.
    """
    page = await _delete_page(admin_client, enrolled_staff.id)
    assert page.status_code == 200
    body = page.text

    asking = [
        form for form in re.findall(r"<form\b.*?</form>", body, re.S)
        if 'autocomplete="current-password"' in form
    ]
    assert len(asking) == 1, asking

    hints = re.findall(r"<input[^>]*autocomplete=\"username\"[^>]*>", asking[0])
    assert len(hints) == 1, (
        "a form asking for the account password must name the account exactly "
        f"once, or the browser guesses; found {len(hints)}"
    )
    hint = hints[0]
    assert f'value="{admin_client.staff.username}"' in hint, hint
    assert enrolled_staff.username not in hint, (
        "the account named to the browser is the one whose password is being "
        "asked for - the administrator acting, never the account being "
        f"deleted:\n{hint}"
    )
    assert "name=" not in hint, hint
    assert "readonly" in hint, hint
    assert "sr-only" in hint, hint
