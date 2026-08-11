"""The signed-in account's own security screen. Contract §8.3.

Everything here drives real HTTP against the running app. That is not
thoroughness for its own sake: ``admin/accounts_view.py``'s module docstring
records that sqladmin 0.30 calls a view's ``is_accessible`` only for the
routes it generates itself, never for a ``@action`` or an ``@expose`` route,
and this project has already shipped one defect of exactly that shape. A test
that called ``SecurityView.security()`` directly, or that reached the page
through a link, would prove nothing about who can POST to the URL.

The account this screen belongs to is read from ``SESSION_KEY`` and nothing
else, so there is no account id on any form here to aim somewhere else. The
one identifier that does travel is ``device_id``, and
``test_one_accounts_screen_cannot_remove_another_accounts_device`` aims it at
a device belonging to somebody else, by id, at the URL.
"""

import re
import time
import uuid
from datetime import datetime, timezone

import httpx
import pyotp
import pytest
import pytest_asyncio
from sqlalchemy import select, text

from admin.accounts import (
    MAX_TOTP_DEVICES,
    RECOVERY_CODE_COUNT,
    begin_mfa_enrolment,
    complete_mfa_enrolment,
    create_staff,
    get_staff,
    set_password,
    verify_staff_totp,
)
from admin.models import AuditLog, StaffRole
from admin.security import verify_password
#: Imported from the service layer, which is where it moved when
#: rename_totp_device began enforcing it. The page renders it as a maxlength;
#: a second copy in the view would be a second number.
from admin.accounts import MAX_DEVICE_NAME_LENGTH
from admin.totp import TOTP_INTERVAL
from admin.views import MIN_PASSWORD_LENGTH

from tests.admin.conftest import (
    _BROWSER_HEADERS,
    SECRET_KEY,
    _cleanup_staff,
    _login,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.db]

SECURITY_URL = "/admin/security"
PASSWORD = "an-adequately-long-password"
NEW_PASSWORD = "a-different-adequately-long-password"


# --- fixtures ---------------------------------------------------------------


def _onboard(admin_app, *, role=StaffRole.staff):
    """A fully onboarded account with exactly one enrolled authenticator.

    Deliberately not conftest's ``_create_onboarded_account``: these tests
    need the password back in order to re-authenticate with it, and they need
    the enrolment placed several time steps in the past so that the login
    below and the re-authentications afterwards do not collide on the replay
    counter.
    """
    username = f"u{uuid.uuid4().hex[:10]}"
    with admin_app.state.session_factory() as db:
        create_staff(db, username=username, display_name="Test User", role=role, actor="test")
        db.flush()
        set_password(db, username, PASSWORD)
        secret, _ = begin_mfa_enrolment(db, username, secret_key=SECRET_KEY)
        enrol_at = int(time.time()) - 20 * TOTP_INTERVAL
        complete_mfa_enrolment(
            db, username,
            pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(enrol_at),
            secret_key=SECRET_KEY, now=enrol_at,
        )
        db.commit()
        staff = get_staff(db, username)
    return staff, secret


@pytest_asyncio.fixture
async def me(admin_app, client, monkeypatch):
    """A logged-in plain `staff` account: the account this screen is for.

    `staff`, not `admin`, on purpose. The screen must work without an
    administrator role — that is the whole point of it existing — and a
    fixture that logged in as an administrator would let an accidental
    admin-only guard pass every test in this file.
    """
    staff, secret = _onboard(admin_app, role=StaffRole.staff)
    try:
        await _login(client, monkeypatch, username=staff.username,
                     password=PASSWORD, secret=secret)
    except BaseException:
        _cleanup_staff(admin_app, staff)
        raise
    client.staff = staff
    client.secret = secret
    yield client
    _cleanup_staff(admin_app, staff)


@pytest.fixture
def db_session(admin_app):
    """A session opened lazily against the running app's own database.

    SQLAlchemy autobegins on first statement, so a query issued after an HTTP
    request already committed is a new transaction and sees that commit. A
    read taken through this session *before* a request would pin a
    REPEATABLE READ snapshot that predates it and make every "unchanged"
    assertion hold regardless — see conftest's `_resync`.
    """
    with admin_app.state.session_factory() as db:
        yield db


# --- helpers ----------------------------------------------------------------


async def _csrf(client):
    page = await client.get(SECURITY_URL)
    assert page.status_code == 200, page.status_code
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match, "no CSRF token rendered on the security page"
    return match.group(1)


async def _post(client, **fields):
    token = fields.pop("csrf_token", None)
    if token is None:
        token = await _csrf(client)
    return await client.post(
        SECURITY_URL, data={"csrf_token": token, **fields}, follow_redirects=False
    )


def _devices(db, username):
    return get_staff(db, username).totp_devices


