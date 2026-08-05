"""End-to-end HTTP walks across the whole admin panel.

Contract: docs/interfaces.md 8.3.

Task 8's entire reason to exist: the previous plan's final review found that
every one of its Critical defects was a *seam* defect - a property one module
held that the pair did not, invisible to a task reviewed against its own
brief in isolation. Every other file in tests/admin/ tests one module (or one
page) at a time, most of them by handing the app a hand-built or forged
session state and making a single request against it. The tests below never
do that: every account starts from create_staff or ensure_bootstrap_admins,
and every step is a real HTTP request using the cookie the previous one
returned - login, then whichever onboarding page comes next, then the one
after that - because that is exactly the shape the seam defects took.

No new production code accompanies this file. Where a walk cannot pass
without one, that is reported rather than worked around.
"""

import re
import time
import uuid

import pyotp
import pytest
from sqlalchemy import text

from admin.accounts import (
    begin_mfa_enrolment,
    complete_mfa_enrolment,
    count_active_admins,
    create_staff,
    deactivate_staff,
    get_staff,
    set_password,
)
from admin.bootstrap import BOOTSTRAP_USERNAMES, ensure_bootstrap_admins
from admin.security import decrypt_totp_secret
from admin.totp import TOTP_INTERVAL
from admin.views import time as views_time

pytestmark = [pytest.mark.db, pytest.mark.asyncio]

SECRET_KEY = "test-secret-key-not-used-anywhere-real"


# --- Shared helpers, copied from the page test files rather than imported -
# pytest fixtures and helpers are not shared across modules without a
# conftest.py move, and Tasks 4-7 each made the same choice. -----------------


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


@pytest.fixture
def clean_bootstrap_slate(admin_app):
    """Guarantee no BOOTSTRAP_USERNAMES rows exist before or after this test.

    ensure_bootstrap_admins is a no-op once *any* active administrator
    exists (admin/bootstrap.py), so a row left behind by an earlier,
    interrupted run of this same walk would quietly turn "a fresh database"
    - the premise walk 1 exists to exercise - into a false one, and the walk
    would then pass (or fail) for a reason that has nothing to do with the
    onboarding gate it is meant to be driving.
    """
    factory = admin_app.state.session_factory

    def _delete():
        with factory() as db:
            db.execute(
                text("DELETE FROM staff WHERE username IN (:a, :b)"),
                {"a": BOOTSTRAP_USERNAMES[0], "b": BOOTSTRAP_USERNAMES[1]},
            )
            db.commit()

    _delete()
    yield
    _delete()


@pytest.fixture
def onboarded(admin_app):
    """A fully set-up account: past both onboarding steps.

    Copied from tests/admin/test_verify_page.py's fixture of the same name.
    Backdated by 4 TOTP steps rather than that file's 2, since walks 2, 3
    and 5 below each need at least one more later, still-unused code after
    the one this fixture spends on enrolment - complete_mfa_enrolment
    records the counter it accepts, and verify_totp refuses to accept that
    counter, or an earlier one, again.
    """
    username = f"u{uuid.uuid4().hex[:10]}"
    factory = admin_app.state.session_factory
    with factory() as db:
        _, password = create_staff(db, username=username, display_name="Test User")
        db.flush()
        set_password(db, username, "a-long-enough-password")
        secret, _ = begin_mfa_enrolment(db, username, secret_key=SECRET_KEY)
        now = int(time.time()) - 4 * TOTP_INTERVAL
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


# --- Walk 1: the full gauntlet from a bootstrapped, account-less database ---


