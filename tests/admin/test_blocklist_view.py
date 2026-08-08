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

from tests.admin.conftest import _resync

pytestmark = [pytest.mark.db, pytest.mark.asyncio]


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
    its own check — see admin/accounts_view.py."""
    response = await staff_client.get("/admin/ip-block/list")

    assert response.status_code in (302, 403)


async def test_a_plain_staff_member_cannot_block_by_hand(staff_client):
    """The manual-block form is an @expose route, not an @action, but it
    carries the same login_required-only wrapping (see
    admin/blocklist_views.py's module docstring) — so it needs the same
    explicit check as the list page and the unblock action."""
    response = await staff_client.get("/admin/ip-block/block")

    assert response.status_code in (302, 403)


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
    either."""
    from db.blocklist import block_ip
    from db.blocklist_models import IpBlock

    block_ip(session, "203.0.113.9", reason="scripted traffic", actor="kim",
             secret_key=settings.secret_key)
    session.commit()
    row = session.scalar(select(IpBlock))

    await admin_client.get(f"/admin/ip-block/action/unblock?pks={row.id}")
    _resync(session)

    entries = session.execute(
        text(
            "SELECT actor, action, before_json, after_json FROM audit_log "
            "WHERE table_name = 'ip_block' ORDER BY id"
        )
    ).all()
    assert any(e.action == "delete" and e.actor == admin_client.staff.username
                for e in entries)
    for entry in entries:
        payload = str(entry.before_json) + str(entry.after_json)
        assert "203.0.113.9" not in payload
