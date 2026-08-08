"""The middleware. Contract §2.3 - nothing here is stored."""

import pytest
from sqlalchemy import text

# pytest.mark.asyncio, not in the brief's own listing, is required here:
# pytest.ini sets asyncio_mode = strict for this project, so an `async def`
# test with no marker is collected but never actually run as a coroutine -
# every other async test file in tests/admin/ (e.g. test_compare_view.py,
# test_factor_set_actions.py) carries this same pair for the same reason.
pytestmark = [pytest.mark.db, pytest.mark.asyncio]


@pytest.fixture
def _protection_default_for_tests() -> str:
    """Overrides tests/conftest.py's own fixture of the same name.

    Every other file in the suite gets ProtectionMiddleware built with
    PROTECTION_ENABLED=false (see that fixture's docstring) - this is the
    one file that must not depend on that default in either direction, so
    it sets its own, explicitly, rather than inheriting whichever way the
    rest of the suite happens to be pointed.
    """
    return "true"


@pytest.fixture
def session(_committed_session):
    """This file's name for the hard-committing session against the running
    admin app's own database - see tests/admin/conftest.py's
    `_committed_session` docstring for the general reasoning.

    Load-bearing, not a style choice: the plain, rolled-back `session`
    tests/conftest.py defines is bound to a `Connection` whose transaction
    was started manually, outside the Session, via `connection.begin()`.
    Calling `.commit()` on a Session in that arrangement does not end that
    outer transaction - confirmed directly: `transaction.is_active` reads
    `True` immediately after `session.commit()` returns - so nothing
    written through it is ever visible to another connection until the
    fixture's own teardown runs, and that teardown rolls everything back
    rather than committing it. admin/protection.py's blocklist check opens
    its *own* connection via `admin_app.state.session_factory` on every
    request, so a `block_ip` written through the rolled-back session can
    never be seen there - a test using it would only pass because the
    request's default httpx User-Agent independently trips the *header*
    check to the same 403, never actually exercising the blocklist path it
    claims to. `_committed_session` performs a real commit, which is what a
    block visible to a second connection requires.
    """
    return _committed_session


@pytest.fixture(autouse=True)
def _cleanup_ip_blocks(admin_app):
    """Remove every ip_block row this file's tests commit through the
    `session` override above.

    Unlike `_committed_session`'s own "e6-"/"e7-"-prefixed cleanup, an
    ip_block row carries no code or label to prefix-match - `db.blocklist`
    identifies rows only by an HMAC fingerprint. `created_by` is the field
    every call in this file sets to the same actor value ("kim", per the
    brief's own test bodies), so that is what is matched here instead - the
    same reasoning tests/admin/conftest.py's `_cleanup_staff` gives for
    keying on the actor rather than on the row.
    """
    yield
    factory = admin_app.state.session_factory
    with factory() as db:
        db.execute(text("DELETE FROM ip_block WHERE created_by = 'kim'"))
        db.commit()


async def test_a_blocked_address_is_refused(client, session, settings):
    from db.blocklist import block_ip

    block_ip(session, "127.0.0.1", reason="test", actor="kim",
             secret_key=settings.secret_key)
    session.commit()

    response = await client.get("/admin/login")

    assert response.status_code == 403


async def test_an_ordinary_browser_request_passes(client):
    response = await client.get("/admin/login", headers={
        "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36",
        "accept": "text/html,application/xhtml+xml",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "none",
        "sec-fetch-dest": "document",
    })

    assert response.status_code == 200


async def test_a_scripted_request_is_refused(client):
    response = await client.get("/admin/login",
                                headers={"user-agent": "curl/8.4.0"})

    assert response.status_code == 403


async def test_an_authenticated_staff_member_is_exempt_from_the_header_check(
    admin_client
):
    """They passed a password, a TOTP code and a session check. Refusing them
    for a header protects nothing - and the panel has no email recovery, so
    walling off its own operators is the failure this exemption exists to
    prevent."""
    response = await admin_client.get("/admin/factor-set/list",
                                      headers={"user-agent": "curl/8.4.0"})

    assert response.status_code == 200


async def test_a_blocked_address_is_refused_even_when_authenticated(
    admin_client, session, settings
):
    """The exemption covers the behavioural checks, not the blocklist. A
    blocked address is a deliberate act by another administrator."""
    from db.blocklist import block_ip

    block_ip(session, "127.0.0.1", reason="test", actor="kim",
             secret_key=settings.secret_key)
    session.commit()

    response = await admin_client.get("/admin/factor-set/list")

    assert response.status_code == 403


async def test_static_files_are_not_checked(client):
    """The stylesheet loads on the login page, before anyone has a session.
    Refusing it leaves an unstyled page that looks broken rather than
    protected."""
    response = await client.get("/admin/static/brand.css",
                                headers={"user-agent": "curl/8.4.0"})

    assert response.status_code == 200


async def test_exceeding_the_rate_limit_is_refused(client, settings):
    """Faster than a human types, from one source, on the login page."""
    headers = {"user-agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/131.0",
               "accept": "text/html", "sec-fetch-mode": "navigate"}
    limit = settings.protection_max_requests_per_minute

    for _ in range(limit):
        await client.get("/admin/login", headers=headers)
    response = await client.get("/admin/login", headers=headers)

    assert response.status_code == 429


async def test_the_refusal_page_does_not_explain_what_tripped_it(client):
    """A caller being refused is not owed the rule they broke - that is a
    free tuning signal. Staff read the reason on the blocklist screen and in
    the audit trail instead."""
    response = await client.get("/admin/login",
                                headers={"user-agent": "curl/8.4.0"})

    body = response.text.lower()
    assert "user-agent" not in body
    assert "curl" not in body
