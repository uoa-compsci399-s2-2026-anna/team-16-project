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
