from decimal import Decimal

import pytest
from pydantic import ValidationError

from api.schemas import CalculatePayload, entry_rule_problems


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


@pytest.mark.parametrize("qty", ["-1", "10000001"])
def test_line_quantity_bounds(qty):
    with pytest.raises(ValidationError):
        CalculatePayload.model_validate(
            _payload([{"destination": "landfill", "qty_kg": qty}])
        )


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
