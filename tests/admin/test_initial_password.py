"""The unclaimed initial password. Contract §8.3, v1.15 item 3.

**The premise this feature was requested on was false, and the tests here start
by pinning that.** The request was to show the initial password more than once,
on the stated understanding that while ``must_change_password`` is true the
password "is not yet hashed into the database" — so that revealing it would be
a matter of showing something already sitting there in the clear. It is not:
``create_staff`` has always called ``hash_password`` at creation and
``staff.password_hash`` is NOT NULL with no default. ``test_the_password_hash_is_bcrypt_from_the_moment_of_creation``
below is that fact as an assertion, because the whole security argument for
this feature rests on it: what was added is a **second, separately encrypted
copy**, not the removal of a hash.

**What is being tested, in order of how much it matters.**

1. *The column is cleared on every path that changes a password.* Three paths —
   self-service, the forced change at first login, and an administrator issuing
   a replacement — each with its own test, driven at the layer a person
   actually reaches it through wherever that is HTTP. A path that changed a
   password and left the column alone would leave a live, readable credential
   on an account whose password is now something else. That is the one failure
   mode worth more than the feature.
2. *The value never reaches the audit log.* The trail is append-only, so a copy
   there outlives the column by the life of the deployment.
3. *Only an administrator can reveal it, and only with proof.*
   tests/admin/test_role_matrix.py holds the role floor for the two routes;
   this file holds the proof requirement, which is the part a correct role
   check does not give you.
4. *The reveal is recorded.*
"""

import dataclasses
import re

import pytest
from sqlalchemy import select, text

from admin.accounts import (
    create_staff,
    get_staff,
    issue_password,
    reveal_initial_password,
    set_password,
)
from admin.models import AuditLog, Staff, StaffRole
from admin.security import (
    INITIAL_PASSWORD_ENCRYPTION_INFO,
    TOTP_ENCRYPTION_INFO,
    decrypt_initial_password,
    encrypt_initial_password,
    verify_password,
)
from tests.admin.conftest import (
    SECRET_KEY,
    _cleanup_staff_named,
    _committed_session,  # noqa: F401  - re-exposed as `session` below
    _resync,
)

#: `pytest.mark.db` for the whole file; `pytest.mark.asyncio` per test rather
#: than in this list, because roughly half the tests below are synchronous
#: service-layer checks and pytest-asyncio warns on every one carrying a mark
#: it will not use. tests/admin/test_factor_set_actions.py uses the same
#: per-test form for the same reason.
pytestmark = [pytest.mark.db]


@pytest.fixture
def session(_committed_session):  # noqa: F811
    """This file's name for the hard-committing session against the running
    admin app's own database — see tests/admin/conftest.py's
    `_committed_session` docstring. Load-bearing: `admin_client` drives real
    HTTP through a different connection entirely, so a row seeded through
    tests/conftest.py's rolled-back `session` would be invisible to it.
    """
    return _committed_session


#: Every account this file creates carries this prefix, so one teardown
#: predicate covers all of them however a test failed part-way through.
_PREFIX = "ip-test-"


@pytest.fixture(autouse=True)
def _cleanup(admin_app):
    def sweep():
        factory = admin_app.state.session_factory
        with factory() as db:
            names = db.scalars(
                select(Staff.username).where(Staff.username.like(f"{_PREFIX}%"))
            ).all()
        if names:
            _cleanup_staff_named(admin_app, *names)

    # Before as well as after: an interrupted earlier run leaves rows behind,
    # and `create_staff` refuses a username that is taken - which would present
    # as this file failing for a reason that has nothing to do with it.
    sweep()
    yield
    sweep()


def _make(session, *, role=StaffRole.staff, suffix="a"):
    staff, password = create_staff(
        session,
        username=f"{_PREFIX}{suffix}",
        display_name="Initial Password Test",
        role=role,
        actor="test",
        secret_key=SECRET_KEY,
    )
    session.commit()
    return staff, password


# --- the premise ------------------------------------------------------------


