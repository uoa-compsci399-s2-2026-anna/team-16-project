"""Contract §8.2: clone, publish and roll back from the factor-set screen.

Two deviations from task-4-brief.md's Step 1 listing, both already made by
tests/admin/test_factor_views.py and test_taxonomy_views.py for the same
reasons - see that file's own module docstring for the fuller account:

1. The end-to-end tests are ``async def`` and ``await`` every
   ``admin_client``/``staff_client`` call. Both are httpx's
   ``AsyncClient`` (the ``client`` fixture in tests/conftest.py); calling
   one of its methods without ``await`` returns an un-awaited coroutine,
   not a response.
2. ``session`` here is a three-line local wrapper around
   tests/admin/conftest.py's hard-committing ``_committed_session``, not
   the rolled-back ``session`` tests/conftest.py defines at the top level -
   ``admin_client``/``staff_client`` drive real HTTP requests that reach
   FactorSetAdmin's actions through the running app's own, separate
   sessionmaker, a different connection entirely.
"""

import re

import pytest
from sqlalchemy import select

from admin.factor_models import FactorSet, FactorSetStatus
from tests.admin.conftest import _add_formula, _resync

pytestmark = pytest.mark.db


@pytest.fixture
def session(_committed_session):
    """This file's own name for tests/admin/conftest.py's hard-committing
    session - see that module's ``_committed_session`` docstring for why the
    shared fixture is not itself called ``session``."""
    return _committed_session


@pytest.mark.asyncio
async def test_cloning_from_the_screen_creates_a_draft(session, admin_client, populated_set):
    session.commit()

    response = await admin_client.get(
        f"/admin/factor-set/action/clone?pks={populated_set.id}"
    )

    _resync(session)
    clone = session.scalar(
        select(FactorSet).where(FactorSet.version_label != populated_set.version_label)
    )
    assert clone is not None
    assert clone.status is FactorSetStatus.draft
    assert response.status_code in (200, 302)


@pytest.mark.asyncio
async def test_publishing_from_the_screen_goes_live(session, admin_client, one_draft):
    session.commit()

    await admin_client.get(f"/admin/factor-set/action/publish?pks={one_draft.id}")

    _resync(session)
    assert session.get(FactorSet, one_draft.id).status is FactorSetStatus.published


@pytest.mark.asyncio
async def test_publishing_a_broken_set_shows_the_reason(session, admin_client, one_draft):
    """A staff member must be able to act on this: the message names the
    formula and what is wrong with it, rather than 500ing."""
    _add_formula(session, one_draft, "qty_kg * const_GONE")
    session.commit()

    response = await admin_client.get(f"/admin/factor-set/action/publish?pks={one_draft.id}")

    _resync(session)
    assert session.get(FactorSet, one_draft.id).status is FactorSetStatus.draft
    assert "const_GONE" in response.text


@pytest.mark.asyncio
async def test_a_plain_staff_member_can_reach_the_actions(session, staff_client, one_draft):
    """Contract §8.3 makes publishing available to both roles, deliberately:
    'audit_log plus one-click rollback already provide accountability and
    recovery, and gating them behind an administrator would stall routine
    work in a three-to-five person team.' So the check inside each action
    below is is_accessible, which a plain staff member passes, not an
    admin-only guard - unlike StaffAdmin's own actions in
    admin/accounts_view.py, which are administrator-only by design.

    The test earns its place even though rollback is refused: sqladmin
    wraps @action routes in login_required only, never is_accessible - the
    URL is not screened just because the menu entry and the list page are
    (admin/accounts_view.py's own module docstring). Without an explicit
    is_accessible check inside each action method, a plain staff session
    reaching this URL would be an accident of sqladmin's own routing, not
    something this project decided.

    ``one_draft`` is a draft, not archived, so rollback_to correctly refuses
    it with a LifecycleError (rendered as a 400, the same status
    admin/accounts_view.py's own deactivate_action uses for a refusal) -
    that is a business-rule outcome, not a permission one, and proves the
    route was reached and ran real logic rather than being screened before
    it got there. 403 (is_accessible failing) and 404 (the route missing
    entirely) are the two outcomes that would mean the route was not
    actually reachable; neither happens here for any of the four slugs.

    The four run in this order against the one draft fixture provides -
    clone, publish, rollback, archive - so each of the later three sees the
    state the one before it actually left behind (clone's own new row is
    never touched again): after publish, ``one_draft`` is published, so
    rollback (which requires an *archived* target) is correctly refused
    (400) and archive (which accepts a published target - see
    test_factor_lifecycle.py's own test_archiving_the_published_set_is_allowed)
    goes through (302).
    """
    session.commit()

    for slug in ("clone", "publish", "rollback", "archive"):
        response = await staff_client.get(
            f"/admin/factor-set/action/{slug}?pks={one_draft.id}"
        )
        assert response.status_code in (200, 302, 400), slug


