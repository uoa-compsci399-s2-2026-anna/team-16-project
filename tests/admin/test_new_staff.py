"""``/admin/staff/new`` — adding a colleague from the panel. Contract §8.3.

**Why this screen exists.** Until it did, the only way to create an account
was ``kaicalc-admin create-staff`` on the container. The client is a trust
with staff turnover and no developer, and the team hands over at the end of
semester: a panel that cannot onboard somebody is a panel that needs its
authors present forever.

Every request here goes through the real HTTP login flow, matching
tests/admin/test_accounts_view.py and test_self_service.py — this proves the
route is reachable the way a person reaches it, through
``AuthenticationBackend``, and not merely that the view class exists.

**Every guard below is asserted from both sides.** A test that a wrong code is
refused passes just as happily against a screen that refuses everything, which
is a failure this project has found fifteen times; so for each refusal there is
a paired test that the permitted case *succeeds* and produces a usable account.
The strongest of those is
``test_the_created_account_can_log_in_and_lands_in_the_forced_change`` — it
spends the issued password against the real login route.

Proof is supplied as the **current password** in most tests rather than a TOTP
code, deliberately. ``conftest._login`` spends a code at its own frozen instant
and ``verify_staff_totp`` advances the device's replay counter past that step,
so a second code minted for the same instant is refused as a replay — correctly.
``test_a_code_from_the_authenticator_is_accepted_as_proof`` is the one test
that drives the code path, and it moves the clock forward to do it.
"""

import re
import time
import uuid

import pyotp
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from admin.accounts import get_staff, issue_password
from admin.models import AuditLog, Staff, StaffRole
from admin.security import verify_password
from admin.totp import TOTP_INTERVAL
from admin.views import time as views_time

pytestmark = [pytest.mark.db, pytest.mark.asyncio]

NEW_URL = "/admin/staff/new"
LIST_URL = "/admin/staff/list"

#: Every account this file creates through the panel carries this prefix, so
#: the teardown below can sweep them by pattern rather than by a hand-kept
#: list — the same choice, for the same reason, as conftest's `_cleanup_e6_rows`.
PREFIX = "e9new"


def _name() -> str:
    return f"{PREFIX}{uuid.uuid4().hex[:8]}"


def _flat(response) -> str:
    """One response's markup with every run of whitespace collapsed.

    Copy of tests/admin/test_guidance.py's helper and for its reason: these
    are wrapped prose, so a phrase in the source is split across lines about
    as often as not, and a test that only passed when a sentence happened to
    sit on one line would fail on the next reflow.
    """
    return re.sub(r"\s+", " ", response.text)


@pytest.fixture(autouse=True)
def _cleanup_created_accounts(admin_app):
    """Remove every account this file created, before and after each test.

    Before as well as after: an interrupted run leaves committed rows behind,
    and the next run's duplicate-username test would then be passing for the
    wrong reason — refused because of a leftover row rather than because of
    the row the test itself created.
    """
    def sweep():
        factory = admin_app.state.session_factory
        with factory() as db:
            ids = db.execute(
                text("SELECT id FROM staff WHERE username LIKE :p"),
                {"p": f"{PREFIX}%"},
            ).scalars().all()
            for row_id in ids:
                db.execute(
                    text("DELETE FROM audit_log WHERE table_name = 'staff' "
                         "AND row_id = :id"),
                    {"id": row_id},
                )
            db.execute(
                text("DELETE FROM staff WHERE username LIKE :p"),
                {"p": f"{PREFIX}%"},
            )
            db.commit()

    sweep()
    yield
    sweep()


async def _open_form(client):
    """GET the form and return (response, csrf token)."""
    page = await client.get(NEW_URL)
    assert page.status_code == 200, "an administrator must be able to open the form"
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match, "no CSRF token rendered on the account form"
    return page, match.group(1)


async def _create(client, **fields):
    """Submit the form with a fresh token, defaulting the proof to the
    acting account's own password."""
    _, token = await _open_form(client)
    data = {
        "csrf_token": token,
        "username": _name(),
        "display_name": "New Colleague",
        "role": "staff",
        "current_password": client.password,
    }
    data.update(fields)
    return data["username"], await client.post(NEW_URL, data=data)


def _find(admin_app, username):
    factory = admin_app.state.session_factory
    with factory() as db:
        return db.scalar(select(Staff).where(Staff.username == username))


def _count(admin_app) -> int:
    factory = admin_app.state.session_factory
    with factory() as db:
        return len(
            db.execute(
                text("SELECT id FROM staff WHERE username LIKE :p"),
                {"p": f"{PREFIX}%"},
            ).scalars().all()
        )


# --- the screen is findable and explains itself -----------------------------


