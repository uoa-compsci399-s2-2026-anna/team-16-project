"""admin.views.VerifyView - the second factor.

Contract: docs/interfaces.md 8.3.
"""

import time

import pyotp
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from admin.accounts import (
    begin_mfa_enrolment,
    complete_mfa_enrolment,
    consume_recovery_code,
    create_staff,
    set_password,
    unused_recovery_code_count,
)
from admin.app import create_app
from admin.auth import SESSION_KEY
from admin.backend import PENDING_SESSION_KEY
from admin.totp import TOTP_INTERVAL

pytestmark = [pytest.mark.db, pytest.mark.asyncio]

SECRET_KEY = "test-secret-key-not-used-anywhere-real"


# The app and client fixtures live in tests/conftest.py as of Task 3 - the
# app fixture is named `admin_app` and disposes its engine on teardown. Do
# not redefine them here; Tasks 4 onward all share those definitions, and a
# private copy would leak an engine per test.


@pytest.fixture
def onboarded(admin_app):
    """A fully set-up account. Returns (username, password, totp secret, codes).

    Committed rather than rolled back: the app under test opens its own
    sessions and cannot see an uncommitted fixture. Each test uses a unique
    username so the rows cannot collide.
    """
    import uuid

    username = f"u{uuid.uuid4().hex[:10]}"
    factory = admin_app.state.session_factory
    with factory() as db:
        _, password = create_staff(db, username=username, display_name="Test User")
        db.flush()
        set_password(db, username, "a-long-enough-password")
        secret, _ = begin_mfa_enrolment(db, username, secret_key=SECRET_KEY)
        # Backdated two TOTP steps: complete_mfa_enrolment stores this step's
        # counter as mfa_last_counter, and verify_totp refuses to accept the
        # same or an earlier counter again (replay protection). Enrolling
        # with a code for "now" and then, moments later in wall-clock time,
        # logging in with a fresh code also for "now" lands in the same
        # 30-second step nearly every time a fast test suite runs the two
        # back to back - the login code would then collide with the
        # enrolment counter and be refused as a replay of itself, which is
        # not what any of the tests below are checking for.
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
        db.execute(
            __import__("sqlalchemy").text("DELETE FROM staff WHERE username = :u"),
            {"u": username},
        )
        db.commit()


