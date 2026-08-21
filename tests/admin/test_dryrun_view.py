"""Contract §8.2: /admin/try. Enter a scenario, see what it computes."""

import dataclasses
from dataclasses import dataclass

import pytest

from admin.calc_client import CalculateRefused, CalculateUnavailable
from tests.admin.conftest import _resync

pytestmark = [pytest.mark.db, pytest.mark.asyncio]


@pytest.fixture
def session(_committed_session):
    """This file's name for the hard-committing session against the running
    admin app's own database - see tests/admin/conftest.py's
    ``_committed_session`` docstring for why every file that needs it
    re-exposes it locally as ``session`` rather than sharing one definition.
    """
    return _committed_session


@dataclass
class _Call:
    body: dict
    actor: str
    factor_set_version: str | None


class FakeCalculateClient:
    """A CalculateClient the tests drive.

    B's endpoint does not exist yet, so every test in this file and the
    comparison file runs against this. It records what the view asked for —
    which is most of what these tests assert — and answers with whatever the
    test set up.
    """

    def __init__(self):
        self.calls: list[_Call] = []
        self.result: dict = {"totals": {"current": {"metrics": {}},
                                        "net_benefit": {}},
                             "entries": []}
        #: (code, message, details) to raise as CalculateRefused, or None.
        self.refuse: tuple[str, str, list] | None = None
        self.unavailable: bool = False
        #: Refuse only the call at this index; used by the comparison tests to
        #: check one failing scenario does not take the others with it.
        self.refuse_on_call: int | None = None

    def dry_run(self, request_body: dict, *, actor: str,
                factor_set_version: str | None) -> dict:
        index = len(self.calls)
        self.calls.append(_Call(request_body, actor, factor_set_version))
        if self.unavailable:
            raise CalculateUnavailable("the calculation service is not reachable")
        if self.refuse is not None or self.refuse_on_call == index:
            code, message, details = self.refuse or (
                "FORMULA_ERROR", "co2e: name 'upstrem' is not defined",
                [{"field": "formula.co2e", "issue": "undefined_name"}],
            )
            raise CalculateRefused(code=code, message=message, details=details)
        return self.result


@pytest.fixture
def fake_calc_client(admin_app):
    """Install the fake on the running app's Runtime for one test.

    ``admin_app`` (tests/conftest.py) is the *outer* FastAPI app -
    ``create_app``'s return value. ``Runtime`` does not live on its
    ``.state``: ``admin/app.py`` attaches it to ``admin.admin.state.runtime``,
    where ``admin.admin`` is sqladmin's own mounted Starlette sub-application
    (see the comment in admin/app.py and admin/runtime.py's module
    docstring for why - ``request.app`` inside a view resolves to that inner
    app, not the outer one). ``Admin(...)`` mounts it under the outer app's
    routes as a ``Mount`` named ``"admin"`` (sqladmin's own
    ``application.py``: ``self.app.mount(base_url, app=self.admin,
    name="admin")``), which is how this fixture reaches it without a
    reference to the ``Admin`` instance itself - `create_app` does not keep
    or expose one.

    ``Runtime`` is also a *frozen* dataclass (admin/runtime.py), so
    ``runtime.calc_client = fake`` raises ``FrozenInstanceError`` - confirmed
    directly before writing this fixture. ``dataclasses.replace`` builds a
    new ``Runtime`` with the fake client and every other field carried over,
    and this fixture reassigns the inner app's ``.state.runtime`` to that new
    object rather than mutating the old one in place.
    """
    inner_app = next(
        route.app for route in admin_app.routes
        if getattr(route, "name", None) == "admin"
    )
    original = inner_app.state.runtime
    fake = FakeCalculateClient()
    inner_app.state.runtime = dataclasses.replace(original, calc_client=fake)
    yield fake
    inner_app.state.runtime = original


async def test_the_form_is_reachable_by_staff(admin_client):
    """Publishing is open to both roles (§8.3) and so is trying — a staff
    member tuning a formula is the person this page exists for."""
    response = await admin_client.get("/admin/try")

    assert response.status_code == 200


