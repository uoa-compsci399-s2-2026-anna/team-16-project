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
    pending_totp_device,
    set_password,
)
from admin.security import decrypt_totp_secret
from admin.totp import TOTP_INTERVAL
from admin.views import _grouped
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
    assert _grouped(secret) in response.text  # grouped for transcription
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
    assert _grouped(secret_before) in response.text


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


# Behaviour 6 used to be test_after_enrolment_and_verification_the_index_is
# _reachable, which walked enrol -> verify -> /admin/ and needed a two-step
# clock patch to get past the replay counter. That second challenge is gone -
# enrolment establishes the session - and the property it protected is now
# asserted by test_enrolment_establishes_the_session_without_a_second_challenge
# further down this file, which additionally pins that no challenge appears.


# --- Behaviour 8 --------------------------------------------------------


async def test_a_second_get_reuses_the_secret_the_first_one_minted(
    admin_app, client, owes_enrolment
):
    """Behaviour 3's property, on the GET path this time.

    Any refresh, second tab, or Back/Forward after scanning is a second GET.
    While that re-minted, the enrolment the user had just completed on their
    phone was silently invalidated, and the code from the first QR was then
    rejected with "That code did not match. Check the authenticator has the
    right account and that the device clock is correct." - a message that
    sends them to look at their phone's clock for a fault that is ours.

    The second half of this test is the part that matters: not merely that
    the two pages agree, but that a code generated from the *first* QR still
    finishes enrolment after the second GET has been served.
    """
    username, password = owes_enrolment
    await _login_password_step(client, username, password)

    first = await client.get("/admin/enrol")
    secret = _stored_secret(admin_app, username)
    assert first.status_code == 200
    assert _grouped(secret) in first.text

    second = await client.get("/admin/enrol")

    assert second.status_code == 200
    assert _stored_secret(admin_app, username) == secret
    assert _grouped(secret) in second.text

    match = re.search(r'name="csrf_token" value="([^"]+)"', second.text)
    assert match
    code = pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(int(time.time()))
    response = await client.post(
        "/admin/enrol",
        data={"code": code, "csrf_token": match.group(1)},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert _mfa_enrolled(admin_app, username) is True


# --- Behaviour 9 --------------------------------------------------------


async def test_the_handler_refuses_an_established_session_even_if_the_gate_admits_one(
    admin_app, client, onboarded, monkeypatch
):
    """The takeover path stays closed if a future maintainer relaxes the gate.

    Today ``_may_open_pre_login_page`` makes an established SESSION_KEY
    unreachable inside EnrolView by construction - that is Task 4 round 5's
    fix, and the whole of what stands between an administrator's mid-session
    MFA reset and the evicted party re-enrolling their own authenticator
    under the cookie the reset was meant to neutralise. "Bounced to
    /admin/login" is also the confusing part of that flow and therefore the
    part most likely to be relaxed later, and VerifyView already defends
    itself at the top of its own handler while this one did not.

    So the gate is patched open here, exactly as a well-meaning relaxation
    would leave it, and the handler alone has to refuse. The strong
    assertion is the last one: no fresh secret is minted, because minting
    one under an evicted cookie *is* the takeover.
    """
    from admin.accounts import reset_mfa
    from admin.backend import AdminAuth

    username, password, _secret, codes = onboarded

    # A full login, second factor included, so SESSION_KEY is genuinely
    # established. A recovery code rather than a TOTP code: enrolment
    # recorded its own counter as mfa_last_counter and verify_totp refuses
    # that counter again, which a fast test suite hits routinely.
    await _login_password_step(client, username, password)
    verify_token = await _csrf_from(client, "/admin/verify")
    verified = await client.post(
        "/admin/verify",
        data={"code": codes[0], "csrf_token": verify_token},
        follow_redirects=False,
    )
    assert verified.status_code == 302

    # The eviction: an administrator resets this account's MFA mid-session.
    with admin_app.state.session_factory() as db:
        reset_mfa(db, username, actor="admin")
        db.commit()

    monkeypatch.setattr(
        AdminAuth, "_may_open_pre_login_page", lambda self, request, path: True
    )

    response = await client.get("/admin/enrol", follow_redirects=False)

    assert response.status_code in (302, 307)
    assert not response.headers["location"].rstrip("/").endswith("/admin/enrol")
    with admin_app.state.session_factory() as db:
        # No device row at all, confirmed or otherwise. reset_mfa clears the
        # whole collection, and asserting on the collection rather than on
        # `mfa_enrolled` is what catches a reset that left a usable secret
        # behind on a second, unconfirmed row.
        assert get_staff(db, username).totp_devices == []


# --- Behaviour 7 --------------------------------------------------------


async def test_a_post_with_no_prior_get_is_refused_not_a_500(
    admin_app, client, owes_enrolment
):
    """Nothing enforces the browser's GET-then-POST order: curl, a scripted
    login, a scanner probing an auth endpoint, or a replayed request can all
    reach POST /admin/enrol with staff.mfa_secret_enc still NULL, because
    the GET handler - the only thing that calls begin_mfa_enrolment on the
    happy path - never ran. Deliberately never issues a GET, unlike every
    other test in this file, and so never obtains a CSRF token either - the
    request is refused on that ground, but the refusal has to be a 400
    with the form re-rendered, not decrypt_totp_secret(None, ...) raising
    TypeError out of the handler as an unhandled 500 on the page that
    fronts the takeover guard."""
    username, password = owes_enrolment
    await _login_password_step(client, username, password)

    response = await client.post(
        "/admin/enrol",
        data={"code": "000000"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert _mfa_enrolled(admin_app, username) is False


# --- Onboarding lands the user in the panel ------------------------------


async def test_enrolment_establishes_the_session_without_a_second_challenge(
    admin_app, client, owes_enrolment
):
    """Completing enrolment logs the user in.

    Both factors are proven by the time this returns: the gate admitted the
    request on a valid pending login, which is the password step, and
    complete_mfa_enrolment has just verified a genuine TOTP code. Sending the
    user on to /admin/verify asked for the same evidence a second time - and
    could not succeed, because enrolment records its accepted time step in
    mfa_last_counter, so the code still showing on their authenticator is
    refused as a replay while the page blames their device clock.
    """
    username, password = owes_enrolment
    await _login_password_step(client, username, password)
    token = await _csrf_from(client, "/admin/enrol")
    secret = _stored_secret(admin_app, username)

    done = await client.post(
        "/admin/enrol",
        data={
            "code": pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(
                int(views_time.time())
            ),
            "csrf_token": token,
        },
        follow_redirects=False,
    )

    assert done.status_code == 200
    index = await client.get("/admin/", follow_redirects=False)
    assert index.status_code == 200, (
        "enrolment should have established the session; the user was sent "
        "back to a second-factor challenge instead"
    )


async def test_the_recovery_codes_page_continues_into_the_panel(
    admin_app, client, owes_enrolment
):
    """The link under the recovery codes goes to the panel, not to a further
    challenge the replay counter would refuse."""
    username, password = owes_enrolment
    await _login_password_step(client, username, password)
    token = await _csrf_from(client, "/admin/enrol")
    secret = _stored_secret(admin_app, username)

    done = await client.post(
        "/admin/enrol",
        data={
            "code": pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(
                int(views_time.time())
            ),
            "csrf_token": token,
        },
        follow_redirects=False,
    )

    assert 'href="/admin/"' in done.text
    assert "/admin/verify" not in done.text


async def test_enrolment_stamps_the_login_time(
    admin_app, client, owes_enrolment
):
    """A login established here must be indistinguishable from one completed
    at /admin/verify, which is the one place that stamps last_login_at."""
    username, password = owes_enrolment
    await _login_password_step(client, username, password)
    token = await _csrf_from(client, "/admin/enrol")
    secret = _stored_secret(admin_app, username)

    await client.post(
        "/admin/enrol",
        data={
            "code": pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(
                int(views_time.time())
            ),
            "csrf_token": token,
        },
        follow_redirects=False,
    )

    with admin_app.state.session_factory() as db:
        assert get_staff(db, username).last_login_at is not None


async def test_the_recovery_codes_page_names_the_system_and_the_account(
    admin_app, client, owes_enrolment
):
    """Eight random strings in a password manager say nothing about what they open.

    Deleting either interpolation from brand/enrol_done.html fails this. Same
    reasoning as the authenticator issuer: the codes outlive the page they
    were shown on, and whoever finds them later has to be able to tell which
    system and which account they belong to.
    """
    username, password = owes_enrolment
    await _login_password_step(client, username, password)
    token = await _csrf_from(client, "/admin/enrol")
    secret = _stored_secret(admin_app, username)

    done = await client.post(
        "/admin/enrol",
        data={
            "code": pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(
                int(views_time.time())
            ),
            "csrf_token": token,
        },
        follow_redirects=False,
    )

    assert done.status_code == 200
    assert "Kai Commitment Admin" in done.text
    assert username in done.text


async def test_a_rejected_code_re_renders_a_byte_identical_qr(
    admin_app, client, owes_enrolment
):
    """Not merely the same secret - the same *image*.

    The existing secret assertions cannot see a **label** that changed between
    the first render and the second, and a changed label is a different
    `otpauth://` URI, which an authenticator adds as a *second entry* rather
    than recognising as the one it already holds.

    To be exact about what this does and does not prove today. Until v1.13
    this page built its resume URI from a bare `staff.username` while
    `begin_mfa_enrolment` built its mint URI from
    `_device_label(username, DEFAULT_DEVICE_NAME)`. Those two produce the
    *same string*, so this test would not have failed on that drift and does
    not claim to have caught it - the drift was latent, waiting on the default
    device's label acquiring a rule the resume path did not share. What this
    pins is the property that made it latent rather than live: the mint path
    and the resume path render one image. `test_the_default_devices_label_is_
    the_bare_username` in test_enrolment.py pins the label's value itself,
    which is the half this cannot see.
    """
    username, password = owes_enrolment
    await _login_password_step(client, username, password)

    first = await client.get("/admin/enrol")
    before = re.search(r'<path d="([^"]+)"', first.text).group(1)

    rejected = await client.post(
        "/admin/enrol",
        data={"csrf_token": await _csrf_from(client, "/admin/enrol"), "code": "000000"},
    )

    assert rejected.status_code == 400
    after = re.search(r'<path d="([^"]+)"', rejected.text).group(1)
    assert after == before