@pytest.mark.asyncio
async def test_rolling_back_from_the_screen_restores_the_archived_set(
    session, admin_client, two_sets
):
    live, draft = two_sets
    session.commit()
    await admin_client.get(f"/admin/factor-set/action/publish?pks={draft.id}")
    _resync(session)

    await admin_client.get(f"/admin/factor-set/action/rollback?pks={live.id}")

    _resync(session)
    assert session.get(FactorSet, live.id).status is FactorSetStatus.published


@pytest.mark.asyncio
async def test_the_clone_confirmation_names_the_new_version(
    session, admin_client, populated_set
):
    """The staff member has to find the draft they just made, and the label
    is generated rather than typed."""
    session.commit()

    response = await admin_client.get(
        f"/admin/factor-set/action/clone?pks={populated_set.id}"
    )

    _resync(session)
    clone = session.scalar(
        select(FactorSet).where(FactorSet.version_label != populated_set.version_label)
    )
    assert clone.version_label in response.text


@pytest.mark.asyncio
async def test_the_clone_confirmation_points_at_the_comparison_view(
    session, admin_client, populated_set
):
    """The pre-publish comparison view (§8.2, admin/dryrun_views.py's
    CompareView) is the natural next stop after cloning a draft to edit -
    the page that tells staff what to do next should close that loop
    rather than leave them to find /admin/factor-sets/{id}/compare on
    their own."""
    session.commit()

    response = await admin_client.get(
        f"/admin/factor-set/action/clone?pks={populated_set.id}"
    )

    assert "compar" in response.text.lower()


@pytest.mark.asyncio
async def test_selecting_more_than_one_set_is_refused(
    session, admin_client, populated_set, one_draft
):
    """sqladmin's own bulk **Actions** dropdown (templates/sqladmin/list.html)
    ties these three actions to every ticked checkbox
    (statics/js/main.js builds ?pks= from all of them), not just the
    row-level action - so more than one id in ?pks= is a real, reachable
    request. Publishing two ticked drafts must not silently publish the
    first and leave the second untouched with no indication anything was
    skipped; both stay exactly as they were and the response names the
    problem.
    """
    session.commit()

    response = await admin_client.get(
        f"/admin/factor-set/action/publish?pks={populated_set.id},{one_draft.id}"
    )

    _resync(session)
    assert session.get(FactorSet, populated_set.id).status is FactorSetStatus.draft
    assert session.get(FactorSet, one_draft.id).status is FactorSetStatus.draft
    assert response.status_code == 400
    assert "one factor set at a time" in response.text.lower()


@pytest.mark.asyncio
async def test_a_refused_factor_set_action_does_not_explain_the_administrator_floor(
    session, admin_client
):
    """brand/action_refused.html is shared with admin/accounts_view.py's
    deactivate_action, which hardcoded a paragraph about the two-
    administrator floor and a 'Back to accounts' link. Every render of that
    template inherited both, including this one - a staff member who clicks
    Publish with nothing selected was told about a feature they were not
    using at all.

    Clicking Publish with no row ticked is the exact scenario the project
    owner found by hand: ``_one_pk`` (admin/factor_views.py) refuses with
    'No factor set was selected.' before any session is even opened, so this
    needs no fixture beyond the client.
    """
    response = await admin_client.get("/admin/factor-set/action/publish")

    assert response.status_code == 400
    assert "no factor set was selected" in response.text.lower()
    assert "lost phone" not in response.text.lower()
    assert "two active administrators" not in response.text.lower()
    assert "back to accounts" not in response.text.lower()


