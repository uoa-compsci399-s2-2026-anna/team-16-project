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
    generate_initial_password,
    get_staff,
    reset_mfa,
    set_password,
)
from admin.auth import SESSION_KEY
from admin.backend import PENDING_SESSION_KEY, _is_pre_login_page, current_username
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


#: The account state that actually owes each page - round 2 tightened
#: reachability from "any valid pending login" to "a pending login *and*
#: the account still owes this specific step", so unlike round 1's version
#: of this test, a single fresh account no longer reaches all three.
_OWES_STATE_FOR_PAGE = {
    "/admin/change-password": {},  # fresh: must_change_password stays True
    "/admin/enrol": {"password_changed": True},  # mfa stays unenrolled
    "/admin/verify": {"password_changed": True, "mfa": True},  # fully onboarded
}


@pytest.mark.parametrize("path", PRE_LOGIN_PAGES)
async def test_pre_login_pages_are_reachable_once_the_account_actually_owes_that_step(
    client, admin_app, path
):
    password = _make_staff(admin_app, "ivy", **_OWES_STATE_FOR_PAGE[path])
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
    """Seeded per path with the state that actually owes it.

    Round 3 reseeded this fully onboarded and justified it with "a fully
    onboarded account would otherwise be admitted to all three" - which was
    never true (the state gate admits such an account to /admin/verify
    only), so the /admin/change-password and /admin/enrol parametrizations
    passed for a reason unrelated to expiry. Seeding from
    _OWES_STATE_FOR_PAGE is what makes expiry the only thing that can
    explain a refusal on every one of the three paths.
    """
    _make_staff(admin_app, "jack", **_OWES_STATE_FOR_PAGE[path])
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


# --- Round 2: a bare pending value (one factor) must not open a page the ---
# --- account does not owe ---------------------------------------------------
#
# Review found round 1's fix over-corrected: any valid PendingLogin admitted
# all three pre-login pages regardless of what the account's own row said,
# so a password alone could open /admin/enrol on an account that had
# already enrolled - the exact takeover MfaAlreadyEnrolledError exists to
# prevent, reachable the moment /admin/enrol stops being a stub. These are
# the reviewer's three live findings, reproduced directly.


@pytest.mark.parametrize("path", ("/admin/change-password", "/admin/enrol"))
async def test_a_fully_onboarded_account_cannot_open_a_page_it_does_not_owe(
    client, admin_app, path
):
    """The reviewer's first live finding: a fully onboarded account logging
    in with just its password could still open /admin/enrol and
    /admin/change-password - pages that exist only to clear a state this
    account does not have."""
    password = _make_staff(admin_app, "kim", password_changed=True, mfa=True)
    login_response = await client.post(
        "/admin/login",
        data={"username": "kim", "password": password},
        follow_redirects=False,
    )
    assert "/admin/verify" in login_response.headers["location"]  # correctly routed

    response = await client.get(path, follow_redirects=False)

    assert response.status_code in (302, 307)
    assert "/admin/login" in response.headers["location"]


@pytest.mark.parametrize("path", PRE_LOGIN_PAGES)
async def test_a_pending_login_is_refused_once_the_account_is_deactivated(
    client, admin_app, path
):
    """The reviewer's second live finding: an account deactivated during
    the 5-minute pending window kept all three pages reachable - the
    account state was never re-read for this branch.

    Seeded per path with the state that actually owes it. Round 3 reseeded
    this fully onboarded and justified it with "a fully onboarded account
    would be admitted to all three on the strength of its state alone" -
    which was never true (the state gate admits such an account to
    /admin/verify only), so the /admin/change-password and /admin/enrol
    parametrizations passed for a reason unrelated to deactivation. The
    sanity request below now proves the page really was open first, so
    deactivation is the only thing that can explain the refusal after."""
    password = _make_staff(admin_app, "leo", **_OWES_STATE_FOR_PAGE[path])
    login_response = await client.post(
        "/admin/login",
        data={"username": "leo", "password": password},
        follow_redirects=False,
    )
    assert login_response.status_code in (302, 307)

    pre = await client.get(path, follow_redirects=False)
    assert pre.status_code == 200

    _deactivate(admin_app, "leo")

    response = await client.get(path, follow_redirects=False)

    assert response.status_code in (302, 307)
    assert "/admin/login" in response.headers["location"]


