"""Every registered view and every custom route, driven at both roles.

**Why this file exists.** The role floor on this panel had been "enforced in
exactly two places (``StaffAdmin``, ``IpBlockAdmin``)", stated as a fact in
review notes rather than as anything a test would notice. That statement was
true, and the audit log's openness to ``staff`` was the instance somebody
happened to look at — there was nothing anywhere that would have said so if a
fifteenth view had been registered with no floor at all, or if one of the
twenty-odd ``@expose``/``@action`` routes had lost its ``_require_admin`` line
in a refactor. ``@action`` in particular inherits neither auditing nor
``is_accessible``: sqladmin registers it with ``login_required`` only, so a
route with no explicit check is reachable by any signed-in account no matter
what its view's ``is_accessible`` returns.

**The shape of the assertions, and why it is this shape.** A test that only
asserts refusal passes against a system that refuses everything — a typo in a
URL, a view that failed to register, a fixture that never logged in, all read
as a pass. So every route below is driven **twice**: once as an administrator,
where it must *not* be refused, and once as a plain ``staff`` member, where the
expectation is whatever the contract's §8.3 role table says for that route.
``_ADMIN_ONLY`` and ``_BOTH_ROLES`` between them name every route the panel
registers; ``test_every_registered_route_is_named_here`` walks the live app's
router and fails if one is missing from both, so adding a view without deciding
its role is a failing test rather than an oversight.

Real HTTP with a real session throughout (``admin_client`` / ``staff_client``
from tests/admin/conftest.py), never ``is_accessible`` called in isolation.
Four defects on this project came from a guard sitting in the wrong layer, and
a unit test on the predicate would have passed for every one of them.
"""

import re

import pytest

#: `pytest.mark.asyncio` alongside `pytest.mark.db`: pytest.ini sets
#: `asyncio_mode = strict`, under which an `async def test_*` without it is
#: collected and then errors as an unsupported coroutine. Every other file in
#: this directory that awaits `admin_client`/`staff_client` carries the same
#: pair - see tests/admin/test_blocklist_view.py's docstring for the full note.
#: The two completeness checks at the end of this file await nothing and are
#: `async def` only so that the file-level mark applies cleanly to every test
#: in it - pytest-asyncio warns on a synchronous test carrying the mark.
pytestmark = [pytest.mark.db, pytest.mark.asyncio]


#: Every route a plain ``staff`` account must be refused, as
#: ``(method, path, why)``. A refusal is 403 from ``_require_admin`` /
#: sqladmin's own ``is_accessible`` check on the routes it generates.
#:
#: ``/admin/staff/*`` and ``/admin/ip-block/*`` are contract §8.3's
#: "Create, deactivate and re-role accounts" and "reset another account's MFA,
#: issue a random password" rows, plus §2.3's administrator-only blocklist.
#: ``/admin/audit-log/*`` joined them in v1.15 — see AuditLogAdmin's docstring.
_ADMIN_ONLY: list[tuple[str, str, str]] = [
    # --- the audit log (v1.15) ---------------------------------------------
    # Three separate handlers inside sqladmin, not one: `_list`, `_details`
    # and `_export` are distinct methods that each consult `is_accessible`
    # separately. Named separately here for exactly that reason - a filter or
    # a guard applied to the list alone leaves the other two open, and the
    # export hands over the whole table in a single request.
    ("GET", "/admin/audit-log/list", "the trail is an administrator's oversight tool"),
    ("GET", "/admin/audit-log/details/1", "detail is one guessed integer from the list"),
    ("GET", "/admin/audit-log/export/csv", "export is the whole table in one request"),
    # --- account management (§8.3) -----------------------------------------
    ("GET", "/admin/staff/list", "account management is administrator-only"),
    ("GET", "/admin/staff/details/1", "the details page is one click from the list"),
    ("GET", "/admin/staff/export/csv", "export bypasses the list page entirely"),
    ("GET", "/admin/staff/new", "creating an account mints a working credential"),
    ("POST", "/admin/staff/new", "the GET being refused does not refuse the POST"),
    ("GET", "/admin/staff/delete", "deletion is irreversible"),
    ("POST", "/admin/staff/delete", "the GET being refused does not refuse the POST"),
    ("GET", "/admin/staff/initial-password", "an unclaimed password is a live credential"),
    ("POST", "/admin/staff/initial-password", "the GET being refused does not refuse the POST"),
    ("GET", "/admin/staff/action/show-initial-password", "it reveals a live credential"),
    ("GET", "/admin/staff/action/issue-password", "recovery action, §8.3"),
    ("GET", "/admin/staff/action/reset-mfa", "recovery action, §8.3"),
    ("GET", "/admin/staff/action/deactivate", "account management, §8.3"),
    ("GET", "/admin/staff/action/reactivate", "account management, §8.3"),
    ("GET", "/admin/staff/action/delete", "account management, §8.3"),
    # --- the blocklist (§2.3) ----------------------------------------------
    ("GET", "/admin/ip-block/list", "blocking a public service is administrator-only"),
    ("GET", "/admin/ip-block/details/1", "ip_hmac is one click from the list"),
    ("GET", "/admin/ip-block/export/csv", "export bypasses the list page entirely"),
    ("GET", "/admin/ip-block/block", "a manual block is an administrator's decision"),
    ("POST", "/admin/ip-block/block", "the GET being refused does not refuse the POST"),
    ("GET", "/admin/ip-block/action/unblock", "removing a block is administrator-only"),
]