async def test_every_choice_on_the_form_explains_itself(admin_client):
    """The counterpart to tests/admin/test_field_help.py, for the fields that
    test cannot reach.

    ``DryRunView`` is a ``BaseView`` with a hand-written template, not a
    ``ModelView``, so no scaffolded form and no ``form_args`` exist for the
    coverage test to walk - and this is the screen a staff member tuning a
    formula spends the most time on. Four of its six controls change the
    answer silently when chosen wrongly: the factor set decides whether a
    draft or the live numbers are being exercised at all, a blank food
    category means the standard mix rather than nothing, the horizon changes
    every greenhouse-gas figure, and the quantity has a precision limit
    inherited from the public API.

    All six, not only those four, and that came out of reading the rendered
    page rather than the source. Sector and Destination each sit directly
    above another control, so a paragraph explaining only the second of the
    pair reads as if it might belong to both - the ambiguity is invisible in
    the template and obvious on screen.

    Asserted on the distinguishing phrase rather than the whole paragraph, so
    rewording the copy does not fail this while deleting it does.
    """
    body = (await admin_client.get("/admin/try")).text

    for phrase in (
        "without making them live",          # factor set
        "the case most worth checking",      # food category
        "several times more heavily",        # methane horizon
        "Three decimal places at most",      # quantity
        "decide the upstream factor",        # sector
        "decides its downstream factor",     # destination
    ):
        assert phrase in body, f"the dry-run form no longer explains: {phrase}"


async def test_it_is_not_reachable_without_a_session(client):
    response = await client.get("/admin/try")

    assert response.status_code in (302, 401, 403)


async def test_a_submitted_scenario_reaches_the_client_with_the_header(
    admin_client, fake_calc_client, one_draft, taxonomy_for_factors, session
):
    """The whole point: the panel does not calculate, it asks."""
    session.commit()

    await admin_client.post("/admin/try", data={
        "factor_set": one_draft.version_label,
        "sector": taxonomy_for_factors.sector.code,
        # Blank means "standard mix" (§6.2's food_category rule) - sent, not
        # omitted, to exercise the form's own blank-select option.
        "food_category": "",
        "gwp_horizon": "100",
        "destination": taxonomy_for_factors.destination.code,
        "qty_kg": "1200.000",
    })

    assert fake_calc_client.calls, "the view never called the calculate client"
    call = fake_calc_client.calls[0]
    assert call.factor_set_version == one_draft.version_label
    assert call.body["entries"][0]["sector"] == taxonomy_for_factors.sector.code
    # gwp_horizon is a top-level request field (contract v1.1), not a
    # per-entry one — a formula bound to const_GWP_CH4 would silently see
    # the wrong horizon if this regressed to living inside the entry.
    assert call.body["gwp_horizon"] == 100
    assert "gwp_horizon" not in call.body["entries"][0]
    # The blank food-category option must send null, not "" — the API
    # treats null as standard_mix (§6.2) but has no rule for an empty string.
    assert call.body["entries"][0]["food_category"] is None


async def test_the_call_vouches_for_the_signed_in_staff_member(
    admin_client, fake_calc_client, one_draft, taxonomy_for_factors, session
):
    """The `actor` the client is given must be *this* session's username.

    `admin/calc_client.py` signs a staff proof over whatever it is handed
    (`db/staff_proof.py`, open item O-9), so this value is the panel's whole
    assertion about who is asking. A view that passed a constant, or read the
    wrong session key, would still produce a proof the API accepts - the
    signature would be valid and the name would be wrong - and every test that
    only checks the call happened would stay green.

    Asserted against `admin_client.staff.username`, the account the fixture
    actually logged in as, rather than against any literal.
    """
    session.commit()

    await admin_client.post("/admin/try", data={
        "factor_set": one_draft.version_label,
        "sector": taxonomy_for_factors.sector.code,
        "food_category": "",
        "gwp_horizon": "100",
        "destination": taxonomy_for_factors.destination.code,
        "qty_kg": "1200.000",
    })

    assert fake_calc_client.calls, "the view never called the calculate client"
    assert fake_calc_client.calls[0].actor == admin_client.staff.username


async def test_a_non_numeric_gwp_horizon_falls_back_to_100(
    admin_client, fake_calc_client, one_draft, taxonomy_for_factors, session
):
    """A non-numeric `gwp_horizon` is a malformed form post, not proof the
    engine should never run - `int()` on it unguarded raises `ValueError`
    out of the view, which is a 500 rather than one of this view's two
    designed failure pages (refused/unavailable). Falling back to the
    contract's own default (100) keeps the request going with a value the
    API accepts."""
    session.commit()

    response = await admin_client.post("/admin/try", data={
        "factor_set": one_draft.version_label,
        "sector": taxonomy_for_factors.sector.code,
        "food_category": "",
        "gwp_horizon": "not-a-number",
        "destination": taxonomy_for_factors.destination.code,
        "qty_kg": "1200.000",
    })

    assert response.status_code == 200
    assert fake_calc_client.calls[0].body["gwp_horizon"] == 100