def test_the_password_hash_is_bcrypt_from_the_moment_of_creation(session):
    """The false premise, pinned.

    `must_change_password` says nothing at all about how the password is
    stored. `password_hash` is a bcrypt hash the instant the row exists — the
    `$2b$` prefix is bcrypt's own identifier — and `verify_password` accepts
    the plaintext against it, which a stored-in-the-clear value would not
    satisfy since `verify_password` is `bcrypt.checkpw`.
    """
    staff, password = _make(session)
    _resync(session)
    staff = get_staff(session, staff.username)

    assert staff.must_change_password is True
    assert staff.password_hash.startswith("$2b$"), (
        "password_hash is not a bcrypt hash; the premise that a "
        "must_change_password account stores its password unhashed would be "
        "true and this whole feature would be a different question"
    )
    assert verify_password(password, staff.password_hash) is True
    assert password not in staff.password_hash


def test_the_stored_copy_is_a_second_thing_and_not_the_plaintext(session):
    """`initial_password_enc` is ciphertext, not the password with a new name.

    Asserted three ways, because "it is encrypted" is exactly the claim that a
    base64 encoding would also appear to satisfy: the password's bytes do not
    occur in the blob, the blob does not decrypt under the *other* purpose's
    key, and it does decrypt under this one.
    """
    staff, password = _make(session)
    _resync(session)
    staff = get_staff(session, staff.username)

    blob = staff.initial_password_enc
    assert blob is not None
    assert password.encode() not in blob
    assert decrypt_initial_password(blob, secret_key=SECRET_KEY) == password


def test_the_two_encryption_purposes_do_not_share_a_key():
    """HKDF `info` separation, asserted rather than assumed.

    admin/security.py's own comment requires it ("any future use of SECRET_KEY
    must pick a different value here"), and nothing enforced it. Two purposes
    sharing one key is silent: each decrypts the other's values perfectly, so
    the only way it surfaces is a rotation or an analysis that turns out to
    cover more than it was meant to.
    """
    assert INITIAL_PASSWORD_ENCRYPTION_INFO != TOTP_ENCRYPTION_INFO

    from admin.security import decrypt_totp_secret

    blob = encrypt_initial_password("a-known-value", secret_key=SECRET_KEY)
    with pytest.raises(Exception):
        # A TOTP-key decrypt of an initial-password token must fail. If this
        # ever passes, the two `info` values have converged.
        decrypt_totp_secret(blob, secret_key=SECRET_KEY)


# --- the clearing paths -----------------------------------------------------


def test_set_password_clears_it(session):
    """Path 1 and 2 in one function, and that is the point of it being one.

    `set_password` has exactly two callers - the self-service screen
    (`/admin/security`, admin/self_service_view.py) and the forced change at
    first login (`/admin/change-password`, admin/views.py). Clearing here
    rather than in either view is what makes the HTTP test below pass for a
    structural reason rather than as a coincidence of two views each
    remembering. (There is no `set-password` CLI command; `issue-password` is
    the CLI's way to replace a password and it clears the column itself.)
    """
    staff, _ = _make(session)
    _resync(session)
    assert get_staff(session, staff.username).initial_password_enc is not None

    set_password(session, staff.username, "a-password-of-their-own")
    session.commit()
    _resync(session)

    assert get_staff(session, staff.username).initial_password_enc is None


def test_issue_password_clears_it(session):
    """Path 3. An administrator-issued replacement is a one-time reveal.

    The column is scoped to "the password the account was created with", so
    issuing a new one clears it rather than storing the new value. Leaving it
    as it was would be the real defect: the row would go on offering a password
    that no longer opens the account.
    """
    staff, _ = _make(session)
    _resync(session)

    replacement = issue_password(session, staff.username, actor="somebody-else")
    session.commit()
    _resync(session)

    staff = get_staff(session, staff.username)
    assert staff.initial_password_enc is None
    assert staff.must_change_password is True, (
        "must_change_password and initial_password_enc are different facts; "
        "this is the state that proves the list page cannot read one off the "
        "other"
    )
    assert verify_password(replacement, staff.password_hash) is True