async def _login_password_step(client, username, password):
    return await client.post(
        "/admin/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )


async def _csrf_from(client, path):
    page = await client.get(path)
    import re

    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match, f"no CSRF token rendered on {path}"
    return match.group(1)


async def test_the_verify_page_is_reachable_after_the_password_step(
    client, onboarded
):
    username, password, _, _ = onboarded

    response = await _login_password_step(client, username, password)

    assert response.status_code == 302
    assert "/admin/verify" in response.headers["location"]


async def test_the_verify_page_is_not_reachable_without_a_pending_login(client):
    """Typing the URL must not present a second-factor prompt to someone who
    never passed the password step."""
    response = await client.get("/admin/verify", follow_redirects=False)

    assert response.status_code in (302, 307)
    assert "/admin/login" in response.headers["location"]


async def test_a_correct_totp_completes_the_login(client, onboarded):
    username, password, secret, _ = onboarded
    await _login_password_step(client, username, password)
    token = await _csrf_from(client, "/admin/verify")

    response = await client.post(
        "/admin/verify",
        data={
            "code": pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(int(time.time())),
            "csrf_token": token,
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    index = await client.get("/admin/", follow_redirects=False)
    assert index.status_code == 200


async def test_a_wrong_totp_is_refused_and_establishes_no_session(client, onboarded):
    username, password, _, _ = onboarded
    await _login_password_step(client, username, password)
    token = await _csrf_from(client, "/admin/verify")

    response = await client.post(
        "/admin/verify",
        data={"code": "000000", "csrf_token": token},
        follow_redirects=False,
    )

    assert response.status_code == 400
    index = await client.get("/admin/", follow_redirects=False)
    assert index.status_code in (302, 307)


async def test_a_recovery_code_completes_the_login_and_is_then_spent(
    admin_app, client, onboarded
):
    username, password, _, codes = onboarded
    await _login_password_step(client, username, password)
    token = await _csrf_from(client, "/admin/verify")

    response = await client.post(
        "/admin/verify",
        data={"code": codes[0], "csrf_token": token},
        follow_redirects=False,
    )

    assert response.status_code == 302
    with admin_app.state.session_factory() as db:
        assert unused_recovery_code_count(db, username) == len(codes) - 1


async def test_one_wrong_submission_records_exactly_one_throttle_failure(
    admin_app, client, onboarded
):
    """The routing fix's own regression test.

    Before routing on shape, a submission that was neither a valid TOTP nor
    a valid recovery code went through both authenticate_totp and
    authenticate_recovery_code - both share the login throttle counter
    (contract 8.3: password and TOTP failures share one counter), so a
    single wrong submission recorded two failures instead of one. With
    LOGIN_MAX_FAILURES=5 and no email-based password reset, that locks a
    fumbling staff member out at roughly half the configured allowance.
    Asserted directly off the throttle's own state, not through observed
    lockout behaviour - nothing else in this suite inspects the throttle."""
    username, password, _, _ = onboarded
    await _login_password_step(client, username, password)
    token = await _csrf_from(client, "/admin/verify")

    response = await client.post(
        "/admin/verify",
        data={"code": "999999", "csrf_token": token},
        follow_redirects=False,
    )

    assert response.status_code == 400
    key = username.strip().casefold()
    assert admin_app.state.throttle._attempts[key].count == 1


async def test_a_valid_recovery_code_still_completes_the_login_via_the_router(
    admin_app, client, onboarded
):
    """Routing on shape must not break the path that exists for someone
    with no authenticator left - a twelve-character recovery code must still
    reach authenticate_recovery_code, not be misrouted to authenticate_totp
    and rejected on shape alone."""
    username, password, _, codes = onboarded
    await _login_password_step(client, username, password)
    token = await _csrf_from(client, "/admin/verify")

    response = await client.post(
        "/admin/verify",
        data={"code": codes[0], "csrf_token": token},
        follow_redirects=False,
    )

    assert response.status_code == 302
    index = await client.get("/admin/", follow_redirects=False)
    assert index.status_code == 200


async def test_a_valid_totp_still_completes_the_login_via_the_router(
    client, onboarded
):
    """The other half of the router's happy path: a six-digit code must
    still reach authenticate_totp."""
    username, password, secret, _ = onboarded
    await _login_password_step(client, username, password)
    token = await _csrf_from(client, "/admin/verify")

    response = await client.post(
        "/admin/verify",
        data={
            "code": pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(int(time.time())),
            "csrf_token": token,
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    index = await client.get("/admin/", follow_redirects=False)
    assert index.status_code == 200


async def test_an_already_logged_in_visitor_is_sent_on_not_shown_the_form(
    client, onboarded
):
    """authenticate() admits a live, fully onboarded SESSION_KEY to
    /admin/verify because its job is reachability, not usefulness (Task 4's
    contract). There is no second factor left to supply for an already
    completed login, and authenticate_totp/authenticate_recovery_code both
    require a real PendingLogin - handing either a bare username raises
    TypeError - so the page itself has to send such a visitor on rather than
    render a form that could never be completed."""
    username, password, secret, _ = onboarded
    await _login_password_step(client, username, password)
    token = await _csrf_from(client, "/admin/verify")
    await client.post(
        "/admin/verify",
        data={
            "code": pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(int(time.time())),
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    # Now fully logged in: SESSION_KEY is set and PENDING_SESSION_KEY is gone.

    response = await client.get("/admin/verify", follow_redirects=False)
    assert response.status_code == 302
    assert "/admin/login" not in response.headers["location"]

    followed = await client.get("/admin/verify", follow_redirects=True)
    assert followed.status_code == 200
    assert followed.url.path == "/admin/"


async def test_running_low_on_recovery_codes_shows_the_interstitial(
    admin_app, client, onboarded
):
    """Contract 8.3: "The panel prompts for regeneration once 2 codes
    remain." This interstitial is currently the whole of that prompt, since
    a regenerate_recovery_codes function does not exist yet - it must
    actually render, not just exist as dead code, once the count reaches
    the threshold.

    Two codes are spent directly first, leaving three; the login below
    spends a third, landing exactly on LOW_RECOVERY_CODE_THRESHOLD (2)."""
    username, password, _, codes = onboarded
    with admin_app.state.session_factory() as db:
        for code in codes[:2]:
            assert consume_recovery_code(db, username, code) is True
        db.commit()

    await _login_password_step(client, username, password)
    token = await _csrf_from(client, "/admin/verify")

    response = await client.post(
        "/admin/verify",
        data={"code": codes[2], "csrf_token": token},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "running low" in response.text.lower()
    with admin_app.state.session_factory() as db:
        assert unused_recovery_code_count(db, username) == 2
    # The interstitial, not a redirect - the session must still be
    # established, since the login itself succeeded.
    established = await client.get("/admin/", follow_redirects=False)
    assert established.status_code == 200


async def test_a_submission_without_a_csrf_token_is_refused(client, onboarded):
    username, password, secret, _ = onboarded
    await _login_password_step(client, username, password)

    response = await client.post(
        "/admin/verify",
        data={
            "code": pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(int(time.time()))
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    index = await client.get("/admin/", follow_redirects=False)
    assert index.status_code in (302, 307)