async def test_the_result_page_shows_what_the_api_returned(
    admin_client, fake_calc_client, one_draft, taxonomy_for_factors, session
):
    """Displayed, not recomputed. The number on screen must be the engine's."""
    fake_calc_client.result = {
        "factor_set": {"version_label": "e6-only-draft", "is_mock": True},
        "factor_source": "version:e6-only-draft",
        "totals": {"current": {"metrics": {
            "co2e": {"total": "3468.0000000000", "unit": "kg CO2e",
                     "display_precision": 1}}}, "net_benefit": {}},
        "entries": [],
    }
    session.commit()

    response = await admin_client.post("/admin/try", data={
        "factor_set": one_draft.version_label,
        "sector": "e6_processing",
        "food_category": "e6_dairy",
        "gwp_horizon": "100",
        "destination": "e6_landfill",
        "qty_kg": "1200.000",
    })

    assert "3468" in response.text


async def test_a_malformed_metrics_shape_does_not_500(
    admin_client, fake_calc_client, one_draft, taxonomy_for_factors, session
):
    """B's endpoint is not deployed and its exact response shape is
    unconfirmed - `totals.current.metrics` coming back as a scalar (or an
    explicit `null` one level up) must read as no metrics rather than crash
    the template's `.items()` call with a 500."""
    fake_calc_client.result = {
        "factor_set": {"version_label": "e6-only-draft", "is_mock": True},
        "totals": {"current": {"metrics": "not-a-mapping"}}, "net_benefit": {},
        "entries": [],
    }
    session.commit()

    response = await admin_client.post("/admin/try", data={
        "factor_set": one_draft.version_label,
        "sector": "e6_processing",
        "food_category": "e6_dairy",
        "gwp_horizon": "100",
        "destination": "e6_landfill",
        "qty_kg": "1200.000",
    })

    assert response.status_code == 200


async def test_a_formula_error_is_shown_with_its_details(
    admin_client, fake_calc_client, one_draft, session
):
    """§9.1 gives FORMULA_ERROR a staff presentation with located details
    specifically so this page can show them. A staff member tuning a formula
    is exactly who needs the line and column."""
    fake_calc_client.refuse = ("FORMULA_ERROR",
                               "co2e: name 'upstrem' is not defined",
                               [{"field": "formula.co2e", "issue": "undefined_name"}])
    session.commit()

    response = await admin_client.post("/admin/try", data={
        "factor_set": one_draft.version_label,
        "sector": "e6_processing",
        "food_category": "e6_dairy",
        "gwp_horizon": "100",
        "destination": "e6_landfill",
        "qty_kg": "1200.000",
    })

    assert "upstrem" in response.text


async def test_an_unreachable_api_says_so_plainly(
    admin_client, fake_calc_client, one_draft, session
):
    """B's endpoint is not deployed yet. This is the state the panel will be
    in for a while, so it has to read as an outage rather than a fault in
    the factors the staff member was checking."""
    fake_calc_client.unavailable = True
    session.commit()

    response = await admin_client.post("/admin/try", data={
        "factor_set": one_draft.version_label,
        "sector": "e6_processing",
        "food_category": "e6_dairy",
        "gwp_horizon": "100",
        "destination": "e6_landfill",
        "qty_kg": "1200.000",
    })

    assert response.status_code == 200
    body = response.text.lower()
    assert "not reachable" in body or "unavailable" in body


async def test_no_audit_row_is_written(
    admin_client, fake_calc_client, one_draft, session
):
    """This pins only what it can: `admin/models.py` has no submission
    table at all (persistence is B's `POST /api/v1/calculate`, behind
    `X-Dry-Run: true`, which this view never bypasses), so nothing in this
    stage could write one either way. What this view *could* wrongly do is
    write its own audit_log row on a dry run, the way every AuditedModelView
    write does — it does not, because the view never opens a session that
    writes anything; it only reads the form context and calls the client.
    """
    from sqlalchemy import func, select
    from admin.models import AuditLog

    before = session.scalar(select(func.count()).select_from(AuditLog))
    session.commit()

    await admin_client.post("/admin/try", data={
        "factor_set": one_draft.version_label,
        "sector": "e6_processing",
        "food_category": "e6_dairy",
        "gwp_horizon": "100",
        "destination": "e6_landfill",
        "qty_kg": "1200.000",
    })

    _resync(session)
    assert session.scalar(select(func.count()).select_from(AuditLog)) == before


# ---------------------------------------------------------------------------
# What the result page CLAIMS about the run
#
# The page grew status states in the redesign - a pill and a banner - and
# nothing here tested any of them. Two mutations survived the whole file: the
# state read straight off `outcome`, and an outage shown as a warning. Both are
# claims a staff member acts on.


