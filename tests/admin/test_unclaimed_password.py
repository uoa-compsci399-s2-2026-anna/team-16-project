"""The unclaimed password. Contract §8.3, v1.15 item 3, widened by v1.16.

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

**What v1.16 changed, and why half of this file is about it.** v1.15 stored the
password an account was *created* with and had ``issue_password`` clear the
column. So the loss the feature exists to prevent — a closed tab taking a
password nobody wrote down — was prevented for a created password and not for
an issued one, and the only recovery from the second was to issue *another*,
which stops the one already read out to the colleague from working. The owner
ruled that the two be aligned. ``issue_password`` now **overwrites** the column
with what it mints, and the column was renamed
``initial_password_enc`` → ``unclaimed_password_enc`` (migration ``0012``)
because a non-NULL value no longer means "this account has never been used".

**What is being tested, in order of how much it matters.**

1. *``set_password`` clears the column on every path that reaches it, whatever
   put a value there.* Self-service and the forced change at first login, each
   driven at the layer a person actually reaches it through, and each driven
   over a *created* password and over an *issued* one — which under v1.15 was
   an unreachable state and is now the ordinary one. A path that changed a
   password and left the column alone would leave a live, readable credential
   on an account whose password is now something else. That is the one failure
   mode worth more than the feature.
2. *``issue_password`` leaves a readable value behind, and it is the new one.*
   Both halves: a test that the column is non-NULL would pass against an
   implementation that never overwrote a stale value, which is the failure that
   hands an administrator a dead string to read out.
3. *A created account and an account handed a replacement land in the same
   state.* That is what "align them" means, asserted as one comparison rather
   than as two separate tests that could both drift.
4. *The value never reaches the audit log.* The trail is append-only, so a copy
   there outlives the column by the life of the deployment.
5. *Only an administrator can reveal it, and only with proof.*
   tests/admin/test_role_matrix.py holds the role floor for the two routes;
   this file holds the proof requirement, which is the part a correct role
   check does not give you.
6. *The reveal is recorded.*
"""

import dataclasses
import re

import pytest
from sqlalchemy import select, text

