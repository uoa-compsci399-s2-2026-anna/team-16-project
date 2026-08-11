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
    RECOVERY_CODE_COUNT,
    begin_mfa_enrolment,
    complete_mfa_enrolment,
    count_active_admins,
    create_staff,
    deactivate_staff,
    get_staff,
    pending_totp_device,
    set_password,
)
from admin.auth import PENDING_LOGIN_TTL_SECONDS
from admin.bootstrap import BOOTSTRAP_USERNAMES, ensure_bootstrap_admins
from admin.security import decrypt_totp_secret
from admin.totp import TOTP_INTERVAL
from admin.views import time as views_time

from tests.admin.conftest import _cleanup_staff_named

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
    """The secret of the enrolment in progress.

    Reads the *unconfirmed* `staff_totp_device` row rather than a column on
    `staff`: contract v1.13 moved TOTP secrets onto their own table so an
    account can enrol a second phone before losing the first. On this page
    there is only ever one device - it is the onboarding enrolment - but
    asking for the pending one is what keeps that true by construction.
    """
    with admin_app.state.session_factory() as db:
        device = pending_totp_device(db, username)
        assert device is not None, "no enrolment in progress for this account"
        return decrypt_totp_secret(device.secret_enc, secret_key=SECRET_KEY)


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
    def _delete():
        # The audit entries as well as the rows, through the shared teardown.
        # These walks drive the real change-password page, which records its
        # change now that an actor is identified there, and audit_log has no
        # foreign key to staff - so deleting the account alone leaves the entry
        # behind in the one shared test database, where test_modelviews.py and
        # test_taxonomy_rules.py read the whole table. It runs before the walk
        # as well as after, which is why the helper tolerates a name with no
        # row.
        _cleanup_staff_named(admin_app, *BOOTSTRAP_USERNAMES)

    _delete()
    yield
    _delete()


