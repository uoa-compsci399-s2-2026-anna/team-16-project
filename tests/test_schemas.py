from decimal import Decimal

import pytest
from pydantic import ValidationError

from api.schemas import CalculatePayload


def test_decimal_strings_are_accepted():
    payload = CalculatePayload.model_validate(
        {"sector": "processing", "current": [{"destination": "landfill", "qty_kg": "1.250"}]}
    )
    assert payload.current[0].qty_kg == Decimal("1.250")


def test_json_numbers_are_rejected_for_decimal_fields():
    with pytest.raises(ValidationError):
        CalculatePayload.model_validate(
            {"sector": "processing", "current": [{"destination": "landfill", "qty_kg": 1.25}]}
        )


@pytest.mark.parametrize("qty", ["-1", "10000001"])
def test_line_quantity_bounds(qty):
    with pytest.raises(ValidationError):
        CalculatePayload.model_validate(
            {"sector": "processing", "current": [{"destination": "landfill", "qty_kg": qty}]}
        )


def test_duplicate_destinations_are_rejected():
    with pytest.raises(ValidationError):
        CalculatePayload.model_validate(
            {
                "sector": "processing",
                "current": [
                    {"destination": "landfill", "qty_kg": "1"},
                    {"destination": "landfill", "qty_kg": "2"},
                ],
            }
        )


def test_quantity_must_fit_submission_decimal_scale():
    with pytest.raises(ValidationError):
        CalculatePayload.model_validate(
            {
                "sector": "processing",
                "current": [
                    {"destination": "landfill", "qty_kg": "0.0001"}
                ],
            }
        )


def test_submission_token_must_be_uuid4():
    with pytest.raises(ValidationError):
        CalculatePayload.model_validate(
            {
                "token": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                "sector": "processing",
                "current": [{"destination": "landfill", "qty_kg": "1"}],
            }
        )
