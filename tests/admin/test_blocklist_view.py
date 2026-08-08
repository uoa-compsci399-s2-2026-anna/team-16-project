"""The blocklist screen. Contract §8.1 — every write is audited.

``pytestmark`` carries ``pytest.mark.asyncio`` alongside ``pytest.mark.db``,
which the task brief's own Step 1 listing omitted. Without it, pytest.ini's
``asyncio_mode = strict`` leaves every ``async def test_*`` below uncollected
as a coroutine: pytest's own fallback raises "async def functions are not
natively supported" for each one — a real failure, not a silent pass —
confirmed directly against this exact configuration (Python 3.12, pytest
9.1.1, pytest-asyncio 1.4.0) with a throwaway probe test carrying only
``pytest.mark.db``. Every other file in this directory that awaits
``admin_client``/``staff_client`` carries this same pair, either as a module-
level list (tests/admin/test_protection.py, test_accounts_view.py) or as a
``@pytest.mark.asyncio`` decorator repeated per test
(tests/admin/test_factor_set_actions.py) — this file uses the list form.
"""

import pytest
from sqlalchemy import select, text

from admin.app import create_app
from tests.admin.conftest import _resync
from tests.conftest import TEST_URL

pytestmark = [pytest.mark.db, pytest.mark.asyncio]


@pytest.fixture
def admin_app(monkeypatch):
    """This file's own app, with ``ProtectionMiddleware`` genuinely on.

    A verbatim copy of tests/admin/test_protection.py's local ``admin_app``
    override - read that fixture's docstring for the full reasoning, which
    applies unchanged here. The short form: tests/conftest.py sets
    ``PROTECTION_ENABLED=false`` for the whole session, and this file's
    ``_cleanup_ip_blocks`` is ``autouse`` and also requires ``admin_app``, so
    a *companion* ``autouse`` fixture patching the environment on the side
    loses an ordering race against ``admin_app``'s own ``create_app()`` call -
    that was tried once already and confirmed to run too late. Owning the
    ``create_app()`` call is what makes the ordering a real dependency edge
    instead of a hoped-for scope tiebreak.
    """
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-not-used-anywhere-real")
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    monkeypatch.setenv("SESSION_HTTPS_ONLY", "false")
    monkeypatch.setenv("PROTECTION_ENABLED", "true")
    app = create_app()
    yield app
    app.state.session_factory.kw["bind"].dispose()


@pytest.fixture
def session(_committed_session):
    """This file's name for the hard-committing session against the running
    admin app's own database — see tests/admin/conftest.py's
    `_committed_session` docstring for the general reasoning, and
    tests/admin/test_protection.py's own `session` fixture for the same
    wrapper.

    Load-bearing, not a style choice: the plain, rolled-back `session`
    tests/conftest.py defines never actually commits to the database as far
    as another connection can see (it is bound to a `Connection` whose
    transaction was started manually via `connection.begin()`, and
    `session.commit()` on it ends nothing outer). `admin_client` drives real
    HTTP requests that reach `IpBlockAdmin` through the running app's own,
    separate connection — a `block_ip` written through the rolled-back
    session is invisible there, and worse, holds a row lock the request's
    own write then blocks on until MySQL's lock-wait timeout fires. Confirmed
    directly: without this override, `test_a_re_block_replaces_the_reason...`
    failed with `(1205, 'Lock wait timeout exceeded')` on the admin_client's
    own INSERT, not a wrong-behaviour assertion.
    """
    return _committed_session


@pytest.fixture(autouse=True)
def _cleanup_ip_blocks(admin_app):
    """Every test below works against the single address "203.0.113.9", and
    ``admin_app``'s own engine points at the same physical database every
    other test file in this directory shares (only the schema is dropped
    and recreated once per session, in tests/conftest.py's ``engine``
    fixture) — so a row left behind here is visible to the next test file
    that queries ``ip_block`` or scans ``audit_log`` from its own start,
    the same cross-test-pollution shape tests/admin/conftest.py's
    ``_cleanup_staff`` docstring already describes for `staff`/`audit_log`.

    Deleting unconditionally by table, not by a prefix or an actor, because
    this file is the only one that writes through the panel's own audited
    `/ip-block/block` and `/ip-block/action/unblock` routes — everything
    those write is safe to clear after every test here. The one other file
    that touches `ip_block` directly, tests/admin/test_protection.py, never
    goes through those routes and so never writes an `ip_block` audit_log
    entry to begin with; its own local `_cleanup_ip_blocks` (keyed on
    `created_by = 'kim'`) is untouched by this one.
    """
    yield
    factory = admin_app.state.session_factory
    with factory() as db:
        db.execute(text("DELETE FROM audit_log WHERE table_name = 'ip_block'"))
        db.execute(text("DELETE FROM ip_block"))
        db.commit()