@pytest.mark.asyncio
async def test_a_non_integer_pk_is_refused_not_500(session, admin_client, one_draft):
    """?pks=abc is only reachable by a hand-typed URL, but must not raise
    ValueError out of the handler - the same unhandled-500 shape the
    LifecycleError catch around every action exists to prevent."""
    session.commit()

    response = await admin_client.get("/admin/factor-set/action/publish?pks=abc")

    assert response.status_code == 400

    _resync(session)
    assert session.get(FactorSet, one_draft.id).status is FactorSetStatus.draft


@pytest.mark.asyncio
async def test_cloning_twice_generates_a_unique_suffix(
    session, admin_client, populated_set
):
    """_unique_clone_label is the only novel algorithm this task added;
    nothing else pins its collision handling."""
    session.commit()

    await admin_client.get(f"/admin/factor-set/action/clone?pks={populated_set.id}")
    _resync(session)
    await admin_client.get(f"/admin/factor-set/action/clone?pks={populated_set.id}")
    _resync(session)

    labels = set(session.scalars(
        select(FactorSet.version_label).where(
            FactorSet.version_label.like(f"{populated_set.version_label} (copy)%")
        )
    ).all())
    assert f"{populated_set.version_label} (copy)" in labels
    assert f"{populated_set.version_label} (copy) 2" in labels


# --- Fix wave: archive -------------------------------------------------
#
# The only route left to take the calculator offline once `status` came off
# the edit form - see admin/factor_lifecycle.py's own archive_factor_set
# docstring.


@pytest.mark.asyncio
async def test_archiving_from_the_screen_marks_it_archived(session, admin_client, one_draft):
    session.commit()

    await admin_client.get(f"/admin/factor-set/action/archive?pks={one_draft.id}")

    _resync(session)
    assert session.get(FactorSet, one_draft.id).status is FactorSetStatus.archived


@pytest.mark.asyncio
async def test_archiving_the_published_set_takes_it_offline(session, admin_client, two_sets):
    """The whole point: a staff member who finds an error in the live
    factors must be able to pull them even when nothing else is ready to
    publish in their place."""
    live, _ = two_sets
    session.commit()

    await admin_client.get(f"/admin/factor-set/action/archive?pks={live.id}")

    _resync(session)
    assert session.get(FactorSet, live.id).status is FactorSetStatus.archived


@pytest.mark.asyncio
async def test_archiving_an_already_archived_set_shows_the_reason(
    session, admin_client, one_draft
):
    session.commit()
    await admin_client.get(f"/admin/factor-set/action/archive?pks={one_draft.id}")
    _resync(session)

    response = await admin_client.get(
        f"/admin/factor-set/action/archive?pks={one_draft.id}"
    )

    assert response.status_code == 400
    assert "already archived" in response.text.lower()


# --- Fix wave: compare ---------------------------------------------------


@pytest.mark.asyncio
async def test_the_compare_action_redirects_to_the_compare_page(
    session, admin_client, one_draft
):
    """Regression test for a route-name bug: `compare_action` first called
    `request.url_for("view-compare", ...)`, the bare name sqladmin
    registers `CompareView.compare` under. `Request.url_for` only sets
    `scope["router"]` when the scope does not already carry one, and by
    the time this action runs, the scope already carries the *outer*
    FastAPI router - the same reason `_list_url` above resolves
    `"admin:list"`, never the bare `"list"`. The unprefixed name is only
    ever visible to `app.url_path_for` called directly on the inner
    sqladmin app; from inside a request handler it raises
    `starlette.routing.NoMatchFound`, an unhandled 500 no LifecycleError
    catch around this action can reach. Fixed to `"admin:view-compare"`.
    """
    session.commit()

    response = await admin_client.get(
        f"/admin/factor-set/action/compare?pks={one_draft.id}"
    )

    assert response.status_code == 302
    assert response.headers["location"].endswith(
        f"/admin/factor-sets/{one_draft.id}/compare"
    )


# --- The placeholder-data flag ------------------------------------------
#
# Contract §2.2. The service function's own rules are in
# tests/admin/test_factor_lifecycle.py; what these cover is the half only a
# request has: which of the two directions asks for proof, and what happens
# when it is not given.