@pytest.fixture
def owes_enrolment(admin_app):
    """An account past the forced password change but not yet MFA-enrolled.

    Copied from tests/admin/test_enrol_page.py's fixture of the same name,
    for the reason every other fixture in this file is copied rather than
    imported. This is the state ``AdminAuth.login`` routes to
    ``admin:view-enrol``: ``must_change_password`` cleared, no confirmed
    authenticator yet.
    """
    username = f"u{uuid.uuid4().hex[:10]}"
    factory = admin_app.state.session_factory
    with factory() as db:
        create_staff(db, username=username, display_name="Test User", actor="test")
        db.flush()
        set_password(db, username, "a-long-enough-password")
        db.commit()
    yield username, "a-long-enough-password"
    _cleanup_staff_named(admin_app, username)


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
        _, password = create_staff(db, username=username, display_name="Test User", actor="test")
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
    _cleanup_staff_named(admin_app, username)


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

    # Enrolment establishes the session, so the walk ends here rather than at
    # a further /admin/verify challenge. It is one hop shorter than it was and
    # still proves the same thing: a bootstrapped account, driven only through
    # HTTP with the password the CLI printed, reaches the panel.
    index = await client.get("/admin/", follow_redirects=False)
    assert index.status_code == 200, (
        "a bootstrapped account should reach the panel once it has changed "
        "its password and enrolled"
    )
    assert "/admin/verify" not in enrol_response.text, (
        "the recovery-codes page should not send the user to a challenge the "
        "replay counter would refuse"
    )


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

    C1's real shape is an *interleaving*, and a walk that only submits wrong
    codes once and then retries the password afterwards never reaches the
    bug: by the time such a retry happens the account is already locked, and
    authenticate_password's own ``throttle.is_locked`` check (admin/auth.py)
    short-circuits before the mutated line - a correct password after that
    point is refused for a reason that has nothing to do with whether it
    clears the counter. (An earlier version of this walk had exactly that
    shape, and a reinstated C1 - ``throttle.clear(username)`` added to a
    correct password step - passed it unchanged.)

    The actual attack resubmits the correct password *before every batch* of
    guesses, each batch staying under the lock threshold on its own: submit
    the password (which, under C1, zeroes the shared counter), then
    ``max_failures - 1`` wrong codes (never enough on their own to lock),
    then the password again, and so on. With C1 fixed, nothing clears the
    counter on a correct password, so the count accumulates across rounds
    regardless of how many times the password is resubmitted, and the
    account locks partway through - this is asserted directly by breaking
    out of the round loop the moment ``throttle.is_locked`` goes true, then
    failing loudly if every round ran out first. Confirmed in a sandboxed
    reinstatement of C1 (``throttle.clear(username)`` added right before
    ``authenticate_password`` returns its PendingLogin on success): with
    that change present, every round's four guesses land on a freshly
    zeroed counter, the account never locks across all ``max_failures``
    rounds, and the ``assert locked is True`` below is the one that fails.
    """
    username, password, secret, _ = onboarded
    max_failures = admin_app.state.settings.login_max_failures
    throttle = admin_app.state.throttle
    key = username.strip().casefold()
    # Each round's guesses stay under the threshold by themselves - only
    # accumulation *across* rounds, which C1 defeats by zeroing the counter
    # at the start of every round, can lock the account.
    guesses_per_round = max_failures - 1

    locked = False
    token = None
    for round_number in range(max_failures):
        login_response = await _login_password_step(client, username, password)
        assert login_response.status_code == 302, (
            f"round {round_number}: the password step was refused before "
            "the account should have locked yet"
        )
        token = await _csrf_from(client, "/admin/verify")
        for _ in range(guesses_per_round):
            response = await client.post(
                "/admin/verify",
                data={"code": "000000", "csrf_token": token},
                follow_redirects=False,
            )
            assert response.status_code == 400
        if throttle.is_locked(username, now=time.time()):
            locked = True
            break

    assert locked is True, (
        "the account never locked across repeated password-reset-then-guess "
        "rounds - something is resetting the shared throttle counter on a "
        "correct password (E-1's C1)"
    )
    assert throttle._attempts[key].count >= max_failures

    now = time.time()
    monkeypatch.setattr(views_time, "time", lambda: now)

    # A *correct* code is refused too - the lock, not the code, is what is
    # refusing this request now.
    correct_code_while_locked = pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(int(now))
    still_locked = await client.post(
        "/admin/verify",
        data={"code": correct_code_while_locked, "csrf_token": token},
        follow_redirects=False,
    )
    assert still_locked.status_code == 400

    index_while_locked = await client.get("/admin/", follow_redirects=False)
    assert index_while_locked.status_code in (302, 307)

    # The attack the shared counter exists to close, restated once more at
    # the fully-locked boundary: holding the real password does not unlock
    # the account, and does not buy a fresh pending login to try more codes
    # against.
    retry_with_correct_password = await _login_password_step(client, username, password)
    assert retry_with_correct_password.status_code == 400
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
            db, username=username, display_name="Half Onboarded", actor="test"
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
        _cleanup_staff_named(admin_app, username)


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
    admin/backend.py's ``_may_open_pre_login_page``.

    The three pre-login pages are probed *before* ``/admin/`` is ever
    requested, and that ordering is load-bearing, not incidental. ``/admin/``
    is handled by ``authenticate()``'s main branch, which independently
    re-reads ``is_active`` and clears the session cookie on its own - a code
    path the historical defect here never touched. An earlier version of
    this walk checked ``/admin/`` first: that call correctly cleared the
    session (the main branch was never broken), so by the time the three
    pre-login pages were probed afterwards the client no longer carried a
    session cookie at all, and each one redirected for that reason -
    "no session" - rather than because ``_may_open_pre_login_page`` itself
    re-checked ``is_active`` on the already-logged-in path. All five walks
    in this file passed unchanged against a reinstated
    ``already_logged_in`` short-circuit for exactly that reason. Probing the
    three pages first, with the cookie ``verify_response`` established and
    untouched since, closes that gap: confirmed in a sandboxed
    reinstatement of the short-circuit, where ``GET /admin/change-password``
    returned 200 for this deactivated session and the assertion on
    ``response.status_code`` below, for that path, is the one that failed.
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

    # Probed first, on the exact cookie the login above set - before any
    # request that would clear it as a side effect through a different code
    # path. See the docstring above: this ordering is the fix.
    for path in ("/admin/verify", "/admin/change-password", "/admin/enrol"):
        response = await client.get(path, follow_redirects=False)
        assert response.status_code in (302, 307), path
        assert "/admin/login" in response.headers["location"], path

    # The ordinary dashboard route refuses too, through its own, separate
    # is_active re-check in authenticate()'s main branch.
    refused = await client.get("/admin/", follow_redirects=False)
    assert refused.status_code in (302, 307)
    assert "/admin/login" in refused.headers["location"]


# --- Walk 6: a password change mid-session refuses the very next request ---


async def test_walk6_a_password_change_mid_session_refuses_its_very_next_request(
    admin_app, client, onboarded, monkeypatch
):
    """A fully onboarded account, logged in for real through both steps, has
    its password changed by another administrator mid-session via the one
    real service function that performs it (``set_password``) - and the very
    next request, not merely a fresh login attempt, is refused.

    This is the walk docs/interfaces.md's v0.8 contract text ("ends that
    account's live sessions immediately") has to survive, and the reason a
    session-dict test cannot stand in for it: a password-only change clears
    neither ``must_change_password`` (``set_password`` itself clears it, so
    it was already False) nor ``mfa_enrolled`` (untouched), so nothing about
    this account's *onboarding state* changes. Before ``session_generation``
    existed - the v0.7 known limitation this task's contract edit replaces -
    this exact session kept working without interruption: "Issue a new
    password only: None. 200 throughout, no interruption." It is the
    generation comparison in ``AdminAuth.authenticate()`` that closes this,
    not either onboarding flag, which is why the walk changes only the
    password and asserts the dashboard - not an onboarding page - refuses.
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

    # Out of band, as another administrator resetting a compromised
    # colleague's account would: the one real service function, not a
    # hand-edited row.
    with admin_app.state.session_factory() as db:
        set_password(db, username, "a-different-freshly-chosen-password")
        db.commit()

    # The stale cookie cannot open /admin/verify either: _may_open_pre_login_page
    # compares generation on the already-logged-in way in too (a later fix,
    # prompted by review - see its docstring's "so:" paragraph), so this is
    # refused directly rather than admitted and bounced by VerifyView itself.
    verify_probe = await client.get("/admin/verify", follow_redirects=False)
    assert verify_probe.status_code in (302, 307)
    assert "/admin/login" in verify_probe.headers["location"]

    # The dashboard route refuses too: authenticate()'s main branch compares
    # the session's stamped generation against the row's current one, finds a
    # mismatch, clears the session and refuses - exactly what the missing
    # check let through before this task.
    refused = await client.get("/admin/", follow_redirects=False)
    assert refused.status_code in (302, 307)
    assert "/admin/login" in refused.headers["location"]


