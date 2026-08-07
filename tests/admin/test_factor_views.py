"""Contract §8.1: the factor screens, audited by inheritance.

Deviates from task-3-brief.md's Step 1 listing the same way
tests/admin/test_taxonomy_views.py deviates from task-3's own brief, and for
the same reasons - see that file's module docstring for the full account:

1. The end-to-end tests are ``async def`` and ``await`` every
   ``admin_client`` call. ``admin_client`` is httpx's ``AsyncClient`` (the
   ``client`` fixture in tests/conftest.py); calling one of its methods
   without ``await`` returns an un-awaited coroutine, not a response.
2. ``session`` here wraps tests/admin/conftest.py's hard-committing
   ``_committed_session``, not the rolled-back one tests/conftest.py
   defines at the top level. ``admin_client`` drives real HTTP requests
   that reach the factor views through the running app's own, separate
   sessionmaker - a different connection entirely - so a test that seeds a
   row via the rolled-back fixture and then expects ``admin_client`` to see
   it would fail for a reason that has nothing to do with the invariant
   under test.

``admin_client``, ``staff_client`` and ``_resync`` come straight from
tests/admin/conftest.py. ``session`` is a three-line local wrapper around
that module's ``_committed_session`` rather than a direct import - see
``_committed_session``'s own docstring for why the hard-committing
implementation is not itself named ``session`` at the conftest level: doing
that shadows every other file in this directory, not just this one. This
file's own ``_cleanup_staff`` used to be the one every other file under
tests/admin/ copied verbatim, because it deletes audit rows by ``actor``
rather than by ``row_id IN (SELECT id FROM <table>)``: the latter cannot
reach a *delete* entry, because the row it describes is precisely the one
that no longer exists. See tests/admin/conftest.py's own copy of that
docstring for the full account.
"""

from decimal import Decimal

import pytest
from sqlalchemy import bindparam as sa_bindparam
from sqlalchemy import select, text

from admin.factor_models import (
    Constant, Equivalence, FactorDownstream, FactorSet, FactorSetStatus,
    FactorUpstream, Formula,
)
from admin.factor_views import (
    ConstantAdmin, EquivalenceAdmin, FactorDownstreamAdmin, FactorUpstreamAdmin,
    FormulaAdmin,
)
from admin.taxonomy_models import Metric
from tests.admin.conftest import _resync

pytestmark = pytest.mark.db


@pytest.fixture
def session(_committed_session):
    """This file's own name for tests/admin/conftest.py's hard-committing
    session - see this module's own docstring and ``_committed_session``'s
    for why the shared fixture is not itself called ``session``."""
    return _committed_session

ALL_VIEWS = [
    FactorUpstreamAdmin, FactorDownstreamAdmin, ConstantAdmin, FormulaAdmin,
    EquivalenceAdmin,
]


# --- Metadata-only tests: no database access, no client -------------------


@pytest.mark.parametrize("view", ALL_VIEWS)
def test_every_factor_view_is_audited(view):
    """Contract §8.1. A subclass that overrode __init__ without calling
    super() would silently lose auditing."""
    from admin.modelviews import AuditedModelView

    assert issubclass(view, AuditedModelView)


@pytest.mark.parametrize("view", ALL_VIEWS)
def test_every_factor_view_can_be_filtered_by_factor_set(view):
    """Several thousand rows spanning several versions. A list that cannot be
    narrowed to one set is a list in which a staff member edits the wrong
    version's number and never notices."""
    parameter_names = {getattr(f, "parameter_name", "") for f in view.column_filters}

    assert any("factor_set" in name for name in parameter_names)


@pytest.mark.parametrize("view", ALL_VIEWS)
def test_the_details_page_shows_no_more_than_the_view_declares(view):
    """column_details_list defaults to every mapped column, which leaked a
    password hash two stages earlier in this project."""
    from sqlalchemy import inspect as sa_inspect

    mapper = sa_inspect(view.model)
    declared = {a.key for a in mapper.column_attrs} | {r.key for r in mapper.relationships}
    shown = {getattr(c, "key", c) for c in view.column_details_list}

    assert shown, f"{view.__name__} leaves column_details_list at its default"
    assert shown <= declared