async def test_the_accounts_list_offers_a_way_to_add_a_staff_member(admin_client):
    """The defect this task fixes is not "the route does not exist" but "no
    page leads anywhere near it". A route nobody can find is the CLI with
    extra steps, so the assertion is on the words a person reads, not only on
    the href."""
    page = await admin_client.get(LIST_URL)

    assert page.status_code == 200
    flat = _flat(page)
    assert "Add a staff member" in flat
    assert "/admin/staff/new" in flat


async def test_the_form_explains_every_field_and_both_roles(admin_client):
    """Field-level help is this panel's convention — 87 descriptions under
    the editable boxes — and a creation form without it would be the odd one
    out. Asserted on the rendered markup rather than on a Python constant:
    the guidance blocks that "passed" while missing from the built image did
    so because the test never looked at a page."""
    page, _ = await _open_form(admin_client)
    flat = _flat(page)

    # Each field says what it is for.
    assert "What this person types at the login box" in flat
    assert "cannot contain spaces" in flat
    assert "Shown on the accounts screen" in flat
    # Both roles say what they permit, in the words of contract §8.3's table.
    assert "This is the right choice for almost everyone" in flat
    assert "issue another person&#39;s password" in flat or \
           "issue another person's password" in flat
    # And the screen says up front that nothing is emailed.
    assert "in person or by phone" in flat


# --- the role gate ----------------------------------------------------------


async def test_a_plain_staff_member_cannot_open_the_form(staff_client):
    response = await staff_client.get(NEW_URL)

    assert response.status_code == 403


async def test_a_plain_staff_member_cannot_post_to_the_form(staff_client, admin_app):
    """The GET check alone would be cosmetic. sqladmin wraps an @expose route
    on a ModelView in ``login_required`` only and never calls
    ``is_accessible`` for it, so the POST is a separate reachable URL."""
    response = await staff_client.post(
        NEW_URL,
        data={"username": _name(), "display_name": "Sneaked In", "role": "admin"},
    )

    assert response.status_code == 403
    assert _count(admin_app) == 0


async def test_an_administrator_can_open_the_form(admin_client):
    response = await admin_client.get(NEW_URL)

    assert response.status_code == 200


# --- the proof --------------------------------------------------------------


async def test_creating_an_account_with_the_current_password_succeeds(
    admin_client, admin_app
):
    """The permitted case, end to end: the account exists, it is staff, it is
    forced through a password change, it has no second factor yet, and the
    password on the page is the one that opens it."""
    username, response = await _create(admin_client)

    assert response.status_code == 200
    staff = _find(admin_app, username)
    assert staff is not None
    assert staff.role is StaffRole.staff
    assert staff.is_active is True
    assert staff.must_change_password is True
    assert staff.mfa_enrolled_at is None
    assert staff.created_by == admin_client.staff.username

    shown = re.search(r'<code class="key">([^<]+)</code>', response.text)
    assert shown, "the one-time password was not rendered"
    assert verify_password(shown.group(1), staff.password_hash) is True


async def test_a_code_from_the_authenticator_is_accepted_as_proof(
    admin_client, admin_app, monkeypatch
):
    """The second permitted proof. Somebody adding a colleague has their
    phone in their hand and may never have memorised the password.

    The clock moves forward three TOTP steps first: ``conftest._login``
    already spent a code at its own frozen instant, and the device's replay
    counter is now at that step, so a code minted for the same instant would
    be refused — correctly, and for a reason that has nothing to do with this
    screen."""
    later = int(time.time()) + 3 * TOTP_INTERVAL
    monkeypatch.setattr(views_time, "time", lambda: later)
    code = pyotp.TOTP(admin_client.secret, interval=TOTP_INTERVAL).at(later)

    username, response = await _create(
        admin_client, current_password="", current_code=code
    )

    assert response.status_code == 200
    assert _find(admin_app, username) is not None


async def test_creation_with_no_proof_at_all_is_refused(admin_client, admin_app):
    username, response = await _create(admin_client, current_password="")

    assert response.status_code == 400
    assert _find(admin_app, username) is None
    assert "Confirm it is you" in _flat(response)


async def test_creation_with_the_wrong_password_is_refused(admin_client, admin_app):
    username, response = await _create(
        admin_client, current_password="not the right password"
    )

    assert response.status_code == 400
    assert _find(admin_app, username) is None


