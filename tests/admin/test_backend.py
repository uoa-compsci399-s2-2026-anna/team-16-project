"""admin.backend - two-step login and the onboarding gates.

Contract: docs/interfaces.md 8.3. Every route except the password-change and
enrolment pages is refused while onboarding is incomplete.
"""

import json
import time
from base64 import b64decode, b64encode

import itsdangerous
import pyotp
import pytest
from sqlalchemy import delete

from admin.accounts import (
    begin_mfa_enrolment,
    complete_mfa_enrolment,
    create_staff,
    get_staff,
    set_password,
)
from admin.auth import SESSION_KEY
from admin.backend import PENDING_SESSION_KEY, current_username
from admin.models import Staff, StaffRole
from admin.totp import TOTP_INTERVAL

pytestmark = [pytest.mark.db, pytest.mark.asyncio]

#: The three pages reachable before SESSION_KEY exists (admin/backend.py's
#: _PRE_LOGIN_PAGES). Declared here too so Finding 3's tests can be
#: parametrized without importing a private module constant.
PRE_LOGIN_PAGES = ("/admin/verify", "/admin/change-password", "/admin/enrol")

#: httpx's cookie jar assigns this domain to any cookie it extracts from a
#: Set-Cookie response header sent to http://testserver (single-label hosts
#: get ".local" appended per its RFC 2965-derived matching). A cookie set
#: directly via client.cookies.set(...) without this domain is stored under
#: domain="" instead, so a later Set-Cookie clearing "session" is treated as
#: a *different* cookie and never overwrites it - the forged cookie would
#: silently outlive a real logout in the test, though not in a browser.
_COOKIE_DOMAIN = "testserver.local"


def _code_for(secret: str) -> str:
    return pyotp.TOTP(secret, interval=TOTP_INTERVAL).now()


def _session_cookie(secret_key: str, data: dict) -> str:
    """Build a Starlette SessionMiddleware cookie value directly.

    Several tests below need to drive authenticate()'s onboarding gates
    from session states the current stub views (Tasks 5-7 build the real
    ones) cannot yet produce end to end. Signing the cookie the same way
    starlette.middleware.sessions.SessionMiddleware signs it lets the test
    drive authenticate() through a real HTTP request against the real app,
    rather than calling the backend object directly. Prefer a real login
    flow (POST /admin/login, then reuse the client's own Set-Cookie) where
    the state under test allows it - only reach for this where it doesn't,
    such as an already-expired pending value.
    """
    signer = itsdangerous.TimestampSigner(secret_key)
    payload = b64encode(json.dumps(data).encode("utf-8"))
    return signer.sign(payload).decode("utf-8")


def _decode_session_cookie(secret_key: str, cookie_value: str) -> dict:
    signer = itsdangerous.TimestampSigner(secret_key)
    data = signer.unsign(cookie_value.encode("utf-8"), max_age=None)
    return json.loads(b64decode(data))


def _make_staff(
    admin_app,
    username: str,
    *,
    password_changed: bool = False,
    mfa: bool = False,
    active: bool = True,
) -> str:
    """Create and commit a staff account through the app's own connection.

    The shared ``session`` fixture holds its writes in a transaction on a
    connection this app's own session factory - a second, independent
    connection to the same MySQL instance - cannot see. Anything a live
    request through ``client`` is meant to find has to be committed for
    real, so this goes through ``admin_app.state.session_factory`` and
    commits. Always role=staff, never role=admin: committing an admin-role
    row here would be visible to test_bootstrap.py's admin-count
    assertions, which run against the same persistent test database.

    Uses ``admin_app.state.settings.secret_key`` for MFA enrolment rather
    than a module-level constant, so every secret-key-dependent value in
    this file - staff enrolment and forged session cookies alike - traces
    back to the one place the running app itself got it from.
    """
    secret_key = admin_app.state.settings.secret_key
    with admin_app.state.session_factory() as db:
        _, password = create_staff(
            db, username=username, display_name=username.title(), role=StaffRole.staff
        )
        db.flush()
        if password_changed:
            set_password(db, username, password)
        if mfa:
            secret, _ = begin_mfa_enrolment(db, username, secret_key=secret_key)
            complete_mfa_enrolment(
                db,
                username,
                _code_for(secret),
                secret_key=secret_key,
                now=int(time.time()),
            )
        if not active:
            get_staff(db, username).is_active = False
        db.commit()
    return password