#: Every route contract §8.3 grants to **both** roles. Named so that
#: restricting one by accident - the easy mistake in the other direction, and
#: one nobody would notice until a staff member could not do their job - fails
#: here rather than in a support conversation.
#:
#: "Publishing and rollback are available to both" is §8.3's decision 4,
#: deliberately: `audit_log` plus one-click rollback already provide
#: accountability, and gating them behind an administrator would stall routine
#: work in a three-to-five person team.
_BOTH_ROLES: list[tuple[str, str, str]] = [
    ("GET", "/admin/", "the panel index"),
    ("GET", "/admin/getting-started", "the first-run walkthrough"),
    ("GET", "/admin/security", "the signed-in account's own security screen"),
    ("GET", "/admin/try", "dry run, §8.3"),
    # The six taxonomy screens, and their two bulk actions on one of them.
    ("GET", "/admin/destination-group/list", "taxonomy CRUD, §8.3"),
    ("GET", "/admin/destination/list", "taxonomy CRUD, §8.3"),
    ("GET", "/admin/sector/list", "taxonomy CRUD, §8.3"),
    ("GET", "/admin/food-category/list", "taxonomy CRUD, §8.3"),
    ("GET", "/admin/metric/list", "taxonomy CRUD, §8.3"),
    ("GET", "/admin/unit-preset/list", "taxonomy CRUD, §8.3"),
    ("GET", "/admin/destination-group/action/activate", "taxonomy bulk action, §8.3"),
    ("GET", "/admin/destination-group/action/deactivate", "taxonomy bulk action, §8.3"),
    ("GET", "/admin/destination/action/activate", "taxonomy bulk action, §8.3"),
    ("GET", "/admin/destination/action/deactivate", "taxonomy bulk action, §8.3"),
    ("GET", "/admin/sector/action/activate", "taxonomy bulk action, §8.3"),
    ("GET", "/admin/sector/action/deactivate", "taxonomy bulk action, §8.3"),
    ("GET", "/admin/food-category/action/activate", "taxonomy bulk action, §8.3"),
    ("GET", "/admin/food-category/action/deactivate", "taxonomy bulk action, §8.3"),
    ("GET", "/admin/metric/action/activate", "taxonomy bulk action, §8.3"),
    ("GET", "/admin/metric/action/deactivate", "taxonomy bulk action, §8.3"),
    ("GET", "/admin/unit-preset/action/activate", "taxonomy bulk action, §8.3"),
    ("GET", "/admin/unit-preset/action/deactivate", "taxonomy bulk action, §8.3"),
    # The six factor screens.
    ("GET", "/admin/factor-set/list", "factor CRUD, §8.3"),
    ("GET", "/admin/factor-upstream/list", "factor CRUD, §8.3"),
    ("GET", "/admin/factor-downstream/list", "factor CRUD, §8.3"),
    ("GET", "/admin/constant/list", "factor CRUD, §8.3"),
    ("GET", "/admin/formula/list", "factor CRUD, §8.3"),
    ("GET", "/admin/equivalence/list", "factor CRUD, §8.3"),
    # Publish, roll back, clone, archive - both roles by §8.3 decision 4.
    ("GET", "/admin/factor-set/action/clone", "publishing is both roles, §8.3 decision 4"),
    ("GET", "/admin/factor-set/action/publish", "publishing is both roles, §8.3 decision 4"),
    ("GET", "/admin/factor-set/action/rollback", "rollback is both roles, §8.3 decision 4"),
    ("GET", "/admin/factor-set/action/archive", "archiving is both roles, §8.3 decision 4"),
    ("GET", "/admin/factor-set/action/compare", "the pre-publish gate, §8.2"),
    # The two comparison-scenario screens.
    ("GET", "/admin/comparison-scenario/list", "the standard scenarios are staff-editable"),
    ("GET", "/admin/comparison-scenario-line/list", "the standard scenarios are staff-editable"),
]