async def test_a_refusal_comes_back_inside_the_dialog_keeping_what_was_typed(
    admin_client
):
    """A modal covers the page, so a message printed behind it is a message
    nobody reads — the property brand/security.html establishes. And a refusal
    that also cleared the three fields would train people to type them into
    the CLI instead."""
    _, response = await _create(
        admin_client,
        username=f"{PREFIX}keepme",
        display_name="Keep This Name",
        role="admin",
        current_password="wrong",
    )
    flat = _flat(response)

    assert 'id="dialog-confirm" class="dialog"' in flat
    # `open` on the dialog element itself, which is what puts the refusal in
    # front of somebody rather than behind the backdrop.
    assert re.search(r'<dialog id="dialog-confirm"[^>]*\sopen>', flat)
    assert 'value="e9newkeepme"' in flat
    assert 'value="Keep This Name"' in flat
    assert re.search(r'id="role-admin"[^>]*checked', flat)


async def test_a_stale_form_token_is_refused(admin_client, admin_app):
    _, token = await _open_form(admin_client)
    response = await admin_client.post(
        NEW_URL,
        data={
            "csrf_token": token + "x",
            "username": _name(),
            "display_name": "No Token",
            "role": "staff",
            "current_password": admin_client.password,
        },
    )

    assert response.status_code == 400
    assert _count(admin_app) == 0


# --- the role choice --------------------------------------------------------


async def test_choosing_administrator_creates_an_administrator(
    admin_client, admin_app
):
    username, response = await _create(admin_client, role="admin")

    assert response.status_code == 200
    assert _find(admin_app, username).role is StaffRole.admin


async def test_an_unrecognised_role_is_refused_rather_than_defaulted(
    admin_client, admin_app
):
    """Neither silently `admin` — which would hand out the capability this
    screen exists to control — nor silently `staff`, which would ignore a
    deliberate choice and be discovered only when the new administrator could
    not do their job."""
    username, response = await _create(admin_client, role="superuser")

    assert response.status_code == 400
    assert _find(admin_app, username) is None


async def test_a_missing_role_is_refused(admin_client, admin_app):
    username, response = await _create(admin_client, role="")

    assert response.status_code == 400
    assert _find(admin_app, username) is None


# --- the username -----------------------------------------------------------


async def test_a_username_already_in_use_is_refused(admin_client, admin_app):
    """The guard that keeps creation strictly additive. Without it, creation
    is ``issue_password`` with ``_guard_not_self`` taken off: name the account
    you already hold, and be handed a fresh password for it."""
    taken, first = await _create(admin_client)
    assert first.status_code == 200

    _, second = await _create(admin_client, username=taken)

    assert second.status_code == 400
    assert "already exists" in _flat(second)
    # And the first account is untouched - not re-passworded, not replaced.
    assert _find(admin_app, taken).created_by == admin_client.staff.username


async def test_a_username_with_a_space_is_refused(admin_client, admin_app):
    _, response = await _create(admin_client, username=f"{PREFIX} spaced")

    assert response.status_code == 400
    assert _count(admin_app) == 0


async def test_an_empty_display_name_is_refused(admin_client, admin_app):
    username, response = await _create(admin_client, display_name="   ")

    assert response.status_code == 400
    assert _find(admin_app, username) is None


# --- what the trail records -------------------------------------------------


async def test_creation_is_audited_and_the_password_is_not_in_the_trail(
    admin_client, admin_app
):
    """Contract §8.1: every write produces an audit_log entry. Creation was
    the one mutation in admin/accounts.py that left none, so an account that
    appeared with nobody named beside it was indistinguishable from one
    inserted by hand against the database."""
    username, response = await _create(admin_client)
    assert response.status_code == 200
    shown = re.search(r'<code class="key">([^<]+)</code>', response.text).group(1)

    staff = _find(admin_app, username)
    factory = admin_app.state.session_factory
    with factory() as db:
        entry = db.scalar(
            select(AuditLog).where(
                AuditLog.table_name == "staff", AuditLog.row_id == staff.id
            )
        )

    assert entry is not None
    assert entry.action == "create"
    assert entry.actor == admin_client.staff.username
    assert entry.after_json["username"] == username
    assert entry.after_json["role"] == "staff"
    assert entry.after_json["created_by"] == admin_client.staff.username
    # Neither the plaintext nor the hash. `password_hash` is absent by name,
    # not merely redacted, because `last_password_change` reads the trail for
    # entries carrying that key - see admin/accounts.py::create_staff. A
    # creation must not answer "when did you last change your password".
    assert "password_hash" not in entry.after_json
    assert shown not in str(entry.after_json)


# --- the account is usable, and recoverable if the page is lost -------------


