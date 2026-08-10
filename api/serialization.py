"""Deterministic JSON-safe conversion for repository DTOs."""

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any


def wire(value: Any) -> Any:
    if is_dataclass(value):
        return wire(asdict(value))
    if isinstance(value, Decimal):
        # `format(..., "f")` rather than `str()`. §1.2 says a decimal travels
        # as a decimal string, and `str(Decimal("0E-10"))` is `"0E-10"` --
        # scientific notation, which is a well-formed JSON string of the
        # right type in the right key and therefore invisible to every shape
        # check in the tree. It is not a rare case: an exact zero quantised
        # to the contracted ten places is what *every* `prevention` line
        # produces (open item O-7), so the calculator's flagship result was
        # the one that carried it. `Number("0E-10")` happens to be 0, but the
        # figure reaches a report and a CSV export as text.
        return format(value, "f")
    if isinstance(value, datetime):
        aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        return aware.isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: wire(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [wire(item) for item in value]
    return value