async def test_walk1_bootstrap_login_change_password_enrol_index(
    admin_app, client, clean_bootstrap_slate, monkeypatch
):
    """Bootstrap a database with no accounts, log in as ``admin`` with the
    password ``ensure_bootstrap_admins`` prints, and walk forced password
    change -> enrolment -> index.

    Deliberately never hand-builds the account: the whole point is that
    every state along the way - the pending login the password step leaves,
    the row change_password's own POST produces, the row enrol's POST
    produces - is whatever the real service functions actually write, in
    the order a real deployment would produce them. This is the walk that
    would have caught Task 4 round 5's worst defect in one shot: that
    defect gated the onboarding pages on SESSION_KEY, a value only a
    *completed* login produces, so a fresh bootstrap could never be
    onboarded at all - the very first page after the password step would
    have bounced straight back to /admin/login instead of rendering. Against
    that defect reinstated, the assertion on ``change_page`` below is the
    one that fails.
    """
    factory = admin_app.state.session_factory
    with factory() as db:
        assert count_active_admins(db) == 0
        created = ensure_bootstrap_admins(db)
        db.commit()
    passwords = dict(created)
    username, password = "admin", passwords["admin"]

    login_response = await _login_password_step(client, username, password)
    assert login_response.status_code == 302
    assert "/admin/change-password" in login_response.headers["location"]

    # A fresh pending login only, no SESSION_KEY yet - confirm the gate
    # actually lets it in rather than bouncing back to /admin/login.
    change_page = await client.get("/admin/change-password", follow_redirects=False)
    assert change_page.status_code == 200

    token = await _csrf_from(client, "/admin/change-password")
    change_response = await client.post(
        "/admin/change-password",
        data={
            "password": "a-freshly-chosen-password",
            "confirm": "a-freshly-chosen-password",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert change_response.status_code == 302
    assert change_response.headers["location"].endswith("/admin/enrol")
    with factory() as db:
        assert get_staff(db, username).must_change_password is False

    enrol_token = await _csrf_from(client, "/admin/enrol")
    secret = _stored_secret(admin_app, username)
    base = int(time.time())
    monkeypatch.setattr(views_time, "time", lambda: base)
    enrol_code = pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(base)
    enrol_response = await client.post(
        "/admin/enrol",
        data={"code": enrol_code, "csrf_token": enrol_token},
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


# --- Walk 2: log out, log back in with password + TOTP, reach the index ----


async def test_walk2_logout_then_password_and_totp_login_reaches_index(
    client, onboarded, monkeypatch
):
    """Log out, log back in through the whole two-step handshake, and land on
    the index directly.

    A real /admin/logout between the two logins, and a fresh, later TOTP
    code for the second one rather than a replay of the first's - the trap
    this plan has hit before (see the ``onboarded`` fixture's docstring):
    complete_mfa_enrolment and verify_totp both refuse to accept the same
    counter twice, so logging back in with a code for the same 30-second
    step as the first login would fail for that reason, not the one this
    walk exists to check.
    """
    username, password, secret, _ = onboarded
    base = int(time.time())
    monkeypatch.setattr(views_time, "time", lambda: base)

    first_login = await _login_password_step(client, username, password)
    assert first_login.status_code == 302
    token = await _csrf_from(client, "/admin/verify")
    first_code = pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(base)
    first_verify = await client.post(
        "/admin/verify",
        data={"code": first_code, "csrf_token": token},
        follow_redirects=False,
    )
    assert first_verify.status_code == 302
    first_index = await client.get("/admin/", follow_redirects=False)
    assert first_index.status_code == 200

    logout = await client.get("/admin/logout", follow_redirects=False)
    assert logout.status_code in (302, 307)
    after_logout = await client.get("/admin/", follow_redirects=False)
    assert after_logout.status_code in (302, 307)
    assert "/admin/login" in after_logout.headers["location"]

    later = base + 2 * TOTP_INTERVAL
    monkeypatch.setattr(views_time, "time", lambda: later)
    second_login = await _login_password_step(client, username, password)
    assert second_login.status_code == 302
    assert "/admin/verify" in second_login.headers["location"]

    second_token = await _csrf_from(client, "/admin/verify")
    second_code = pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(later)
    second_verify = await client.post(
        "/admin/verify",
        data={"code": second_code, "csrf_token": second_token},
        follow_redirects=False,
    )
    assert second_verify.status_code == 302

    # Reach the index directly - no extra hop, no leftover redirect from the
    # first session.
    second_index = await client.get("/admin/", follow_redirects=False)
    assert second_index.status_code == 200


# --- Walk 3: the attacker walk ----------------------------------------------


async def test_walk3_wrong_totp_codes_lock_the_account_and_a_correct_password_cannot_reset_the_counter(
    admin_app, client, onboarded, monkeypatch
):
    """E-1's C1, expressed at the HTTP layer - "the single most valuable test
    in this plan" per the brief.

    An attacker who already holds a valid password submits wrong
    second-factor codes until the account locks. The original bug this
    walk exists to catch let such an attacker reset the shared throttle
    counter before every guess simply by re-submitting the correct
    password, turning a nominally 5-attempt lock into unlimited guesses
    against a six-digit code. So this does not stop at the eventual 400/302
    split: it inspects the throttle's own failure count after every wrong
    submission (not just the final refusal, which a mutant could satisfy by
    locking correctly while still letting the count wander), confirms the
    account is genuinely locked - a *correct* TOTP code is refused too, not
    just wrong ones - and then, the crux of it, re-submits the real
    password and asserts that neither the login succeeds nor the counter
    moves.
    """
    username, password, secret, _ = onboarded
    max_failures = admin_app.state.settings.login_max_failures
    throttle = admin_app.state.throttle
    key = username.strip().casefold()

    login_response = await _login_password_step(client, username, password)
    assert login_response.status_code == 302
    token = await _csrf_from(client, "/admin/verify")

    for attempt in range(1, max_failures + 1):
        response = await client.post(
            "/admin/verify",
            data={"code": "000000", "csrf_token": token},
            follow_redirects=False,
        )
        assert response.status_code == 400
        assert throttle._attempts[key].count == attempt

    now = time.time()
    assert throttle.is_locked(username, now=now) is True

    # A *correct* code is refused too - the lock, not the code, is what is
    # refusing this request now, and the attempt does not add a further
    # failure on top of the lock that already exists.
    monkeypatch.setattr(views_time, "time", lambda: now)
    correct_code_while_locked = pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(int(now))
    still_locked = await client.post(
        "/admin/verify",
        data={"code": correct_code_while_locked, "csrf_token": token},
        follow_redirects=False,
    )
    assert still_locked.status_code == 400
    assert throttle._attempts[key].count == max_failures

    index_while_locked = await client.get("/admin/", follow_redirects=False)
    assert index_while_locked.status_code in (302, 307)

    # The attack the shared counter exists to close: holding the real
    # password does not reset it, and does not buy a fresh pending login to
    # try more codes against.
    retry_with_correct_password = await _login_password_step(client, username, password)
    assert retry_with_correct_password.status_code == 400
    assert throttle._attempts[key].count == max_failures
    assert throttle.is_locked(username, now=now) is True


# --- Walk 4: a half-onboarded account cannot reach any panel URL by typing -


async def test_walk4_a_half_onboarded_account_cannot_reach_any_panel_url_by_typing_it(
    admin_app, client
):
    """A password-only holder - passed the password step, but not yet past
    the forced password change - cannot reach the dashboard, and cannot
    reach either of the *other* two onboarding pages either: contract 8.3's
    ladder order means it does not yet owe them. Only the one page it
    actually owes, /admin/change-password, is open.

    No ModelViews are mounted yet (admin/app.py: the eleven taxonomy and
    factor tables are blocked on B's db/models.py), so /admin/ is the only
    panel URL there currently is to try against a half-onboarded account;
    the same ladder-order gate (admin/backend.py's
    _may_open_pre_login_page) is what would have to keep refusing their
    /admin/<identity>/list URLs once they exist, and this walk is what
    would need extending to cover one when it does.
    """
    username = f"u{uuid.uuid4().hex[:10]}"
    factory = admin_app.state.session_factory
    with factory() as db:
        _, password = create_staff(
            db, username=username, display_name="Half Onboarded"
        )
        db.commit()
    try:
        login_response = await _login_password_step(client, username, password)
        assert login_response.status_code == 302
        assert "/admin/change-password" in login_response.headers["location"]

        dashboard = await client.get("/admin/", follow_redirects=False)
        assert dashboard.status_code in (302, 307)
        assert "/admin/login" in dashboard.headers["location"]

        enrol = await client.get("/admin/enrol", follow_redirects=False)
        assert enrol.status_code in (302, 307)
        assert "/admin/login" in enrol.headers["location"]

        verify = await client.get("/admin/verify", follow_redirects=False)
        assert verify.status_code in (302, 307)
        assert "/admin/login" in verify.headers["location"]

        # The one page it does owe is still open - a refusal above that was
        # really "nothing is reachable yet" rather than the ladder-order gate
        # actually working would make this assertion fail too.
        change_password = await client.get(
            "/admin/change-password", follow_redirects=False
        )
        assert change_password.status_code == 200
    finally:
        with factory() as db:
            db.execute(text("DELETE FROM staff WHERE username = :u"), {"u": username})
            db.commit()


# --- Walk 5: deactivation mid-session refuses the very next request --------


async def test_walk5_deactivating_an_account_mid_session_refuses_its_very_next_request(
    admin_app, client, onboarded, monkeypatch
):
    """A fully onboarded account, logged in for real through both steps,
    deactivated by another administrator mid-session via the one real
    service function that performs deactivation (``deactivate_staff``) -
    and the very next request, not merely a fresh login attempt, is
    refused.

    The session cookie is signed but client-held, so nothing server-side
    expires it on deactivation; every gated request has to re-check
    ``is_active`` itself. This also drives the specific historical defect
    this project's own record names: "a deactivated session kept all three
    onboarding pages open" - the fix lives in the SESSION_KEY branch of
    admin/backend.py's ``_may_open_pre_login_page``, so this checks all
    three of those pages after deactivation, not just the dashboard.
    """
    username, password, secret, _ = onboarded
    base = int(time.time())
    monkeypatch.setattr(views_time, "time", lambda: base)

    login_response = await _login_password_step(client, username, password)
    assert login_response.status_code == 302
    token = await _csrf_from(client, "/admin/verify")
    code = pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(base)
    verify_response = await client.post(
        "/admin/verify",
        data={"code": code, "csrf_token": token},
        follow_redirects=False,
    )
    assert verify_response.status_code == 302

    established = await client.get("/admin/", follow_redirects=False)
    assert established.status_code == 200

    with admin_app.state.session_factory() as db:
        deactivate_staff(db, username)
        db.commit()

    refused = await client.get("/admin/", follow_redirects=False)
    assert refused.status_code in (302, 307)
    assert "/admin/login" in refused.headers["location"]

    for path in ("/admin/verify", "/admin/change-password", "/admin/enrol"):
        response = await client.get(path, follow_redirects=False)
        assert response.status_code in (302, 307), path
        assert "/admin/login" in response.headers["location"], path