async def test_the_created_account_can_log_in_and_lands_in_the_forced_change(
    admin_client, admin_app
):
    """The permitted case that matters most: the password on that page really
    opens the account, and it lands in the onboarding flow that already exists
    rather than in a parallel one.

    A second client, so the new account's login is not made from the
    administrator's own established session — the cookie jar is what
    distinguishes the two."""
    username, response = await _create(admin_client)
    password = re.search(r'<code class="key">([^<]+)</code>', response.text).group(1)

    transport = ASGITransport(app=admin_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as fresh:
        login = await fresh.post(
            "/admin/login",
            data={"username": username, "password": password},
            follow_redirects=False,
        )
        assert login.status_code == 302, "the issued password should be accepted"
        # The forced password change, not the panel and not the TOTP step:
        # the account has must_change_password set and no enrolment.
        assert login.headers["location"].endswith("/admin/change-password")

        forced = await fresh.get("/admin/change-password")
        assert forced.status_code == 200


async def test_an_account_whose_password_was_never_collected_is_recoverable(
    admin_client, admin_app
):
    """**The failure mode this design chose to handle.** If the page carrying
    the one-time password is never seen — a crash, a closed tab, a bounce —
    the password is gone for good and the account is not: nobody can log in as
    it, and another administrator can issue it a new one through the path that
    already exists. What must never happen is a row in `staff` nobody can ever
    log in as and nobody can tell why."""
    username, response = await _create(admin_client)
    assert response.status_code == 200

    factory = admin_app.state.session_factory
    with factory() as db:
        issued = issue_password(db, username, actor=admin_client.staff.username)
        db.commit()
        staff = get_staff(db, username)
        assert verify_password(issued, staff.password_hash) is True
        assert staff.must_change_password is True


async def test_the_result_page_says_the_account_is_recoverable(admin_client):
    """An administrator who does not know this is one who assumes a lost
    password means a ruined account — and this screen has no delete, so what
    they would actually do is create a second account for the same person and
    leave the first sitting in the table forever."""
    _, response = await _create(admin_client)
    flat = _flat(response)

    assert "shown once" in flat
    assert "If you have lost this password, the account is not lost" in flat
    assert "Issue a new password" in flat
    assert "Do not create a second account for the same person" in flat


# --- the confirmation can actually be pressed -------------------------------


async def test_the_dialog_action_row_is_not_part_of_the_dialogs_scrolled_content(
    admin_client
):
    """**The defect that shipped past 21 passing tests in this file.**

    Every test above submits with ``client.post``, which reaches the route
    without ever going near the markup. None of them presses anything, so all
    of them passed against a screen whose confirmation dialog could not be
    submitted from a browser at all: ``dialog.dialog`` is a scroll container
    (``max-height: min(85vh, 44rem)`` with ``overflow-y: auto``), the action
    row carrying **Create the account** sat in that scrolled content, and once
    the dialog's content was taller than the clamp the button was clipped out
    of the visible box. Pressing where it was laid out hit the dialog instead.
    No submission, no request, no error - the report was "点不动", and it was
    exactly right.

    Measured in Chrome over CDP against the running panel: this dialog is
    470px of content and its button is entirely gone below a 460px viewport;
    /admin/security's change-password dialog is 594px and gone below 600px.
    The stylesheet is shared, so this asserts the invariant for every dialog
    in the panel at once.

    The assertion is written as the *pairing*, not as "sticky appears in the
    file". A clamped, scrolling dialog and an action row in normal flow is the
    combination that hides the button; either alone is fine. If a later change
    gives dialogs a separate scrolling body, the clamp moves off
    ``dialog.dialog`` and this test stops demanding stickiness - which is the
    correct behaviour, because the hazard is gone with it.
    """
    stylesheet = await admin_client.get("/admin/static/brand.css")
    assert stylesheet.status_code == 200
    css = stylesheet.text

    dialog_rule = re.search(r"^dialog\.dialog\s*\{([^}]*)\}", css, re.M)
    assert dialog_rule is not None, "brand.css must define dialog.dialog"
    body = dialog_rule.group(1)
    clamped = "max-height" in body
    scrolls = re.search(r"overflow(-y)?:\s*(auto|scroll)", body) is not None

    if not (clamped and scrolls):
        return  # no clipping hazard to guard against

    actions = re.search(r"^\.dialog__actions\s*\{([^}]*)\}", css, re.M)
    assert actions is not None, "brand.css must define .dialog__actions"
    declarations = actions.group(1)

    assert re.search(r"position:\s*sticky", declarations), (
        "dialog.dialog clamps its height and scrolls, so an action row left in "
        "normal flow is scrolled out of reach and the dialog cannot be "
        "submitted. .dialog__actions must be pinned out of that scroll:\n"
        + declarations
    )
    assert re.search(r"bottom:\s*-?[0-9.]+", declarations), (
        "position: sticky does nothing without an inset; the action row needs "
        "a `bottom` to pin against:\n" + declarations
    )
    assert re.search(r"background:", declarations), (
        "a pinned action row over scrolling content needs its own ground, or "
        "the content scrolls visibly through it:\n" + declarations
    )