def _deactivate(admin_app, username: str) -> None:
    with admin_app.state.session_factory() as db:
        get_staff(db, username).is_active = False
        db.commit()


@pytest.fixture(autouse=True)
def _reset_staff_table(admin_app):
    """Wipe committed staff rows after each test in this module.

    ``_make_staff`` commits for real (see its docstring), so without this a
    row would persist in the shared test database beyond the test that
    created it - invisible to the transactional ``session`` fixture other
    test modules use, but not to a fresh count query against the same
    schema. staff_recovery_code cascades via its FK's ON DELETE CASCADE.
    """
    yield
    with admin_app.state.session_factory() as db:
        db.execute(delete(Staff))
        db.commit()


# --- 1: correct password alone does not authenticate -----------------------


async def test_a_correct_password_alone_does_not_authenticate(client, admin_app):
    """A fully onboarded account: login() sends it to /admin/verify, and the
    thing that matters is what the *session* holds, not just where the
    redirect points - a redirect-only assertion would pass even if login()
    accidentally set SESSION_KEY too."""
    password = _make_staff(admin_app, "wanda", password_changed=True, mfa=True)

    response = await client.post(
        "/admin/login",
        data={"username": "wanda", "password": password},
        follow_redirects=False,
    )

    assert response.status_code in (302, 307)
    assert "/admin/verify" in response.headers["location"]

    cookie_value = response.cookies.get("session")
    assert cookie_value is not None
    session_data = _decode_session_cookie(
        admin_app.state.settings.secret_key, cookie_value
    )
    assert SESSION_KEY not in session_data
    assert PENDING_SESSION_KEY in session_data
    assert session_data[PENDING_SESSION_KEY]["username"] == "wanda"


# --- 2: wrong password ------------------------------------------------------