def _ids(cases) -> list[str]:
    """Readable parametrize ids, built up front rather than through `ids=fn`.

    Two things had to be worked around. pytest calls an `ids=` callable once
    per *argument*, not once per tuple, so a function taking the whole case
    raises "error raised while trying to determine id of parameter 'path'".
    And left to itself pytest turns "GET /admin/staff/list" into "G E-/ a-s t"
    - it sanitises per character and then dedupes - which makes both a failure
    list and a `-k` filter useless. A precomputed list of strings sidesteps
    both.
    """
    return [f"{method}:{path.strip('/').replace('/', '.')}" for method, path, _ in cases]


# --- the refusals -----------------------------------------------------------


@pytest.mark.parametrize("method,path,why", _ADMIN_ONLY, ids=_ids(_ADMIN_ONLY))
async def test_a_plain_staff_member_is_refused(staff_client, method, path, why):
    """403, asserted exactly rather than `in (302, 403)`.

    `staff_client` is a real, fully onboarded session, so anything that
    redirects is a redirect to onboarding or to the login page - which would
    mean the account never got in and the refusal proves nothing about the
    role floor. A 404 would mean the route does not exist and the test is
    passing against a typo; `test_an_administrator_is_not_refused` below
    drives the same literal string as an administrator and would catch that,
    but asserting the exact code here is what makes the two halves comparable.
    """
    response = await staff_client.request(method, path, follow_redirects=False)
    assert response.status_code == 403, why


@pytest.mark.parametrize("method,path,why", _ADMIN_ONLY, ids=_ids(_ADMIN_ONLY))
async def test_an_administrator_is_not_refused(admin_client, method, path, why):
    """The other half, and the half that makes the refusals mean anything.

    Not `== 200`: several of these legitimately answer 302 (an `@action` that
    finished and redirected to its list), 400 (a POST with no CSRF token,
    which is what an empty POST body is), or 404 (`details/1` where row 1 does
    not exist in this test database). Every one of those is the route being
    *reached*. 403 is the single answer that would mean an administrator was
    turned away, and it is the only one refused here.

    This is what stops the file above from being a list of typos: a path that
    does not exist answers 404 as staff too, and 404 != 403, so the refusal
    test would already have failed - but a path that exists and is refused to
    *everybody* would pass the refusal test and fail here.
    """
    response = await admin_client.request(method, path, follow_redirects=False)
    assert response.status_code != 403, (
        f"an administrator was refused {method} {path}; {why}"
    )


# --- the grants -------------------------------------------------------------


@pytest.mark.parametrize("method,path,why", _BOTH_ROLES, ids=_ids(_BOTH_ROLES))
async def test_both_roles_reach_it(staff_client, method, path, why):
    response = await staff_client.request(method, path, follow_redirects=False)
    assert response.status_code != 403, (
        f"a staff member was refused {method} {path}; {why}"
    )


@pytest.mark.parametrize("method,path,why", _BOTH_ROLES, ids=_ids(_BOTH_ROLES))
async def test_an_administrator_reaches_it_too(admin_client, method, path, why):
    response = await admin_client.request(method, path, follow_redirects=False)
    assert response.status_code != 403, (
        f"an administrator was refused {method} {path}; {why}"
    )


# --- the sidebar ------------------------------------------------------------