@pytest.mark.parametrize("view", [FactorUpstreamAdmin, FactorDownstreamAdmin])
def test_the_high_volume_views_page_at_a_workable_size(view):
    """~270 upstream and ~600 downstream rows per set."""
    assert view.page_size >= 50


# --- Fixtures ---------------------------------------------------------
#
# admin_client, staff_client, session and _resync now come from
# tests/admin/conftest.py, which took its copy of the shared plumbing from
# this file - see this file's own module docstring. What stays here is what
# names this file's own fixed labels/codes, plus the `two_sets` fixture's
# own "e6-"/"e6_" labels and codes (tests/admin/conftest.py's own docstring
# on that fixture block), since the published-set guard tests below are the
# first in this file to use it.


_FACTOR_SET_LABELS = ["kc-factor-view-test", "kc-factor-view-test-other"]
_METRIC_CODES = ["kc-factor-view-test-metric"]

_E6_FACTOR_SET_LABELS = ["e6-source", "e6-only-draft", "e6-live", "e6-next",
                        "e6-second-draft", "e6-move-target"]
_E6_TAXONOMY = {
    "destination_group": ["e6_disposal"],
    "destination": ["e6_landfill"],
    "sector": ["e6_processing"],
    "food_category": ["e6_dairy"],
    "metric": ["e6_co2e"],
}


def _cleanup_factor_rows(admin_app):
    """Remove every row this file's tests may have hard-committed via the
    shared `session` fixture, by the fixed set of labels/codes those tests
    are written against. `factor_set_id` cascades (ondelete="CASCADE" on
    FactorUpstream/FactorDownstream/Constant/Formula/Equivalence, see
    admin/factor_models.py) so deleting the factor_set rows themselves is
    enough to take their children with it; the metric row and any audit_log
    entries are cleaned up explicitly.

    Also covers the `two_sets`/`populated_set`/`one_draft` fixtures'
    "e6-"-labelled factor sets and "e6_"-coded taxonomy rows
    (tests/admin/conftest.py): those cascade the same way, but the taxonomy
    rows underneath them (sector, food_category, metric, destination,
    destination_group) do not belong to any factor_set and need their own
    cleanup, run after the factor sets that reference them are gone.
    """
    factory = admin_app.state.session_factory
    with factory() as db:
        factor_set_ids = db.execute(
            text("SELECT id FROM factor_set WHERE version_label IN :labels")
            .bindparams(sa_bindparam("labels", expanding=True)),
            {"labels": _FACTOR_SET_LABELS + _E6_FACTOR_SET_LABELS},
        ).scalars().all()
        metric_ids = db.execute(
            text("SELECT id FROM metric WHERE code IN :codes")
            .bindparams(sa_bindparam("codes", expanding=True)),
            {"codes": _METRIC_CODES},
        ).scalars().all()

        if factor_set_ids:
            for table in ("formula", "constant", "factor_upstream",
                          "factor_downstream", "equivalence"):
                db.execute(
                    text(f"DELETE FROM audit_log WHERE table_name = '{table}' "
                         "AND row_id IN (SELECT id FROM " + table +
                         " WHERE factor_set_id IN :ids)")
                    .bindparams(sa_bindparam("ids", expanding=True)),
                    {"ids": factor_set_ids},
                )
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
        if metric_ids:
            db.execute(
                text("DELETE FROM audit_log WHERE table_name = 'metric' "
                     "AND row_id IN :ids").bindparams(sa_bindparam("ids", expanding=True)),
                {"ids": metric_ids},
            )
            db.execute(
                text("DELETE FROM metric WHERE id IN :ids")
                .bindparams(sa_bindparam("ids", expanding=True)),
                {"ids": metric_ids},
            )

        # e6_extra_<factor_set_id> metrics (tests/admin/conftest.py's
        # _add_formula) have already lost their referencing formula rows via
        # the factor_set cascade above; only the metric rows themselves are
        # left to remove, alongside the rest of the e6_ taxonomy.
        e6_metric_ids = db.execute(
            text("SELECT id FROM metric WHERE code IN :codes "
                 "OR code LIKE 'e6\\_extra\\_%' ESCAPE '\\\\'")
            .bindparams(sa_bindparam("codes", expanding=True)),
            {"codes": _E6_TAXONOMY["metric"]},
        ).scalars().all()
        destination_ids = db.execute(
            text("SELECT id FROM destination WHERE code IN :codes")
            .bindparams(sa_bindparam("codes", expanding=True)),
            {"codes": _E6_TAXONOMY["destination"]},
        ).scalars().all()
        sector_ids = db.execute(
            text("SELECT id FROM sector WHERE code IN :codes")
            .bindparams(sa_bindparam("codes", expanding=True)),
            {"codes": _E6_TAXONOMY["sector"]},
        ).scalars().all()
        food_category_ids = db.execute(
            text("SELECT id FROM food_category WHERE code IN :codes")
            .bindparams(sa_bindparam("codes", expanding=True)),
            {"codes": _E6_TAXONOMY["food_category"]},
        ).scalars().all()
        group_ids = db.execute(
            text("SELECT id FROM destination_group WHERE code IN :codes")
            .bindparams(sa_bindparam("codes", expanding=True)),
            {"codes": _E6_TAXONOMY["destination_group"]},
        ).scalars().all()

        for table, ids in (
            ("metric", e6_metric_ids), ("destination", destination_ids),
            ("sector", sector_ids), ("food_category", food_category_ids),
        ):
            if ids:
                db.execute(
                    text(f"DELETE FROM audit_log WHERE table_name = '{table}' "
                         "AND row_id IN :ids").bindparams(sa_bindparam("ids", expanding=True)),
                    {"ids": ids},
                )
                db.execute(
                    text(f"DELETE FROM {table} WHERE id IN :ids")
                    .bindparams(sa_bindparam("ids", expanding=True)),
                    {"ids": ids},
                )
        # destination_group after destination: the latter carries a foreign
        # key to the former.
        if group_ids:
            db.execute(
                text("DELETE FROM audit_log WHERE table_name = 'destination_group' "
                     "AND row_id IN :ids").bindparams(sa_bindparam("ids", expanding=True)),
                {"ids": group_ids},
            )
            db.execute(
                text("DELETE FROM destination_group WHERE id IN :ids")
                .bindparams(sa_bindparam("ids", expanding=True)),
                {"ids": group_ids},
            )
        db.commit()


