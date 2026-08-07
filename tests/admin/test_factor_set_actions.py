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
    actually reachable; neither happens here for any of the three slugs.
    """
    session.commit()

    for slug in ("clone", "publish", "rollback"):
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