# --- Walk 7: an enrolment slower than the pending login still completes -----


async def test_walk7_an_enrolment_slower_than_the_pending_login_still_shows_its_recovery_codes(
    admin_app, client, owes_enrolment, monkeypatch
):
    """A first-time user takes longer than ``PENDING_LOGIN_TTL_SECONDS`` to
    install an authenticator, scan the QR and type a code - and still lands on
    the recovery codes.

    This is the walk the reported defect takes, and it is a seam defect of
    exactly the shape this file exists for. The pending login's ``expires_at``
    is written once, by ``AdminAuth.login``, and refreshed in exactly one
    other place - ``ChangePasswordView`` (admin/views.py), whose own comment
    already names the hazard: "Choosing a password and then scanning a QR
    routinely outlasts the five-minute pending login". That refresh covers the
    password step. Nothing covers the *enrolment* step, which is the longer
    one: it is not typing, it is installing an app on a second device.

    When the deadline passes mid-scan, ``_may_open_pre_login_page`` refuses
    /admin/enrol and sqladmin's ``login_required`` turns that refusal into a
    302 to /admin/login - so ``EnrolView.enrol`` never runs, and the page that
    shows the recovery codes is never reached. **The codes are shown once and
    cannot be recovered**, so a bounce here is not merely an extra login: it
    is the one moment the account's fallback credential is legible, spent on a
    redirect.

    The assertions below are deliberately not just "200". A fix that let the
    request through without finishing the enrolment, or that finished it
    without rendering the codes, would leave the user in the same place; so
    the walk pins the confirmed device, the five stored code hashes, and the
    codes themselves being on the page.
    """
    username, password = owes_enrolment

    base = int(time.time())
    monkeypatch.setattr(views_time, "time", lambda: base)

    login_response = await _login_password_step(client, username, password)
    assert login_response.status_code == 302
    assert login_response.headers["location"].endswith("/admin/enrol")

    enrol_token = await _csrf_from(client, "/admin/enrol")
    secret = _stored_secret(admin_app, username)

    # The slow part, and the whole point of the walk: the QR is on screen and
    # the person is across the room with a phone, an app store and a camera.
    # One second past the deadline is enough to show the seam; a real first
    # enrolment is not close to it.
    slow = base + PENDING_LOGIN_TTL_SECONDS + 1
    monkeypatch.setattr(views_time, "time", lambda: slow)

    enrol_response = await client.post(
        "/admin/enrol",
        data={
            "code": pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(slow),
            "csrf_token": enrol_token,
        },
        follow_redirects=False,
    )
    assert enrol_response.status_code == 200, (
        "a correct code typed after the pending login lapsed was bounced to "
        "/admin/login, spending the one render of the recovery codes"
    )

    with admin_app.state.session_factory() as db:
        staff = get_staff(db, username)
        assert staff.mfa_enrolled, "the authenticator was not confirmed"
        assert [d.enrolled_at is not None for d in staff.totp_devices] == [True]
        stored = db.execute(
            text("SELECT COUNT(*) FROM staff_recovery_code WHERE staff_id = :i"),
            {"i": staff.id},
        ).scalar()
    assert stored == RECOVERY_CODE_COUNT, (
        "the account finished enrolment without a recovery path"
    )

    codes = re.findall(r"<li>([A-Za-z0-9-]+)</li>", enrol_response.text)
    assert len(codes) == RECOVERY_CODE_COUNT, (
        "the recovery codes were not rendered; they exist only in this response"
    )