@pytest.fixture(autouse=True)
def _cleanup_this_files_rows(admin_app):
    """Runs after every test in this file, regardless of whether it used
    `session` - matching the unconditional (`finally`) cleanup the old
    file-local `session` fixture used to perform itself."""
    yield
    _cleanup_factor_rows(admin_app)


def _seed_set_and_metric(session):
    """A draft factor set and a metric to hang a formula off of."""
    fs = FactorSet(version_label=_FACTOR_SET_LABELS[0], status=FactorSetStatus.draft,
                   is_mock=True)
    session.add(fs)
    metric = Metric(code=_METRIC_CODES[0], name="Test metric", unit="kg")
    session.add(metric)
    session.flush()
    return fs, metric


# --- End-to-end tests: FormulaAdmin's real behaviour -----------------------


@pytest.mark.asyncio
async def test_a_valid_formula_is_accepted(session, admin_client):
    """The whole point of the formula screen: it must still let real work
    through. A validator that refused everything would pass every test that
    only checks refusals."""
    fs, metric = _seed_set_and_metric(session)
    session.commit()

    await admin_client.post("/admin/formula/create", data={
        "factor_set": str(fs.id), "metric": str(metric.id),
        "expression": "qty_kg * (upstream + downstream)", "notes": "",
    })

    _resync(session)
    saved = session.scalar(select(Formula))
    assert saved is not None
    assert saved.expression == "qty_kg * (upstream + downstream)"