@pytest.mark.asyncio
async def test_the_forced_change_at_first_login_clears_it(admin_app, client, session):
    """Path 2, over real HTTP, through the page a new account is actually sent
    to — not by calling `set_password` again.

    This is the path that decides how long the column exists at all, and it is
    the one a guard in the wrong layer would miss: `/admin/change-password` is
    reached with a *pending* login and no session key, through
    `admin/backend.py`'s pre-login gate, which is a different code path from
    every other screen on the panel.
    """
    staff, password = _make(session)
    _resync(session)
    assert get_staff(session, staff.username).initial_password_enc is not None

    headers = {
        "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36",
        "accept": "text/html,application/xhtml+xml",
        "sec-fetch-mode": "navigate",
    }
    login = await client.post(
        "/admin/login",
        data={"username": staff.username, "password": password},
        headers=headers,
        follow_redirects=False,
    )
    assert login.status_code == 302
    assert login.headers["location"].endswith("/admin/change-password"), (
        "a fresh account should be sent to the forced password change"
    )

    page = await client.get("/admin/change-password", headers=headers)
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert token, "no CSRF token on the forced change page"

    changed = await client.post(
        "/admin/change-password",
        data={
            "password": "a-password-of-their-own",
            "confirm": "a-password-of-their-own",
            "csrf_token": token.group(1),
        },
        headers=headers,
        follow_redirects=False,
    )
    assert changed.status_code == 302, changed.text[:400]

    _resync(session)
    staff = get_staff(session, staff.username)
    assert staff.must_change_password is False
    assert staff.initial_password_enc is None, (
        "the forced change at first login left the initial password stored - "
        "a readable credential on an account whose password is now something "
        "else"
    )


# --- the audit trail --------------------------------------------------------


def test_the_ciphertext_never_reaches_the_audit_log(session):
    """`row_to_dict` snapshots every mapped column; the trail is append-only.

    A copy in `audit_log` would outlive the column by the life of the
    deployment, which defeats the entire point of clearing it. And it is the
    *ciphertext* that has to be kept out, not just a plaintext: the key is
    derived from SECRET_KEY, which anything able to read this table already
    has, so ciphertext in the trail is plaintext in the trail.
    """
    from admin.audit import REDACTED_FIELDS, row_to_dict
    from db.repository import _json_safe

    assert "initial_password_enc" in REDACTED_FIELDS

    staff, password = _make(session)
    _resync(session)
    staff = get_staff(session, staff.username)

    snapshot = row_to_dict(staff)
    assert snapshot["initial_password_enc"] == staff.initial_password_enc
    safe = _json_safe(snapshot)
    assert safe["initial_password_enc"] == "[redacted]"
    assert password not in repr(safe)


def test_revealing_writes_an_audit_entry_that_does_not_carry_the_value(session):
    staff, password = _make(session)
    _resync(session)

    revealed = reveal_initial_password(
        session, staff.username, actor="an-administrator", secret_key=SECRET_KEY
    )
    session.commit()
    _resync(session)

    assert revealed == password

    entry = session.scalars(
        select(AuditLog)
        .where(AuditLog.table_name == "staff", AuditLog.row_id == staff.id)
        .order_by(AuditLog.id.desc())
    ).first()
    assert entry.action == "reveal"
    assert entry.actor == "an-administrator"
    assert entry.after_json == {
        "username": staff.username, "revealed": "initial_password"
    }
    assert password not in repr(entry.after_json)
    assert password not in repr(entry.before_json)


def test_revealing_nothing_returns_none_and_writes_no_entry(session):
    """"There is nothing here" is a first-class answer, not an error.

    And it writes no audit row: an entry claiming a reveal that showed nothing
    would put noise into the one table that exists to be trusted, and would
    make "who has seen this password" unanswerable from the trail.
    """
    staff, _ = _make(session)
    set_password(session, staff.username, "a-password-of-their-own")
    session.commit()
    _resync(session)

    before = session.scalar(
        select(text("COUNT(*)")).select_from(AuditLog)
        .where(AuditLog.table_name == "staff", AuditLog.row_id == staff.id)
    )
    assert reveal_initial_password(
        session, staff.username, actor="an-administrator", secret_key=SECRET_KEY
    ) is None
    session.commit()
    _resync(session)
    after = session.scalar(
        select(text("COUNT(*)")).select_from(AuditLog)
        .where(AuditLog.table_name == "staff", AuditLog.row_id == staff.id)
    )
    assert after == before