async def test_the_sidebar_offers_a_staff_member_nothing_they_cannot_open(
    staff_client,
):
    """`is_accessible` alone leaves the entry in the menu.

    An entry that 403s when pressed is not a security hole, but it is the
    panel telling somebody they have a capability they do not have, which on
    a five-person team becomes a message asking why it is broken. Asserted
    against the rendered index rather than by calling `is_visible`, because
    `is_visible` returning False and the template rendering the item anyway
    is precisely the failure this catches.

    Anchored on the `href` attribute with its closing quote, not on the bare
    path: an unanchored `"/admin/audit" in html` also matches
    `/admin/audit-something-else` and, more to the point here, matched
    nothing at all when the URL was right - an unanchored pattern let a
    mutant live on this branch two days ago.
    """
    index = await staff_client.get("/admin/")
    assert index.status_code == 200
    html = index.text

    for identity in ("audit-log", "staff", "ip-block"):
        assert not re.search(_menu_link(identity), html), (
            f"the sidebar offered /admin/{identity}/list to a staff member"
        )

    # The other half: the menu is not simply empty. A test asserting only
    # absence passes against a template that renders no navigation at all.
    assert re.search(_menu_link("factor-set"), html), (
        "the sidebar rendered no factor-set entry either, so the absences "
        "above prove nothing"
    )


async def test_the_sidebar_offers_an_administrator_all_three(admin_client):
    """The counterpart, and the reason the absences above are not vacuous.

    Same anchoring: the full `href="..."` attribute including its closing
    quote, so that a substring of some longer URL cannot satisfy it.
    """
    index = await admin_client.get("/admin/")
    assert index.status_code == 200
    html = index.text
    for identity in ("audit-log", "staff", "ip-block"):
        assert re.search(_menu_link(identity), html), (
            f"the sidebar withheld /admin/{identity}/list from an administrator"
        )


# --- completeness: nothing gets registered without a role decision ----------


#: Custom routes deliberately outside the matrix above, with the reason.
#: Anything else concrete the router carries under /admin has to appear in
#: _ADMIN_ONLY or _BOTH_ROLES, or this file fails.
_NOT_ROLE_GATED = {
    # sqladmin's own login/logout, and the three pre-login onboarding pages.
    # None of them can be role-gated: they are what an account uses *before*
    # it has a role-bearing session at all. admin/backend.py's
    # `_may_open_pre_login_page` is the guard that applies to them instead,
    # and tests/admin/test_flow.py drives it.
    "/admin/login",
    "/admin/logout",
    "/admin/verify",
    "/admin/change-password",
    "/admin/enrol",
    # The two static mounts. Brand CSS, fonts and security.js - no account
    # data passes through either, and the login page itself needs them before
    # anybody has a session at all.
    "/admin/static",
    "/admin/statics",
}


def _concrete_admin_routes(app) -> set[str]:
    """Every registered path under /admin that carries no path parameter.

    The parameterised ones are sqladmin's own generic CRUD templates -
    `/admin/{identity}/list`, `/admin/{identity}/details/{pk:path}`,
    `/admin/{identity}/export/{export_type}` and six more - which serve all
    seventeen model views through one route each and are covered by
    `test_every_registered_model_view_has_a_decided_role` below, per view
    rather than per template. What is left is exactly the set of hand-written
    `@expose` and `@action` routes, which is the set that needs naming one at
    a time, because each one carries its own guard or fails to.

    `/admin/factor-sets/{factor_set_id:int}/compare` drops out here as
    parameterised. It is both roles (contract §8.2's pre-publish gate) and
    tests/admin/test_compare_view.py drives it at both; `/admin/factor-set/
    action/compare`, the route that redirects into it, is in _BOTH_ROLES above.
    """
    found: set[str] = set()

    def walk(route, prefix: str = "") -> None:
        path = prefix + getattr(route, "path", "")
        inner = getattr(route, "routes", None)
        if inner:
            for sub in inner:
                walk(sub, path)
            return
        if path.startswith("/admin") and "{" not in path:
            found.add(path.rstrip("/") or "/admin/")

    for route in app.routes:
        walk(route)
    return found


