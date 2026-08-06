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
from sqlalchemy import select, text

from admin.accounts import (
    begin_mfa_enrolment,
    complete_mfa_enrolment,
    create_staff,
    get_staff,
    set_password,
)
from admin.models import AuditLog, StaffRole
from admin.security import verify_password
from admin.totp import TOTP_INTERVAL
from admin.views import time as views_time

pytestmark = [pytest.mark.db, pytest.mark.asyncio]

SECRET_KEY = "test-secret-key-not-used-anywhere-real"


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
        create_staff(db, username=username, display_name="Target User")
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
    assert "mfa_secret_enc" not in response.text
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
