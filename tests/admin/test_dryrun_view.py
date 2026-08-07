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
    cookies: dict
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

    def dry_run(self, request_body: dict, *, cookies: dict,
                factor_set_version: str | None) -> dict:
        index = len(self.calls)
        self.calls.append(_Call(request_body, cookies, factor_set_version))
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
        "food_category": taxonomy_for_factors.category.code,
        "gwp_horizon": "100",
        "destination": taxonomy_for_factors.destination.code,
        "qty_kg": "1200.000",
    })

    assert fake_calc_client.calls, "the view never called the calculate client"
    call = fake_calc_client.calls[0]
    assert call.factor_set_version == one_draft.version_label
    assert call.body["entries"][0]["sector"] == taxonomy_for_factors.sector.code


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


async def test_no_submission_row_is_written(
    admin_client, fake_calc_client, one_draft, session
):
    """The header exists for this. Dozens of tuning runs must not appear in
    the public statistics."""
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
