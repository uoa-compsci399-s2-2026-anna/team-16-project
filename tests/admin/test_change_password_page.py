"""admin.views.ChangePasswordView - the first onboarding gate.

Contract: docs/interfaces.md 8.3.
"""

import re
import time

import pyotp
import pytest
from sqlalchemy import select

from admin.accounts import (
    begin_mfa_enrolment,
    complete_mfa_enrolment,
    create_staff,
    get_staff,
    last_password_change,
    set_password,
)
from admin.models import AuditLog
from admin.totp import TOTP_INTERVAL
from admin.views import MIN_PASSWORD_LENGTH

from tests.admin.conftest import _cleanup_staff_named

pytestmark = [pytest.mark.db, pytest.mark.asyncio]

SECRET_KEY = "test-secret-key-not-used-anywhere-real"


# The app and client fixtures live in tests/conftest.py as of Task 3 - the
# app fixture is named `admin_app` and disposes its engine on teardown. Do
# not redefine them here; Tasks 4 onward all share those definitions, and a
# private copy would leak an engine per test.


# The account teardown is `tests/admin/conftest.py::_cleanup_staff_named`, not
# a copy of it here. It deletes the audit entries as well as the staff row, on
# both of the two predicates that matter, and the audit half is not tidiness:
# these tests commit to the one shared `kaicalc_test` database, and
# test_modelviews.py and test_taxonomy_rules.py both read the whole of
# `audit_log` - one of them asserting it is empty. A row left behind here fails
# those files, and only when the whole directory runs. That became reachable
# from this file the moment ChangePasswordView started auditing its own change.


@pytest.fixture
def not_onboarded(admin_app):
    """A freshly created account that has never changed its password.

    create_staff is the only writer of must_change_password=True (accounts.py
    :177), so this is the state every real account starts in - no MFA
    enrolment either, matching the diagram in contract 8.3: "first login ->
    must_change_password = true". Returns (username, password).
    """
    import uuid

    username = f"u{uuid.uuid4().hex[:10]}"
    factory = admin_app.state.session_factory
    with factory() as db:
        _, password = create_staff(db, username=username, display_name="Test User")
        db.commit()
    yield username, password
    _cleanup_staff_named(admin_app, username)


@pytest.fixture
def enrolled_but_owes_password_change(admin_app):
    """An account that finished MFA enrolment before ever changing its
    password.

    Nothing in the service layer requires the two onboarding steps to happen
    in the HTTP flow's order - only the *view* routing (login(), authenticate())
    enforces password-change-before-enrolment. Calling begin/complete_mfa_enrolment
    directly, without ever calling set_password, produces
    (must_change_password=True, mfa_enrolled=True) using only real service
    functions - no hand-edited row. This is exactly the state
    ChangePasswordView's "elif ... else: target = admin:view-verify" branch
    exists to route: MFA already enrolled, but no established session yet.

    Returns (username, password, secret, codes).
    """
    import uuid

    username = f"u{uuid.uuid4().hex[:10]}"
    factory = admin_app.state.session_factory
    with factory() as db:
        _, password = create_staff(db, username=username, display_name="Test User")
        db.flush()
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
    yield username, password, secret, codes
    _cleanup_staff_named(admin_app, username)


