"""The reporting period labels a calculation. It never enters one.

Contract §2.3 and §6.2: *"Optional — it only labels your figures, it never
changes a result."* That is the field's own hint, shown to the visitor on step
5 of the calculator, and v1.67 has to leave it true.

**v1.67 is the revision that makes this worth a test file of its own.** Until
now the period was one short string, `time_frame`, and the worst arithmetic
anybody could do with it was to look up a multiplier. From v1.67 it is a pair
of instants, and a pair of instants is exactly what it takes to write
`(period_end - period_start)` against a metric total and annualise a figure the
client ruled must not be annualised. §2.3 records that rejection twice over;
this file is what holds it.

The route taken is the cheap one and the reliable one: **the engine is never
handed the values at all.** `api/router.py` passes them to
`db.repository.upsert_submission` as keyword arguments of their own, beside
`time_frame`, and never onto the §3 `CalculationRequest` the engine also
consumes. A value the engine cannot reach is a value no formula can read, and
no reviewer has to check that nobody looked.

Modelled on `tests/test_item_level_inertness.py`, which holds the same
property for `item_level_enabled` and explains the shape. No database and no
marker: this reads source.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from engine.bundle import REQUIRED_KEYS, FactorBundle
from engine.types import CalculationRequest

_ROOT = Path(__file__).resolve().parent.parent

#: Every name the period travels under. `time_frame` is here as well as the
#: two new columns: it has had this property since v1.48 and nothing asserted
#: it outside `tests/api/test_engine_adapter.py`'s single `hasattr` check.
_PERIOD_FIELDS = ("period_start", "period_end", "time_frame")


def test_the_period_is_not_a_field_of_the_request():
    """§3's `CalculationRequest` is what `calculate(request, bundle)` takes.

    `tests/api/test_engine_adapter.py` proves the *adapter* does not put
    `time_frame` on the object it builds. This proves the object has nowhere
    to put any of the three even if an adapter tried -- which is the stronger
    statement, and the one that survives somebody writing a second adapter.
    """
    fields = {f.name for f in dataclasses.fields(CalculationRequest)}
    assert fields.isdisjoint(_PERIOD_FIELDS), (
        "the engine is a pure function of (request, bundle) and the period is "
        "neither an input nor a factor: "
        f"{sorted(fields & set(_PERIOD_FIELDS))}"
    )


def test_the_period_is_not_a_bundle_key_or_a_bundle_field():
    """The other half of the engine's two arguments. A period is not a factor
    and §10.2's bundle shape must not learn one."""
    assert set(REQUIRED_KEYS).isdisjoint(_PERIOD_FIELDS)
    assert {f.name for f in dataclasses.fields(FactorBundle)}.isdisjoint(
        _PERIOD_FIELDS
    )


def test_no_module_under_engine_mentions_the_period():
    """The generic form the two assertions above cannot give: the period must
    not reach the evaluator, the calculator, the formatter or the types
    either, under any name they might carry it as.

    This is the assertion that would fail first if somebody added an
    `annualise` step, because such a step has to name one of these three
    somewhere in `engine/` to read it.
    """
    offenders = sorted(
        f"{path.relative_to(_ROOT)} ({field})"
        for path in (_ROOT / "engine").rglob("*.py")
        for field in _PERIOD_FIELDS
        if field in path.read_text(encoding="utf-8")
    )
    assert offenders == [], (
        "the period labels a calculation and never enters one (§2.3); "
        f"engine/ now mentions it: {offenders}"
    )


def test_no_golden_case_carries_the_period():
    """The golden suite is the only evidence the calculator computes
    correctly (§10.1), and v1.67 must leave every case byte-identical.

    All three files per case, not just the bundle: a `request.json` carrying
    an interval would mean the suite had agreed to a request shape §3 does
    not have, and an `expected.json` carrying one would mean a result had
    started reporting a label the engine is not given.
    """
    offenders = sorted(
        f"{path.relative_to(_ROOT)} ({field})"
        for path in (_ROOT / "tests" / "golden").rglob("*.json")
        for field in _PERIOD_FIELDS
        if field in path.read_text(encoding="utf-8")
    )
    assert offenders == [], (
        f"the golden suite has learned the period: {offenders}"
    )
