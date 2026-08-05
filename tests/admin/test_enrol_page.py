"""admin.views.EnrolView - the MFA enrolment page.

The front door to E-1's account-takeover guard: begin_mfa_enrolment refuses
to mint a second secret for an account that has already finished enrolling,
and this page must surface that as a refusal rather than a traceback, and
never as a "re-enrol" option.

Contract: docs/interfaces.md 8.3.
"""

import re
import time

import pyotp
import pytest
from sqlalchemy import text

from admin.accounts import (
    RECOVERY_CODE_COUNT,
    begin_mfa_enrolment,
    complete_mfa_enrolment,
    create_staff,
    get_staff,
    set_password,
)
from admin.security import decrypt_totp_secret
from admin.totp import TOTP_INTERVAL
from admin.views import time as views_time

pytestmark = [pytest.mark.db, pytest.mark.asyncio]

SECRET_KEY = "test-secret-key-not-used-anywhere-real"


# The app and client fixtures live in tests/conftest.py as of Task 3 - the
# app fixture is named `admin_app` and disposes its engine on teardown. Do
# not redefine them here; Tasks 4 onward all share those definitions, and a
# private copy would leak an engine per test.


@pytest.fixture
def owes_enrolment(admin_app):
    """An account past the forced password change but not yet MFA-enrolled.

    This is exactly the state login() routes to admin:view-enrol
    (admin/backend.py:292): must_change_password already cleared, no
    mfa_secret_enc yet. Returns (username, password).
    """
    import uuid

    username = f"u{uuid.uuid4().hex[:10]}"
    factory = admin_app.state.session_factory
    with factory() as db:
        _, password = create_staff(db, username=username, display_name="Test User")
        db.flush()
        set_password(db, username, "a-long-enough-password")
        db.commit()
    yield username, "a-long-enough-password"
    with factory() as db:
        db.execute(text("DELETE FROM staff WHERE username = :u"), {"u": username})
        db.commit()


@pytest.fixture
def onboarded(admin_app):
    """A fully set-up account: past both onboarding steps.

    Copied from tests/admin/test_verify_page.py rather than imported -
    pytest fixtures are not shared across modules without a conftest.py
    move. Needed for the already-enrolled-refusal test (behaviour 4); see
    that file for the reasoning behind the backdated TOTP step.
    """
    import uuid

    username = f"u{uuid.uuid4().hex[:10]}"
    factory = admin_app.state.session_factory
    with factory() as db:
        _, password = create_staff(db, username=username, display_name="Test User")
        db.flush()
        set_password(db, username, "a-long-enough-password")
        secret, _ = begin_mfa_enrolment(db, username, secret_key=SECRET_KEY)
        now = int(time.time()) - 2 * TOTP_INTERVAL
        codes = complete_mfa_enrolment(
            db,
            username,
            pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(now),
            secret_key=SECRET_KEY,
            now=now,
        )
        db.commit()
    yield username, "a-long-enough-password", secret, codes
    with factory() as db:
        db.execute(text("DELETE FROM staff WHERE username = :u"), {"u": username})
        db.commit()


