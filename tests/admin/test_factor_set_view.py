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
async def test_a_status_change_through_the_edit_form_is_ignored(session, admin_client):
    """Task 3 review finding: `status` used to be a plain form field, reached
    directly through sqladmin's generic Query.update -> setattr -> commit,
    with no service function anywhere near it. That let a staff member move
    a draft straight to `published` (or `archived`) from the edit screen,
    getting none of what `admin/factor_lifecycle.py`'s publish_factor_set /
    rollback_to actually guarantee: the `SELECT ... FOR UPDATE` that settles
    two simultaneous publishes, the formula re-validation, the
    published_at/published_by stamps, the publish/archive audit pair - as
    long as no other set happened to be published at that moment, the panel
    raised no objection at all.

    `status` was removed from `FactorSetAdmin.form_columns` to close this.
    sqladmin ignores form fields outside form_columns rather than rejecting
    the request, so this POST is expected to succeed (a 4xx would actually
    indicate a *different* bug) - the only thing this test checks is that
    the stored status did not move, which is why it asserts on the database
    row and not on response.status_code.
    """
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
    ).status is FactorSetStatus.draft