def _csrf_of(page):
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match is not None, "no CSRF token on the clear-placeholder page"
    return match.group(1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [FactorSetStatus.draft, FactorSetStatus.published, FactorSetStatus.archived],
)
async def test_flagging_as_placeholder_needs_no_proof_in_any_status(
    session, admin_client, one_draft, status
):
    """The safe direction, and the one that must stay frictionless: a staff
    member who doubts the live numbers has to be able to put the warning in
    front of the public in one press, without cloning or publishing anything
    and without finding their authenticator.

    One GET, the way sqladmin's own action dropdown issues it. If this ever
    needs a POST, a form or a proof, this test fails - which is the point.
    """
    one_draft.status = status
    one_draft.is_mock = False
    session.commit()

    response = await admin_client.get(
        f"/admin/factor-set/action/flag-placeholder?pks={one_draft.id}"
    )

    assert response.status_code in (200, 302)
    _resync(session)
    assert session.get(FactorSet, one_draft.id).is_mock is True


@pytest.mark.asyncio
async def test_clearing_the_flag_with_proof_succeeds(session, admin_client, one_draft):
    one_draft.status = FactorSetStatus.published
    session.commit()

    page = await admin_client.get(
        f"/admin/factor-set/clear-placeholder?pks={one_draft.id}"
    )
    response = await admin_client.post("/admin/factor-set/clear-placeholder", data={
        "pks": str(one_draft.id),
        "csrf_token": _csrf_of(page),
        "current_password": admin_client.password,
    })

    assert response.status_code == 302
    _resync(session)
    assert session.get(FactorSet, one_draft.id).is_mock is False


@pytest.mark.asyncio
async def test_clearing_the_flag_without_proof_is_refused(
    session, admin_client, one_draft
):
    """The whole gate. A stolen session carries everything this request
    carries except the password, so an empty proof field has to be the one
    thing that stops it - and the flag has to still be set afterwards, not
    merely the response be a 400."""
    one_draft.status = FactorSetStatus.published
    session.commit()

    page = await admin_client.get(
        f"/admin/factor-set/clear-placeholder?pks={one_draft.id}"
    )
    response = await admin_client.post("/admin/factor-set/clear-placeholder", data={
        "pks": str(one_draft.id),
        "csrf_token": _csrf_of(page),
    })

    assert response.status_code == 400
    _resync(session)
    assert session.get(FactorSet, one_draft.id).is_mock is True


@pytest.mark.asyncio
async def test_clearing_the_flag_with_the_wrong_password_is_refused(
    session, admin_client, one_draft
):
    session.commit()

    page = await admin_client.get(
        f"/admin/factor-set/clear-placeholder?pks={one_draft.id}"
    )
    response = await admin_client.post("/admin/factor-set/clear-placeholder", data={
        "pks": str(one_draft.id),
        "csrf_token": _csrf_of(page),
        "current_password": "not-the-password",
    })

    assert response.status_code == 400
    _resync(session)
    assert session.get(FactorSet, one_draft.id).is_mock is True


@pytest.mark.asyncio
async def test_clearing_the_flag_without_a_csrf_token_is_refused(
    session, admin_client, one_draft
):
    session.commit()

    response = await admin_client.post("/admin/factor-set/clear-placeholder", data={
        "pks": str(one_draft.id),
        "current_password": admin_client.password,
    })

    assert response.status_code == 400
    _resync(session)
    assert session.get(FactorSet, one_draft.id).is_mock is True


@pytest.mark.asyncio
async def test_clearing_the_flag_writes_the_audit_entry_with_both_values(
    session, admin_client, one_draft
):
    """Who, when, which set, which value to which - and under an action of
    its own, so that the one change that removes a public disclaimer is not
    filed in the trail as an `update` beside somebody fixing a typo."""
    from admin.models import AuditLog

    session.commit()

    page = await admin_client.get(
        f"/admin/factor-set/clear-placeholder?pks={one_draft.id}"
    )
    await admin_client.post("/admin/factor-set/clear-placeholder", data={
        "pks": str(one_draft.id),
        "csrf_token": _csrf_of(page),
        "current_password": admin_client.password,
    })

    _resync(session)
    entry = session.scalar(
        select(AuditLog).where(AuditLog.table_name == "factor_set",
                               AuditLog.row_id == one_draft.id,
                               AuditLog.action == "clear_placeholder")
    )
    assert entry is not None
    assert entry.actor == admin_client.staff.username
    assert entry.before_json["is_mock"] is True
    assert entry.after_json["is_mock"] is False


