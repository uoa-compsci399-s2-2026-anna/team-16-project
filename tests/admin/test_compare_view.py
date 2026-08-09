"""Contract §8.2: the last gate before publishing."""

from decimal import Decimal

import pytest

from admin.comparison_models import ComparisonScenario, ComparisonScenarioLine
from tests.admin.test_dryrun_view import fake_calc_client  # noqa: F401 - re-used fixture

pytestmark = [pytest.mark.db, pytest.mark.asyncio]


@pytest.fixture
def session(_committed_session):
    """This file's name for the hard-committing session against the running
    admin app's own database - see tests/admin/conftest.py's
    ``_committed_session`` docstring for why every file that needs it
    re-exposes it locally as ``session`` rather than sharing one definition.
    """
    return _committed_session


def _make_scenario(session, taxonomy, code, *, active=True):
    scenario = ComparisonScenario(
        code=code, name=code.replace("-", " ").title(),
        sector_id=taxonomy.sector.id,
        food_category_id=taxonomy.category.id,
        gwp_horizon=100, active=active,
    )
    session.add(scenario)
    session.flush()
    session.add(ComparisonScenarioLine(
        scenario_id=scenario.id, destination_id=taxonomy.destination.id,
        qty_kg=Decimal("1200.000"),
    ))
    session.flush()
    return scenario


@pytest.fixture
def seeded_scenario(session, taxonomy_for_factors):
    return _make_scenario(session, taxonomy_for_factors, "e7-typical")


@pytest.fixture
def seeded_scenario_pair(session, taxonomy_for_factors):
    """Two scenarios, so a failure in the first can be shown not to hide the
    second — which is the whole reason this page is worth having when a
    draft is broken."""
    return [
        _make_scenario(session, taxonomy_for_factors, "e7-first"),
        _make_scenario(session, taxonomy_for_factors, "e7-second"),
    ]


@pytest.fixture
def inactive_scenario(session, taxonomy_for_factors):
    return _make_scenario(session, taxonomy_for_factors, "e7-retired", active=False)


async def test_each_scenario_is_run_against_both_sets(
    admin_client, fake_calc_client, two_sets, seeded_scenario, session
):
    """Two calls per scenario — one at the published label, one at the
    draft. That is what makes the difference meaningful rather than a
    comparison against whatever happened to be cached."""
    published, draft = two_sets
    session.commit()

    await admin_client.get(f"/admin/factor-sets/{draft.id}/compare")

    versions = [c.factor_set_version for c in fake_calc_client.calls]
    assert versions.count(published.version_label) == 1
    assert versions.count(draft.version_label) == 1


async def test_both_values_are_rendered_from_the_responses(
    admin_client, fake_calc_client, two_sets, seeded_scenario, session
):
    """Decision 6: impact numbers are computed server-side, in one place.
    Both columns are strings taken from two responses — the page must not
    parse them into floats to display them, and must not subtract them into
    a third number the engine never produced.
    """
    fake_calc_client.result = {
        "factor_set": {"version_label": "x", "is_mock": True},
        "totals": {"current": {"metrics": {
            "co2e": {"total": "3468.0000000000", "unit": "kg CO2e",
                     "display_precision": 1}}}, "net_benefit": {}},
        "entries": [],
    }
    published, draft = two_sets
    session.commit()

    response = await admin_client.get(f"/admin/factor-sets/{draft.id}/compare")

    assert "3468" in response.text
    # Both calls returned the same figure, so if the page were subtracting it
    # would show a zero it invented. It should show the value twice instead.
    assert response.text.count("3468") >= 2
    # Built with url_for(), not hard-coded, so it keeps resolving if this
    # route ever moves - pinned here as the link text/target it must still
    # resolve to (url_for renders an absolute URL, hence the substring).
    assert '/admin/try">Try a single scenario</a>' in response.text


async def test_a_scenario_that_fails_does_not_hide_the_others(
    admin_client, fake_calc_client, two_sets, seeded_scenario_pair, session
):
    """A draft with one broken formula is exactly when this page matters
    most. Losing the whole report to one failing scenario would send the
    staff member to publish blind.

    Checking only the status code and an error string (the brief's own
    version of this test) would pass unchanged if the loop aborted after
    the first refusal and rendered one page-level error — exactly the
    regression this test's name claims to guard. Asserting the call count
    and that the *second* scenario's own name and metric value are present
    is what actually pins "the others still run".
    """
    fake_calc_client.result = {
        "totals": {"current": {"metrics": {
            "co2e": {"total": "111.0000000000", "unit": "kg CO2e",
                     "display_precision": 1}}}, "net_benefit": {}},
        "entries": [],
    }
    fake_calc_client.refuse_on_call = 1
    session.commit()

    response = await admin_client.get(f"/admin/factor-sets/{two_sets[1].id}/compare")

    assert response.status_code == 200
    assert "FORMULA_ERROR" in response.text or "could not" in response.text.lower()
    # Two scenarios, two calls each, even though the first scenario's
    # second call was refused — the loop kept going rather than stopping.
    assert len(fake_calc_client.calls) == 4
    second_scenario = seeded_scenario_pair[1]
    assert second_scenario.name in response.text
    assert "111" in response.text