@pytest.mark.asyncio
async def test_a_formula_with_an_unknown_variable_is_refused(session, admin_client):
    """Saved broken, this reaches the public as a FORMULA_ERROR 500. The
    typo is the commonest real mistake and reads perfectly well."""
    fs, metric = _seed_set_and_metric(session)
    session.commit()

    response = await admin_client.post("/admin/formula/create", data={
        "factor_set": str(fs.id), "metric": str(metric.id),
        "expression": "qty_kg * upstrem", "notes": "",
    })

    _resync(session)
    assert session.scalar(
        select(Formula).where(Formula.factor_set_id == fs.id)
    ) is None
    assert "upstrem" in response.text


@pytest.mark.asyncio
async def test_a_formula_referring_to_its_own_sets_constant_is_accepted(
    session, admin_client
):
    """The positive half of the cross-set check below. A guard that hard-
    coded `constant_codes=[]` would still pass every refusal test in this
    file - neither refusal expression uses a const_ name that only a
    correctly-scoped lookup would admit - so nothing here would prove the
    lookup ever finds anything. This is the case that only passes if
    `const_LEVY_NZD_PER_T` correctly resolves against a constant defined in
    the formula's *own* factor set."""
    fs, metric = _seed_set_and_metric(session)
    session.add(Constant(factor_set_id=fs.id, code="LEVY_NZD_PER_T",
                         value=Decimal("60")))
    session.commit()

    await admin_client.post("/admin/formula/create", data={
        "factor_set": str(fs.id), "metric": str(metric.id),
        "expression": "qty_kg * const_LEVY_NZD_PER_T", "notes": "",
    })

    _resync(session)
    saved = session.scalar(select(Formula).where(Formula.factor_set_id == fs.id))
    assert saved is not None
    assert saved.expression == "qty_kg * const_LEVY_NZD_PER_T"


@pytest.mark.asyncio
async def test_editing_a_formula_to_an_invalid_expression_is_refused(
    session, admin_client
):
    """The guard's own docstring notes that AuditedModelView's before_commit
    listener flushes before calling validate_before_commit, which is exactly
    what emptied session.new/session.dirty in the first draft of this hook
    and made it a dead loop that accepted everything. All three tests above
    only exercise /admin/formula/create - a regression back to that dead
    loop on the *edit* path specifically (the path the identity_map fix
    changed) would go unnoticed without a case that posts to
    /admin/formula/edit/{id}."""
    fs, metric = _seed_set_and_metric(session)
    session.commit()

    await admin_client.post("/admin/formula/create", data={
        "factor_set": str(fs.id), "metric": str(metric.id),
        "expression": "qty_kg * (upstream + downstream)", "notes": "",
    })
    _resync(session)
    formula = session.scalar(select(Formula).where(Formula.factor_set_id == fs.id))
    assert formula is not None

    response = await admin_client.post(
        f"/admin/formula/edit/{formula.id}",
        data={"factor_set": str(fs.id), "metric": str(metric.id),
              "expression": "qty_kg * upstrem", "notes": ""},
    )

    _resync(session)
    assert session.scalar(
        select(Formula).where(Formula.factor_set_id == fs.id)
    ).expression == "qty_kg * (upstream + downstream)"
    assert "upstrem" in response.text


@pytest.mark.asyncio
async def test_a_formula_referring_to_a_constant_from_another_set_is_refused(
    session, admin_client
):
    """const_ names resolve against the set's own rows, so a constant that
    exists in a different version does not make this expression valid."""
    fs, metric = _seed_set_and_metric(session)
    other = FactorSet(version_label=_FACTOR_SET_LABELS[1],
                      status=FactorSetStatus.draft, is_mock=True)
    session.add(other)
    session.flush()
    session.add(Constant(factor_set_id=other.id, code="LEVY_NZD_PER_T",
                         value=Decimal("60")))
    session.commit()

    await admin_client.post("/admin/formula/create", data={
        "factor_set": str(fs.id), "metric": str(metric.id),
        "expression": "qty_kg * const_LEVY_NZD_PER_T", "notes": "",
    })

    _resync(session)
    assert session.scalar(
        select(Formula).where(Formula.factor_set_id == fs.id)
    ) is None