async def test_a_wrong_password_re_renders_the_login_page_with_an_error(
    client, admin_app
):
    _make_staff(admin_app, "wanda")

    response = await client.post(
        "/admin/login",
        data={"username": "wanda", "password": "not-the-right-password"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "Invalid credentials." in response.text
    assert "location" not in response.headers


# --- 3: fully onboarded account reaches /admin/ -----------------------------


async def test_a_fully_onboarded_account_reaches_the_dashboard(client, admin_app):
    _make_staff(admin_app, "olive", password_changed=True, mfa=True)
    cookie = _session_cookie(
        admin_app.state.settings.secret_key, {SESSION_KEY: "olive"}
    )
    client.cookies.set("session", cookie, domain=_COOKIE_DOMAIN)

    response = await client.get("/admin/", follow_redirects=False)

    assert response.status_code == 200


# --- 4/5: forced password-change gate --------------------------------------


async def test_must_change_password_redirects_the_dashboard_to_change_password(
    client, admin_app
):
    _make_staff(admin_app, "penny")  # must_change_password stays True
    cookie = _session_cookie(
        admin_app.state.settings.secret_key, {SESSION_KEY: "penny"}
    )
    client.cookies.set("session", cookie, domain=_COOKIE_DOMAIN)

    response = await client.get("/admin/", follow_redirects=False)

    assert response.status_code in (302, 307)
    assert "/admin/change-password" in response.headers["location"]


async def test_must_change_password_does_not_redirect_its_own_page(
    client, admin_app
):
    _make_staff(admin_app, "penny")
    cookie = _session_cookie(
        admin_app.state.settings.secret_key, {SESSION_KEY: "penny"}
    )
    client.cookies.set("session", cookie, domain=_COOKIE_DOMAIN)

    response = await client.get("/admin/change-password", follow_redirects=False)

    assert response.status_code == 200


# --- 6/7: forced MFA-enrolment gate ------------------------------------------


async def test_no_mfa_enrolment_redirects_the_dashboard_to_enrol(client, admin_app):
    _make_staff(admin_app, "quinn", password_changed=True)  # mfa stays unenrolled
    cookie = _session_cookie(
        admin_app.state.settings.secret_key, {SESSION_KEY: "quinn"}
    )
    client.cookies.set("session", cookie, domain=_COOKIE_DOMAIN)

    response = await client.get("/admin/", follow_redirects=False)

    assert response.status_code in (302, 307)
    assert "/admin/enrol" in response.headers["location"]


async def test_no_mfa_enrolment_does_not_redirect_its_own_page(client, admin_app):
    _make_staff(admin_app, "quinn", password_changed=True)
    cookie = _session_cookie(
        admin_app.state.settings.secret_key, {SESSION_KEY: "quinn"}
    )
    client.cookies.set("session", cookie, domain=_COOKIE_DOMAIN)

    response = await client.get("/admin/enrol", follow_redirects=False)

    assert response.status_code == 200


# --- 8: logout clears the session -------------------------------------------


async def test_logout_clears_the_session(client, admin_app):
    _make_staff(admin_app, "gina", password_changed=True, mfa=True)
    cookie = _session_cookie(admin_app.state.settings.secret_key, {SESSION_KEY: "gina"})
    client.cookies.set("session", cookie, domain=_COOKIE_DOMAIN)

    pre = await client.get("/admin/", follow_redirects=False)
    assert pre.status_code == 200

    logout_response = await client.get("/admin/logout", follow_redirects=False)
    assert logout_response.status_code in (302, 307)

    response = await client.get("/admin/", follow_redirects=False)

    assert response.status_code in (302, 307)
    assert "/admin/login" in response.headers["location"]


# --- 9: a deactivated account's existing session is refused next request ---


async def test_a_deactivated_accounts_session_is_refused_on_its_next_request(
    client, admin_app
):
    _make_staff(admin_app, "hank", password_changed=True, mfa=True)
    cookie = _session_cookie(admin_app.state.settings.secret_key, {SESSION_KEY: "hank"})
    client.cookies.set("session", cookie, domain=_COOKIE_DOMAIN)

    pre = await client.get("/admin/", follow_redirects=False)
    assert pre.status_code == 200

    _deactivate(admin_app, "hank")

    response = await client.get("/admin/", follow_redirects=False)

    assert response.status_code in (302, 307)
    assert "/admin/login" in response.headers["location"]


# --- Step 4: the branded login template -------------------------------------


async def test_the_login_page_uses_the_brand_layout(client):
    """Task 1's File Structure named this override but its steps never wrote
    it, so the first page anyone sees was rendering sqladmin's stock
    template."""
    response = await client.get("/admin/login")

    assert response.status_code == 200
    assert "/admin/static/brand.css" in response.text


# --- Finding 1: a fresh account has a route through onboarding -------------
#
# The gap the review caught: authenticate() required SESSION_KEY before
# ever consulting the exempt list, and SESSION_KEY is only ever written by
# the second factor - which an unenrolled account cannot pass. A brand-new
# account (bootstrap leaves admin and admin2 in exactly this state) had a
# redirect to /admin/verify and no way to get past it.


async def test_login_sends_a_brand_new_account_to_the_forced_password_change(
    client, admin_app
):
    password = _make_staff(admin_app, "frank")  # must_change_password, no MFA

    response = await client.post(
        "/admin/login",
        data={"username": "frank", "password": password},
        follow_redirects=False,
    )

    assert response.status_code in (302, 307)
    assert "/admin/change-password" in response.headers["location"]


async def test_a_brand_new_account_can_actually_reach_that_page(client, admin_app):
    """The walk itself, not just the redirect target: the Set-Cookie from
    the login POST has to carry a pending value /admin/change-password will
    actually accept, or the account is still stuck."""
    password = _make_staff(admin_app, "frank")

    login_response = await client.post(
        "/admin/login",
        data={"username": "frank", "password": password},
        follow_redirects=False,
    )
    assert "/admin/change-password" in login_response.headers["location"]

    response = await client.get("/admin/change-password", follow_redirects=False)

    assert response.status_code == 200


async def test_login_sends_a_password_changed_account_to_enrolment(client, admin_app):
    password = _make_staff(admin_app, "george", password_changed=True)  # no MFA yet

    response = await client.post(
        "/admin/login",
        data={"username": "george", "password": password},
        follow_redirects=False,
    )

    assert response.status_code in (302, 307)
    assert "/admin/enrol" in response.headers["location"]


# --- Finding 2: login() clears any pre-existing session --------------------


async def test_login_clears_a_preexisting_session_on_success(client, admin_app):
    """Alice is logged in on a shared machine; Bob then logs in with his own
    correct credentials. Without a clear at the start of login(), Bob's
    request would carry Alice's SESSION_KEY into the response too, and
    contract 8.4 writes that value straight to audit_log.actor."""
    _make_staff(admin_app, "alice", password_changed=True, mfa=True)
    bob_password = _make_staff(admin_app, "bob", password_changed=True, mfa=True)
    alice_cookie = _session_cookie(
        admin_app.state.settings.secret_key, {SESSION_KEY: "alice"}
    )
    client.cookies.set("session", alice_cookie, domain=_COOKIE_DOMAIN)

    response = await client.post(
        "/admin/login",
        data={"username": "bob", "password": bob_password},
        follow_redirects=False,
    )

    cookie_value = response.cookies.get("session")
    assert cookie_value is not None
    session_data = _decode_session_cookie(
        admin_app.state.settings.secret_key, cookie_value
    )
    assert session_data.get(SESSION_KEY) is None
    assert session_data[PENDING_SESSION_KEY]["username"] == "bob"


async def test_a_failed_login_also_clears_a_preexisting_session(client, admin_app):
    _make_staff(admin_app, "alice", password_changed=True, mfa=True)
    alice_cookie = _session_cookie(
        admin_app.state.settings.secret_key, {SESSION_KEY: "alice"}
    )
    client.cookies.set("session", alice_cookie, domain=_COOKIE_DOMAIN)

    response = await client.post(
        "/admin/login",
        data={"username": "bob", "password": "wrong"},
        follow_redirects=False,
    )
    assert response.status_code == 400

    # An emptied session clears via a "session=null" Set-Cookie rather than
    # a decodable signed value (starlette.middleware.sessions.
    # SessionMiddleware), so the clearest proof is that the next request no
    # longer carries Alice's access.
    dashboard = await client.get("/admin/", follow_redirects=False)

    assert dashboard.status_code in (302, 307)
    assert "/admin/login" in dashboard.headers["location"]


# --- Finding 3: the pre-login pages themselves, requested directly ---------
#
# Previously /admin/verify appeared in this file only as a substring of a
# redirect Location header - no test ever issued a request to that path
# itself. A mutant that deleted the branch entirely left every prior test
# green. These request all three pre-login pages directly.


@pytest.mark.parametrize("path", PRE_LOGIN_PAGES)
async def test_pre_login_pages_redirect_to_login_with_no_session_at_all(
    client, path
):
    response = await client.get(path, follow_redirects=False)

    assert response.status_code in (302, 307)
    assert "/admin/login" in response.headers["location"]


@pytest.mark.parametrize("path", PRE_LOGIN_PAGES)
async def test_pre_login_pages_are_reachable_with_a_valid_pending_login(
    client, admin_app, path
):
    password = _make_staff(admin_app, "ivy")
    login_response = await client.post(
        "/admin/login",
        data={"username": "ivy", "password": password},
        follow_redirects=False,
    )
    assert login_response.status_code in (302, 307)  # the password was accepted

    response = await client.get(path, follow_redirects=False)

    assert response.status_code == 200


@pytest.mark.parametrize("path", PRE_LOGIN_PAGES)
async def test_pre_login_pages_redirect_to_login_once_the_pending_value_expires(
    client, admin_app, path
):
    _make_staff(admin_app, "jack")
    expired = _session_cookie(
        admin_app.state.settings.secret_key,
        {PENDING_SESSION_KEY: {"username": "jack", "expires_at": time.time() - 1}},
    )
    client.cookies.set("session", expired, domain=_COOKIE_DOMAIN)

    response = await client.get(path, follow_redirects=False)

    assert response.status_code in (302, 307)
    assert "/admin/login" in response.headers["location"]


# --- current_username(): the helper Tasks 5-7 import ------------------------


async def test_current_username_prefers_an_established_session_over_a_pending_one():
    # async def only so the module-wide pytest.mark.asyncio (needed by every
    # other test in this file, which all drive the real app) does not warn
    # on a plain sync function - current_username itself is pure and awaits
    # nothing.
    session_data = {
        SESSION_KEY: "alice",
        PENDING_SESSION_KEY: {"username": "bob", "expires_at": 1},
    }

    assert current_username(session_data) == "alice"


async def test_current_username_falls_back_to_a_pending_login():
    session_data = {PENDING_SESSION_KEY: {"username": "bob", "expires_at": 1}}

    assert current_username(session_data) == "bob"


async def test_current_username_is_none_with_no_session_state():
    assert current_username({}) is None