@pytest.mark.parametrize("path", PRE_LOGIN_PAGES)
async def test_a_pending_login_naming_an_unknown_account_is_refused(
    client, admin_app, path
):
    """The reviewer's third live finding: a pending value naming an
    account that does not exist (never existed, or was deleted since) kept
    all three pages reachable."""
    forged = _session_cookie(
        admin_app.state.settings.secret_key,
        {PENDING_SESSION_KEY: {"username": "ghost", "expires_at": time.time() + 60}},
    )
    client.cookies.set("session", forged, domain=_COOKIE_DOMAIN)

    response = await client.get(path, follow_redirects=False)

    assert response.status_code in (302, 307)
    assert "/admin/login" in response.headers["location"]


# --- Round 2 minor: a malformed pending value must not 500 -----------------


async def test_a_pending_value_with_a_non_numeric_expiry_is_refused_not_500(
    client, admin_app
):
    """_pending_login_from_session's docstring claimed defensiveness the
    code did not actually provide: PendingLogin is an unchecked frozen
    dataclass, so construction succeeded with any type and a malformed
    expires_at only failed later, inside is_expired()'s comparison, outside
    the try/except meant to catch it."""
    _make_staff(admin_app, "mia")
    malformed = _session_cookie(
        admin_app.state.settings.secret_key,
        {PENDING_SESSION_KEY: {"username": "mia", "expires_at": None}},
    )
    client.cookies.set("session", malformed, domain=_COOKIE_DOMAIN)

    response = await client.get("/admin/verify", follow_redirects=False)

    assert response.status_code in (302, 307)
    assert "/admin/login" in response.headers["location"]


# --- Round 2 coverage gap: exact membership, not a prefix match ------------


async def test_pre_login_page_membership_is_exact_not_a_prefix():
    """A mutant changing `path in _PRE_LOGIN_PAGES` to a startswith()
    prefix match kept the whole suite green, since nothing today is
    registered under any of the three pre-login paths - an end-to-end HTTP
    request to a path like /admin/enrol/anything 404s at the router before
    authenticate() is ever consulted, regardless of this property, so the
    property has to be pinned directly against the function that implements
    it rather than through a live request.

    async def only for the same reason as the current_username tests above
    - the module-wide pytest.mark.asyncio would otherwise warn on a plain
    sync function.
    """
    assert _is_pre_login_page("/admin/enrol") is True
    assert _is_pre_login_page("/admin/change-password") is True
    assert _is_pre_login_page("/admin/verify") is True

    assert _is_pre_login_page("/admin/enrol/anything") is False
    assert _is_pre_login_page("/admin/enrolment") is False
    assert _is_pre_login_page("/admin/enrolment/list") is False


# --- Round 3: the SESSION_KEY branch skipped the same re-read the pending --
# --- branch got in round 2 --------------------------------------------------
#
# Review found round 2 fixed the account re-read only for the pending-login
# branch of _may_open_pre_login_page and left the SESSION_KEY branch
# short-circuiting straight to True with no read at all - so an
# already-logged-in, since-deactivated account, or a SESSION_KEY naming a
# row that no longer exists, still opened all three pre-login pages. Same
# bug as round 2, other branch in.


@pytest.mark.parametrize("path", PRE_LOGIN_PAGES)
async def test_a_logged_in_but_deactivated_account_cannot_open_a_pre_login_page(
    client, admin_app, path
):
    # Seeded per path with the state that owes it: round 4 removed the
    # already-logged-in short-circuit, so a SESSION_KEY no longer opens all
    # three pages on its own and a fully onboarded account would fail the
    # sanity request below on two of the three paths.
    _make_staff(admin_app, "ruth", **_OWES_STATE_FOR_PAGE[path])
    cookie = _session_cookie(admin_app.state.settings.secret_key, {SESSION_KEY: "ruth"})
    client.cookies.set("session", cookie, domain=_COOKIE_DOMAIN)

    # Sanity: reachable before deactivation - otherwise a refusal below
    # could not be pinned on deactivation specifically.
    pre = await client.get(path, follow_redirects=False)
    assert pre.status_code == 200

    _deactivate(admin_app, "ruth")

    response = await client.get(path, follow_redirects=False)

    assert response.status_code in (302, 307)
    assert "/admin/login" in response.headers["location"]