def _fresh_totp(admin_app, username, secret, monkeypatch):
    """A code the replay counter has not already seen, and the clock to match.

    Every login and every TOTP re-authentication records the step it accepted
    on that device, so reusing "now" produces a code that is correct and
    refused. Stepping the whole application's clock forward past the highest
    counter any of this account's devices holds is what keeps that from being
    a flaky test rather than a real one.
    """
    with admin_app.state.session_factory() as db:
        highest = max(
            [d.last_counter or 0 for d in _devices(db, username)] or [0]
        )
    now = (max(highest, int(time.time()) // TOTP_INTERVAL) + 2) * TOTP_INTERVAL
    monkeypatch.setattr(time, "time", lambda: float(now))
    return pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(now)


def _text(html: str) -> str:
    """Tags stripped and whitespace collapsed: what a person actually reads.

    Every assertion in the section below goes through this rather than
    searching the raw body. This project has twice shipped a regression that
    every "the string is present" test passed - 87 field descriptions rendered
    invisible by a widget's CSS class, and five guidance blocks absent from the
    wheel - and a substring search over markup cannot tell the difference
    between a word in a table cell and the same word in an attribute.
    """
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _element(html: str, pattern: str) -> str:
    match = re.search(pattern, html, re.S)
    assert match, f"no match for {pattern!r}"
    return match.group(0)


def _device_rows(html: str) -> list[list[str]]:
    """The authenticator table's body rows, cell by readable cell."""
    table = _element(html, r'<table class="devices">.*?</table>')
    rows = re.findall(r"<tr>(.*?)</tr>", table, re.S)
    assert len(rows) >= 2, "the table rendered no body rows"
    return [
        [_text(cell) for cell in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.S)]
        for row in rows[1:]  # rows[0] is the header
    ]


async def _enrol_second_device(client, admin_app, monkeypatch, name="Backup phone"):
    """Drive the real two-request add-a-device flow. Returns the new secret.

    **The confirmation reuses the CSRF token out of the QR page**, rather than
    letting ``_post`` fetch a fresh one, because that is what a browser does:
    the enrolment dialog is rendered *inside* the begin-device response and its
    form carries that response's token. There is no GET between the two steps
    in the real flow, and there must not be one here either — a GET of
    /admin/security discards an unconfirmed enrolment by design (see
    ``admin/accounts.py::discard_unconfirmed_devices``), so a helper that
    fetched a token mid-flow would be testing a sequence no browser performs
    and would destroy the very row it was about to confirm.
    """
    begun = await _post(
        client, action="begin-device", device_name=name,
        current_password=PASSWORD,
    )
    assert begun.status_code == 200, begun.status_code
    match = re.search(r'<code class="key">([A-Z2-7 ]+)</code>', begun.text)
    assert match, "the enrolment page rendered no setup key"
    secret = match.group(1).replace(" ", "")
    token = re.search(r'name="csrf_token" value="([^"]+)"', begun.text)
    assert token, "the enrolment page rendered no CSRF token"

    now = int(time.time())
    monkeypatch.setattr(time, "time", lambda: float(now))
    confirmed = await _post(
        client, action="confirm-device", device_name=name,
        csrf_token=token.group(1),
        code=pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(now),
    )
    assert confirmed.status_code == 200, confirmed.text[:400]
    return secret


# --- reachability -----------------------------------------------------------


async def test_a_staff_member_reaches_their_own_security_screen(me):
    """No administrator role. Managing your own second factor is not an
    administrative act, and requiring one would put it back behind the
    colleague this screen exists to make unnecessary."""
    response = await me.get(SECURITY_URL)

    assert response.status_code == 200
    assert me.staff.username in response.text


async def test_a_staff_member_still_cannot_reach_the_account_screen(me):
    """The other half of the same sentence. /admin/staff is recovery layer
    L2 - one administrator acting on another - and stays administrator-only.
    Opening this screen to `staff` must not have opened that one."""
    response = await me.get("/admin/staff/list")

    assert response.status_code == 403


async def test_a_staff_member_cannot_reach_the_account_screens_actions(me):
    """The URL, not the menu entry. sqladmin registers an @action route
    wrapped in login_required alone - is_accessible is never consulted for it
    - so a plain staff session reaching /admin/staff/action/... directly is
    the thing to prove refused, and it is not proven by the list page's 403.
    """
    for slug in ("issue-password", "reset-mfa", "deactivate"):
        response = await me.get(
            f"/admin/staff/action/{slug}", params={"pks": me.staff.id}
        )
        assert response.status_code == 403, slug


async def test_the_screen_is_not_reachable_without_a_session(client):
    """No SESSION_KEY at all. This path is deliberately absent from
    _PRE_LOGIN_PAGES, so a pending login - half a login - must not open it
    either; authenticate()'s ordinary branch refuses both the same way."""
    response = await client.get(SECURITY_URL, follow_redirects=False)

    assert response.status_code in (302, 307)
    assert "/admin/login" in response.headers["location"]


async def test_a_post_without_a_csrf_token_changes_nothing(me, db_session):
    response = await me.post(
        SECURITY_URL,
        data={"action": "change-password", "current_password": PASSWORD,
              "password": NEW_PASSWORD, "confirm": NEW_PASSWORD},
        follow_redirects=False,
    )

    assert response.status_code == 400
    staff = get_staff(db_session, me.staff.username)
    assert verify_password(PASSWORD, staff.password_hash)


async def test_an_unrecognised_action_is_refused(me):
    response = await _post(me, action="reset-mfa")

    assert response.status_code == 400


# --- changing your own password ---------------------------------------------


async def test_changing_the_password_requires_the_current_one(me, db_session):
    """The whole point of the screen. A stolen session has already cleared
    both factors, so the session cookie cannot be what authorises a password
    change - the current password is the one secret it does not carry."""
    response = await _post(
        me, action="change-password",
        password=NEW_PASSWORD, confirm=NEW_PASSWORD,
    )

    assert response.status_code == 400
    staff = get_staff(db_session, me.staff.username)
    assert verify_password(PASSWORD, staff.password_hash)
    assert staff.session_generation == me.staff.session_generation


async def test_a_wrong_current_password_changes_nothing(me, db_session):
    response = await _post(
        me, action="change-password", current_password="not-the-password",
        password=NEW_PASSWORD, confirm=NEW_PASSWORD,
    )

    assert response.status_code == 400
    staff = get_staff(db_session, me.staff.username)
    assert verify_password(PASSWORD, staff.password_hash)
    assert staff.session_generation == me.staff.session_generation


async def test_a_totp_code_is_not_accepted_in_place_of_the_password(
    me, admin_app, db_session, monkeypatch
):
    """A code proves the second factor, and the session presenting it has
    already cleared the second factor - so it proves nothing the cookie did
    not. Accepting one here would put the password change back within reach
    of a stolen session, which is the state this screen exists to close."""
    code = _fresh_totp(admin_app, me.staff.username, me.secret, monkeypatch)

    response = await _post(
        me, action="change-password", current_code=code,
        password=NEW_PASSWORD, confirm=NEW_PASSWORD,
    )

    assert response.status_code == 400
    staff = get_staff(db_session, me.staff.username)
    assert verify_password(PASSWORD, staff.password_hash)


async def test_the_current_password_changes_the_password(me, db_session):
    response = await _post(
        me, action="change-password", current_password=PASSWORD,
        password=NEW_PASSWORD, confirm=NEW_PASSWORD,
    )

    assert response.status_code == 200
    staff = get_staff(db_session, me.staff.username)
    assert verify_password(NEW_PASSWORD, staff.password_hash)
    # Contract §8.3 treats a password change as an eviction. Leaving the
    # generation alone would make it cosmetic: every other session naming
    # this account would go on working.
    assert staff.session_generation == me.staff.session_generation + 1


async def test_the_session_that_changed_the_password_survives_it(me):
    """set_password bumps session_generation, which invalidates the very
    cookie this request arrived on. Without the re-stamp the person is
    silently signed out by their own deliberate action, and the next page
    they open is the login form with nothing explaining why."""
    changed = await _post(
        me, action="change-password", current_password=PASSWORD,
        password=NEW_PASSWORD, confirm=NEW_PASSWORD,
    )
    assert changed.status_code == 200

    after = await me.get(SECURITY_URL, follow_redirects=False)

    assert after.status_code == 200


async def test_the_password_rules_are_the_forced_change_pages_rules(me, db_session):
    """Reused rather than restated, so the two pages cannot drift into
    telling one person a password is acceptable and another that it is not.
    The reuse assertion is the one that matters: too short, mismatched, and
    the password already in force are all refused here too."""
    for password, confirm in (
        # One character below the shared floor, derived from the constant so
        # this stays a boundary rather than a comfortably short string.
        ("a" * (MIN_PASSWORD_LENGTH - 1), "a" * (MIN_PASSWORD_LENGTH - 1)),
        (NEW_PASSWORD, "something-else-entirely"),
        (PASSWORD, PASSWORD),
    ):
        response = await _post(
            me, action="change-password", current_password=PASSWORD,
            password=password, confirm=confirm,
        )
        assert response.status_code == 400, password

    staff = get_staff(db_session, me.staff.username)
    assert verify_password(PASSWORD, staff.password_hash)


async def test_a_password_of_exactly_the_minimum_length_is_accepted(me, db_session):
    """The accepting side of the floor, on this page too.

    ``test_the_password_rules_are_the_forced_change_pages_rules`` above only
    checks refusals, and a refusal-only suite is satisfied by a page that
    refuses everything. The length is derived from MIN_PASSWORD_LENGTH: the
    floor moved from twelve to eight and a literal would have survived that
    without asserting anything about the rule in force.
    """
    exactly = "b" * MIN_PASSWORD_LENGTH

    response = await _post(
        me, action="change-password", current_password=PASSWORD,
        password=exactly, confirm=exactly,
    )

    assert response.status_code == 200, response.text
    staff = get_staff(db_session, me.staff.username)
    assert verify_password(exactly, staff.password_hash)


async def test_the_screen_states_the_length_rule_from_the_constant(me):
    """The number on screen is MIN_PASSWORD_LENGTH's, not a copy of it.

    ``_context`` has passed ``min_password_length`` into this template since
    the screen was built, and until now nothing rendered it - the rule was
    stated only in the refusal a user got for breaking it.
    """
    page = await me.get(SECURITY_URL, follow_redirects=False)

    assert page.status_code == 200
    assert f"At least {MIN_PASSWORD_LENGTH} characters" in page.text
    # Every character count on the page comes from a constant, not from a
    # number somebody typed into the template: the password rule from
    # MIN_PASSWORD_LENGTH, the authenticator-name rule from
    # MAX_DEVICE_NAME_LENGTH. Asserted as the exact set, so a third hardcoded
    # figure appearing anywhere on this screen fails here.
    stated = set(re.findall(r"(\d+) characters", page.text))
    assert stated == {
        str(MIN_PASSWORD_LENGTH), str(MAX_DEVICE_NAME_LENGTH)
    }, stated


async def test_the_password_form_carries_the_attributes_a_password_manager_needs(me):
    """The attributes are in the markup. That, and nothing beyond it.

    No test here can show that Chrome offers to *update* the credential it
    already holds rather than save a second one; that needs a browser with a
    password manager signed in, and it was checked by hand. What this holds
    is that a refactor of this template cannot drop them silently - they all
    read as decoration, and losing them breaks nothing else in this file.

    The username field is asserted to carry no ``name``: it exists for the
    browser, it is never submitted, and this screen's rule that the account
    comes from the session and from no form field stays intact.
    """
    page = await me.get(SECURITY_URL, follow_redirects=False)
    assert page.status_code == 200
    body = page.text

    match = re.search(r"<input[^>]*autocomplete=\"username\"[^>]*>", body)
    assert match, body
    field = match.group(0)
    assert f'value="{me.staff.username}"' in field, field
    # Not hidden - password managers skip hidden inputs - and not named, so
    # it never reaches a request handler.
    assert 'type="text"' in field, field
    assert 'type="hidden"' not in field, field
    assert "readonly" in field, field
    assert "name=" not in field, field

    assert 'autocomplete="current-password"' in body
    assert 'autocomplete="new-password"' in body


async def test_a_password_change_is_audited_naming_the_actor(me, db_session):
    await _post(
        me, action="change-password", current_password=PASSWORD,
        password=NEW_PASSWORD, confirm=NEW_PASSWORD,
    )

    rows = db_session.scalars(
        select(AuditLog).where(AuditLog.actor == me.staff.username)
    ).all()
    assert [r for r in rows if r.after_json.get("changed") == "password"], rows
    entry = next(r for r in rows if r.after_json.get("changed") == "password")
    assert entry.table_name == "staff"
    assert entry.row_id == me.staff.id
    # The plaintext and the hash are both absent. audit_log is readable by
    # every staff member, which is the whole reason §5.5 has a blocklist.
    assert NEW_PASSWORD not in str(entry.after_json)
    assert "password_hash" not in entry.after_json


# --- adding an authenticator ------------------------------------------------


async def test_adding_an_authenticator_requires_re_authentication(me, db_session):
    response = await _post(me, action="begin-device", device_name="Backup phone")

    assert response.status_code == 400
    assert len(_devices(db_session, me.staff.username)) == 1


async def test_a_wrong_password_adds_no_authenticator(me, db_session):
    response = await _post(
        me, action="begin-device", device_name="Backup phone",
        current_password="not-the-password",
    )

    assert response.status_code == 400
    assert len(_devices(db_session, me.staff.username)) == 1


async def test_the_current_password_starts_an_enrolment(me, db_session):
    response = await _post(
        me, action="begin-device", device_name="Backup phone",
        current_password=PASSWORD,
    )

    assert response.status_code == 200
    assert "<svg" in response.text, "no QR code rendered"
    devices = _devices(db_session, me.staff.username)
    # Present but unconfirmed. A secret that counted as a factor the moment
    # it was minted would let anyone who reached this page grant themselves
    # one without ever proving the device holds it.
    assert len(devices) == 2
    pending = next(d for d in devices if d.name == "Backup phone")
    assert pending.enrolled_at is None


async def test_a_code_from_an_existing_device_also_starts_an_enrolment(
    me, admin_app, db_session, monkeypatch
):
    """The other accepted proof. Someone changing phones has their old device
    to hand and may not have their password memorised - requiring only the
    password would send them to an administrator for something they can
    prove themselves."""
    code = _fresh_totp(admin_app, me.staff.username, me.secret, monkeypatch)

    response = await _post(
        me, action="begin-device", device_name="Backup phone", current_code=code,
    )

    assert response.status_code == 200
    assert len(_devices(db_session, me.staff.username)) == 2


async def test_both_authenticators_work_once_the_second_is_confirmed(
    me, admin_app, db_session, monkeypatch
):
    """The property the whole schema change exists for: two phones, both
    live, so losing one is not a lockout."""
    second = await _enrol_second_device(me, admin_app, monkeypatch)

    devices = _devices(db_session, me.staff.username)
    assert len(devices) == 2
    assert all(d.enrolled_at is not None for d in devices)

    # Verified through the real login path, one step apart so that the first
    # verification's counter cannot be what refuses the second.
    with admin_app.state.session_factory() as db:
        base = (int(time.time()) // TOTP_INTERVAL + 5) * TOTP_INTERVAL
        assert verify_staff_totp(
            db, me.staff.username,
            pyotp.TOTP(me.secret, interval=TOTP_INTERVAL).at(base),
            secret_key=SECRET_KEY, now=base,
        )
        later = base + TOTP_INTERVAL
        assert verify_staff_totp(
            db, me.staff.username,
            pyotp.TOTP(second, interval=TOTP_INTERVAL).at(later),
            secret_key=SECRET_KEY, now=later,
        )
        db.commit()


async def test_each_device_carries_its_own_replay_counter(
    me, admin_app, db_session, monkeypatch
):
    """Replay protection is a property of a secret, not of an account. Two
    phones emit two *different* codes for the same time step, so a shared
    counter would let a login on one refuse the other's current, entirely
    unused code for the rest of that step - a lockout that only appears on
    accounts with two devices, and gets blamed on the phone."""
    second = await _enrol_second_device(me, admin_app, monkeypatch)

    step = (int(time.time()) // TOTP_INTERVAL + 5) * TOTP_INTERVAL
    with admin_app.state.session_factory() as db:
        assert verify_staff_totp(
            db, me.staff.username,
            pyotp.TOTP(me.secret, interval=TOTP_INTERVAL).at(step),
            secret_key=SECRET_KEY, now=step,
        )
        # The *same* time step, the other device. A shared counter refuses
        # this; per-device counters accept it.
        assert verify_staff_totp(
            db, me.staff.username,
            pyotp.TOTP(second, interval=TOTP_INTERVAL).at(step),
            secret_key=SECRET_KEY, now=step,
        )
        db.commit()


async def test_the_new_devices_name_reaches_the_authenticator_label(me):
    """Task 1 put the system in the issuer and the account in the label. A
    second device on one account needs a third distinction or the app shows
    two identical entries, and the person deleting the lost phone is
    guessing which one it is."""
    response = await _post(
        me, action="begin-device", device_name="Backup phone",
        current_password=PASSWORD,
    )

    assert response.status_code == 200
    match = re.search(r'<code class="key">([A-Z2-7 ]+)</code>', response.text)
    secret = match.group(1).replace(" ", "")
    from admin.accounts import _device_label
    from admin.totp import provisioning_uri

    uri = provisioning_uri(
        secret, username=_device_label(me.staff.username, "Backup phone")
    )
    assert "Backup%20phone" in uri or "Backup phone" in uri
    assert me.staff.username in uri
    assert "Kai%20Commitment%20Admin" in uri


async def test_a_wrong_confirmation_code_keeps_the_same_secret(me, db_session):
    """The property admin/views.py's enrolment page already holds: a rejected
    code must re-render the QR already on the person's phone, not mint a
    second secret and then blame their device clock for the code that
    follows."""
    begun = await _post(
        me, action="begin-device", device_name="Backup phone",
        current_password=PASSWORD,
    )
    first = re.search(r'<code class="key">([A-Z2-7 ]+)</code>', begun.text).group(1)

    rejected = await _post(
        me, action="confirm-device", device_name="Backup phone", code="000000",
        # The token out of the QR page, as the browser sends it. A GET to
        # fetch a fresh one would discard the enrolment this test is about -
        # see _enrol_second_device.
        csrf_token=re.search(
            r'name="csrf_token" value="([^"]+)"', begun.text
        ).group(1),
    )

    assert rejected.status_code == 400
    again = re.search(r'<code class="key">([A-Z2-7 ]+)</code>', rejected.text).group(1)
    assert again == first
    devices = _devices(db_session, me.staff.username)
    assert next(d for d in devices if d.name == "Backup phone").enrolled_at is None


async def test_a_second_device_mints_no_new_recovery_codes(
    me, admin_app, db_session, monkeypatch
):
    """Recovery codes are the account's fallback when *no* authenticator is
    available, not a per-device credential. Minting five more would leave
    somebody holding two printed sheets with no way to tell which is
    current - and the older sheet just as valid as the newer one."""
    before = db_session.execute(
        text("SELECT COUNT(*) FROM staff_recovery_code WHERE staff_id = :id"),
        {"id": me.staff.id},
    ).scalar()
    assert before == RECOVERY_CODE_COUNT

    response_text = await _enrol_second_device(me, admin_app, monkeypatch)
    assert response_text

    db_session.commit()
    after = db_session.execute(
        text("SELECT COUNT(*) FROM staff_recovery_code WHERE staff_id = :id"),
        {"id": me.staff.id},
    ).scalar()
    assert after == RECOVERY_CODE_COUNT


async def test_two_devices_cannot_share_a_name(me, admin_app, db_session, monkeypatch):
    """The list has to be readable by the person deciding which phone to
    remove, and two entries called the same thing make that a guess."""
    await _enrol_second_device(me, admin_app, monkeypatch)

    response = await _post(
        me, action="begin-device", device_name="Backup phone",
        current_password=PASSWORD,
    )

    assert response.status_code == 400
    assert len(_devices(db_session, me.staff.username)) == 2


async def test_adding_a_device_is_audited_naming_the_actor(
    me, admin_app, db_session, monkeypatch
):
    await _enrol_second_device(me, admin_app, monkeypatch)

    rows = db_session.scalars(
        select(AuditLog).where(
            AuditLog.actor == me.staff.username,
            AuditLog.table_name == "staff_totp_device",
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].action == "create"
    assert rows[0].after_json["name"] == "Backup phone"
    assert "secret_enc" not in str(rows[0].after_json)


# --- removing an authenticator ----------------------------------------------


async def test_the_last_authenticator_cannot_be_removed(me, db_session):
    """Otherwise the account silently downgrades to password-only. Nothing
    announces it: the session in hand goes on working exactly as before, and
    the discovery comes at the next login, when require_staff refuses the
    account and sends it back through onboarding."""
    device_id = _devices(db_session, me.staff.username)[0].id
    db_session.commit()

    response = await _post(
        me, action="remove-device", device_id=device_id,
        current_password=PASSWORD,
    )

    assert response.status_code == 400
    staff = get_staff(db_session, me.staff.username)
    assert len(staff.totp_devices) == 1
    assert staff.mfa_enrolled is True


async def test_removing_an_authenticator_requires_re_authentication(
    me, admin_app, db_session, monkeypatch
):
    await _enrol_second_device(me, admin_app, monkeypatch)
    db_session.commit()
    device_id = _devices(db_session, me.staff.username)[0].id
    db_session.commit()

    response = await _post(me, action="remove-device", device_id=device_id)

    assert response.status_code == 400
    assert len(_devices(db_session, me.staff.username)) == 2


async def test_the_first_device_can_be_removed_once_a_second_exists(
    me, admin_app, db_session, monkeypatch
):
    """The lost-phone case end to end, and the reason the table exists."""
    second = await _enrol_second_device(me, admin_app, monkeypatch)
    db_session.commit()
    first_id = next(
        d.id for d in _devices(db_session, me.staff.username)
        if d.name == "Authenticator"
    )
    db_session.commit()

    response = await _post(
        me, action="remove-device", device_id=first_id, current_password=PASSWORD,
    )

    assert response.status_code == 200
    staff = get_staff(db_session, me.staff.username)
    assert [d.name for d in staff.totp_devices] == ["Backup phone"]
    assert staff.mfa_enrolled is True

    # The survivor still works, and the removed one no longer does.
    with admin_app.state.session_factory() as db:
        step = (int(time.time()) // TOTP_INTERVAL + 8) * TOTP_INTERVAL
        assert verify_staff_totp(
            db, me.staff.username,
            pyotp.TOTP(second, interval=TOTP_INTERVAL).at(step),
            secret_key=SECRET_KEY, now=step,
        )
        assert not verify_staff_totp(
            db, me.staff.username,
            pyotp.TOTP(me.secret, interval=TOTP_INTERVAL).at(step + TOTP_INTERVAL),
            secret_key=SECRET_KEY, now=step + TOTP_INTERVAL,
        )
        db.commit()


async def test_removing_a_device_evicts_every_other_session(
    me, admin_app, db_session, monkeypatch
):
    """A device is usually removed because it is out of the owner's hands.
    Leaving other sessions live would leave whoever holds it signed in."""
    await _enrol_second_device(me, admin_app, monkeypatch)
    db_session.commit()
    before = get_staff(db_session, me.staff.username).session_generation
    first_id = next(
        d.id for d in _devices(db_session, me.staff.username)
        if d.name == "Authenticator"
    )
    db_session.commit()

    response = await _post(
        me, action="remove-device", device_id=first_id, current_password=PASSWORD,
    )
    assert response.status_code == 200

    staff = get_staff(db_session, me.staff.username)
    assert staff.session_generation == before + 1
    # ...and the session that did it is not one of them.
    after = await me.get(SECURITY_URL, follow_redirects=False)
    assert after.status_code == 200


async def test_removing_a_device_is_audited_naming_the_actor(
    me, admin_app, db_session, monkeypatch
):
    await _enrol_second_device(me, admin_app, monkeypatch)
    db_session.commit()
    first_id = next(
        d.id for d in _devices(db_session, me.staff.username)
        if d.name == "Authenticator"
    )
    db_session.commit()

    await _post(
        me, action="remove-device", device_id=first_id, current_password=PASSWORD,
    )

    rows = db_session.scalars(
        select(AuditLog).where(
            AuditLog.actor == me.staff.username,
            AuditLog.table_name == "staff_totp_device",
            AuditLog.action == "delete",
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].row_id == first_id
    assert rows[0].before_json["name"] == "Authenticator"


# --- one account's screen cannot act on another -----------------------------


async def test_one_accounts_screen_cannot_remove_another_accounts_device(
    me, admin_app, db_session, monkeypatch
):
    """Aimed at the URL with somebody else's device id, which is the only
    identifier that travels on any form here - no field names an account, so
    there is nothing else to try.

    **Both accounts hold two devices, and that is what makes this test
    real.** Written with the actor holding only one, it passed against a
    `remove_totp_device` that looked devices up *globally* - the mutation was
    caught by the actor's own last-authenticator guard (`len(my enrolled
    devices) <= 1`) and returned the same 400 for a completely different
    reason. Giving the actor a second device takes that guard out of the
    picture, so the only thing left that can refuse is the ownership scope.
    The victim gets a second one for the mirror-image reason: their own last
    device would be refused by that guard no matter who asked.
    """
    await _enrol_second_device(me, admin_app, monkeypatch, name="My second phone")
    db_session.commit()
    assert len(_devices(db_session, me.staff.username)) == 2
    db_session.commit()

    victim, victim_secret = _onboard(admin_app, role=StaffRole.staff)
    try:
        with admin_app.state.session_factory() as db:
            begin_mfa_enrolment(
                db, victim.username, secret_key=SECRET_KEY,
                device_name="Backup phone", allow_additional=True,
            )
            db.flush()
            device = next(
                d for d in get_staff(db, victim.username).totp_devices
                if d.name == "Backup phone"
            )
            at = int(time.time()) - 12 * TOTP_INTERVAL
            from admin.security import decrypt_totp_secret
            plain = decrypt_totp_secret(device.secret_enc, secret_key=SECRET_KEY)
            complete_mfa_enrolment(
                db, victim.username,
                pyotp.TOTP(plain, interval=TOTP_INTERVAL).at(at),
                secret_key=SECRET_KEY, now=at, device_name="Backup phone",
            )
            db.commit()
            victim_ids = [d.id for d in get_staff(db, victim.username).totp_devices]
        assert len(victim_ids) == 2

        for device_id in victim_ids:
            response = await _post(
                me, action="remove-device", device_id=device_id,
                current_password=PASSWORD,
            )
            assert response.status_code == 400, device_id

        with admin_app.state.session_factory() as db:
            survivors = [d.id for d in get_staff(db, victim.username).totp_devices]
        assert survivors == victim_ids
        # And the actor's own devices are untouched too - a refusal that
        # removed the wrong row would be worse than one that removed none.
        db_session.commit()
        assert len(_devices(db_session, me.staff.username)) == 2
        assert victim_secret
    finally:
        _cleanup_staff(admin_app, victim)


async def test_a_device_id_that_does_not_exist_is_refused_the_same_way(me, db_session):
    """Same status, same page, as somebody else's id. A different answer for
    "not yours" and "not real" is an oracle for which ids exist and whose
    they are."""
    response = await _post(
        me, action="remove-device", device_id=99999999, current_password=PASSWORD,
    )

    assert response.status_code == 400
    assert len(_devices(db_session, me.staff.username)) == 1


# --- the shared login throttle ----------------------------------------------
#
# `_reauthenticate` calls `is_locked` and `record_failure`, and before these
# tests existed **deleting both survived the entire suite**. That is the one
# genuinely security-relevant behaviour on a screen whose whole purpose is
# proving identity: without the counter a stolen session gets an unthrottled
# oracle for the account's own password, and a 10^6 search against the TOTP
# field. The throttle is in-memory, so the failure path's `db.rollback()` does
# not undo a recorded failure - which is what makes it testable here at all.


def _max_failures(admin_app):
    return admin_app.state.settings.login_max_failures


async def test_repeated_wrong_passwords_lock_the_screen(me, admin_app, db_session):
    """`record_failure` proven: without it the counter never reaches the
    threshold and the correct password below is simply accepted."""
    for _ in range(_max_failures(admin_app)):
        r = await _post(
            me, action="change-password", current_password="not-the-password",
            password=NEW_PASSWORD, confirm=NEW_PASSWORD,
        )
        assert r.status_code == 400

    # The CORRECT password now, which is what separates a lockout from a
    # sixth ordinary rejection.
    r = await _post(
        me, action="change-password", current_password=PASSWORD,
        password=NEW_PASSWORD, confirm=NEW_PASSWORD,
    )

    assert r.status_code == 400
    assert "Too many failed attempts" in r.text
    staff = get_staff(db_session, me.staff.username)
    assert verify_password(PASSWORD, staff.password_hash), "the change went through"


async def test_the_lock_also_closes_the_add_device_path(me, admin_app, db_session):
    """`is_locked` is consulted on every action, not only the one that
    recorded the failures. A lock that covered the password form alone would
    leave the TOTP field - the 10^6 search - wide open beside it."""
    for _ in range(_max_failures(admin_app)):
        await _post(
            me, action="change-password", current_password="not-the-password",
            password=NEW_PASSWORD, confirm=NEW_PASSWORD,
        )

    r = await _post(
        me, action="begin-device", device_name="Backup phone",
        current_password=PASSWORD,
    )

    assert r.status_code == 400
    assert "Too many failed attempts" in r.text
    assert len(_devices(db_session, me.staff.username)) == 1


async def test_wrong_codes_and_wrong_passwords_share_one_counter(me, admin_app):
    """Contract 8.3: one counter across both proofs. Two counters would
    double the allowance for anyone willing to alternate, which is the
    cheapest thing in the world for a script to do."""
    half = _max_failures(admin_app) // 2
    for _ in range(half):
        await _post(
            me, action="begin-device", device_name="Backup phone",
            current_password="not-the-password",
        )
    for _ in range(_max_failures(admin_app) - half):
        await _post(
            me, action="begin-device", device_name="Backup phone",
            current_code="000000",
        )

    r = await _post(
        me, action="begin-device", device_name="Backup phone",
        current_password=PASSWORD,
    )

    assert r.status_code == 400
    assert "Too many failed attempts" in r.text


async def test_the_counter_is_the_login_counter(me, admin_app):
    """Not a second, screen-local one. Sharing it is what makes contract
    8.3's guarantee true, and it is also the cost recorded in the task
    report - an attacker on a stolen session can lock the owner out of
    logging in. Pinned either way, so the trade-off cannot change silently.
    """
    for _ in range(_max_failures(admin_app)):
        await _post(
            me, action="change-password", current_password="not-the-password",
            password=NEW_PASSWORD, confirm=NEW_PASSWORD,
        )

    fresh = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=admin_app),
        base_url="http://testserver", follow_redirects=False,
    )
    try:
        login = await fresh.post(
            "/admin/login",
            data={"username": me.staff.username, "password": PASSWORD},
            headers=_BROWSER_HEADERS,
        )
    finally:
        await fresh.aclose()

    # A correct password, refused: 302 would mean the password step passed.
    assert login.status_code != 302


async def test_a_successful_re_authentication_does_not_clear_the_counter(
    me, admin_app
):
    """`throttle.clear` is for a *completed login* only, and admin/throttle.py
    says so. Clearing here would let anyone holding the password alternate a
    correct re-authentication with guesses and never reach the threshold -
    the exact attack one shared counter exists to stop."""
    for _ in range(_max_failures(admin_app) - 1):
        await _post(
            me, action="begin-device", device_name="Backup phone",
            current_password="not-the-password",
        )

    # One success in the middle.
    ok = await _post(
        me, action="begin-device", device_name="Backup phone",
        current_password=PASSWORD,
    )
    assert ok.status_code == 200

    # One more failure now tips it over, which it can only do if the success
    # above left the earlier failures in place.
    await _post(
        me, action="begin-device", device_name="Another phone",
        current_password="not-the-password",
    )
    r = await _post(
        me, action="begin-device", device_name="Another phone",
        current_password=PASSWORD,
    )

    assert r.status_code == 400
    assert "Too many failed attempts" in r.text


# --- the limits this task invented ------------------------------------------


async def test_a_device_name_longer_than_the_column_is_refused(me, db_session):
    """MAX_DEVICE_NAME_LENGTH matches staff_totp_device.name's VARCHAR(64).
    Unenforced, MySQL either truncates silently (non-strict) or raises a
    DataError this page has no handler for (strict), and neither reads as an
    explanation to the person who pasted something long."""
    response = await _post(
        me, action="begin-device", device_name="x" * (MAX_DEVICE_NAME_LENGTH + 1),
        current_password=PASSWORD,
    )

    assert response.status_code == 400
    assert len(_devices(db_session, me.staff.username)) == 1


async def test_a_device_name_exactly_at_the_limit_is_accepted(me, db_session):
    """The boundary in the other direction, so the rule cannot be satisfied
    by refusing everything."""
    name = "y" * MAX_DEVICE_NAME_LENGTH

    response = await _post(
        me, action="begin-device", device_name=name, current_password=PASSWORD,
    )

    assert response.status_code == 200
    devices = _devices(db_session, me.staff.username)
    assert any(d.name == name for d in devices)


async def test_an_empty_device_name_is_refused(me, db_session):
    """A device nobody named cannot be told from the one beside it, which is
    the whole reason the column exists."""
    response = await _post(
        me, action="begin-device", device_name="   ", current_password=PASSWORD,
    )

    assert response.status_code == 400
    assert len(_devices(db_session, me.staff.username)) == 1


async def test_the_device_ceiling_is_refused_at_the_url(
    me, admin_app, db_session, monkeypatch
):
    """The service layer's ceiling proven through the screen, and proven to
    say the right thing: TooManyDevicesError, not the "this is your only
    authenticator" message whose condition is its opposite."""
    for i in range(MAX_TOTP_DEVICES - 1):
        await _enrol_second_device(me, admin_app, monkeypatch, name=f"Phone {i}")
    db_session.commit()
    assert len(_devices(db_session, me.staff.username)) == MAX_TOTP_DEVICES
    db_session.commit()

    response = await _post(
        me, action="begin-device", device_name="One too many",
        current_password=PASSWORD,
    )

    assert response.status_code == 400
    assert "maximum" in response.text
    assert "only authenticator" not in response.text
    db_session.commit()
    assert len(_devices(db_session, me.staff.username)) == MAX_TOTP_DEVICES


# --- the shape of the screen ------------------------------------------------
#
# The screen was rebuilt around the operations rather than around the fields.
# Everything below asserts what a person can read and reach, not that a string
# exists somewhere in the body - see _text.


async def test_the_authenticators_are_a_numbered_table(
    me, admin_app, db_session, monkeypatch
):
    """One row per device, numbered 1, 2, 3 down the page.

    The numbering is a real column, not a list marker: the Remove dialog names
    the same numbers, so the two have to be read together by somebody deciding
    which phone to remove.
    """
    await _enrol_second_device(me, admin_app, monkeypatch)
    db_session.commit()
    with admin_app.state.session_factory() as db:
        devices = _devices(db, me.staff.username)
        expected = [
            (
                d.name,
                d.created_at.strftime("%d %b %Y"),
                # The derivation, written out here rather than imported: the
                # counter is a TOTP time step and TOTP_INTERVAL is its width.
                # A test that called the view's own helper would agree with it
                # about a wrong column just as readily.
                datetime.fromtimestamp(
                    d.last_counter * TOTP_INTERVAL, tz=timezone.utc
                ).replace(tzinfo=None).strftime("%d %b %Y, %H:%M"),
                d.enrolled_at.strftime("%d %b %Y"),
            )
            for d in devices
        ]

    body = (await me.get(SECURITY_URL)).text
    rows = _device_rows(body)

    assert len(rows) == 2
    assert [row[0] for row in rows] == ["1", "2"]

    # The *name* is each row's header, not its number. A row headed "1" makes
    # a screen reader announce "1" before every cell in that row - the number
    # twice and the device not at all.
    tbody = _element(body, r"<tbody>.*?</tbody>")
    headers = [_text(c) for c in re.findall(
        r'<th scope="row"[^>]*>(.*?)</th>', tbody, re.S
    )]
    assert headers == [name for name, _, _, _ in expected], headers

    for row, (name, added, used, enrolled) in zip(rows, expected):
        assert row[1] == name
        assert row[2] == added
        assert row[3] == used
        assert row[4] == f"In use since {enrolled}"


async def test_a_device_that_has_never_been_used_says_so(me):
    """`last_counter` NULL is the ordinary state of an enrolment in progress,
    and it must not render as an epoch date or as a blank cell.

    Read out of the begin-device response, which is the only place an
    unconfirmed row is ever rendered now: it is discarded on the next GET of
    this page, because the QR and the setup key are shown once and the row can
    never be picked up again. Its status therefore says "Being set up now"
    rather than the "Set-up not finished" this screen used to leave sitting in
    the list with no control able to clear it.
    """
    begun = await _post(
        me, action="begin-device", device_name="Backup phone",
        current_password=PASSWORD,
    )
    assert begun.status_code == 200

    rows = _device_rows(begun.text)

    assert len(rows) == 2
    assert rows[1][1] == "Backup phone"
    assert rows[1][3] == "Never used"
    assert rows[1][4] == "Being set up now"


async def test_add_and_remove_are_above_the_table_and_not_inside_a_row(
    me, admin_app, db_session, monkeypatch
):
    """Actions on the list, not a control repeated in every row.

    A per-row Remove asks somebody to choose and act in one press. Above the
    table it is: press Remove, choose which, then prove who you are.
    """
    await _enrol_second_device(me, admin_app, monkeypatch)
    db_session.commit()
    body = (await me.get(SECURITY_URL)).text

    add = body.index('id="add-authenticator"')
    remove = body.index('id="remove-authenticator"')
    table = body.index('<table class="devices">')
    assert add < table, "Add is not above the table"
    assert remove < table, "Remove is not above the table"

    rows = _element(body, r"<tbody>.*?</tbody>")
    assert "<form" not in rows, rows
    assert "<button" not in rows, rows
    assert "current_password" not in rows, rows


async def test_each_dialog_has_an_accessible_name_and_a_button_that_names_it(me):
    """Not "the words appear in the body".

    A dialog with no accessible name announces itself as "dialog" and nothing
    else, and a button that does not reference the dialog it opens is a button
    whose relationship exists only in the stylesheet.
    """
    body = (await me.get(SECURITY_URL)).text

    for dialog_id, button_id in (
        ("dialog-add", "add-authenticator"),
        ("dialog-remove", "remove-authenticator"),
        ("dialog-password", "change-password"),
    ):
        tag = _element(body, rf'<dialog id="{dialog_id}"[^>]*>')
        labelled_by = re.search(r'aria-labelledby="([^"]+)"', tag)
        assert labelled_by, tag

        # The element it names exists, is a heading, and has words in it.
        heading = _element(
            body, rf'<h2 id="{labelled_by.group(1)}"[^>]*>.*?</h2>'
        )
        assert len(_text(heading)) > 3, heading

        opener = _element(body, rf'<button[^>]*id="{button_id}"[^>]*>')
        assert 'type="button"' in opener, opener
        assert f'aria-controls="{dialog_id}"' in opener, opener
        assert f'data-dialog="{dialog_id}"' in opener, opener


async def test_the_dialog_module_is_a_module_and_is_actually_served(me):
    """The tag is right *and* the file is there.

    A `<script>` tag naming a file the panel does not serve is a page whose
    buttons do nothing, and it looks identical in the markup to one that
    works - which is the shape of the failure that shipped five guidance
    blocks in a wheel that did not contain them.
    """
    body = (await me.get(SECURITY_URL)).text
    tag = _element(body, r"<script[^>]*security\.js[^>]*>")
    assert 'type="module"' in tag, tag
    assert "defer" in tag, tag

    served = await me.get("/admin/static/security.js")

    assert served.status_code == 200, served.status_code
    assert "showModal" in served.text


async def test_the_enrolment_dialog_is_open_without_javascript(me):
    """The one step whose secret is shown exactly once per re-authenticated
    request. It is server-rendered with `open` in the markup, so the QR is on
    screen with the module blocked, missing or still loading; the module only
    upgrades it to a modal."""
    begun = await _post(
        me, action="begin-device", device_name="Backup phone",
        current_password=PASSWORD,
    )

    assert begun.status_code == 200
    tag = _element(begun.text, r'<dialog id="dialog-enrol"[^>]*>')
    assert re.search(r"\bopen\b", tag), tag
    # It has no opener, so it names where the keyboard goes when it closes.
    assert 'data-return-focus="add-authenticator"' in tag, tag
    dialog = _element(begun.text, r'<dialog id="dialog-enrol".*?</dialog>')
    assert "<svg" in dialog, "the QR is not inside the dialog"


async def test_a_refused_action_comes_back_inside_its_own_dialog(me):
    """A modal dialog covers the page behind it, so a message printed there is
    a message nobody reads. The refusal has to be where the person is."""
    refused = await _post(
        me, action="begin-device", device_name="Backup phone",
        current_password="not-the-password",
    )

    assert refused.status_code == 400
    tag = _element(refused.text, r'<dialog id="dialog-add"[^>]*>')
    assert re.search(r"\bopen\b", tag), tag
    dialog = _element(refused.text, r'<dialog id="dialog-add".*?</dialog>')
    assert "not the current password" in _text(dialog), _text(dialog)

    # ...and *only* there. The page-level banner is suppressed when a dialog
    # is carrying the message, which is the whole job of the
    # `{% if error and not open_dialog %}` gate: printed in both places, the
    # copy on the page sits behind the backdrop, where it is announced to a
    # screen reader and invisible to everyone else. Asserting the message is
    # inside the dialog cannot see that; this can.
    outside = _text(re.sub(r"<dialog\b.*?</dialog>", " ", refused.text, flags=re.S))
    assert "not the current password" not in outside, outside


async def test_the_remove_dialog_numbers_its_choices_as_the_table_does(
    me, admin_app, db_session, monkeypatch
):
    """The chooser lives in the dialog, because Remove needs a target and the
    table is a list rather than a set of controls."""
    await _enrol_second_device(me, admin_app, monkeypatch)
    db_session.commit()
    with admin_app.state.session_factory() as db:
        ids = [d.id for d in _devices(db, me.staff.username)]
    body = (await me.get(SECURITY_URL)).text

    dialog = _element(body, r'<dialog id="dialog-remove".*?</dialog>')
    for number, device_id in enumerate(ids, start=1):
        option = _element(
            dialog, rf'<label class="choose__option" for="remove-{device_id}".*?</label>'
        )
        assert _text(option).startswith(f"{number} —"), _text(option)
        assert 'name="device_id"' in option, option
        assert f'value="{device_id}"' in option, option
        assert 'type="radio"' in option, option


async def test_the_chooser_focuses_a_choice_it_actually_offers(me):
    """The arrangement that catches a `loop.first` taken over the wrong list.

    One confirmed authenticator, which cannot be removed, plus an unfinished
    enrolment, which can. The first *device* is therefore not the first
    *option*, so an initial focus keyed on the device list marks nothing at
    all - and a Remove dialog the server reopens after a refusal then opens
    with the keyboard nowhere. The numbering has to keep counting devices
    even so, or the dialog and the table stop agreeing.

    Read out of the begin-device response rather than a following GET: that
    GET now discards the unfinished enrolment, so the response that renders
    the QR is where this arrangement exists. The property under test has not
    moved - the dialog still has to loop over what may be removed rather than
    over every device - only the request that produces it.
    """
    begun = await _post(
        me, action="begin-device", device_name="Backup phone",
        current_password=PASSWORD,
    )
    assert begun.status_code == 200

    dialog = _element(begun.text, r'<dialog id="dialog-remove".*?</dialog>')

    options = re.findall(r'<label class="choose__option".*?</label>', dialog, re.S)
    assert len(options) == 1, options
    # The unfinished enrolment is the second device, and says so.
    assert _text(options[0]).startswith("2 — Backup phone"), _text(options[0])
    assert "data-initial-focus" in options[0], options[0]


async def test_the_only_authenticator_is_explained_rather_than_hidden(me):
    """The refusal is a rule, and it says so. A control that is simply absent
    reads as a bug, and this property survived the rebuild."""
    dialog = _element(
        (await me.get(SECURITY_URL)).text, r'<dialog id="dialog-remove".*?</dialog>'
    )

    readable = _text(dialog)
    assert "only authenticator on the account" in readable, readable
    assert "Authenticator is the only" in readable, readable
    # ...and there is nothing to select, because there is nothing removable.
    assert 'name="device_id"' not in dialog, dialog


async def test_the_requirements_did_not_move_when_the_prompt_did(
    me, admin_app, db_session, monkeypatch
):
    """The rebuild is a rearrangement of *when* the prompt appears.

    The change-password dialog asks for the current password and offers no
    code field at all, and a code alone is still refused at the URL -
    `_reauthenticate(..., password_only=True)`. A session presenting a code
    has already cleared the second factor, so a code proves nothing the cookie
    did not; the current password is the one secret a stolen session does not
    carry.
    """
    dialog = _element(
        (await me.get(SECURITY_URL)).text, r'<dialog id="dialog-password".*?</dialog>'
    )
    assert 'name="action" value="change-password"' in dialog, dialog
    assert 'name="current_password"' in dialog, dialog
    # No code field in this dialog: it is not an accepted proof here, and a
    # field that is refused on submission is worse than no field.
    assert 'name="current_code"' not in dialog, dialog

    code = _fresh_totp(admin_app, me.staff.username, me.secret, monkeypatch)
    refused = await _post(
        me, action="change-password", current_code=code,
        password=NEW_PASSWORD, confirm=NEW_PASSWORD,
    )

    assert refused.status_code == 400
    assert verify_password(
        PASSWORD, get_staff(db_session, me.staff.username).password_hash
    )
    # The status code alone does not say *why*. A throttle lock, a CSRF
    # expiry and a malformed form all answer 400 with the password equally
    # unchanged, so a test asserting only that would pass against a page
    # that had stopped requiring the password and started failing for some
    # other reason entirely. This is `_reauthenticate`'s password_only
    # branch, named, inside the dialog the person is looking at.
    refusal = _text(_element(refused.text, r'<dialog id="dialog-password".*?</dialog>'))
    assert "Enter your current password to confirm this change." in refusal, refusal

    # The add and remove dialogs, by contrast, take either - and the code is
    # offered first, which is what Step 3 asked for. Remove needs something it
    # is allowed to remove before it renders a form at all.
    await _enrol_second_device(me, admin_app, monkeypatch)
    for dialog_id in ("dialog-add", "dialog-remove"):
        markup = _element(
            (await me.get(SECURITY_URL)).text, rf'<dialog id="{dialog_id}".*?</dialog>'
        )
        assert 'name="current_code"' in markup, dialog_id
        assert 'name="current_password"' in markup, dialog_id
        assert markup.index('name="current_code"') < markup.index(
            'name="current_password"'
        ), dialog_id


async def test_the_password_section_states_its_own_age(me, db_session):
    """"Last changed on ⟨date⟩", read out of the audit trail.

    This account was created and given a password by the service layer
    directly, which writes no audit entry, so the honest answer at first is
    that there is no record - never `created_at`, and never today. A change
    made through the screen then puts a real date there.
    """
    before = _text((await me.get(SECURITY_URL)).text)
    assert "Last changed: not recorded" in before, before
    assert "Last changed on" not in before, before

    changed = await _post(
        me, action="change-password", current_password=PASSWORD,
        password=NEW_PASSWORD, confirm=NEW_PASSWORD,
    )

    assert changed.status_code == 200
    after = _text(changed.text)
    today = datetime.now(timezone.utc).strftime("%d %b %Y")
    assert f"Last changed on {today}" in after, after
    assert "not recorded" not in after, after