from admin.accounts import (
    create_staff,
    get_staff,
    issue_password,
    reveal_unclaimed_password,
    set_password,
)
from admin.models import AuditLog, Staff, StaffRole
from admin.security import (
    UNCLAIMED_PASSWORD_ENCRYPTION_INFO,
    TOTP_ENCRYPTION_INFO,
    decrypt_unclaimed_password,
    encrypt_unclaimed_password,
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

#: What `admin/protection.py` needs to see before it will treat a request with
#: no session cookie as a person rather than a script - the same block
#: tests/admin/conftest.py's `_login` sends, and needed here for the same
#: reason: the two forced-change tests below drive `/admin/login` directly,
#: before any cookie exists.
_BROWSER_HEADERS = {
    "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0 Safari/537.36",
    "accept": "text/html,application/xhtml+xml",
    "sec-fetch-mode": "navigate",
}


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
    """`unclaimed_password_enc` is ciphertext, not the password with a new name.

    Asserted three ways, because "it is encrypted" is exactly the claim that a
    base64 encoding would also appear to satisfy: the password's bytes do not
    occur in the blob, the blob does not decrypt under the *other* purpose's
    key, and it does decrypt under this one.
    """
    staff, password = _make(session)
    _resync(session)
    staff = get_staff(session, staff.username)

    blob = staff.unclaimed_password_enc
    assert blob is not None
    assert password.encode() not in blob
    assert decrypt_unclaimed_password(blob, secret_key=SECRET_KEY) == password


def test_the_two_encryption_purposes_do_not_share_a_key():
    """HKDF `info` separation, asserted rather than assumed.

    admin/security.py's own comment requires it ("any future use of SECRET_KEY
    must pick a different value here"), and nothing enforced it. Two purposes
    sharing one key is silent: each decrypts the other's values perfectly, so
    the only way it surfaces is a rotation or an analysis that turns out to
    cover more than it was meant to.
    """
    assert UNCLAIMED_PASSWORD_ENCRYPTION_INFO != TOTP_ENCRYPTION_INFO

    from admin.security import decrypt_totp_secret

    blob = encrypt_unclaimed_password("a-known-value", secret_key=SECRET_KEY)
    with pytest.raises(Exception):
        # A TOTP-key decrypt of an initial-password token must fail. If this
        # ever passes, the two `info` values have converged.
        decrypt_totp_secret(blob, secret_key=SECRET_KEY)


# --- what issue_password leaves behind (contract v1.16) ---------------------


def test_issue_password_stores_the_replacement(session):
    """The alignment itself, at the service layer.

    Under v1.15 this column was NULL after an issue and the replacement was a
    one-time reveal. That is what the owner ruled should change: the loss the
    feature prevents for a created password was not prevented for an issued
    one, and the only recovery was to issue yet another - invalidating a
    password that may already have been handed over.

    Three assertions, and none of them is redundant. Non-NULL alone would pass
    against an implementation that simply stopped touching the column (which
    would leave the *creation* password sitting there, opening nothing).
    Decrypting alone would pass against one that stored something unrelated.
    The pairing - it is non-NULL, it decrypts to exactly the value returned to
    the caller, and that value is the one the account's hash now verifies - is
    what says the stored copy is the live credential.
    """
    staff, created = _make(session)
    _resync(session)

    replacement = issue_password(
        session, staff.username, actor="somebody-else", secret_key=SECRET_KEY
    )
    session.commit()
    _resync(session)

    staff = get_staff(session, staff.username)
    assert staff.unclaimed_password_enc is not None, (
        "an issued password left nothing to reveal - the v1.15 asymmetry"
    )
    assert decrypt_unclaimed_password(
        staff.unclaimed_password_enc, secret_key=SECRET_KEY
    ) == replacement
    assert verify_password(replacement, staff.password_hash) is True
    assert replacement != created


def test_issuing_overwrites_the_password_that_was_there(session):
    """The half a "the column is not NULL" test cannot see.

    An implementation that stopped clearing the column but never wrote to it
    would satisfy every assertion above except this one, and would be the worst
    of the three possible behaviours: the row would go on offering the password
    the account was *created* with, which the issue has just invalidated, and
    the administrator reading it out would be handing over a dead string with
    the panel's assurance behind it.
    """
    staff, created = _make(session)
    _resync(session)
    stored_before = get_staff(session, staff.username).unclaimed_password_enc

    replacement = issue_password(
        session, staff.username, actor="somebody-else", secret_key=SECRET_KEY
    )
    session.commit()
    _resync(session)

    stored_after = get_staff(session, staff.username).unclaimed_password_enc
    assert stored_after != stored_before
    assert decrypt_unclaimed_password(
        stored_after, secret_key=SECRET_KEY
    ) == replacement
    assert decrypt_unclaimed_password(stored_after, secret_key=SECRET_KEY) != created

    revealed = reveal_unclaimed_password(
        session, staff.username, actor="an-administrator", secret_key=SECRET_KEY
    )
    session.commit()
    assert revealed == replacement, "the reveal handed back the superseded password"


def test_a_created_account_and_an_issued_one_land_in_the_same_state(session):
    """"Align them", as one comparison rather than two tests that could drift.

    The observable state of an account holding a password it did not choose is
    three things: the forced change is owed, a value is stored, and the list
    can say so. Both minting paths must produce all three - that is the whole
    of what v1.16 asked for, and asserting it as a tuple equality means a
    future change to either path that moves one of them fails here rather than
    in whichever of the two files happened to cover it.

    `must_change_password` was already True on both paths before v1.16, and it
    is asserted anyway: the brief asked whether the two agreed, and a property
    that holds by accident and is never checked is one that stops holding
    quietly.
    """
    created_account, _ = _make(session, suffix="created")
    issued_account, _ = _make(session, suffix="issued")
    # Claim the second account's creation password first, so that what is
    # measured afterwards is the *issue*, not a leftover from creation.
    set_password(session, issued_account.username, "a-password-of-their-own")
    session.commit()
    issue_password(
        session, issued_account.username, actor="somebody-else",
        secret_key=SECRET_KEY,
    )
    session.commit()
    _resync(session)

    def state(username):
        row = get_staff(session, username)
        return (
            row.must_change_password,
            row.unclaimed_password_enc is not None,
            row.has_unclaimed_password,
        )

    assert state(issued_account.username) == state(created_account.username)
    assert state(created_account.username) == (True, True, True)


def test_a_token_written_under_the_v1_15_derivation_still_opens(session):
    """The key derivation is a compatibility contract with data already on
    disk, and v1.16's rename is exactly the change that would break it.

    ``UNCLAIMED_PASSWORD_ENCRYPTION_INFO`` reads
    ``b"initial-password-encryption"`` under a name that no longer says
    "initial", which invites a tidy-up. The `info` is HKDF input: change those
    bytes and a different key comes out, every value stored by v1.15 becomes a
    blob nothing can open, and **nothing in this suite would notice** - encrypt
    and decrypt stay consistent with each other within one process, so the
    failure appears only on a deployment, on a credential screen, for exactly
    the accounts that were mid-onboarding.

    So the fixture is a literal token, produced by v1.15's derivation under
    this file's SECRET_KEY and pasted here. It pins the whole derivation - the
    `info`, the hash, the length, the absent salt - rather than only the
    constant's spelling, which an equality assertion on the bytes would do and
    which would not catch a change to `_derive_key` itself.

    If this ever fails: do not regenerate the token. Restore the derivation,
    or ship a re-encryption pass in the migration that changes it.
    """
    token = (
        b"gAAAAABqe67nHLXy4ZtEfmjruFUb3-PETvmzl1ch9x8vdeC6CwI4vvmEI7D85sVHer"
        b"eEmUH9WWwSGkWbmTfcbef3oyJ2OFobM2OIYl4avKBR-uL6aXXWwpE="
    )
    assert decrypt_unclaimed_password(
        token, secret_key=SECRET_KEY
    ) == "a-password-written-by-v1-15"


# --- the clearing path ------------------------------------------------------


def test_set_password_clears_it(session):
    """Both of `set_password`'s callers in one function, and that is the point
    of it being one.

    `set_password` has exactly two - the self-service screen
    (`/admin/security`, admin/self_service_view.py) and the forced change at
    first login (`/admin/change-password`, admin/views.py). Clearing here
    rather than in either view is what makes the HTTP test below pass for a
    structural reason rather than as a coincidence of two views each
    remembering. (There is no `set-password` CLI command; `issue-password` is
    the CLI's way to replace a password, and since v1.16 it stores what it
    mints rather than clearing.)
    """
    staff, _ = _make(session)
    _resync(session)
    assert get_staff(session, staff.username).unclaimed_password_enc is not None

    set_password(session, staff.username, "a-password-of-their-own")
    session.commit()
    _resync(session)

    assert get_staff(session, staff.username).unclaimed_password_enc is None


def test_set_password_clears_an_issued_password_too(session):
    """The state v1.16 created, and the reason the clearing had to survive it.

    Under v1.15 an issued password left the column NULL, so "an account with a
    stored password that is not its creation password" did not exist, and no
    test could distinguish a `set_password` that clears unconditionally from
    one that only ever happened to be called on freshly created rows. That
    state exists now, and it is the ordinary one for any account past its
    first day.

    Driven with the creation password already claimed, so the value being
    cleared is unambiguously the issued one.
    """
    staff, _ = _make(session)
    set_password(session, staff.username, "a-password-of-their-own")
    session.commit()
    _resync(session)
    assert get_staff(session, staff.username).unclaimed_password_enc is None

    issue_password(
        session, staff.username, actor="somebody-else", secret_key=SECRET_KEY
    )
    session.commit()
    _resync(session)
    assert get_staff(session, staff.username).unclaimed_password_enc is not None

    set_password(session, staff.username, "a-second-password-of-their-own")
    session.commit()
    _resync(session)

    row = get_staff(session, staff.username)
    assert row.unclaimed_password_enc is None, (
        "an issued password survived the account setting one of its own - a "
        "readable credential on an account whose password is now something else"
    )
    assert row.must_change_password is False


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
    assert get_staff(session, staff.username).unclaimed_password_enc is not None

    headers = _BROWSER_HEADERS
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
    assert staff.unclaimed_password_enc is None, (
        "the forced change at first login left the initial password stored - "
        "a readable credential on an account whose password is now something "
        "else"
    )


@pytest.mark.asyncio
async def test_the_forced_change_after_an_issued_password_clears_it(
    admin_app, client, session
):
    """The same pre-login page, reached with an *issued* password in the
    column - a combination that could not occur before v1.16.

    It is worth its own test rather than being folded into the one above.
    `/admin/change-password` is the one screen a locked-out colleague reaches
    with a password an administrator has just read out to them, so it is the
    screen that decides how long an *issued* password stays readable; and it is
    reached through `admin/backend.py`'s pending-login gate, a different code
    path from every other page on the panel. Under v1.15 this test would have
    passed vacuously - the column was already NULL before the request.
    """
    staff, _created = _make(session)
    issued = issue_password(
        session, staff.username, actor="somebody-else", secret_key=SECRET_KEY
    )
    session.commit()
    _resync(session)
    assert get_staff(session, staff.username).unclaimed_password_enc is not None, (
        "nothing was stored, so this test would prove nothing about clearing"
    )

    login = await client.post(
        "/admin/login",
        data={"username": staff.username, "password": issued},
        headers=_BROWSER_HEADERS,
        follow_redirects=False,
    )
    assert login.status_code == 302, "the issued password should be accepted"
    assert login.headers["location"].endswith("/admin/change-password")

    page = await client.get("/admin/change-password", headers=_BROWSER_HEADERS)
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert token, "no CSRF token on the forced change page"

    changed = await client.post(
        "/admin/change-password",
        data={
            "password": "a-password-of-their-own",
            "confirm": "a-password-of-their-own",
            "csrf_token": token.group(1),
        },
        headers=_BROWSER_HEADERS,
        follow_redirects=False,
    )
    assert changed.status_code == 302, changed.text[:400]

    _resync(session)
    row = get_staff(session, staff.username)
    assert row.must_change_password is False
    assert row.unclaimed_password_enc is None, (
        "the forced change left the issued password stored - a readable "
        "credential on an account whose password is now something else"
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

    assert "unclaimed_password_enc" in REDACTED_FIELDS

    staff, password = _make(session)
    _resync(session)
    staff = get_staff(session, staff.username)

    snapshot = row_to_dict(staff)
    assert snapshot["unclaimed_password_enc"] == staff.unclaimed_password_enc
    safe = _json_safe(snapshot)
    assert safe["unclaimed_password_enc"] == "[redacted]"
    assert password not in repr(safe)


def test_revealing_writes_an_audit_entry_that_does_not_carry_the_value(session):
    staff, password = _make(session)
    _resync(session)

    revealed = reveal_unclaimed_password(
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
        "username": staff.username, "revealed": "unclaimed_password"
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
    assert reveal_unclaimed_password(
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

    page = await admin_client.get(f"/admin/staff/unclaimed-password?pks={staff.id}")
    assert page.status_code == 200
    assert password not in page.text, (
        "the page showed the password before any proof was given"
    )
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert token

    refused = await admin_client.post(
        "/admin/staff/unclaimed-password",
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

    page = await admin_client.get(f"/admin/staff/unclaimed-password?pks={staff.id}")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert token

    shown = await admin_client.post(
        "/admin/staff/unclaimed-password",
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
async def test_the_list_says_a_password_is_waiting_after_one_is_issued(
    admin_client, session
):
    """The indicator has to be truthful for an issued password too.

    Before v1.16 this column read "No" from the instant `Issue a new password`
    had displayed one - correct about the column and useless to the person
    reading the screen, since a password *was* waiting to be collected. Driven
    with the creation password claimed first, so the "Yes" can only be the
    issue.

    Both halves again: the account must be on the page at all, and the
    plaintext must not be.
    """
    staff, _ = _make(session)
    set_password(session, staff.username, "a-password-of-their-own")
    session.commit()
    issued = issue_password(
        session, staff.username, actor="somebody-else", secret_key=SECRET_KEY
    )
    session.commit()
    _resync(session)

    listing = await admin_client.get("/admin/staff/list?pageSize=100")
    assert listing.status_code == 200
    assert staff.username in listing.text, "the account was not on the list at all"
    assert "Yes - can be revealed" in listing.text
    assert issued not in listing.text, "the list page rendered the password itself"


@pytest.mark.asyncio
async def test_the_list_says_no_when_a_forced_change_is_owed_with_nothing_stored(
    admin_client, session
):
    """The one state that separates the indicator from `must_change_password`,
    and the reason `StaffAdmin` renders them as two columns.

    They agree on both minting paths - v1.16 made sure of it - so an indicator
    that simply returned `must_change_password` would pass every other test in
    this file. It would be wrong here: an account created before v1.15, or one
    whose stored copy went undecryptable across an unrotated SECRET_KEY change,
    owes a password change with nothing to reveal. Conflating them puts "Yes -
    can be revealed" on a row the reveal page then answers "Not recoverable",
    which is the panel promising something it refuses one click later.

    The pre-v1.15 row is made by clearing the column directly rather than
    through a service function, because no service function can produce this
    state - which is the point: it arrives from history, not from a code path
    anybody can drive.
    """
    staff, _ = _make(session)
    _resync(session)
    row = get_staff(session, staff.username)
    row.unclaimed_password_enc = None
    session.commit()
    _resync(session)

    row = get_staff(session, staff.username)
    assert row.must_change_password is True, "the fixture is not the state under test"
    assert row.has_unclaimed_password is False

    listing = await admin_client.get("/admin/staff/list?pageSize=100")
    assert listing.status_code == 200
    assert staff.username in listing.text, "the account was not on the list at all"
    # The row's own cell, not the page: another account on the same list may
    # legitimately be showing "Yes".
    cell = re.search(
        rf"{re.escape(staff.username)}.*?</tr>", listing.text, re.S
    )
    assert cell, "the account's row was not found in the table"
    assert "Yes - can be revealed" not in cell.group(0), (
        "the list offered a reveal for an account with nothing stored"
    )


@pytest.mark.asyncio
async def test_the_panel_can_read_back_a_password_it_issued(admin_client, session):
    """End to end through the panel, which is where the asymmetry was visible.

    Drives the real `Issue a new password` action over HTTP - not
    `issue_password` directly - because the view had to be given a
    `secret_key` for the service function to store anything, and a view that
    forgot it would fail here and nowhere else. Then reveals, with proof, and
    asserts the value handed back is the one the action displayed.

    The account is a scratch one, never the acting administrator: issuing a
    password to yourself is refused by `_guard_not_self`.
    """
    staff, created = _make(session)
    _resync(session)

    issued_page = await admin_client.get(
        "/admin/staff/action/issue-password", params={"pks": staff.id}
    )
    assert issued_page.status_code == 200, issued_page.text[:400]
    shown = re.search(r'<code class="key">([^<]+)</code>', issued_page.text)
    assert shown, issued_page.text[:600]
    issued = shown.group(1)
    assert issued != created

    page = await admin_client.get(f"/admin/staff/unclaimed-password?pks={staff.id}")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert token

    revealed = await admin_client.post(
        "/admin/staff/unclaimed-password",
        data={
            "pks": str(staff.id),
            "csrf_token": token.group(1),
            "current_password": admin_client.password,
        },
    )
    assert revealed.status_code == 200
    assert issued in revealed.text, (
        "the panel issued a password and then could not show it again - the "
        "asymmetry contract v1.16 removed"
    )
    assert created not in revealed.text, (
        "the superseded creation password was shown instead of the live one"
    )


@pytest.mark.asyncio
async def test_the_issued_page_says_the_password_can_be_read_again(
    admin_client, session
):
    """The wording, which was a defect of its own.

    `brand/issued_credential.html` said "This is shown once and is not
    recoverable". An administrator who believed it and lost the tab would issue
    *another* password - which stops the one they may already have read out
    from working, which is the whole loss this feature exists to prevent.
    """
    staff, _ = _make(session)
    _resync(session)

    page = await admin_client.get(
        "/admin/staff/action/issue-password", params={"pks": staff.id}
    )
    flat = re.sub(r"\s+", " ", page.text)

    assert "shown once and is not recoverable" not in flat
    assert "If you lose this page, the password is not lost" in flat
    assert "Show the password waiting to be collected" in flat


@pytest.mark.asyncio
async def test_the_details_page_does_not_render_the_ciphertext(admin_client, session):
    """`column_details_list` defaults to *every* mapped column.

    This is a live defect this project has already had once — the staff details
    page rendered the bcrypt hash and the encrypted TOTP secret in full, from a
    list the same view correctly redacted. `unclaimed_password_enc` is a new
    mapped column and would have joined them without `column_details_list`
    being explicit.
    """
    staff, password = _make(session)
    _resync(session)

    details = await admin_client.get(f"/admin/staff/details/{staff.id}")
    assert details.status_code == 200
    assert staff.username in details.text
    assert password not in details.text

    blob = get_staff(session, staff.username).unclaimed_password_enc
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
            f"/admin/staff/unclaimed-password?pks={staff.id}"
        )
        token = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
        return await admin_client.post(
            "/admin/staff/unclaimed-password",
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
    assert decrypt_unclaimed_password(
        staff.unclaimed_password_enc, secret_key=new_key
    ) == password


def test_a_key_rotation_carries_an_issued_password_across(session):
    """`cmd_rotate_key` selects on `unclaimed_password_enc IS NOT NULL`, so it
    picks up an issued password with no change - asserted rather than assumed,
    because v1.16 multiplied the number of rows that predicate matches and an
    account that has one issued to it is by definition one somebody is waiting
    on.
    """
    from admin.cli import cmd_rotate_key

    staff, _created = _make(session)
    set_password(session, staff.username, "a-password-of-their-own")
    session.commit()
    issued = issue_password(
        session, staff.username, actor="somebody-else", secret_key=SECRET_KEY
    )
    session.commit()
    _resync(session)

    new_key = "another-different-secret-key-for-this-test"
    _totp, count, _blocks = cmd_rotate_key(
        session, old_key=SECRET_KEY, new_key=new_key
    )
    session.commit()
    _resync(session)

    assert count >= 1
    row = get_staff(session, staff.username)
    assert decrypt_unclaimed_password(
        row.unclaimed_password_enc, secret_key=new_key
    ) == issued


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

    page = await admin_client.get(f"/admin/staff/unclaimed-password?pks={staff.id}")
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
        "/admin/staff/unclaimed-password",
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