@pytest.mark.parametrize("path", PRE_LOGIN_PAGES)
async def test_a_session_naming_an_unknown_account_cannot_open_a_pre_login_page(
    client, admin_app, path
):
    forged = _session_cookie(
        admin_app.state.settings.secret_key, {SESSION_KEY: "ghost2"}
    )
    client.cookies.set("session", forged, domain=_COOKIE_DOMAIN)

    response = await client.get(path, follow_redirects=False)

    assert response.status_code in (302, 307)
    assert "/admin/login" in response.headers["location"]


# --- Round 3 minor: /admin/enrol is reachable before the forced password ---
# --- change ------------------------------------------------------------------


async def test_enrol_is_refused_while_a_password_change_is_still_owed(
    client, admin_app
):
    """The enrol gate checked only `not mfa_enrolled`, so a brand-new
    account satisfied both /admin/change-password and /admin/enrol at
    once. login() enforces the ladder order contract 8.3 states, but the
    gate itself did not, making the redirect advisory rather than
    binding."""
    password = _make_staff(admin_app, "nadia")  # fresh: must_change_password stays True

    login_response = await client.post(
        "/admin/login",
        data={"username": "nadia", "password": password},
        follow_redirects=False,
    )
    assert "/admin/change-password" in login_response.headers["location"]

    response = await client.get("/admin/enrol", follow_redirects=False)

    assert response.status_code in (302, 307)
    assert "/admin/login" in response.headers["location"]


# --- Round 3 minor: the /admin/verify mirror-image property was untested ---
#
# Round 2 self-review claimed the "both cleared" condition on /admin/verify
# was confirmed against every account state the suite exercised; the suite
# in fact exercised no such combination, and mutant M7 - deleting the
# `not staff.must_change_password` half of that condition - survived all 51
# tests. Two tests below: one pins the property the reviewer's minor asked
# for directly (a half-onboarded account, password already changed but not
# yet enrolled, must not reach /admin/verify); the other is the one that
# actually distinguishes M7 - the two operands of the gate's `and` only ever
# disagree with a mutant dropping one of them when must_change_password and
# mfa_enrolled are BOTH true at once (enrolled, but a password reset is
# outstanding), which the first test's state does not produce.


async def test_verify_is_refused_for_a_half_onboarded_account_password_changed_only(
    client, admin_app
):
    password = _make_staff(admin_app, "olga", password_changed=True)  # mfa not enrolled

    login_response = await client.post(
        "/admin/login",
        data={"username": "olga", "password": password},
        follow_redirects=False,
    )
    assert "/admin/enrol" in login_response.headers["location"]

    response = await client.get("/admin/verify", follow_redirects=False)

    assert response.status_code in (302, 307)
    assert "/admin/login" in response.headers["location"]


async def test_verify_is_refused_while_enrolled_but_a_password_change_is_still_owed(
    client, admin_app
):
    """The test that kills mutant M7: must_change_password and
    mfa_enrolled both true at once - reachable if an administrator resets
    a password without touching MFA - is the only state where dropping the
    `not staff.must_change_password` half of the verify gate changes the
    answer. mfa=True with password_changed left False (the default)
    produces exactly that combination."""
    password = _make_staff(admin_app, "priya", mfa=True)  # must_change_password stays True

    login_response = await client.post(
        "/admin/login",
        data={"username": "priya", "password": password},
        follow_redirects=False,
    )
    assert "/admin/change-password" in login_response.headers["location"]

    response = await client.get("/admin/verify", follow_redirects=False)

    assert response.status_code in (302, 307)
    assert "/admin/login" in response.headers["location"]