# --- the screen -------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_page_will_not_show_it_without_proof(admin_client, session):
    """The role floor is not the whole guard, and this is the half it misses.

    tests/admin/test_role_matrix.py already proves a `staff` account cannot
    reach this route at all. What it cannot prove is that an *administrator*
    session on its own is not enough — which is the property that matters,
    because a stolen session is an administrator session. Driven with a wrong
    password rather than none: an empty proof could be refused by a
    "some field was blank" check that a real credential test would not have.
    """
    staff, password = _make(session)
    _resync(session)

    page = await admin_client.get(f"/admin/staff/initial-password?pks={staff.id}")
    assert page.status_code == 200
    assert password not in page.text, (
        "the page showed the password before any proof was given"
    )
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert token

    refused = await admin_client.post(
        "/admin/staff/initial-password",
        data={
            "pks": str(staff.id),
            "csrf_token": token.group(1),
            "current_password": "not-the-administrator-s-password",
        },
    )
    assert refused.status_code == 400
    assert password not in refused.text


@pytest.mark.asyncio
async def test_an_administrator_who_proves_it_sees_the_password(admin_client, session):
    """The other half, and the one that stops every assertion above from
    passing against a page that shows nothing to anybody.

    `admin_client` carries the acting administrator's own password
    (tests/admin/conftest.py hangs it off the client for exactly this), so the
    permitted case can be driven and not only the refused one.
    """
    staff, password = _make(session)
    _resync(session)

    page = await admin_client.get(f"/admin/staff/initial-password?pks={staff.id}")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert token

    shown = await admin_client.post(
        "/admin/staff/initial-password",
        data={
            "pks": str(staff.id),
            "csrf_token": token.group(1),
            "current_password": admin_client.password,
        },
    )
    assert shown.status_code == 200
    assert password in shown.text, shown.text[:600]

    _resync(session)
    entry = session.scalars(
        select(AuditLog)
        .where(AuditLog.table_name == "staff", AuditLog.row_id == staff.id)
        .order_by(AuditLog.id.desc())
    ).first()
    assert entry.action == "reveal"
    assert entry.actor == admin_client.staff.username


@pytest.mark.asyncio
async def test_the_list_says_a_password_is_waiting_without_showing_it(
    admin_client, session
):
    """The state is administrator-public; the value is not.

    Both halves in one test on purpose: an assertion that the password is
    absent from the list would pass against a list that rendered no such
    account at all.
    """
    staff, password = _make(session)
    _resync(session)

    listing = await admin_client.get("/admin/staff/list?pageSize=100")
    assert listing.status_code == 200
    assert staff.username in listing.text, "the account was not on the list at all"
    assert "Yes - can be revealed" in listing.text
    assert password not in listing.text, "the list page rendered the password itself"


@pytest.mark.asyncio
async def test_the_details_page_does_not_render_the_ciphertext(admin_client, session):
    """`column_details_list` defaults to *every* mapped column.

    This is a live defect this project has already had once — the staff details
    page rendered the bcrypt hash and the encrypted TOTP secret in full, from a
    list the same view correctly redacted. `initial_password_enc` is a new
    mapped column and would have joined them without `column_details_list`
    being explicit.
    """
    staff, password = _make(session)
    _resync(session)

    details = await admin_client.get(f"/admin/staff/details/{staff.id}")
    assert details.status_code == 200
    assert staff.username in details.text
    assert password not in details.text

    blob = get_staff(session, staff.username).initial_password_enc
    assert blob is not None
    assert blob.decode("ascii", "ignore") not in details.text


