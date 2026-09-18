"""`item_level_enabled` releases step 2.5. It never reaches the engine.

Design §3: *not a `FactorBundle` field, not a `bundle.json` key, not an
argument to `calculate`.* That is what keeps reproducibility free — a flag
flipped in either direction changes no stored figure, so a submission
recomputed years later under a different flag returns the same numbers.

The temptation this guards against is real and cheap to give in to: the flag
sits on `factor_set`, every other column of which the bundle loader reads, and
`load_factor_bundle` is the obvious place to carry it to the front end. The
moment it is in the bundle it is an input to a pure function, and §4.1's
guarantee that the engine is reproducible from `bundle.json` alone stops being
true.

No database and no marker: this reads source.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from engine.bundle import REQUIRED_KEYS, FactorBundle

_ROOT = Path(__file__).resolve().parent.parent
_FLAG = "item_level_enabled"


def test_the_switch_is_not_a_bundle_key():
    assert _FLAG not in REQUIRED_KEYS, (
        "item_level_enabled has become part of §10.2's bundle shape. It "
        "releases a step of the interface; it is not a factor."
    )


def test_the_switch_is_not_a_field_of_the_bundle():
    assert _FLAG not in {f.name for f in dataclasses.fields(FactorBundle)}


def test_no_module_under_engine_mentions_the_switch():
    """The generic form of the two assertions above, which they cannot give:
    the flag must not reach the evaluator, the calculator or the types either,
    under any name they might carry it as."""
    offenders = sorted(
        str(path.relative_to(_ROOT))
        for path in (_ROOT / "engine").rglob("*.py")
        if _FLAG in path.read_text(encoding="utf-8")
    )
    assert offenders == [], (
        "the engine is a pure function of (request, bundle) and "
        f"{_FLAG} is neither: {offenders}"
    )


def test_no_golden_bundle_carries_the_switch():
    """The golden suite is the only evidence the calculator computes correctly
    (§10.1) and every case is three files handed to the engine. A bundle that
    carried the flag would make the suite agree with a shape §4.1 forbids."""
    offenders = sorted(
        str(path.relative_to(_ROOT))
        for path in (_ROOT / "tests" / "golden").rglob("bundle.json")
        if _FLAG in path.read_text(encoding="utf-8")
    )
    assert offenders == []