def _scenario():
    return {
        "factor_set": None,          # filled in by the caller
        "sector": "e6_processing",
        "food_category": "e6_dairy",
        "gwp_horizon": "100",
        "destination": "e6_landfill",
        "qty_kg": "1200.000",
    }


async def _run(admin_client, version_label):
    data = _scenario()
    data["factor_set"] = version_label
    return await admin_client.post("/admin/try", data=data)


async def test_a_response_with_no_metrics_is_not_reported_as_a_pass(
    admin_client, fake_calc_client, one_draft, taxonomy_for_factors, session
):
    """**`outcome == "ok"` means the client did not raise. It does not mean the
    calculator returned anything.**

    The same malformed shape `test_a_malformed_metrics_shape_does_not_500`
    drives - B's endpoint is not deployed and its response shape is unconfirmed,
    so this is a real case and not a contrived one. Read straight off `outcome`,
    the page draws a green tick and "Calculation completed successfully"
    directly above "No calculated metrics were returned", which is the page
    telling a staff member their formula works when it has no idea.
    """
    fake_calc_client.result = {
        "factor_set": {"version_label": "e6-only-draft", "is_mock": False},
        "totals": {"current": {"metrics": "not-a-mapping"}}, "net_benefit": {},
        "entries": [],
    }
    session.commit()

    response = await _run(admin_client, one_draft.version_label)
    body = response.text

    assert response.status_code == 200
    assert "No calculated metrics were returned" in body
    assert "status-pill--pass" not in body, (
        "a response carrying no metrics is being reported as a pass"
    )
    assert "returned no metrics" in body


async def test_a_mock_factor_set_is_not_reported_as_a_clean_pass(
    admin_client, fake_calc_client, one_draft, taxonomy_for_factors, session
):
    """The mock banner is mandatory (§2.2) and it was already rendering. What
    it sat under was a full-width green PASS, which is the louder of the two
    and says the opposite thing: the numbers are arithmetically correct and
    substantively meaningless."""
    fake_calc_client.result = {
        "factor_set": {"version_label": "e6-only-draft", "is_mock": True},
        "totals": {"current": {"metrics": {"co2e": {"total": "1.000", "unit": "kg"}}}},
        "net_benefit": {}, "entries": [],
    }
    session.commit()

    response = await _run(admin_client, one_draft.version_label)
    body = response.text

    assert response.status_code == 200
    #: The banner itself, unchanged and unconditional.
    assert "Placeholder data" in body
    #: And the state above it agrees with it rather than contradicting it.
    assert "status-pill--pass" not in body, (
        "a run against placeholder factors is being reported as a clean pass"
    )
    assert "placeholder factors" in body


async def test_a_real_result_is_reported_as_returned_not_as_passed(
    admin_client, fake_calc_client, one_draft, taxonomy_for_factors, session
):
    """**The affirmative half**, without which the two tests above are
    satisfied by a page that never shows a pass at all.

    And the wording is asserted, not just the class: nothing in this flow
    compares against an expected value, so "Test passed" claims a verdict the
    page has no basis for. It reports what came back and leaves the judgement
    to the person who knows what they expected.
    """
    fake_calc_client.result = {
        "factor_set": {"version_label": "e6-only-draft", "is_mock": False},
        "totals": {"current": {"metrics": {"co2e": {"total": "1234.500", "unit": "kg"}}}},
        "net_benefit": {}, "entries": [],
    }
    session.commit()

    response = await _run(admin_client, one_draft.version_label)
    body = response.text

    assert response.status_code == 200
    assert "status-pill--pass" in body
    assert "1234.500" in body, "the metric it is reporting on is not on the page"
    assert "passed" not in body.lower(), (
        "the page claims a verdict it has no expected value to compare against"
    )


async def test_an_unreachable_service_is_an_error_not_a_warning(
    admin_client, fake_calc_client, one_draft, taxonomy_for_factors, session
):
    """The template this redesign replaced used `notice--error` for an outage.

    `admin/dryrun_views.py`'s docstring is emphatic that an outage and a
    refusal must never read as the same thing, and they do not - but a yellow
    "Warning" under-states an outage in the other direction, and a staff member
    who reads it as "something was wrong with my scenario" goes looking at
    their own figures for a fault that is not there.
    """
    fake_calc_client.unavailable = True
    session.commit()

    response = await _run(admin_client, one_draft.version_label)
    body = response.text

    assert response.status_code == 200
    assert "not reachable" in body
    assert "result-state--error" in body, "an outage is being shown as a warning"
    assert "result-state--warning" not in body