# --- End-to-end tests: ConstantAdmin's real behaviour -----------------------


@pytest.mark.asyncio
async def test_deleting_a_referenced_constant_is_refused(session, admin_client):
    """ConstantAdmin's own flush never contains a Formula row, so
    FormulaAdmin's validate_before_commit - which only fires for rows of its
    own model, per AuditedModelView's own docstring - never runs when a
    constant is deleted. Without a guard of ConstantAdmin's own, this delete
    goes through silently and the formula becomes unrunnable the next time a
    member of the public calculates - a FORMULA_ERROR 500 with nothing on
    any admin screen ever having said so."""
    fs, metric = _seed_set_and_metric(session)
    constant = Constant(factor_set_id=fs.id, code="LEVY_NZD_PER_T", value=Decimal("60"))
    session.add(constant)
    session.flush()
    formula = Formula(factor_set_id=fs.id, metric_id=metric.id,
                      expression="qty_kg * const_LEVY_NZD_PER_T")
    session.add(formula)
    session.commit()

    await admin_client.delete("/admin/constant/delete", params={"pks": str(constant.id)})

    _resync(session)
    assert session.scalar(select(Constant).where(Constant.id == constant.id)) is not None
    saved_formula = session.scalar(select(Formula).where(Formula.id == formula.id))
    assert saved_formula is not None
    assert saved_formula.expression == "qty_kg * const_LEVY_NZD_PER_T"


@pytest.mark.asyncio
async def test_deleting_an_unreferenced_constant_goes_through(session, admin_client):
    """The guard refuses only a delete that would orphan a formula - a guard
    that refused every constant delete would pass the test above too."""
    fs, _metric = _seed_set_and_metric(session)
    constant = Constant(factor_set_id=fs.id, code="UNUSED_CONST", value=Decimal("1"))
    session.add(constant)
    session.commit()
    constant_id = constant.id

    await admin_client.delete("/admin/constant/delete", params={"pks": str(constant_id)})

    _resync(session)
    # constant_id, not constant.id: the row is gone, and _resync's
    # expire_all() means touching the already-identity-mapped `constant`
    # object's own attributes here would trigger an implicit refresh against
    # a row that no longer exists, raising ObjectDeletedError rather than
    # answering the question this assertion is actually asking.
    assert session.scalar(select(Constant).where(Constant.id == constant_id)) is None


@pytest.mark.asyncio
async def test_renaming_a_referenced_constant_is_refused(session, admin_client):
    """Renaming a constant's code is indistinguishable from deleting it, from
    a formula's point of view: the exact code the expression resolves
    against is gone either way. This exercises ConstantAdmin's *edit* path,
    where the row does stay in session.identity_map after flush - unlike
    delete, this needs no extra bookkeeping - but only if the guard scopes
    its recheck to the constant actually being edited."""
    fs, metric = _seed_set_and_metric(session)
    constant = Constant(factor_set_id=fs.id, code="LEVY_NZD_PER_T", value=Decimal("60"))
    session.add(constant)
    session.flush()
    formula = Formula(factor_set_id=fs.id, metric_id=metric.id,
                      expression="qty_kg * const_LEVY_NZD_PER_T")
    session.add(formula)
    session.commit()

    await admin_client.post(f"/admin/constant/edit/{constant.id}", data={
        "factor_set": str(fs.id), "code": "LEVY_RENAMED", "value": "60",
        "unit": "", "note": "",
    })

    _resync(session)
    assert session.scalar(
        select(Constant).where(Constant.id == constant.id)
    ).code == "LEVY_NZD_PER_T"


# --- End-to-end tests: the published-set immutability guard ----------------


def _seed_published_set_and_metric(session):
    """A published factor set and a metric, the counterpart to
    _seed_set_and_metric's draft one - for the guard below, whose whole
    point is telling those two states apart."""
    fs = FactorSet(version_label=_FACTOR_SET_LABELS[0], status=FactorSetStatus.published,
                   is_mock=False)
    session.add(fs)
    metric = Metric(code=_METRIC_CODES[0], name="Test metric", unit="kg")
    session.add(metric)
    session.flush()
    return fs, metric