@pytest.mark.asyncio
async def test_a_refused_clearing_writes_no_audit_entry(
    session, admin_client, one_draft
):
    """A trail that recorded attempts as changes would say the warning came
    off when it did not."""
    from admin.models import AuditLog

    session.commit()

    page = await admin_client.get(
        f"/admin/factor-set/clear-placeholder?pks={one_draft.id}"
    )
    await admin_client.post("/admin/factor-set/clear-placeholder", data={
        "pks": str(one_draft.id),
        "csrf_token": _csrf_of(page),
        "current_password": "not-the-password",
    })

    _resync(session)
    assert session.scalar(
        select(AuditLog).where(AuditLog.table_name == "factor_set",
                               AuditLog.row_id == one_draft.id,
                               AuditLog.action == "clear_placeholder")
    ) is None


@pytest.mark.asyncio
async def test_the_clear_action_carries_the_selection_to_the_proof_page(
    session, admin_client, one_draft
):
    """sqladmin registers an @action with methods=["GET"] only, so the
    dropdown entry cannot itself take a proof. It redirects instead, and the
    selection has to survive the redirect or the page has nothing to act
    on."""
    session.commit()

    response = await admin_client.get(
        f"/admin/factor-set/action/clear-placeholder?pks={one_draft.id}"
    )

    assert response.status_code == 302
    assert response.headers["location"].endswith(
        f"/admin/factor-set/clear-placeholder?pks={one_draft.id}"
    )
    _resync(session)
    assert session.get(FactorSet, one_draft.id).is_mock is True


@pytest.mark.asyncio
async def test_the_proof_page_states_what_clearing_the_flag_costs(
    session, admin_client, one_draft
):
    """The confirmation has to name the consequence, not just ask for a
    password: this is the only control in the panel that removes a disclaimer
    from a page the public is reading."""
    one_draft.status = FactorSetStatus.published
    session.commit()

    page = await admin_client.get(
        f"/admin/factor-set/clear-placeholder?pks={one_draft.id}"
    )
    body = page.text.lower()

    assert "warning" in body
    assert "export" in body
    assert "immediately" in body


@pytest.mark.asyncio
async def test_a_plain_staff_member_can_reach_the_placeholder_actions(
    session, staff_client, one_draft
):
    """Contract §8.3 keeps publishing available to both roles. Publishing a
    whole set of numbers is the larger act, so gating the flag behind an
    administrator while leaving that to any staff member would be the wrong
    way round. 403 or 404 here would mean the route is not actually
    reachable; neither is an outcome this panel decided."""
    session.commit()

    for url in (
        f"/admin/factor-set/action/flag-placeholder?pks={one_draft.id}",
        f"/admin/factor-set/action/clear-placeholder?pks={one_draft.id}",
        f"/admin/factor-set/clear-placeholder?pks={one_draft.id}",
    ):
        response = await staff_client.get(url)
        assert response.status_code in (200, 302, 400), url


@pytest.mark.asyncio
async def test_selecting_more_than_one_set_to_clear_is_refused(
    session, admin_client, populated_set, one_draft
):
    """Same rule as the four lifecycle actions, and it has to hold on the
    proof page too: a page that showed one set's name and cleared two would
    be worse than one that refused."""
    session.commit()

    response = await admin_client.get(
        f"/admin/factor-set/clear-placeholder?pks={populated_set.id},{one_draft.id}"
    )

    assert response.status_code == 400
    assert "one factor set at a time" in response.text.lower()
    _resync(session)
    assert session.get(FactorSet, populated_set.id).is_mock is True
    assert session.get(FactorSet, one_draft.id).is_mock is True


@pytest.mark.asyncio
async def test_the_proof_page_says_so_when_there_is_nothing_to_clear(
    session, admin_client, one_draft
):
    """Reachable from the bulk Actions dropdown against any row, including one
    that is already unflagged. A proof dialog for a change that would be
    refused after the password was typed is worse than a sentence."""
    one_draft.is_mock = False
    session.commit()

    page = await admin_client.get(
        f"/admin/factor-set/clear-placeholder?pks={one_draft.id}"
    )

    assert page.status_code == 200
    assert "nothing here to clear" in page.text.lower()
    assert 'name="current_password"' not in page.text