async def test_the_screen_lists_blocks_without_showing_an_address(
    admin_client, session, settings
):
    """There is no address to show — that is the design. The screen shows the
    reason, who blocked, and when it expires."""
    from db.blocklist import block_ip

    block_ip(session, "203.0.113.9", reason="scripted traffic", actor="kim",
             secret_key=settings.secret_key)
    session.commit()

    response = await admin_client.get("/admin/ip-block/list")

    assert response.status_code == 200
    assert "scripted traffic" in response.text
    assert "203.0.113.9" not in response.text


async def test_an_administrator_can_block_an_address_by_hand(
    admin_client, session, settings
):
    """The reason this screen exists: an attack in progress, and no CDN or
    upstream firewall to stop it."""
    from db.blocklist import is_blocked
    from db.blocklist_models import IpBlock

    await admin_client.post("/admin/ip-block/block", data={
        "address": "203.0.113.9", "reason": "manual block during an incident",
        "minutes": "60",
    })

    _resync(session)
    assert session.scalar(select(IpBlock)) is not None
    assert is_blocked(session, "203.0.113.9", secret_key=settings.secret_key)


async def test_unblocking_from_the_screen_works(
    admin_client, session, settings
):
    from db.blocklist import block_ip, is_blocked
    from db.blocklist_models import IpBlock

    block_ip(session, "203.0.113.9", reason="burst", actor="kim",
             secret_key=settings.secret_key)
    session.commit()
    row = session.scalar(select(IpBlock))

    await admin_client.get(f"/admin/ip-block/action/unblock?pks={row.id}")

    _resync(session)
    assert not is_blocked(session, "203.0.113.9", secret_key=settings.secret_key)


async def test_a_plain_staff_member_cannot_reach_it(staff_client):
    """Blocking access to a public service is an administrator's decision.
    sqladmin registers @action routes with login_required only, so this needs
    its own check — see admin/accounts_view.py.

    Asserted exactly, not `in (302, 403)`: `staff_client` is a real,
    logged-in session, so `login_required` (which only checks that *some*
    onboarded session exists) already passes and sqladmin's own `_list`
    (application.py) raises `HTTPException(403)` outright when
    `is_accessible` is False — there is no code path here that would
    legitimately produce a 302. A loose assertion that accepts either would
    stay green even if `is_accessible` silently returned True for every
    session, as long as *something* about the request still redirected."""
    response = await staff_client.get("/admin/ip-block/list")

    assert response.status_code == 403


async def test_a_plain_staff_member_cannot_block_by_hand(staff_client):
    """The manual-block form is an @expose route, not an @action, but it
    carries the same login_required-only wrapping (see
    admin/blocklist_views.py's module docstring) — so it needs the same
    explicit check as the list page and the unblock action. See the test
    above for why this is asserted exactly rather than `in (302, 403)`."""
    response = await staff_client.get("/admin/ip-block/block")

    assert response.status_code == 403


async def test_a_plain_staff_member_cannot_unblock_by_url(
    staff_client, session, settings
):
    """`@action` routes are registered with `login_required` only —
    sqladmin never calls `is_accessible` for them (see
    admin/accounts_view.py's module docstring, confirmed against
    sqladmin/application.py). Hiding the menu entry and refusing
    `/admin/ip-block/list` is not enough on its own: without
    `unblock_action`'s own `_require_admin` call, a plain staff member who
    knows or guesses the action URL and a target `pks` could remove any
    block directly, bypassing the administrator-only floor entirely — the
    same defect this project has already shipped once on a different
    action.
    """
    from db.blocklist import block_ip, is_blocked
    from db.blocklist_models import IpBlock

    block_ip(session, "203.0.113.9", reason="staff must not remove this",
             actor="kim", secret_key=settings.secret_key)
    session.commit()
    row = session.scalar(select(IpBlock))

    response = await staff_client.get(
        f"/admin/ip-block/action/unblock?pks={row.id}"
    )

    assert response.status_code == 403
    _resync(session)
    assert is_blocked(session, "203.0.113.9", secret_key=settings.secret_key)