async def _login_password_step(client, username, password):
    return await client.post(
        "/admin/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )


async def _csrf_from(client, path):
    page = await client.get(path)
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match, f"no CSRF token rendered on {path}"
    return match.group(1)


def _stored_secret(admin_app, username):
    with admin_app.state.session_factory() as db:
        staff = get_staff(db, username)
        return decrypt_totp_secret(staff.mfa_secret_enc, secret_key=SECRET_KEY)


def _mfa_enrolled(admin_app, username):
    with admin_app.state.session_factory() as db:
        return get_staff(db, username).mfa_enrolled


# --- Behaviour 1 --------------------------------------------------------


async def test_get_renders_the_qr_and_secret_and_leaves_the_account_unenrolled(
    admin_app, client, owes_enrolment
):
    username, password = owes_enrolment
    await _login_password_step(client, username, password)

    response = await client.get("/admin/enrol", follow_redirects=False)

    assert response.status_code == 200
    assert "<svg" in response.text
    secret = _stored_secret(admin_app, username)
    assert secret in response.text
    assert _mfa_enrolled(admin_app, username) is False


# --- Behaviour 2 --------------------------------------------------------


async def test_a_correct_code_completes_enrolment_and_shows_the_recovery_codes(
    admin_app, client, owes_enrolment
):
    username, password = owes_enrolment
    await _login_password_step(client, username, password)
    token = await _csrf_from(client, "/admin/enrol")
    secret = _stored_secret(admin_app, username)
    code = pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(int(time.time()))

    response = await client.post(
        "/admin/enrol",
        data={"code": code, "csrf_token": token},
        follow_redirects=False,
    )

    assert response.status_code == 200
    codes = re.findall(r"<li>([^<]+)</li>", response.text)
    assert len(codes) == RECOVERY_CODE_COUNT
    assert _mfa_enrolled(admin_app, username) is True


# --- Behaviour 3 --------------------------------------------------------


async def test_a_wrong_code_re_renders_with_an_error_and_keeps_the_same_secret(
    admin_app, client, owes_enrolment
):
    """The subtle one: a fresh secret on every failed attempt would
    invalidate the code the user just scanned and make enrolment impossible
    to complete. Assert secret stability by decrypting the stored secret
    before and after the failed POST and comparing - not by comparing
    displayed text, which a mutant could fake without touching storage."""
    username, password = owes_enrolment
    await _login_password_step(client, username, password)
    token = await _csrf_from(client, "/admin/enrol")
    secret_before = _stored_secret(admin_app, username)

    response = await client.post(
        "/admin/enrol",
        data={"code": "000000", "csrf_token": token},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "notice--error" in response.text
    assert _mfa_enrolled(admin_app, username) is False
    secret_after = _stored_secret(admin_app, username)
    assert secret_after == secret_before
    # The re-rendered QR/secret must correspond to that same, unchanged
    # secret - not merely leave storage untouched while showing something
    # else.
    assert secret_before in response.text


# --- Behaviour 4 --------------------------------------------------------


async def test_an_already_enrolled_account_is_redirected_away_not_shown_a_traceback(
    client, onboarded
):
    """E-1's C2 guard surfacing at the page: an already-enrolled account
    must never be offered re-enrolment through this page, since that is
    exactly the account-takeover path begin_mfa_enrolment's
    MfaAlreadyEnrolledError exists to close. A fresh password login for an
    already-enrolled account leaves a valid pending login in the session
    (login() always sets one, regardless of which page it then redirects
    to) - typing /admin/enrol directly with that pending login must not
    produce a 500 or present a fresh QR code."""
    username, password, secret, _ = onboarded
    await _login_password_step(client, username, password)

    response = await client.get("/admin/enrol", follow_redirects=False)

    assert response.status_code in (302, 307)
    assert not response.headers["location"].rstrip("/").endswith("/admin/enrol")


# --- Behaviour 5 --------------------------------------------------------


async def test_a_submission_without_a_csrf_token_is_refused(
    admin_app, client, owes_enrolment
):
    """Uses a *correct* code, not a wrong one: complete_mfa_enrolment also
    returns 400 for a wrong code, so a CSRF check that had been disabled
    would still coincidentally produce 400 for "000000" and this test would
    not notice. Only a code that would otherwise succeed proves the CSRF
    check, rather than the code check, is what refused the request."""
    username, password = owes_enrolment
    await _login_password_step(client, username, password)
    await client.get("/admin/enrol")
    secret = _stored_secret(admin_app, username)
    code = pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(int(time.time()))

    response = await client.post(
        "/admin/enrol",
        data={"code": code},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert _mfa_enrolled(admin_app, username) is False


# --- Behaviour 6 --------------------------------------------------------


async def test_after_enrolment_and_verification_the_index_is_reachable(
    admin_app, client, owes_enrolment, monkeypatch
):
    """The full gauntlet, end to end: change-password (done by the
    fixture) -> enrol -> verify -> /admin/. Enrolling alone does not
    establish a session - the account still owes the second-factor
    challenge at /admin/verify - so this exercises both steps.

    complete_mfa_enrolment records the counter it accepted as
    mfa_last_counter, and verify_totp then refuses that same counter on the
    very next login - so the login code has to come from a later time step
    than the enrolment code, not the same one. admin.views reads the clock
    through the shared `time` module, so patching `time.time` there moves
    both calls without needing a real 30-second sleep.
    """
    username, password = owes_enrolment
    await _login_password_step(client, username, password)
    token = await _csrf_from(client, "/admin/enrol")
    secret = _stored_secret(admin_app, username)

    base = int(time.time())
    monkeypatch.setattr(views_time, "time", lambda: base)
    enrol_code = pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(base)
    enrol_response = await client.post(
        "/admin/enrol",
        data={"code": enrol_code, "csrf_token": token},
        follow_redirects=False,
    )
    assert enrol_response.status_code == 200

    verify_token = await _csrf_from(client, "/admin/verify")
    later = base + 2 * TOTP_INTERVAL
    monkeypatch.setattr(views_time, "time", lambda: later)
    verify_code = pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(later)
    verify_response = await client.post(
        "/admin/verify",
        data={"code": verify_code, "csrf_token": verify_token},
        follow_redirects=False,
    )
    assert verify_response.status_code == 302

    index = await client.get("/admin/", follow_redirects=False)
    assert index.status_code == 200
