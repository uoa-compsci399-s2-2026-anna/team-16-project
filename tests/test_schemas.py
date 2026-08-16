from decimal import Decimal

import pytest
from pydantic import ValidationError

from api.schemas import (
    MAX_LINE_QTY,
    MAX_SCENARIO_QTY,
    CalculatePayload,
    entry_rule_problems,
    scenario_mass,
)


def _payload(current, alternative=None, **entry_extra):
    entry = {"sector": "processing", "current": current, **entry_extra}
    if alternative is not None:
        entry["alternative"] = alternative
    return {"entries": [entry]}


def test_decimal_strings_are_accepted():
    payload = CalculatePayload.model_validate(
        _payload([{"destination": "landfill", "qty_kg": "1.250"}])
    )
    assert payload.entries[0].current[0].qty_kg == Decimal("1.250")


def test_json_numbers_are_rejected_for_decimal_fields():
    with pytest.raises(ValidationError):
        CalculatePayload.model_validate(
            _payload([{"destination": "landfill", "qty_kg": 1.25}])
        )


@pytest.mark.parametrize("qty", ["-1", "50000001"])
def test_line_quantity_bounds(qty):
    with pytest.raises(ValidationError):
        CalculatePayload.model_validate(
            _payload([{"destination": "landfill", "qty_kg": qty}])
        )


# ---------------------------------------------------------------------------
# §6.2's two amount ceilings.
#
# **A test that asserts a refusal does not assert that the permitted case is
# permitted**, and for two years these bounds had only the refusal above.
# `MAX_SCENARIO_QTY` had no test at all, at any layer. Each bound below is
# asserted at the limit as well as past it, and the equality between them is
# asserted directly, because the ratio is the thing that was wrong.
# ---------------------------------------------------------------------------


def test_the_line_ceiling_and_the_scenario_ceiling_are_the_same_number():
    """v1.46. The line cap was a *fifth* of the scenario cap, which made "at
    least five destinations" a precondition of reaching the scenario ceiling —
    a rule nothing in the model asks for. One line may now be a whole scenario,
    which is the point of the change; a test on the two names is what keeps a
    later edit from reintroducing a ratio nobody chose."""
    assert MAX_LINE_QTY == MAX_SCENARIO_QTY == Decimal("50000000")


def test_a_line_exactly_at_the_ceiling_is_accepted():
    payload = CalculatePayload.model_validate(
        _payload([{"destination": "landfill", "qty_kg": "50000000.000"}])
    )
    assert payload.entries[0].current[0].qty_kg == MAX_LINE_QTY


def test_a_single_line_may_carry_an_entire_legal_scenario():
    """The reported case, at the unit layer.

    "All of our waste goes to animal feed" is an ordinary answer and a site
    with one destination has nothing to split across. Asserted at exactly
    `MAX_SCENARIO_QTY`: any smaller figure passed under the old bound too and
    would assert nothing.
    """
    payload = CalculatePayload.model_validate(
        _payload([{"destination": "animal_feed", "qty_kg": "50000000.000"}])
    )
    assert scenario_mass(payload.entries[0].current) == MAX_SCENARIO_QTY


def test_a_scenario_exactly_at_the_ceiling_is_accepted():
    """Spread across five lines — what the old ratio required of everybody."""
    payload = CalculatePayload.model_validate(
        _payload([
            {"destination": f"dest_{index}", "qty_kg": "10000000.000"}
            for index in range(5)
        ])
    )
    assert scenario_mass(payload.entries[0].current) == MAX_SCENARIO_QTY


def test_a_scenario_one_kilogram_over_the_ceiling_is_rejected():
    """Two lines, so that the scenario rule is what refuses it. A single line
    over the scenario cap is now also over the line cap, and a test that let
    the wrong rule answer would still be green with the scenario rule deleted.
    """
    with pytest.raises(ValidationError) as caught:
        CalculatePayload.model_validate(
            _payload([
                {"destination": "landfill", "qty_kg": "25000000.000"},
                {"destination": "animal_feed", "qty_kg": "25000001.000"},
            ])
        )
    assert "exceeds 50,000,000 kg" in str(caught.value)


def test_duplicate_destinations_are_rejected():
    with pytest.raises(ValidationError):
        CalculatePayload.model_validate(
            _payload(
                [
                    {"destination": "landfill", "qty_kg": "1"},
                    {"destination": "landfill", "qty_kg": "2"},
                ]
            )
        )


def test_quantity_must_fit_submission_decimal_scale():
    with pytest.raises(ValidationError):
        CalculatePayload.model_validate(
            _payload([{"destination": "landfill", "qty_kg": "0.0001"}])
        )


def test_a_scenario_failure_is_located_on_the_scenario_not_the_entry():
    """§9's `field` has to reach `entries[0].current`, which is why the
    per-scenario rules are an `AfterValidator` on the field rather than a
    `model_validator` on `EntryPayload`."""
    with pytest.raises(ValidationError) as raised:
        CalculatePayload.model_validate(
            _payload(
                [
                    {"destination": "landfill", "qty_kg": "1"},
                    {"destination": "landfill", "qty_kg": "2"},
                ]
            )
        )
    assert raised.value.errors()[0]["loc"] == ("entries", 0, "current")


def test_a_submission_token_may_be_any_string():
    """§6.2: a value that resolves to nothing is treated as absent. Typing
    the field `UUID4` turned a stale `sessionStorage` value from an earlier
    deployment into a 400 the user could not act on."""
    payload = CalculatePayload.model_validate(
        {
            "token": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
            **_payload([{"destination": "landfill", "qty_kg": "1"}]),
        }
    )
    assert payload.token == "6ba7b810-9dad-11d1-80b4-00c04fd430c8"


def test_mass_conservation_is_checked_only_where_an_alternative_exists():
    payload = CalculatePayload.model_validate(
        _payload([{"destination": "landfill", "qty_kg": "1200"}])
    )
    assert entry_rule_problems(payload, prevention_codes=()) == []


@pytest.mark.parametrize(
    ("alternative_qty", "expected"),
    [
        ("1000.000", True),
        ("1000.010", True),
        ("999.990", True),
        ("1000.011", False),
        ("999.989", False),
    ],
)
def test_the_mass_tolerance_is_ten_grams_inclusive_in_both_directions(
    alternative_qty, expected
):
    payload = CalculatePayload.model_validate(
        _payload(
            [{"destination": "landfill", "qty_kg": "1000.000"}],
            alternative=[{"destination": "compost", "qty_kg": alternative_qty}],
        )
    )
    assert (entry_rule_problems(payload, prevention_codes=()) == []) is expected