@pytest.mark.asyncio
async def test_the_proof_page_refuses_a_set_that_does_not_exist(admin_client):
    """Only reachable by a hand-typed URL, and it must not 500 - the same
    shape the LifecycleError catch around every action exists to prevent."""
    response = await admin_client.get("/admin/factor-set/clear-placeholder?pks=999999")

    assert response.status_code == 400
    assert "999999" in response.text


# --- Importing the published set into a draft -------------------------------
#
# The fix for a draft a staff member has edited and now regrets - see
# admin/factor_lifecycle.py's import_published_into for the operation itself.
# tests/admin/test_factor_lifecycle.py covers the service function; what these
# cover is the half only a request has: the redirect-to-confirmation shape,
# the counts the confirmation page names, and what the two "nothing to
# import" states look like over HTTP.


def _import_csrf_of(page):
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match is not None, "no CSRF token on the import-published page"
    return match.group(1)


@pytest.mark.asyncio
async def test_the_import_action_carries_the_selection_to_the_confirmation_page(
    session, admin_client, one_draft
):
    """sqladmin registers an @action with methods=["GET"] only, so the
    dropdown entry cannot itself take a POST - it redirects instead, the
    same shape test_the_clear_action_carries_the_selection_to_the_proof_page
    proves for clear-placeholder."""
    session.commit()

    response = await admin_client.get(
        f"/admin/factor-set/action/import-published?pks={one_draft.id}"
    )

    assert response.status_code == 302
    assert response.headers["location"].endswith(
        f"/admin/factor-set/import-published?pks={one_draft.id}"
    )


@pytest.mark.asyncio
async def test_the_confirmation_page_names_the_counts(session, admin_client, two_sets):
    """The task's own bar: not "are you sure?", but the actual numbers this
    draft is about to lose - read back from count_child_rows so this test
    breaks if the wording and the real counts ever drift apart, not just if
    the counts happen to look plausible."""
    from admin.factor_lifecycle import count_child_rows

    live, draft = two_sets
    session.commit()

    page = await admin_client.get(f"/admin/factor-set/import-published?pks={draft.id}")

    assert page.status_code == 200
    counts = count_child_rows(session, draft.id)
    expected = (
        f"This discards this draft's {counts['factor_upstream']} upstream "
        f"factors, {counts['factor_downstream']} downstream factors, "
        f"{counts['constant']} constants, {counts['formula']} formulas and "
        f"{counts['equivalence']} equivalences, and replaces them with the "
        "published set's."
    )
    assert expected in page.text


@pytest.mark.asyncio
async def test_the_confirmation_page_says_so_for_a_non_draft_target(
    session, admin_client, two_sets
):
    """A published (or archived) target has nothing this page can safely
    offer to overwrite - the same "say so in words" treatment
    factor_set_clear_placeholder.html gives its own already-correct state,
    rather than a hard refusal for a state reachable from the ordinary
    Actions dropdown."""
    live, _ = two_sets
    session.commit()

    page = await admin_client.get(f"/admin/factor-set/import-published?pks={live.id}")

    assert page.status_code == 200
    assert "not draft" in page.text.lower()
    assert 'name="csrf_token"' not in page.text
    assert "import and discard" not in page.text.lower()


@pytest.mark.asyncio
async def test_the_confirmation_page_says_so_when_nothing_is_published(
    session, admin_client, one_draft
):
    session.commit()

    page = await admin_client.get(f"/admin/factor-set/import-published?pks={one_draft.id}")

    assert page.status_code == 200
    assert "no factor set is currently published" in page.text.lower()
    assert 'name="csrf_token"' not in page.text


@pytest.mark.asyncio
async def test_posting_the_import_replaces_the_draft(session, admin_client, two_sets):
    live, draft = two_sets
    live.is_mock = False
    draft.is_mock = True
    session.commit()

    page = await admin_client.get(f"/admin/factor-set/import-published?pks={draft.id}")
    response = await admin_client.post("/admin/factor-set/import-published", data={
        "pks": str(draft.id),
        "csrf_token": _import_csrf_of(page),
    })

    assert response.status_code == 302
    _resync(session)
    assert session.get(FactorSet, draft.id).is_mock is False