async def test_an_unreachable_service_is_a_page_level_message(
    admin_client, fake_calc_client, two_sets, seeded_scenario_pair, session
):
    """B's endpoint is not deployed for most of this project's life — this
    is the state both `/admin/try` and this page will be in for a while,
    and it must read as an outage rather than a fault in either factor
    set. Unlike a refusal, an unreachable service is not this scenario's
    own problem — it says nothing looked at any scenario at all — so it is
    one message for the whole page, not a row repeated per scenario.

    A single seeded scenario cannot tell a page-level message from a
    per-row one that happens to fire once — both render exactly one error
    notice either way. `seeded_scenario_pair` gives two scenarios, so a
    per-row rendering would produce one error notice per scenario it got
    to before failing, plus that scenario's own heading. This asserts
    exactly one error notice renders on the whole page and that neither
    scenario's own heading renders at all — the loop never got past the
    first call before `CalculateUnavailable` propagated out of it.
    """
    fake_calc_client.unavailable = True
    session.commit()

    response = await admin_client.get(f"/admin/factor-sets/{two_sets[1].id}/compare")

    assert response.status_code == 200
    body = response.text.lower()
    assert "not reachable" in body
    assert response.text.count('notice notice--error') == 1
    for scenario in seeded_scenario_pair:
        assert scenario.name not in response.text


async def test_a_mock_factor_set_shows_the_placeholder_banner(
    admin_client, fake_calc_client, two_sets, seeded_scenario, session
):
    """Every factor set in this project is mock right now (O-1: the client
    has not supplied real emissions factors). `target` and `published` are
    already in this view's context with their own `is_mock` — this is the
    page staff use to decide whether to publish, so it must carry the same
    mandatory, non-dismissible warning `dry_run_result.html` shows, not let
    a mock total sit on screen looking like a real one."""
    published, draft = two_sets
    session.commit()

    response = await admin_client.get(f"/admin/factor-sets/{draft.id}/compare")

    assert "notice--warning" in response.text
    assert "Placeholder data" in response.text


async def test_a_malformed_metrics_shape_does_not_500(
    admin_client, fake_calc_client, two_sets, seeded_scenario, session
):
    """§8.2's endpoint is not deployed and its exact response shape is
    unconfirmed - `metrics` coming back as a scalar, or a per-metric entry
    coming back as something other than a dict, must read as "could not be
    computed" rather than crash `_metric_rows`/`_metrics_of` with a
    TypeError/AttributeError that turns into a 500."""
    fake_calc_client.result = {
        "totals": {"current": {"metrics": "not-a-mapping"}}, "net_benefit": {},
        "entries": [],
    }
    session.commit()

    response = await admin_client.get(f"/admin/factor-sets/{two_sets[1].id}/compare")

    assert response.status_code == 200


async def test_a_non_dict_metric_entry_reads_as_could_not_be_computed(
    admin_client, fake_calc_client, two_sets, seeded_scenario, session
):
    """The metrics map itself can be a well-formed dict while one entry in
    it is not - `{"co2e": "oops"}` rather than `{"co2e": {"total": ...}}`.
    `_metric_rows` indexed straight into that entry with `published["total"]`,
    which is a TypeError on a string. Guarded the same way as a missing
    metric: renders "could not be computed" for that side."""
    fake_calc_client.result = {
        "totals": {"current": {"metrics": {"co2e": "oops"}}}, "net_benefit": {},
        "entries": [],
    }
    published, draft = two_sets
    session.commit()

    response = await admin_client.get(f"/admin/factor-sets/{draft.id}/compare")

    assert response.status_code == 200
    assert "could not be computed" in response.text.lower()


async def test_comparing_against_nothing_published_says_so(
    admin_client, fake_calc_client, one_draft, seeded_scenario, session
):
    """A fresh deployment has no published set. The page must explain that
    rather than rendering an empty table or dividing by a missing baseline."""
    session.commit()

    response = await admin_client.get(f"/admin/factor-sets/{one_draft.id}/compare")

    assert response.status_code == 200
    assert "no published" in response.text.lower()


async def test_inactive_scenarios_are_not_run(
    admin_client, fake_calc_client, two_sets, inactive_scenario, session
):
    """`active` is how a scenario is retired without losing it."""
    session.commit()

    await admin_client.get(f"/admin/factor-sets/{two_sets[1].id}/compare")

    assert fake_calc_client.calls == []