async def test_a_re_block_replaces_the_reason_rather_than_duplicating_the_row(
    admin_client, session, settings
):
    """db.blocklist.block_ip upserts on the fingerprint — a second block of
    the same address is a change of reason, not a second row."""
    from db.blocklist import block_ip
    from db.blocklist_models import IpBlock

    block_ip(session, "203.0.113.9", reason="first reason", actor="kim",
             secret_key=settings.secret_key)
    session.commit()

    await admin_client.post("/admin/ip-block/block", data={
        "address": "203.0.113.9", "reason": "second reason",
    })

    _resync(session)
    rows = session.scalars(select(IpBlock)).all()
    assert len(rows) == 1
    assert rows[0].reason == "second reason"


async def test_every_block_and_unblock_is_audited(admin_client, session, settings):
    """Contract §8.1: every admin write produces an audit_log entry — and
    §2.3's exception is specific that the entry must not carry the address
    either.

    Both halves are driven through the panel itself, not seeded with a
    direct `db.blocklist.block_ip` call: a version of this test that only
    unblocks through the UI stays green even if `block_form`'s own
    `write_audit` call is deleted outright, since nothing then checks for a
    "create" entry at all. Blocking through `/admin/ip-block/block` is what
    makes the "create" assertion below load-bearing.
    """
    from db.blocklist_models import IpBlock

    await admin_client.post("/admin/ip-block/block", data={
        "address": "203.0.113.9", "reason": "scripted traffic",
    })
    _resync(session)
    row = session.scalar(select(IpBlock))
    assert row is not None

    await admin_client.get(f"/admin/ip-block/action/unblock?pks={row.id}")
    _resync(session)

    entries = session.execute(
        text(
            "SELECT actor, action, before_json, after_json FROM audit_log "
            "WHERE table_name = 'ip_block' ORDER BY id"
        )
    ).all()
    assert any(e.action == "create" and e.actor == admin_client.staff.username
                for e in entries)
    assert any(e.action == "delete" and e.actor == admin_client.staff.username
                for e in entries)
    for entry in entries:
        payload = str(entry.before_json) + str(entry.after_json)
        assert "203.0.113.9" not in payload


async def test_the_ip_hmac_itself_never_appears_anywhere(
    admin_client, session, settings
):
    """The redaction this whole module exists for.

    Every other test in this file checks for the plaintext address
    ("203.0.113.9") — necessary, but not sufficient: a version of the list
    page that rendered the raw 64-character `ip_hmac` instead of the
    address would pass every one of those checks while defeating the exact
    thing contract §2.3's exception depends on (an operator, or an attacker
    holding only the panel's output, learning nothing that identifies who
    visited). `column_details_list = column_list` on `IpBlockAdmin` is what
    stops the details page falling back to sqladmin's own default of every
    mapped column — delete that one line and this test is what catches it,
    the same live defect `AuditedModelView`'s own docstring records having
    shipped once already on a different screen. `can_view_details` and
    `can_export` are both `True` by sqladmin's own default and neither is
    overridden here, so `/details/{pk}` and `/export/csv` are both real,
    reachable routes worth checking alongside `/list`.
    """
    from db.blocklist import block_ip, ip_fingerprint
    from db.blocklist_models import IpBlock

    block_ip(session, "203.0.113.9", reason="scripted traffic", actor="kim",
             secret_key=settings.secret_key)
    session.commit()
    row = session.scalar(select(IpBlock))
    fingerprint = ip_fingerprint("203.0.113.9", secret_key=settings.secret_key)
    # Sanity check on the test itself: this really is the value stored for
    # this row, not a fingerprint that happens never to appear anyway.
    assert row.ip_hmac == fingerprint

    list_response = await admin_client.get("/admin/ip-block/list")
    details_response = await admin_client.get(f"/admin/ip-block/details/{row.id}")
    export_response = await admin_client.get("/admin/ip-block/export/csv")

    assert list_response.status_code == 200
    assert details_response.status_code == 200
    assert export_response.status_code == 200
    assert fingerprint not in list_response.text
    assert fingerprint not in details_response.text
    assert fingerprint not in export_response.text


async def test_a_re_block_with_no_duration_wipes_an_existing_expiry(
    admin_client, session, settings
):
    """Carried-forward item 4. `block_ip`'s upsert treats `minutes=None` as
    "no expiry" and writes it unconditionally — correct and deliberate
    (db/blocklist.py, not changed here) when a staff member leaves the
    duration blank on purpose, but exactly the behaviour most likely to
    surprise an operator re-blocking an address without noticing the
    duration field is empty: a 30-minute block silently becomes permanent.
    `test_a_re_block_replaces_the_reason...` above never observes this,
    because the block it seeds has no expiry to begin with — there is
    nothing there for a wipe to be visible against. This test seeds one
    with an expiry first.
    """
    from db.blocklist import block_ip
    from db.blocklist_models import IpBlock

    block_ip(session, "203.0.113.9", reason="temporary burst", actor="kim",
             secret_key=settings.secret_key, minutes=30)
    session.commit()
    row = session.scalar(select(IpBlock))
    assert row.expires_at is not None

    await admin_client.post("/admin/ip-block/block", data={
        "address": "203.0.113.9", "reason": "re-blocked with no duration",
    })

    _resync(session)
    row = session.scalar(select(IpBlock))
    assert row.expires_at is None