@pytest.fixture
def onboarded(admin_app):
    """A fully set-up account: past both onboarding steps.

    Copied from tests/admin/test_verify_page.py rather than imported - pytest
    fixtures are not shared across modules without a conftest.py move, and
    this file only needs it for the one test that establishes a live
    SESSION_KEY session (behaviour 4). See that file for the reasoning behind
    the backdated TOTP step.
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
    _cleanup_staff_named(admin_app, username)


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


def _must_change_password(admin_app, username):
    with admin_app.state.session_factory() as db:
        return get_staff(db, username).must_change_password


# --- Behaviour 1 -------------------------------------------------------------


async def test_the_change_password_page_is_reached_after_the_password_step(
    client, not_onboarded
):
    username, password = not_onboarded

    response = await _login_password_step(client, username, password)

    assert response.status_code == 302
    assert "/admin/change-password" in response.headers["location"]


# --- Behaviour 2 -------------------------------------------------------------


async def test_the_page_renders_and_does_not_redirect_to_itself(client, not_onboarded):
    """The single most likely bug in this task: a gate that redirects its
    own clearing page loops forever and the account can never escape."""
    username, password = not_onboarded
    await _login_password_step(client, username, password)

    response = await client.get("/admin/change-password", follow_redirects=False)

    assert response.status_code == 200
    assert "location" not in response.headers
    assert "csrf_token" in response.text


# --- Behaviour 3 -------------------------------------------------------------


async def test_a_valid_change_clears_the_flag_and_sends_an_unenrolled_account_to_enrol(
    admin_app, client, not_onboarded
):
    username, password = not_onboarded
    await _login_password_step(client, username, password)
    token = await _csrf_from(client, "/admin/change-password")

    response = await client.post(
        "/admin/change-password",
        data={
            "password": "a-brand-new-password",
            "confirm": "a-brand-new-password",
            "csrf_token": token,
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"].endswith("/admin/enrol")
    assert _must_change_password(admin_app, username) is False


async def test_the_change_is_recorded_in_the_audit_trail(
    admin_app, client, not_onboarded
):
    """This page wrote nothing until now, and the gap was not academic.

    It is the page every account passes through on first login, so for a fresh
    account the trail held no record of the only password change that had ever
    happened - and /admin/security's "last changed on ⟨date⟩" reads that trail.
    The reason given for writing nothing was that no actor exists in any
    meaningful sense mid-onboarding; `current_username` identifies one, and it
    is the only account that could have reached this page.
    """
    username, password = not_onboarded
    await _login_password_step(client, username, password)
    token = await _csrf_from(client, "/admin/change-password")

    response = await client.post(
        "/admin/change-password",
        data={
            "password": "a-brand-new-password",
            "confirm": "a-brand-new-password",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 302

    with admin_app.state.session_factory() as db:
        staff = get_staff(db, username)
        rows = db.scalars(
            select(AuditLog).where(
                AuditLog.actor == username, AuditLog.table_name == "staff"
            )
        ).all()
        assert len(rows) == 1, rows
        entry = rows[0]
        assert entry.action == "update"
        assert entry.row_id == staff.id
        assert entry.after_json["changed"] == "password"
        # Not the self-service screen. The trail says which page did it.
        assert entry.after_json["self_service"] is False
        # Neither the plaintext nor the hash: audit_log is readable by every
        # staff member.
        assert "a-brand-new-password" not in str(entry.after_json)
        assert "password_hash" not in entry.after_json

        # ...and this is the row the security screen's date comes from.
        assert last_password_change(db, staff) == entry.at


# --- Behaviour 4 -------------------------------------------------------------


async def test_a_valid_change_sends_an_already_logged_in_enrolled_account_to_the_index(
    admin_app, client, onboarded
):
    """Contract 8.3's known limitation notes that an administrator can force
    a password change mid-session (issuing a fresh password), and
    authenticate()'s defence-in-depth branch is what re-routes the very next
    request to this page in that case. No service function performs that
    issuance yet (docs/interfaces.md 8.3, "Related gap, same root"), so the
    flag is set directly here after a real, complete login has established
    SESSION_KEY - the one piece this test cannot obtain through an existing
    service function, and the only thing about the row being hand-set."""
    username, password, secret, _ = onboarded
    await _login_password_step(client, username, password)
    verify_token = await _csrf_from(client, "/admin/verify")
    await client.post(
        "/admin/verify",
        data={
            "code": pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(int(time.time())),
            "csrf_token": verify_token,
        },
        follow_redirects=False,
    )
    # SESSION_KEY is now established. Force a mid-session password change,
    # as an administrator issuing a fresh password would (contract 8.3).
    with admin_app.state.session_factory() as db:
        staff = get_staff(db, username)
        staff.must_change_password = True
        db.commit()

    token = await _csrf_from(client, "/admin/change-password")
    response = await client.post(
        "/admin/change-password",
        data={
            "password": "another-brand-new-password",
            "confirm": "another-brand-new-password",
            "csrf_token": token,
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"].rstrip("/").endswith("/admin")
    assert _must_change_password(admin_app, username) is False


async def test_a_valid_change_sends_a_pending_enrolled_account_to_verify(
    admin_app, client, enrolled_but_owes_password_change
):
    """The third branch of the same routing decision, exercised by neither
    the brief's numbered list nor the SESSION_KEY case above: MFA already
    enrolled, but reached via a pending login rather than an established
    session (no administrator eviction involved - just a differently ordered
    but perfectly legal sequence of real onboarding calls). Left untested,
    this branch is exactly the shape of gap mutation testing has caught
    elsewhere on this project: deleting the ``elif ... else`` split would
    leave the suite green without it."""
    username, password, secret, codes = enrolled_but_owes_password_change
    await _login_password_step(client, username, password)
    token = await _csrf_from(client, "/admin/change-password")

    response = await client.post(
        "/admin/change-password",
        data={
            "password": "yet-another-new-password",
            "confirm": "yet-another-new-password",
            "csrf_token": token,
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"].endswith("/admin/verify")
    assert _must_change_password(admin_app, username) is False


# --- Behaviour 5 -------------------------------------------------------------


async def test_a_too_short_password_is_refused_and_the_flag_stays_set(
    admin_app, client, not_onboarded
):
    username, password = not_onboarded
    await _login_password_step(client, username, password)
    token = await _csrf_from(client, "/admin/change-password")

    response = await client.post(
        "/admin/change-password",
        data={"password": "short1", "confirm": "short1", "csrf_token": token},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert _must_change_password(admin_app, username) is True


async def test_the_floor_refuses_one_short_and_accepts_exactly_the_minimum(
    admin_app, client, not_onboarded
):
    """Both sides of the length boundary, against one account.

    The refusing half on its own would pass against a page that refuses
    every password, so the accepting half is the one carrying the weight:
    a password of exactly MIN_PASSWORD_LENGTH characters must go through.

    Lengths are derived from the constant rather than written out. The floor
    has already moved once - twelve to eight - and a literal 12 here would
    have stayed green across that change while asserting nothing about the
    rule actually in force.
    """
    username, password = not_onboarded
    await _login_password_step(client, username, password)

    one_short = "a" * (MIN_PASSWORD_LENGTH - 1)
    refused = await client.post(
        "/admin/change-password",
        data={
            "password": one_short,
            "confirm": one_short,
            "csrf_token": await _csrf_from(client, "/admin/change-password"),
        },
        follow_redirects=False,
    )

    assert refused.status_code == 400
    # The message names the real floor; a hard-coded number in the copy
    # would fail here rather than mislead a user.
    assert f"at least {MIN_PASSWORD_LENGTH} characters" in refused.text
    assert _must_change_password(admin_app, username) is True

    exactly = "a" * MIN_PASSWORD_LENGTH
    accepted = await client.post(
        "/admin/change-password",
        data={
            "password": exactly,
            "confirm": exactly,
            "csrf_token": await _csrf_from(client, "/admin/change-password"),
        },
        follow_redirects=False,
    )

    assert accepted.status_code == 302, accepted.text
    assert _must_change_password(admin_app, username) is False


async def test_the_page_states_the_length_rule_before_anyone_is_refused(
    admin_app, client, not_onboarded
):
    """The number on screen comes from MIN_PASSWORD_LENGTH.

    Prose carrying its own copy of the number is worse than no guidance: it
    goes quietly wrong the first time the constant moves, and this one has
    moved. The page is fetched with no error on it, because a rule that is
    only stated in a refusal is a rule you learn by failing.
    """
    username, password = not_onboarded
    await _login_password_step(client, username, password)

    page = await client.get("/admin/change-password", follow_redirects=False)

    assert page.status_code == 200
    assert f"At least {MIN_PASSWORD_LENGTH} characters" in page.text
    # And no second, stale number anywhere else on the page. Written as a
    # sweep rather than "12 is absent" so it keeps working if the floor moves
    # again, in either direction.
    stated = set(re.findall(r"(\d+) characters", page.text))
    assert stated == {str(MIN_PASSWORD_LENGTH)}, stated


async def test_the_form_carries_the_attributes_a_password_manager_needs(
    admin_app, client, not_onboarded
):
    """The attributes are in the markup. That, and nothing beyond it.

    This test cannot show that Chrome offers to *update* the stored
    credential instead of saving a second one - only a browser with a
    password manager signed in can show that, and it was checked by hand.
    What it does hold is that a later edit of this template cannot drop them
    without turning something red. That matters because every one of these
    attributes reads as decoration: a readonly field nobody can see, an
    autocomplete value on a field the server never fills. Removing them
    breaks no behaviour any other test in this file observes, and the
    symptom appears weeks later in somebody's password manager.
    """
    username, password = not_onboarded
    await _login_password_step(client, username, password)

    page = await client.get("/admin/change-password", follow_redirects=False)
    assert page.status_code == 200
    body = page.text

    # The username the browser will file the credential under, and it is the
    # account actually signing in.
    assert 'autocomplete="username"' in body
    assert f'value="{username}"' in body
    username_field = re.search(r"<input[^>]*autocomplete=\"username\"[^>]*>", body)
    assert username_field, body
    field = username_field.group(0)
    # type="hidden" is the obvious way to write this and the one that does
    # not work: password managers skip hidden inputs. It must be a text
    # input that is merely off screen, and readonly so it cannot be edited.
    assert 'type="text"' in field, field
    assert 'type="hidden"' not in field, field
    assert "readonly" in field, field
    assert "sr-only" in field, field

    assert body.count('autocomplete="new-password"') == 2
    # The form must not switch autocomplete off wholesale; that is the state
    # this page was in when the browser treated a password change as a new
    # signup.
    assert 'autocomplete="off"' not in body


# --- Behaviour 6 -------------------------------------------------------------


async def test_mismatched_entries_are_refused_and_the_flag_stays_set(
    admin_app, client, not_onboarded
):
    username, password = not_onboarded
    await _login_password_step(client, username, password)
    token = await _csrf_from(client, "/admin/change-password")

    response = await client.post(
        "/admin/change-password",
        data={
            "password": "a-brand-new-password",
            "confirm": "a-different-new-password",
            "csrf_token": token,
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert _must_change_password(admin_app, username) is True


# --- Behaviour 7 -------------------------------------------------------------


async def test_resubmitting_the_current_password_is_refused(
    admin_app, client, not_onboarded
):
    """The property this whole page exists for. The password on this
    account was handed over out of band - spoken aloud, written on paper -
    and E-1's set_password (carry-over item 7) does not refuse it back, so
    the page has to. The current password is the real value create_staff
    generated and returned, not a value invented for the test."""
    username, password = not_onboarded
    await _login_password_step(client, username, password)
    token = await _csrf_from(client, "/admin/change-password")

    response = await client.post(
        "/admin/change-password",
        data={"password": password, "confirm": password, "csrf_token": token},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "have not used" in response.text
    assert _must_change_password(admin_app, username) is True


# --- Behaviour 8 -------------------------------------------------------------


async def test_a_submission_without_a_csrf_token_is_refused(
    admin_app, client, not_onboarded
):
    username, password = not_onboarded
    await _login_password_step(client, username, password)

    response = await client.post(
        "/admin/change-password",
        data={"password": "a-brand-new-password", "confirm": "a-brand-new-password"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert _must_change_password(admin_app, username) is True


# --- Extra: bcrypt's byte ceiling, not named among the eight but a real
# branch in _password_problem that a deleted check would leave silently
# unguarded. ------------------------------------------------------------------


async def test_a_password_over_the_bcrypt_byte_limit_is_refused(
    admin_app, client, not_onboarded
):
    username, password = not_onboarded
    await _login_password_step(client, username, password)
    token = await _csrf_from(client, "/admin/change-password")

    too_long = "a" * 100
    response = await client.post(
        "/admin/change-password",
        data={"password": too_long, "confirm": too_long, "csrf_token": token},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "72" in response.text
    assert _must_change_password(admin_app, username) is True


async def test_a_password_short_in_characters_but_over_the_byte_limit_is_refused(
    admin_app, client, not_onboarded
):
    """Behaviour 9 (added after review): the byte check and a character
    check agree on every ASCII input, including every password used
    elsewhere in this file - so a mutant that swaps
    ``len(new.encode("utf-8")) > BCRYPT_MAX_BYTES`` for ``len(new) >
    BCRYPT_MAX_BYTES`` leaves the rest of this suite green. This is not a
    theoretical edge case for this project: macron-bearing te reo Maori
    words and names are ordinary input for the staff who will use this
    panel, and the error message this page shows explicitly names
    "accented or non-Latin characters" as the reason the two counts differ.

    "e"-with-acute times 40 is 40 *characters* (well clear of
    MIN_PASSWORD_LENGTH's floor, and nowhere near a character-count limit of
    72, so no other check accounts for the refusal) but 80
    *bytes* in UTF-8 (over BCRYPT_MAX_BYTES=72) - so this input is accepted
    by a character-counting mutant and correctly refused only by the real
    byte-counting check.
    """
    username, password = not_onboarded
    await _login_password_step(client, username, password)
    token = await _csrf_from(client, "/admin/change-password")

    too_long = "é" * 40
    assert len(too_long) == 40
    assert len(too_long.encode("utf-8")) == 80

    response = await client.post(
        "/admin/change-password",
        data={"password": too_long, "confirm": too_long, "csrf_token": token},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "72" in response.text
    # The form, not an error page: PasswordTooLongError must never reach
    # set_password/hash_password for this input.
    assert "Choose a new password" in response.text
    assert _must_change_password(admin_app, username) is True