@pytest.mark.asyncio
async def test_editing_an_equivalence_in_a_published_set_is_refused(session, admin_client):
    """admin/factor_views.py's own module docstring says "versions are
    cloned rather than edited in place" - nothing enforced that before this
    guard existed. Every historical submission stamped with this
    factor_set_id depends on its numbers staying exactly as published."""
    fs, metric = _seed_published_set_and_metric(session)
    equivalence = Equivalence(
        factor_set_id=fs.id, code="km_driven", name="Km driven",
        source_metric_id=metric.id, value_per_unit=Decimal("1.5"),
        label_template="Equivalent to driving {value} km",
    )
    session.add(equivalence)
    session.commit()

    await admin_client.post(f"/admin/equivalence/edit/{equivalence.id}", data={
        "factor_set": str(fs.id), "code": "km_driven", "name": "Km driven",
        "source_metric": str(metric.id), "value_per_unit": "999",
        "label_template": "Equivalent to driving {value} km",
        "source_note": "", "sort_order": "0", "active": "on",
    })

    _resync(session)
    assert session.scalar(
        select(Equivalence).where(Equivalence.id == equivalence.id)
    ).value_per_unit == Decimal("1.5")


@pytest.mark.asyncio
async def test_editing_an_equivalence_in_a_draft_set_goes_through(session, admin_client):
    """The guard refuses only a published/archived set's rows - a guard that
    refused every edit would pass the test above too."""
    fs, metric = _seed_set_and_metric(session)
    equivalence = Equivalence(
        factor_set_id=fs.id, code="km_driven", name="Km driven",
        source_metric_id=metric.id, value_per_unit=Decimal("1.5"),
        label_template="Equivalent to driving {value} km",
    )
    session.add(equivalence)
    session.commit()

    await admin_client.post(f"/admin/equivalence/edit/{equivalence.id}", data={
        "factor_set": str(fs.id), "code": "km_driven", "name": "Km driven",
        "source_metric": str(metric.id), "value_per_unit": "999",
        "label_template": "Equivalent to driving {value} km",
        "source_note": "", "sort_order": "0", "active": "on",
    })

    _resync(session)
    assert session.scalar(
        select(Equivalence).where(Equivalence.id == equivalence.id)
    ).value_per_unit == Decimal("999")


@pytest.mark.asyncio
async def test_editing_a_formula_in_a_published_set_is_refused_even_when_valid(
    session, admin_client
):
    """Proves the new published-set guard fires independently of
    FormulaAdmin's own pre-existing expression-validity check: the new
    expression submitted here is perfectly valid, so only the new guard can
    be the one refusing this - a regression that dropped the composed call
    would let this straight through."""
    fs, metric = _seed_published_set_and_metric(session)
    formula = Formula(factor_set_id=fs.id, metric_id=metric.id,
                      expression="qty_kg * upstream")
    session.add(formula)
    session.commit()

    await admin_client.post(f"/admin/formula/edit/{formula.id}", data={
        "factor_set": str(fs.id), "metric": str(metric.id),
        "expression": "qty_kg * downstream", "notes": "",
    })

    _resync(session)
    assert session.scalar(
        select(Formula).where(Formula.id == formula.id)
    ).expression == "qty_kg * upstream"


# --- End-to-end tests: the published-set guard's delete and move paths -----
#
# _require_draft_factor_set (admin/factor_views.py) already refuses editing
# a row in place. These four close the two gaps E-5's reviewer found: delete
# was unguarded entirely, and a *move* was checked only against the row's
# pending factor_set_id - a row on its way out of a published set looks, by
# that value alone, like an ordinary draft edit.


