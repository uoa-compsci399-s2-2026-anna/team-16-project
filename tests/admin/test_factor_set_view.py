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
    the request, so this POST is expected to succeed - asserting only that
    the stored status did not move is not enough on its own, though: a 404,
    a rejected CSRF token, or a WTForms validation failure on some other
    omitted field would each also leave `status` at `draft` and pass this
    test even if the edit form had stopped working entirely. Posting
    `notes="x"` alongside the ignored `status` field and asserting both that
    the response is a 302 *and* that `notes` actually landed proves the edit
    itself went through - the real claim being tested is that `status` alone
    was ignored, not that nothing was.
    """
    draft = FactorSet(version_label=_FACTOR_SET_LABELS[2], status=FactorSetStatus.draft,
                      is_mock=True)
    session.add(draft)
    session.commit()

    response = await admin_client.post(f"/admin/factor-set/edit/{draft.id}", data={
        "version_label": _FACTOR_SET_LABELS[2], "status": "published", "is_mock": "on",
        "notes": "x",
    })

    assert response.status_code == 302
    _resync(session)
    updated = session.scalar(
        select(FactorSet).where(FactorSet.version_label == _FACTOR_SET_LABELS[2])
    )
    assert updated.status is FactorSetStatus.draft
    assert updated.notes == "x"


@pytest.mark.asyncio
async def test_editing_a_drafts_notes_still_works(session, admin_client):
    """The guard added below (test_editing_a_published_sets_is_mock_is_refused)
    must refuse only a non-draft set's own fields, not every edit - a
    version that refused every FactorSetAdmin edit would pass that test too
    and make drafts uneditable, which is the opposite of the intent."""
    draft = FactorSet(version_label=_FACTOR_SET_LABELS[2], status=FactorSetStatus.draft,
                      is_mock=True)
    session.add(draft)
    session.commit()

    response = await admin_client.post(f"/admin/factor-set/edit/{draft.id}", data={
        "version_label": _FACTOR_SET_LABELS[2], "is_mock": "on", "notes": "edited",
    })

    assert response.status_code == 302
    _resync(session)
    assert session.scalar(
        select(FactorSet).where(FactorSet.version_label == _FACTOR_SET_LABELS[2])
    ).notes == "edited"


@pytest.mark.asyncio
async def test_editing_a_published_sets_own_fields_is_still_refused(session, admin_client):
    """The half of `validate_before_commit` that did not change when the
    placeholder flag came off this form.

    A published set's own fields must not change in place: every stored
    result names that version, and editing it does not correct those results,
    it quietly changes what they claimed. `notes` is the field under test
    because it is the one a staff member has a genuine reason to want to
    touch on a live set.

    **This test used to submit no field change at all.** It posted
    `version_label` unchanged and `notes=""` against a set whose notes were
    already NULL, and passed - because the *omitted* `is_mock` checkbox was
    itself the change that made the row dirty (HTML omits an unticked box
    rather than sending "off"). Taking `is_mock` off `form_columns` made that
    POST a no-op, `_write_audit_entries` returns before calling
    `validate_before_commit` when nothing is dirty, and the assertion went
    from proving the guard to proving that nothing happened. It now changes a
    field the form still carries, which is what it always meant to.
    """
    live = FactorSet(version_label=_FACTOR_SET_LABELS[0], status=FactorSetStatus.published,
                     is_mock=True, notes="as published")
    session.add(live)
    session.commit()

    response = await admin_client.post(f"/admin/factor-set/edit/{live.id}", data={
        "version_label": _FACTOR_SET_LABELS[0], "notes": "edited after publishing",
    })

    assert response.status_code == 400
    _resync(session)
    assert session.scalar(
        select(FactorSet).where(FactorSet.version_label == _FACTOR_SET_LABELS[0])
    ).notes == "as published"


@pytest.mark.asyncio
@pytest.mark.parametrize("started_mock, submitted", [(True, {}), (False, {"is_mock": "on"})])
async def test_the_edit_form_cannot_move_the_placeholder_flag(
    session, admin_client, started_mock, submitted
):
    """`is_mock` is off `form_columns`, so a submitted value is ignored - the
    same property `status` has had since it came off, and asserted the same
    way (tests above).

    Both directions, because a form that ignored only the dangerous one would
    still be a form: an unticked checkbox arrives as an *absent* field, so
    "clear the flag" and "leave the flag alone" are the identical request and
    the field cannot be trusted in either direction.

    `notes` is posted alongside and asserted, so that a 404, a rejected CSRF
    token or a WTForms failure on some other field - each of which would also
    leave `is_mock` where it was - cannot pass this test. The claim is that
    `is_mock` alone was ignored, not that nothing was.
    """
    draft = FactorSet(version_label=_FACTOR_SET_LABELS[2], status=FactorSetStatus.draft,
                      is_mock=started_mock)
    session.add(draft)
    session.commit()

    response = await admin_client.post(f"/admin/factor-set/edit/{draft.id}", data={
        "version_label": _FACTOR_SET_LABELS[2], "notes": "x", **submitted,
    })

    assert response.status_code == 302
    _resync(session)
    updated = session.scalar(
        select(FactorSet).where(FactorSet.version_label == _FACTOR_SET_LABELS[2])
    )
    assert updated.is_mock is started_mock
    assert updated.notes == "x"


@pytest.mark.asyncio
async def test_a_new_factor_set_is_always_created_flagged_as_placeholder(
    session, admin_client
):
    """"Nothing is published as real data by omission" used to rest on the
    creator leaving a ticked box alone. `is_mock` is off the create form too,
    so a new set takes the column default and there is no way to create one
    unflagged - including by hand-posting the field, which is what this
    submits."""
    response = await admin_client.post("/admin/factor-set/create", data={
        "version_label": _FACTOR_SET_LABELS[2], "notes": "new", "is_mock": "",
    })

    assert response.status_code == 302
    _resync(session)
    assert session.scalar(
        select(FactorSet).where(FactorSet.version_label == _FACTOR_SET_LABELS[2])
    ).is_mock is True
