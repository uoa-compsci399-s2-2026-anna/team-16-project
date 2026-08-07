"""Contract §8.1: the factor screens, audited by inheritance.

Deviates from task-3-brief.md's Step 1 listing the same way
tests/admin/test_taxonomy_views.py deviates from task-3's own brief, and for
the same reasons - see that file's module docstring for the full account:

1. The end-to-end tests are ``async def`` and ``await`` every
   ``admin_client`` call. ``admin_client`` is httpx's ``AsyncClient`` (the
   ``client`` fixture in tests/conftest.py); calling one of its methods
   without ``await`` returns an un-awaited coroutine, not a response.
2. ``session`` here is a *local*, hard-committing fixture, not the
   rolled-back one tests/conftest.py defines. ``admin_client`` drives real
   HTTP requests that reach the factor views through the running app's own,
   separate sessionmaker - a different connection entirely - so a test that
   seeds a row via the rolled-back fixture and then expects ``admin_client``
   to see it would fail for a reason that has nothing to do with the
   invariant under test.
"""

import re
import time
import uuid
from decimal import Decimal

import pyotp
import pytest
import pytest_asyncio
from sqlalchemy import bindparam as sa_bindparam
from sqlalchemy import select, text

from admin.accounts import (
    begin_mfa_enrolment, complete_mfa_enrolment, create_staff, get_staff, set_password,
)
from admin.factor_models import (
    Constant, Equivalence, FactorDownstream, FactorSet, FactorSetStatus,
    FactorUpstream, Formula,
)
from admin.factor_views import (
    ConstantAdmin, EquivalenceAdmin, FactorDownstreamAdmin, FactorUpstreamAdmin,
    FormulaAdmin,
)
from admin.models import StaffRole
from admin.taxonomy_models import Metric
from admin.totp import TOTP_INTERVAL
from admin.views import time as views_time

pytestmark = pytest.mark.db

SECRET_KEY = "test-secret-key-not-used-anywhere-real"

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
# No tests/admin/conftest.py exists, so - matching every other file under
# tests/admin/ - these are file-local. admin_client and its helpers are the
# same shape as tests/admin/test_taxonomy_views.py's; duplicated rather than
# imported to keep this file self-contained the way every sibling file in
# this directory already is.


def _create_onboarded_account(admin_app, *, role=StaffRole.admin):
    """Create and commit a fully onboarded account of the given role."""
    username = f"u{uuid.uuid4().hex[:10]}"
    password = "a-long-enough-password"
    factory = admin_app.state.session_factory
    with factory() as db:
        create_staff(db, username=username, display_name="Test User", role=role)
        db.flush()
        set_password(db, username, password)
        secret, _ = begin_mfa_enrolment(db, username, secret_key=SECRET_KEY)
        # Backdated so the login step below, which mints a fresh TOTP code
        # for "now", cannot collide with the counter enrolment just spent.
        enrol_now = int(time.time()) - 4 * TOTP_INTERVAL
        complete_mfa_enrolment(
            db, username,
            pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(enrol_now),
            secret_key=SECRET_KEY, now=enrol_now,
        )
        db.commit()
        staff = get_staff(db, username)
    return staff, password, secret


async def _login(client, monkeypatch, *, username, password, secret):
    """Drive the real HTTP login flow for an already-created, onboarded account."""
    now = int(time.time())
    monkeypatch.setattr(views_time, "time", lambda: now)

    login = await client.post(
        "/admin/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert login.status_code == 302, "password step should have succeeded"

    verify_page = await client.get("/admin/verify")
    match = re.search(r'name="csrf_token" value="([^"]+)"', verify_page.text)
    assert match, "no CSRF token rendered on /admin/verify"
    code = pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(now)
    verify = await client.post(
        "/admin/verify",
        data={"code": code, "csrf_token": match.group(1)},
        follow_redirects=False,
    )
    assert verify.status_code == 302, "TOTP step should have completed the login"


def _cleanup_staff(admin_app, *staff_rows):
    """Remove the staff row (and any audit_log entry against it) this
    fixture created - see tests/admin/test_accounts_view.py's function of
    the same name for the full rationale."""
    factory = admin_app.state.session_factory
    with factory() as db:
        for staff in staff_rows:
            db.execute(
                text("DELETE FROM audit_log WHERE table_name = 'staff' AND row_id = :id"),
                {"id": staff.id},
            )
            db.execute(text("DELETE FROM staff WHERE id = :id"), {"id": staff.id})
        db.commit()


@pytest_asyncio.fixture
async def admin_client(admin_app, client, monkeypatch):
    """A client logged in as a fully onboarded administrator.

    See tests/admin/test_taxonomy_views.py's fixture of the same name:
    cleanup is registered before login is attempted, not after, so a failure
    partway through _login does not leave a committed staff row behind.
    """
    staff, password, secret = _create_onboarded_account(admin_app, role=StaffRole.admin)
    try:
        await _login(client, monkeypatch, username=staff.username, password=password, secret=secret)
    except BaseException:
        _cleanup_staff(admin_app, staff)
        raise
    yield client
    _cleanup_staff(admin_app, staff)


_FACTOR_SET_LABELS = ["kc-factor-view-test", "kc-factor-view-test-other"]
_METRIC_CODES = ["kc-factor-view-test-metric"]


def _cleanup_factor_rows(admin_app):
    """Remove every row this file's tests may have hard-committed via the
    local `session` fixture below, by the fixed set of labels/codes those
    tests are written against. `factor_set_id` cascades (ondelete="CASCADE"
    on FactorUpstream/FactorDownstream/Constant/Formula/Equivalence, see
    admin/factor_models.py) so deleting the factor_set rows themselves is
    enough to take their children with it; the metric row and any audit_log
    entries are cleaned up explicitly.
    """
    factory = admin_app.state.session_factory
    with factory() as db:
        factor_set_ids = db.execute(
            text("SELECT id FROM factor_set WHERE version_label IN :labels")
            .bindparams(sa_bindparam("labels", expanding=True)),
            {"labels": _FACTOR_SET_LABELS},
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
        db.commit()


def _resync(session):
    """End this session's own transaction and clear its identity map, so the
    next query reflects what `admin_client`'s HTTP request - a different
    connection - just committed.

    See tests/admin/test_taxonomy_views.py's function of the same name:
    MySQL's REPEATABLE READ isolation means `session.expire_all()` alone is
    not enough - a query re-issued on the same still-open transaction keeps
    seeing the pre-request snapshot regardless.
    """
    session.commit()
    session.expire_all()


@pytest.fixture
def session(admin_app):
    """A real, hard-committing session against the running admin app's own
    database. See tests/admin/test_taxonomy_views.py's module docstring for
    why the rolled-back `session` fixture in tests/conftest.py cannot stand
    in here.

    Cleanup is unconditional (`finally`), not merely on success.
    """
    factory = admin_app.state.session_factory
    db = factory()
    try:
        yield db
    finally:
        db.close()
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