@pytest.mark.asyncio
async def test_deleting_a_row_from_a_published_set_is_refused(
    session, admin_client, two_sets
):
    """A submission stamps the factor set it was calculated against, so
    every historical result naming this set stops reproducing. Deleting is
    not gentler than editing - it is the same damage."""
    published, _ = two_sets
    row = session.scalar(
        select(FactorUpstream).where(FactorUpstream.factor_set_id == published.id)
    )
    row_id = row.id
    session.commit()

    # sqladmin's generic delete route is registered for DELETE only
    # (sqladmin/application.py's Route(..., methods=["DELETE"])) - unlike an
    # @action route, which is GET-only. test_deleting_a_referenced_constant_is_refused
    # above already drives this the same way.
    await admin_client.delete("/admin/factor-upstream/delete", params={"pks": str(row_id)})

    _resync(session)
    assert session.get(FactorUpstream, row_id) is not None


@pytest.mark.asyncio
async def test_moving_a_row_out_of_a_published_set_is_refused(
    session, admin_client, two_sets
):
    """The guard reads the *pending* factor_set_id, so a row on its way out
    of a published set looks like a draft row to a naive check.

    The move target is a fresh, empty draft of its own rather than
    `two_sets`'s own populated one: that one already carries a
    FactorUpstream row on the same (sector, food_category, metric) tuple -
    both sets in `two_sets` are built from the same `taxonomy_for_factors` -
    so moving into it collides with `uq_factor_upstream` and the edit is
    refused by a plain IntegrityError before the guard this test targets
    ever runs. That refusal happens to look identical from the outside
    (the row stays on `published`), which is exactly what makes it a false
    positive worth guarding against here.
    """
    published, _ = two_sets
    empty_draft = FactorSet(version_label="e6-move-target",
                            status=FactorSetStatus.draft, is_mock=True)
    session.add(empty_draft)
    session.flush()
    row = session.scalar(
        select(FactorUpstream).where(FactorUpstream.factor_set_id == published.id)
    )
    row_id, sector_id = row.id, row.sector_id
    category_id, metric_id = row.food_category_id, row.metric_id
    session.commit()

    await admin_client.post(f"/admin/factor-upstream/edit/{row_id}", data={
        "factor_set": str(empty_draft.id), "sector": str(sector_id),
        "food_category": str(category_id), "metric": str(metric_id),
        "value_per_kg": "1.9", "data_quality": "", "source_note": "",
    })

    _resync(session)
    assert session.get(FactorUpstream, row_id).factor_set_id == published.id


@pytest.mark.asyncio
async def test_a_draft_row_can_still_be_deleted(session, admin_client, two_sets):
    """The guard must refuse the published case and nothing else. A version
    that refused every delete would satisfy the first test and make drafts
    uneditable, which is the opposite of the intent."""
    _, draft = two_sets
    row = session.scalar(
        select(FactorUpstream).where(FactorUpstream.factor_set_id == draft.id)
    )
    row_id = row.id
    session.commit()

    await admin_client.delete("/admin/factor-upstream/delete", params={"pks": str(row_id)})

    _resync(session)
    assert session.get(FactorUpstream, row_id) is None


@pytest.mark.asyncio
async def test_moving_a_row_between_two_drafts_still_goes_through(
    session, admin_client, two_sets
):
    """The move guard's counterpart to the draft-delete test above: refusing
    only a move whose source or destination is published, not every move.
    Needs a second draft of its own - `two_sets` provides only one."""
    _, draft = two_sets
    other_draft = FactorSet(version_label="e6-second-draft",
                            status=FactorSetStatus.draft, is_mock=True)
    session.add(other_draft)
    session.flush()
    row = session.scalar(
        select(FactorUpstream).where(FactorUpstream.factor_set_id == draft.id)
    )
    row_id, sector_id = row.id, row.sector_id
    category_id, metric_id = row.food_category_id, row.metric_id
    session.commit()

    await admin_client.post(f"/admin/factor-upstream/edit/{row_id}", data={
        "factor_set": str(other_draft.id), "sector": str(sector_id),
        "food_category": str(category_id), "metric": str(metric_id),
        "value_per_kg": "1.9", "data_quality": "", "source_note": "",
    })

    _resync(session)
    assert session.get(FactorUpstream, row_id).factor_set_id == other_draft.id