async def test_every_custom_admin_route_has_a_decided_role(admin_app):
    """Refuse a hand-written route nobody has decided the role for.

    This is the assertion the panel did not have, and the reason the audit log
    stayed open as long as it did. A route added with no `_require_admin` line
    is not a visible omission anywhere - it is a page that works, for
    everybody, and looks exactly like one that was meant to.
    """
    registered = _concrete_admin_routes(admin_app)
    named = {path.rstrip("/") or "/admin/" for _, path, _ in _ADMIN_ONLY}
    named |= {path.rstrip("/") or "/admin/" for _, path, _ in _BOTH_ROLES}
    named |= _NOT_ROLE_GATED

    unnamed = sorted(registered - named)
    assert not unnamed, (
        "these routes are registered but no role is decided for them in "
        f"tests/admin/test_role_matrix.py: {unnamed}"
    )

    # The other direction, and the one that catches a stale entry: a path
    # named here that no longer exists would make every assertion about it a
    # 404 rather than a real answer. `/admin/*/list`, `/admin/*/details/1` and
    # `/admin/*/export/csv` are served by parameterised templates and so are
    # never in `registered`; they are excluded by that shape, not by name.
    generic = ("/list", "/export/csv")
    stale = sorted(
        path for path in named
        if path not in registered
        and not path.endswith(generic)
        and "/details/" not in path
        and path != "/admin/"
    )
    assert not stale, (
        f"these paths are named in this file but no longer registered: {stale}"
    )


async def test_every_registered_model_view_has_a_decided_role(admin_app):
    """The per-view half: seventeen `ModelView`s, one decision each.

    `ModelView.is_accessible` defaults to "allow access for everyone", so a
    view registered with no override is open to `staff` silently. Reaching the
    live `Admin` object rather than importing the view classes is deliberate:
    a class that exists and is never passed to `admin.add_view` would satisfy
    an import-based check while being unreachable, and a class that is
    registered but was never imported by this file would be missed entirely.
    The endpoint of any sqladmin-generated route is a bound method of the one
    `Admin` instance, which is the only handle on it from outside.
    """
    admin = _live_admin(admin_app)
    identities = {
        view.identity for view in admin._views if hasattr(view, "model")
    }
    assert len(identities) == 17, (
        f"expected seventeen model views, found {len(identities)}: "
        f"{sorted(identities)}"
    )

    admin_only = {
        path.split("/")[2] for _, path, _ in _ADMIN_ONLY if path.endswith("/list")
    }
    both = {
        path.split("/")[2] for _, path, _ in _BOTH_ROLES if path.endswith("/list")
    }

    undecided = sorted(identities - admin_only - both)
    assert not undecided, (
        "these model views are registered but their role is decided nowhere "
        f"in tests/admin/test_role_matrix.py: {undecided}"
    )
    overlap = sorted(admin_only & both)
    assert not overlap, f"decided twice, in both directions: {overlap}"


def _live_admin(app):
    """sqladmin's `Admin` instance, via a bound endpoint on its mounted app."""
    for route in app.routes:
        for sub in getattr(getattr(route, "app", None), "routes", []):
            endpoint = getattr(sub, "endpoint", None)
            if endpoint is not None and hasattr(endpoint, "__self__"):
                return endpoint.__self__
    raise AssertionError("sqladmin's Admin instance was not reachable")


def _menu_link(identity: str) -> str:
    """An anchored pattern for one sidebar entry's own link.

    `menu.url(request)` (sqladmin/_menu.py, via `request.url_for`) renders an
    **absolute** URL — `href="http://host/admin/audit-log/list"` — so a bare
    `'href="/admin/audit-log/list"' in html` matches nothing at all and passes
    an absence assertion for the wrong reason. That is not hypothetical: it is
    what this file's first draft did, and both sidebar tests passed the
    "staff sees nothing" half while the administrator half failed, which is
    the exact shape of a test that would have gone on passing after the guard
    was removed again.

    Anchored at both ends: `/admin/` before the identity, and the closing
    double quote of the attribute immediately after `/list`, so neither
    `/admin/audit-log-archive/list` nor `/admin/audit-log/list-something`
    satisfies it. `re.escape` on the identity because `ip-block` and the
    others carry hyphens.
    """
    return rf'href="[^"]*/admin/{re.escape(identity)}/list"'