async def test_the_form_refuses_a_value_that_is_not_an_address(
    admin_client, session
):
    """The silent failure this closes. Before normalisation, the form accepted
    any non-empty string and `ip_fingerprint` hashed it verbatim - so a typo
    wrote a row, showed it on the list page, wrote an audit entry, and blocked
    nobody, because the fingerprint the middleware computes for a real caller
    could never equal the fingerprint of a typo. Nothing failed anywhere.
    """
    from db.blocklist_models import IpBlock

    response = await admin_client.post("/admin/ip-block/block", data={
        "address": "203.0.113.9:54321", "reason": "carries a port",
    })

    assert response.status_code == 400
    _resync(session)
    assert session.scalar(select(IpBlock)) is None


async def test_the_form_does_not_echo_a_rejected_address_back_into_the_page(
    admin_client
):
    """§2.3's concern, applied to the one field on this screen that holds a
    plaintext address. The reason a staff member's mistyped value might be
    worth re-populating for their convenience is exactly the reason it must
    not be: it is an address, or something they believed was one, and this
    page renders inside a panel every staff member can reach. The template
    renders `error` and nothing else from the submitted form - this pins
    that.
    """
    response = await admin_client.post("/admin/ip-block/block", data={
        "address": "203.0.113.99-and-a-typo", "reason": "mistyped",
    })

    assert response.status_code == 400
    assert "203.0.113.99" not in response.text


async def test_a_block_made_through_the_form_actually_refuses_that_caller(
    admin_client, session, settings
):
    """The one test that runs the whole feature end to end.

    Every other test in this file stops at the database: it asserts a row
    exists, or that `is_blocked` returns True. Nothing joined the two halves -
    the form writes a fingerprint via `get_runtime(request).settings`, and
    `ProtectionMiddleware` computes one via its own `self._settings`. The same
    object today, but two paths, and a divergence between them (a different
    `SECRET_KEY`, a different HKDF `info`, a different normalisation) produces
    a block that appears on the screen and stops nobody, with nothing failing
    anywhere. That is the same silent-failure class the contract names
    `BLOCKLIST_INFO` in §2.3 to guard against, and until now nothing in the
    suite would have caught it.

    Blocks `127.0.0.1` because that is the address `httpx`'s `ASGITransport`
    presents to the app, so the very next request through this client is the
    blocked caller. The client is already an authenticated administrator,
    which makes the assertion stronger rather than weaker: the blocklist is
    the *only* check that has no exemption for a live staff session, so a 403
    here cannot be the header check or the rate limit.
    """
    await admin_client.post("/admin/ip-block/block", data={
        "address": "127.0.0.1", "reason": "end-to-end block",
    })
    _resync(session)

    response = await admin_client.get("/admin/ip-block/list")

    assert response.status_code == 403


async def test_unblocking_through_the_screen_lets_that_caller_back_in(
    admin_client, session, settings
):
    """The other half, and the reason the unblock action must reach the same
    row the form wrote. `unblock_action` deletes by primary key while the form
    and the CLI work from a recomputed fingerprint (see
    admin/blocklist_views.py's module docstring) - two paths to one row, and
    nothing previously pinned that they meet.

    The block is seeded through `db.blocklist.block_ip` rather than the form
    because the form's own 127.0.0.1 block locks this client out of the
    unblock route as well; seeding leaves the client able to reach the action
    it is here to exercise, and `test_a_block_made_through_the_form...` above
    already covers the form's write reaching the middleware.
    """
    from db.blocklist import block_ip
    from db.blocklist_models import IpBlock

    block_ip(session, "203.0.113.9", reason="burst", actor="kim",
             secret_key=settings.secret_key)
    session.commit()
    row = session.scalar(select(IpBlock))

    response = await admin_client.get(
        f"/admin/ip-block/action/unblock?pks={row.id}", follow_redirects=False
    )

    assert response.status_code == 302
    _resync(session)
    assert session.scalar(select(IpBlock)) is None