@pytest.mark.asyncio
async def test_posting_without_a_csrf_token_is_refused(session, admin_client, two_sets):
    live, draft = two_sets
    live.is_mock = False
    draft.is_mock = True
    session.commit()

    response = await admin_client.post("/admin/factor-set/import-published", data={
        "pks": str(draft.id),
    })

    assert response.status_code == 400
    _resync(session)
    assert session.get(FactorSet, draft.id).is_mock is True


@pytest.mark.asyncio
async def test_selecting_more_than_one_set_to_import_is_refused(
    session, admin_client, populated_set, one_draft
):
    session.commit()

    response = await admin_client.get(
        f"/admin/factor-set/import-published?pks={populated_set.id},{one_draft.id}"
    )

    assert response.status_code == 400
    assert "one factor set at a time" in response.text.lower()


@pytest.mark.asyncio
async def test_a_plain_staff_member_is_refused_the_import_routes(
    session, staff_client, two_sets
):
    """Unlike clone/publish/rollback/archive/the placeholder flag - all
    both-roles by §8.3 decision 4 - import discards a draft's own data with
    no undo, the same shape as StaffAdmin's administrator-only `delete`.
    tests/admin/test_role_matrix.py carries the same two routes in
    `_ADMIN_ONLY` and drives this same refusal generically; this copy is
    the bespoke, in-context version the task asked for, driven through the
    real app exactly like every other role test in this project - not an
    assertion that a decorator is present.
    """
    live, draft = two_sets
    session.commit()

    for url in (
        f"/admin/factor-set/action/import-published?pks={draft.id}",
        f"/admin/factor-set/import-published?pks={draft.id}",
    ):
        response = await staff_client.get(url, follow_redirects=False)
        assert response.status_code == 403, url


@pytest.mark.asyncio
async def test_an_administrator_can_still_reach_the_import_routes(
    session, admin_client, two_sets
):
    """The other half - a test that only proves refusal would also pass
    against a route that refuses everyone, which would be a worse defect
    than the one this whole exchange started from."""
    live, draft = two_sets
    session.commit()

    for url in (
        f"/admin/factor-set/action/import-published?pks={draft.id}",
        f"/admin/factor-set/import-published?pks={draft.id}",
    ):
        response = await admin_client.get(url, follow_redirects=False)
        assert response.status_code != 403, url


@pytest.mark.asyncio
async def test_the_import_writes_an_audit_entry_naming_the_actor(
    session, admin_client, two_sets
):
    from admin.models import AuditLog

    live, draft = two_sets
    session.commit()

    page = await admin_client.get(f"/admin/factor-set/import-published?pks={draft.id}")
    await admin_client.post("/admin/factor-set/import-published", data={
        "pks": str(draft.id),
        "csrf_token": _import_csrf_of(page),
    })

    _resync(session)
    entry = session.scalar(
        select(AuditLog).where(AuditLog.table_name == "factor_set",
                               AuditLog.row_id == draft.id,
                               AuditLog.action == "import_published")
    )
    assert entry is not None
    assert entry.actor == admin_client.staff.username


# --- The published-set panel above the list ---------------------------------
#
# Contract: shows what the calculator is currently running on above the
# ordinary factor-sets table, and says so in words when nothing is published.


@pytest.mark.asyncio
async def test_the_list_page_shows_the_published_set(session, admin_client, two_sets):
    """Checked against wording the panel alone produces, not merely against
    `live.version_label`/"kim" appearing anywhere on the page - sqladmin's
    own table already shows every factor_set's version_label and
    published_by as plain columns, so those two strings are on the page
    (inside the ordinary table) whether or not the published-set panel
    renders anything at all. "Currently published" and the "Published ... by
    ..." sentence are this panel's own wording and appear nowhere else.
    """
    from admin.models import utcnow

    live, _draft = two_sets
    live.published_at = utcnow()
    live.published_by = "kim"
    session.commit()

    response = await admin_client.get("/admin/factor-set/list")

    assert response.status_code == 200
    assert "Currently published" in response.text
    assert f"Published {live.published_at.strftime('%d %b %Y, %H:%M')} by kim." in response.text


@pytest.mark.asyncio
async def test_the_list_page_says_so_when_nothing_is_published(
    session, admin_client, one_draft
):
    session.commit()

    response = await admin_client.get("/admin/factor-set/list")

    assert response.status_code == 200
    assert "no factor set is currently published" in response.text.lower()