# ===========================================================================
# Round 4: the state gate applies whichever way in
# ===========================================================================
#
# Rounds 1-3 each fixed one combination of (way in) x (account state) and
# left the symmetric one behind. Round 3 ended with
# _may_open_pre_login_page reading the account row on both ways in, but
# still short-circuiting `if already_logged_in: return True` *before* the
# per-page state gate - so a live SESSION_KEY opened all three pre-login
# pages regardless of what the account owed.
#
# Why that branch was invisible: a live SESSION_KEY with
# must_change_password set, or mfa_enrolled clear, is unreachable through
# any ordinary login - SESSION_KEY is written only by
# admin.auth._complete_login, which refuses an unenrolled account and is
# reachable only through /admin/verify, which the gate opens only once
# both are cleared. The only way into those states is an administrator's
# mid-session reset - which is exactly the states contract 8.3's L2
# eviction creates, and L2 is the *only* eviction available while
# _guard_admin_floor forbids deactivating an administrator (the normal
# state of a two-administrator charity deployment).
#
# The tests below stop fixing combinations one at a time and sweep the
# whole space instead: 2 ways in x 4 account states x 3 pages = 24 cases,
# each stating what the gate must answer.


#: The four account states the two booleans can produce, and which of the
#: three pre-login pages each one may open. Derived from the gate's own
#: rule - a page is open exactly while the account still owes that step,
#: in contract 8.3's ladder order - not read back off the implementation.
#:
#: (must_change_password, mfa_enrolled):
#:   fresh              (True,  False) -> owes the password change
#:   password_changed   (False, False) -> owes enrolment
#:   fully_onboarded    (False, True)  -> owes only the second factor
#:   password_reset_only(True,  True)  -> enrolled, but a password reset is
#:                                        outstanding: an administrator
#:                                        forced a change without touching
#:                                        MFA, so the ladder restarts at the
#:                                        password change
_ACCOUNT_STATES = {
    "fresh": ({}, "/admin/change-password"),
    "password_changed": ({"password_changed": True}, "/admin/enrol"),
    "fully_onboarded": ({"password_changed": True, "mfa": True}, "/admin/verify"),
    "password_reset_only": ({"mfa": True}, "/admin/change-password"),
}

#: Every (state, page) pair, with the single expected answer.
_STATE_GATE_MATRIX = [
    (state, path, path == owed)
    for state, (_, owed) in _ACCOUNT_STATES.items()
    for path in PRE_LOGIN_PAGES
]


@pytest.mark.parametrize("state,path,expected_open", _STATE_GATE_MATRIX)
async def test_the_state_gate_on_the_session_key_way_in(
    client, admin_app, state, path, expected_open
):
    """All 12 (account state, page) pairs, entered with a live SESSION_KEY.

    This is the combination three rounds of review kept missing. Before
    round 4 every one of these twelve returned 200, because
    _may_open_pre_login_page short-circuited on already_logged_in before
    reaching the gate at all.
    """
    seed, _ = _ACCOUNT_STATES[state]
    _make_staff(admin_app, "sasha", **seed)
    cookie = _session_cookie(
        admin_app.state.settings.secret_key, {SESSION_KEY: "sasha"}
    )
    client.cookies.set("session", cookie, domain=_COOKIE_DOMAIN)

    response = await client.get(path, follow_redirects=False)

    if expected_open:
        assert response.status_code == 200
    else:
        assert response.status_code in (302, 307)
        assert "/admin/login" in response.headers["location"]


@pytest.mark.parametrize("state,path,expected_open", _STATE_GATE_MATRIX)
async def test_the_state_gate_on_the_pending_login_way_in(
    client, admin_app, state, path, expected_open
):
    """The same 12 pairs entered with only a password, via a real login POST.

    Rounds 2 and 3 fixed this branch piecemeal; sweeping it whole is what
    pins the property rather than the individual cases that were reported.
    """
    seed, _ = _ACCOUNT_STATES[state]
    password = _make_staff(admin_app, "sasha", **seed)
    login_response = await client.post(
        "/admin/login",
        data={"username": "sasha", "password": password},
        follow_redirects=False,
    )
    assert login_response.status_code in (302, 307)  # the password was accepted

    response = await client.get(path, follow_redirects=False)

    if expected_open:
        assert response.status_code == 200
    else:
        assert response.status_code in (302, 307)
        assert "/admin/login" in response.headers["location"]