@pytest.mark.asyncio
async def test_revealing_is_refused_after_the_password_is_claimed(
    admin_client, session
):
    """End to end: create, reveal, change, reveal again.

    The sequence rather than the states separately, because what is being
    asserted is that the first reveal and the second differ *for this one
    account* — two tests against two accounts would pass against an
    implementation that never showed anything to anyone.
    """
    staff, password = _make(session)
    _resync(session)

    async def attempt():
        page = await admin_client.get(
            f"/admin/staff/initial-password?pks={staff.id}"
        )
        token = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
        return await admin_client.post(
            "/admin/staff/initial-password",
            data={
                "pks": str(staff.id),
                "csrf_token": token.group(1),
                "current_password": admin_client.password,
            },
        )

    first = await attempt()
    assert password in first.text

    set_password(session, staff.username, "a-password-of-their-own")
    session.commit()
    _resync(session)

    second = await attempt()
    assert second.status_code == 200
    assert password not in second.text
    assert "Not recoverable" in second.text


# --- key rotation -----------------------------------------------------------


def test_a_key_rotation_carries_the_unclaimed_passwords_across(session):
    """Left out of `rotate-key`, every unclaimed password becomes an
    undecryptable blob and the reveal page 500s for the accounts that were
    mid-onboarding when the key changed.
    """
    from admin.cli import cmd_rotate_key

    staff, password = _make(session)
    session.commit()
    _resync(session)

    new_key = "a-different-secret-key-for-this-test"
    _totp, initial_count, _blocks = cmd_rotate_key(
        session, old_key=SECRET_KEY, new_key=new_key
    )
    session.commit()
    _resync(session)

    assert initial_count >= 1
    staff = get_staff(session, staff.username)
    assert decrypt_initial_password(
        staff.initial_password_enc, secret_key=new_key
    ) == password


@pytest.mark.asyncio
async def test_an_unrotated_key_change_is_explained_rather_than_a_500(
    admin_app, admin_client, session, monkeypatch
):
    """SECRET_KEY changed without `kaicalc-admin rotate-key`.

    The ciphertext then cannot be opened, and the naive outcome is a stack
    trace on an administrator screen that names neither the cause nor the way
    out. Both exist: `issue-password` mints a working credential, and
    `rotate-key` re-wraps every stored secret. The page says so.

    Driven by pointing the *running app's* settings at a different key rather
    than by writing a corrupt blob, because that is the shape the failure
    actually takes - a real deployment has good ciphertext under the old key,
    not damaged ciphertext.
    """
    staff, password = _make(session)
    _resync(session)

    page = await admin_client.get(f"/admin/staff/initial-password?pks={staff.id}")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert token

    # `create_app` hangs the Runtime off sqladmin's own mounted Starlette
    # app, not the outer FastAPI's state (admin/runtime.py), which is the
    # object a view's `request.app` resolves to. Reached here the same way.
    #
    # Both `Runtime` and `Settings` are frozen dataclasses, so this replaces
    # the whole Runtime on the mounted app's `state` (a plain namespace, which
    # is not frozen). `dataclasses.replace` twice keeps every other setting and
    # every other Runtime field the app was built with - which matters, because
    # this same Runtime carries the throttle that the re-authentication step
    # below reads.
    mounted = _mounted_admin_app(admin_app)
    runtime = mounted.state.runtime
    monkeypatch.setattr(
        mounted.state,
        "runtime",
        dataclasses.replace(
            runtime,
            settings=dataclasses.replace(
                runtime.settings, secret_key="a-completely-different-secret-key"
            ),
        ),
    )

    answer = await admin_client.post(
        "/admin/staff/initial-password",
        data={
            "pks": str(staff.id),
            "csrf_token": token.group(1),
            "current_password": admin_client.password,
        },
    )
    assert answer.status_code == 400, "a 500 here is the failure being fixed"
    assert password not in answer.text
    assert "rotate-key" in answer.text
    assert "Issue a new password" in answer.text


def _mounted_admin_app(app):
    """sqladmin's mounted sub-application, which carries `state.runtime`."""
    for route in app.routes:
        inner = getattr(route, "app", None)
        if inner is not None and hasattr(getattr(inner, "state", None), "runtime"):
            return inner
    raise AssertionError("sqladmin's mounted application was not reachable")
