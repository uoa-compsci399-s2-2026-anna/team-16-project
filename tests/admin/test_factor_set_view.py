"""Contract §2.2 and §8.2: the factor-set screen.

Deviates from task-4-brief.md's Step 1 listing the same way
tests/admin/test_factor_views.py and tests/admin/test_taxonomy_views.py
deviate from their own briefs, and for the same reasons - see those files'
module docstrings for the full account:

1. The end-to-end tests are ``async def`` and ``await`` every
   ``admin_client`` call. ``admin_client`` is httpx's ``AsyncClient`` (the
   ``client`` fixture in tests/conftest.py); calling one of its methods
   without ``await`` returns an un-awaited coroutine, not a response, and the
   response text asserted against below would never have been sent.
2. ``session`` here wraps the shared, hard-committing
   ``_committed_session`` tests/admin/conftest.py defines, not the
   rolled-back ``session`` tests/conftest.py defines at the top level (see
   ``_committed_session``'s own docstring for why the shared fixture is not
   itself called ``session``). ``admin_client`` drives real HTTP requests
   that reach the factor-set view through the running app's own, separate
   sessionmaker - a different connection entirely - so a test that seeds a
   row via the rolled-back fixture and then expects ``admin_client`` to see
   it would fail for a reason that has nothing to do with the invariant
   under test.
"""

import pytest
from sqlalchemy import bindparam as sa_bindparam
from sqlalchemy import select, text

from admin.factor_models import FactorSet, FactorSetStatus
from admin.factor_views import FactorSetAdmin
from tests.admin.conftest import _resync

pytestmark = pytest.mark.db


@pytest.fixture
def session(_committed_session):
    """This file's own name for tests/admin/conftest.py's hard-committing
    session - see this module's own docstring."""
    return _committed_session


# --- Metadata-only test: no database access, no client ---------------------


def test_a_factor_set_cannot_be_deleted_from_the_panel():
    """Every submission stamps the set it was calculated against, so results
    stay reproducible. Deleting a set would strand every result that names
    it. Archiving is how a version leaves service."""
    assert FactorSetAdmin.can_delete is False


# --- Fixtures ---------------------------------------------------------
#
# admin_client, staff_client, session and _resync now come from
# tests/admin/conftest.py. This file keeps only what names its own fixed
# labels: the cleanup helper below and the autouse fixture that runs it,
# since tests/admin/conftest.py's own `session` fixture cannot know what a
# given file's tests committed through it.


_FACTOR_SET_LABELS = ["kc-factor-set-view-test-live", "kc-factor-set-view-test-next",
                     "kc-factor-set-view-test-first"]


def _cleanup_factor_set_rows(admin_app):
    """Remove every row this file's tests may have hard-committed via the
    shared `session` fixture, by the fixed set of labels those tests are
    written against."""
    factory = admin_app.state.session_factory
    with factory() as db:
        factor_set_ids = db.execute(
            text("SELECT id FROM factor_set WHERE version_label IN :labels")
            .bindparams(sa_bindparam("labels", expanding=True)),
            {"labels": _FACTOR_SET_LABELS},
        ).scalars().all()

        if factor_set_ids:
            db.execute(
                text("DELETE FROM audit_log WHERE table_name = 'factor_set' "
                     "AND row_id IN :ids").bindparams(sa_bindparam("ids", expanding=True)),
                {"ids": factor_set_ids},
            )
            db.execute(
                text("DELETE FROM factor_set WHERE id IN :ids")
                .bindparams(sa_bindparam("ids", expanding=True)),
                {"ids": factor_set_ids},
            )
        db.commit()


@pytest.fixture(autouse=True)
def _cleanup_this_files_rows(admin_app):
    """Runs after every test in this file, regardless of whether it used
    `session` - matching the unconditional (`finally`) cleanup the old
    file-local `session` fixture used to perform itself."""
    yield
    _cleanup_factor_set_rows(admin_app)


# --- End-to-end tests: FactorSetAdmin's real behaviour ----------------------


@pytest.mark.asyncio
async def test_publishing_a_second_set_through_the_edit_form_is_refused(
    session, admin_client
):
    """§2.2's invariant, on the path that bypasses every service function.

    The edit form reaches `status` directly through sqladmin's
    Query.update -> setattr -> commit, which is exactly how an earlier stage
    of this project shipped an administrator-floor guard that a checkbox
    walked straight past.
    """
    live = FactorSet(version_label=_FACTOR_SET_LABELS[0], status=FactorSetStatus.published,
                     is_mock=False)
    draft = FactorSet(version_label=_FACTOR_SET_LABELS[1], status=FactorSetStatus.draft,
                      is_mock=True)
    session.add_all([live, draft])
    session.commit()

    response = await admin_client.post(f"/admin/factor-set/edit/{draft.id}", data={
        "version_label": _FACTOR_SET_LABELS[1], "status": "published", "is_mock": "on",
        "notes": "",
    })

    _resync(session)
    assert session.scalar(
        select(FactorSet).where(FactorSet.version_label == _FACTOR_SET_LABELS[1])
    ).status is FactorSetStatus.draft
    # Not "published" in response.text.lower(): that word also appears in
    # the re-rendered status <select>'s own option list, so it passes
    # against a raw traceback just as easily as against a real refusal.
    # sqladmin's edit route (see admin/taxonomy_views.py's module docstring
    # for the confirmed mechanism) wraps update_model in a bare
    # `except Exception`, sets `context["error"] = str(e)` and re-renders
    # with a 400 - checking the status code and the invariant's own message
    # is what actually proves this was refused for the right reason.
    assert response.status_code == 400
    assert "Archive all but one" in response.text


@pytest.mark.asyncio
async def test_publishing_when_nothing_is_published_goes_through(session, admin_client):
    """The invariant refuses the second published set and nothing else. A
    guard that refused every status change would pass the test above."""
    draft = FactorSet(version_label=_FACTOR_SET_LABELS[2], status=FactorSetStatus.draft,
                      is_mock=True)
    session.add(draft)
    session.commit()

    await admin_client.post(f"/admin/factor-set/edit/{draft.id}", data={
        "version_label": _FACTOR_SET_LABELS[2], "status": "published", "is_mock": "on",
        "notes": "",
    })

    _resync(session)
    assert session.scalar(
        select(FactorSet).where(FactorSet.version_label == _FACTOR_SET_LABELS[2])
    ).status is FactorSetStatus.published
