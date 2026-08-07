"""Contract §8.2: the comparison-scenario CRUD screens.

`ComparisonScenario.gwp_horizon` carries a database CHECK constraint (20 or
100 - see `admin/comparison_models.py` and
`tests/admin/test_comparison_models.py::
test_the_gwp_horizon_is_one_of_the_two_the_contract_allows`), but the admin
form rendered it as a free `IntegerField` - so a staff member typing 57
would sail past the form and land on MySQL's own CHECK-violation text
(error 3819), not a form the panel actually validates.
"""

import pytest

pytestmark = [pytest.mark.db, pytest.mark.asyncio]


@pytest.fixture
def session(_committed_session):
    """See tests/admin/conftest.py's ``_committed_session`` docstring for
    why every file that needs it re-exposes it locally as ``session``."""
    return _committed_session


async def test_the_create_form_offers_only_the_two_allowed_horizons(admin_client):
    """A free number input lets staff type anything the CHECK constraint
    will later refuse. A two-option select can't be typed into wrong.

    Checked against the field itself, not just "a <select> is somewhere on
    the page" - the sector/food_category foreign keys already render as
    <select>, so that alone would pass unchanged with gwp_horizon still a
    free IntegerField.
    """
    response = await admin_client.get("/admin/comparison-scenario/create")

    assert response.status_code == 200
    assert '<select class="form-control" id="gwp_horizon"' in response.text
    assert '<option value="20">20 years</option>' in response.text
    assert '<option selected value="100">100 years</option>' in response.text
    assert 'name="gwp_horizon" type="number"' not in response.text


async def test_an_out_of_range_horizon_does_not_reach_the_database(
    admin_client, taxonomy_for_factors, session
):
    """Posting a value outside the select's own choices must not produce
    MySQL's raw CHECK-violation text (error 3819) - that means the form
    validated nothing and the database caught it instead."""
    session.commit()

    response = await admin_client.post("/admin/comparison-scenario/create", data={
        "code": "e7-out-of-range",
        "name": "Out of range",
        "sector": str(taxonomy_for_factors.sector.id),
        "gwp_horizon": "57",
        "sort_order": "0",
    })

    assert "3819" not in response.text
    assert "constraint" not in response.text.lower()
