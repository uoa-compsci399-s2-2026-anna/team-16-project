"""Contract §2.2 and §8.2: the factor-set screen.

Deviates from task-4-brief.md's Step 1 listing the same way
tests/admin/test_factor_views.py and tests/admin/test_taxonomy_views.py
deviate from their own briefs, and for the same reasons - see those files'
module docstrings for the full account:

1. The end-to-end tests are ``async def`` and ``await`` every
   ``admin_client`` call. ``admin_client`` is httpx's ``AsyncClient`` (the
   ``client`` fixture in tests/conftest.py); calling one of its methods
   without ``await`` returns an un-awaited coroutine, not a response, and the
   response text asserted against below would never have been sent.
2. ``session`` here is a *local*, hard-committing fixture, not the
   rolled-back one tests/conftest.py defines. ``admin_client`` drives real
   HTTP requests that reach the factor-set view through the running app's own,
   separate sessionmaker - a different connection entirely - so a test that
   seeds a row via the rolled-back fixture and then expects ``admin_client``
   to see it would fail for a reason that has nothing to do with the
   invariant under test.
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
from admin.factor_models import FactorSet, FactorSetStatus
from admin.factor_views import FactorSetAdmin
from admin.models import StaffRole
from admin.totp import TOTP_INTERVAL
from admin.views import time as views_time

pytestmark = pytest.mark.db

SECRET_KEY = "test-secret-key-not-used-anywhere-real"


# --- Metadata-only test: no database access, no client ---------------------


def test_a_factor_set_cannot_be_deleted_from_the_panel():
    """Every submission stamps the set it was calculated against, so results
    stay reproducible. Deleting a set would strand every result that names
    it. Archiving is how a version leaves service."""
    assert FactorSetAdmin.can_delete is False


# --- Fixtures ---------------------------------------------------------
#
# No tests/admin/conftest.py exists, so - matching every other file under
# tests/admin/ - these are file-local, duplicated from
# tests/admin/test_factor_views.py's fixtures of the same names rather than
# imported, to keep this file self-contained the way every sibling file in
# this directory already is.


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
    the same name for the full rationale."""
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

    See tests/admin/test_factor_views.py's fixture of the same name: cleanup
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


_FACTOR_SET_LABELS = ["kc-factor-set-view-test-live", "kc-factor-set-view-test-next",
                     "kc-factor-set-view-test-first"]


def _cleanup_factor_set_rows(admin_app):
    """Remove every row this file's tests may have hard-committed via the
    local `session` fixture below, by the fixed set of labels those tests
    are written against."""
    factory = admin_app.state.session_factory
    with factory() as db:
        factor_set_ids = db.execute(
            text("SELECT id FROM factor_set WHERE version_label IN :labels")
            .bindparams(sa_bindparam("labels", expanding=True)),
            {"labels": _FACTOR_SET_LABELS},
        ).scalars().all()

        if factor_set_ids:
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
        db.commit()


@pytest.fixture
def session(admin_app):
    """A real, hard-committing session against the running admin app's own
    database. See tests/admin/test_factor_views.py's module docstring for why
    the rolled-back `session` fixture in tests/conftest.py cannot stand in
    here.

    Cleanup is unconditional (`finally`), not merely on success.
    """
    factory = admin_app.state.session_factory
    db = factory()
    try:
        yield db
    finally:
        db.close()
        _cleanup_factor_set_rows(admin_app)


def _resync(session):
    """End this session's own transaction and clear its identity map, so the
    next query reflects what `admin_client`'s HTTP request - a different
    connection - just committed.

    See tests/admin/test_factor_views.py's function of the same name: MySQL's
    REPEATABLE READ isolation means `session.expire_all()` alone is not
    enough - a query re-issued on the same still-open transaction keeps
    seeing the pre-request snapshot regardless.
    """
    session.commit()
    session.expire_all()


# --- End-to-end tests: FactorSetAdmin's real behaviour ----------------------


@pytest.mark.asyncio
async def test_publishing_a_second_set_through_the_edit_form_is_refused(
    session, admin_client
):
    """§2.2's invariant, on the path that bypasses every service function.

    The edit form reaches `status` directly through sqladmin's
    Query.update -> setattr -> commit, which is exactly how an earlier stage
    of this project shipped an administrator-floor guard that a checkbox
    walked straight past.
    """
    live = FactorSet(version_label=_FACTOR_SET_LABELS[0], status=FactorSetStatus.published,
                     is_mock=False)
    draft = FactorSet(version_label=_FACTOR_SET_LABELS[1], status=FactorSetStatus.draft,
                      is_mock=True)
    session.add_all([live, draft])
    session.commit()

    response = await admin_client.post(f"/admin/factor-set/edit/{draft.id}", data={
        "version_label": _FACTOR_SET_LABELS[1], "status": "published", "is_mock": "on",
        "notes": "",
    })

    _resync(session)
    assert session.scalar(
        select(FactorSet).where(FactorSet.version_label == _FACTOR_SET_LABELS[1])
    ).status is FactorSetStatus.draft
    # Not "published" in response.text.lower(): that word also appears in
    # the re-rendered status <select>'s own option list, so it passes
    # against a raw traceback just as easily as against a real refusal.
    # sqladmin's edit route (see admin/taxonomy_views.py's module docstring
    # for the confirmed mechanism) wraps update_model in a bare
    # `except Exception`, sets `context["error"] = str(e)` and re-renders
    # with a 400 - checking the status code and the invariant's own message
    # is what actually proves this was refused for the right reason.
    assert response.status_code == 400
    assert "Archive all but one" in response.text


@pytest.mark.asyncio
async def test_publishing_when_nothing_is_published_goes_through(session, admin_client):
    """The invariant refuses the second published set and nothing else. A
    guard that refused every status change would pass the test above."""
    draft = FactorSet(version_label=_FACTOR_SET_LABELS[2], status=FactorSetStatus.draft,
                      is_mock=True)
    session.add(draft)
    session.commit()

    await admin_client.post(f"/admin/factor-set/edit/{draft.id}", data={
        "version_label": _FACTOR_SET_LABELS[2], "status": "published", "is_mock": "on",
        "notes": "",
    })

    _resync(session)
    assert session.scalar(
        select(FactorSet).where(FactorSet.version_label == _FACTOR_SET_LABELS[2])
    ).status is FactorSetStatus.published