def _evict_l2(admin_app, username: str) -> str:
    """Contract 8.3 recovery layer L2, performed against a live session.

    "Another administrator resets MFA and issues a random password" - the
    only eviction available while _guard_admin_floor refuses to deactivate
    an administrator, which is the normal state of a two-administrator
    deployment. set_password clears must_change_password, so the flag is
    set back afterwards: an issued password is a temporary one the holder
    must change, exactly as create_staff leaves a new account.
    """
    new_password = generate_initial_password()
    with admin_app.state.session_factory() as db:
        reset_mfa(db, username)
        set_password(db, username, new_password)
        get_staff(db, username).must_change_password = True
        db.commit()
    return new_password


async def test_an_evicted_account_cannot_re_enrol_its_own_authenticator(
    client, admin_app
):
    """The exploit the round-3 short-circuit left open, end to end.

    reset_mfa clears mfa_enrolled_at - and that clearing is exactly what
    removes begin_mfa_enrolment's own MfaAlreadyEnrolledError guard, so
    there is no service-layer backstop behind this gate. With the
    short-circuit in place, the person being evicted kept a live session on
    /admin/enrol and could enrol a fresh authenticator, mint five new
    recovery codes, and hold both factors again - with audit_log.actor
    (contract 8.4) naming them throughout. The gate is the only thing
    stopping it.
    """
    _make_staff(admin_app, "trent", password_changed=True, mfa=True)
    cookie = _session_cookie(
        admin_app.state.settings.secret_key, {SESSION_KEY: "trent"}
    )
    client.cookies.set("session", cookie, domain=_COOKIE_DOMAIN)
    assert (await client.get("/admin/", follow_redirects=False)).status_code == 200

    _evict_l2(admin_app, "trent")

    # The state the eviction actually produced - asserted, not assumed,
    # since the whole finding turns on which states this branch is
    # reachable in.
    with admin_app.state.session_factory() as db:
        staff = get_staff(db, "trent")
        assert staff.is_active is True
        assert staff.must_change_password is True
        assert staff.mfa_enrolled is False

    enrol = await client.get("/admin/enrol", follow_redirects=False)

    assert enrol.status_code in (302, 307)
    assert "/admin/login" in enrol.headers["location"]

    # ...and the second factor is out of reach too, so the evicted session
    # cannot be turned back into a full one by any route.
    verify = await client.get("/admin/verify", follow_redirects=False)
    assert verify.status_code in (302, 307)


#: The account states an administrator can force a live session into
#: mid-session, and where /admin/ must send that session next. Removing
#: the already_logged_in short-circuit must not break the ladder - keeping
#: it working is the point of the fix, not a side effect.
_MID_SESSION_LADDER = [
    ("fully_onboarded", None),  # nothing owed: the dashboard renders
    ("password_reset_only", "/admin/change-password"),
    ("fresh", "/admin/change-password"),
    ("password_changed", "/admin/enrol"),
]


@pytest.mark.parametrize("state,expected_target", _MID_SESSION_LADDER)
async def test_the_dashboard_ladder_survives_the_removed_short_circuit(
    client, admin_app, state, expected_target
):
    """authenticate()'s non-pre-login branch, for every account state.

    The counterpart to the two matrix tests above: those pin what the three
    pre-login pages answer, this pins where everything else sends a live
    session, so the fix cannot be "correct" by simply refusing more.
    """
    seed, _ = _ACCOUNT_STATES[state]
    _make_staff(admin_app, "ursula", **seed)
    cookie = _session_cookie(
        admin_app.state.settings.secret_key, {SESSION_KEY: "ursula"}
    )
    client.cookies.set("session", cookie, domain=_COOKIE_DOMAIN)

    response = await client.get("/admin/", follow_redirects=False)

    if expected_target is None:
        assert response.status_code == 200
    else:
        assert response.status_code in (302, 307)
        assert expected_target in response.headers["location"]


# --- Round 4 minor: the two session.clear() calls were pinned by nothing ---
#
# Mutation testing (round 4, mutants N3 and N4) found both
# request.session.clear() calls in _may_open_pre_login_page could be
# deleted with the whole suite still green: every test asserted only the
# 302 and its Location, never that the cookie itself was cleared. They are
# behaviourally load-bearing - without them a forged or stale cookie
# survives the refusal and buys an unauthenticated caller one database read
# per request, indefinitely.
#
# starlette.middleware.sessions.SessionMiddleware signals a cleared session
# with a literal "session=null" Set-Cookie carrying an expiry in the past,
# rather than by re-signing an empty payload, so that string is the marker.


def _clearing_set_cookie(response) -> bool:
    return any(
        header.startswith("session=null")
        for header in response.headers.get_list("set-cookie")
    )


@pytest.mark.parametrize("path", PRE_LOGIN_PAGES)
async def test_an_unknown_account_has_its_session_cookie_cleared(
    client, admin_app, path
):
    forged = _session_cookie(
        admin_app.state.settings.secret_key, {SESSION_KEY: "ghost3"}
    )
    client.cookies.set("session", forged, domain=_COOKIE_DOMAIN)

    response = await client.get(path, follow_redirects=False)

    assert response.status_code in (302, 307)
    assert _clearing_set_cookie(response), response.headers.get_list("set-cookie")


@pytest.mark.parametrize("path", PRE_LOGIN_PAGES)
async def test_a_deactivated_account_has_its_session_cookie_cleared(
    client, admin_app, path
):
    _make_staff(admin_app, "vera", active=False, **_OWES_STATE_FOR_PAGE[path])
    cookie = _session_cookie(
        admin_app.state.settings.secret_key, {SESSION_KEY: "vera"}
    )
    client.cookies.set("session", cookie, domain=_COOKIE_DOMAIN)

    response = await client.get(path, follow_redirects=False)

    assert response.status_code in (302, 307)
    assert _clearing_set_cookie(response), response.headers.get_list("set-cookie")


# --- Round 4, gaps found by enumerating rather than by review --------------
#
# Walking every path through authenticate() rather than only the ones the
# review pointed at turned up three combinations nothing pinned. All three
# are on the *other* branch of authenticate() - the one for every path that
# is not a pre-login page - which is the same "fixed the case I was shown"
# shape as the four findings this file already carries:
#
#   * a SESSION_KEY naming an account that no longer exists was pinned on
#     the three pre-login pages and nowhere else, even though the main
#     branch has its own separate get_staff/UnknownStaffError handling;
#   * neither of that branch's two request.session.clear() calls was pinned
#     by anything - the same mutants (N3/N4) that survived against
#     _may_open_pre_login_page survive here for the same reason: every test
#     asserted the 302 and its Location, never the cookie.


async def test_a_session_naming_an_unknown_account_is_refused_on_the_dashboard(
    client, admin_app
):
    """authenticate()'s main branch has its own UnknownStaffError handling,
    separate from _may_open_pre_login_page's - and only the latter was
    pinned. A row deleted out from under a live session must refuse here
    too, or the request reaches a view whose first get_staff call raises."""
    forged = _session_cookie(
        admin_app.state.settings.secret_key, {SESSION_KEY: "ghost4"}
    )
    client.cookies.set("session", forged, domain=_COOKIE_DOMAIN)

    response = await client.get("/admin/", follow_redirects=False)

    assert response.status_code in (302, 307)
    assert "/admin/login" in response.headers["location"]
    assert _clearing_set_cookie(response), response.headers.get_list("set-cookie")


async def test_a_deactivated_session_is_cleared_not_merely_refused(
    client, admin_app
):
    """The other half of the same gap: refusing a deactivated account on
    the dashboard was pinned, clearing its cookie was not. Without the
    clear, the cookie survives the refusal and buys an unauthenticated
    caller one database read per request for as long as it is replayed."""
    _make_staff(admin_app, "wilma", password_changed=True, mfa=True)
    cookie = _session_cookie(
        admin_app.state.settings.secret_key, {SESSION_KEY: "wilma"}
    )
    client.cookies.set("session", cookie, domain=_COOKIE_DOMAIN)
    assert (await client.get("/admin/", follow_redirects=False)).status_code == 200

    _deactivate(admin_app, "wilma")

    response = await client.get("/admin/", follow_redirects=False)

    assert response.status_code in (302, 307)
    assert _clearing_set_cookie(response), response.headers.get_list("set-cookie")
