"""`units.js` converts a container count in decimal, not in double (§7.3).

**The number this file exists for is `"1.668"`.** A quarter of the seeded 23 L
kerbside food scraps bin is `0.25 x 6.6700 = 1.6675 kg` — exactly, with no
recurring tail and nothing to round away. The nearest double to `6.67` is a
shade *below* it, so the product is a shade below `1.6675`, and `toFixed(3)`
reads the exact tie as a value under the half and answers `"1.667"`. That was
the previous body of `toKg` and it is wrong in the **third** decimal place, on
a real seeded preset, at a count a person would actually type.

So every assertion here is an equality against a decimal string this file
computes with `decimal.Decimal` — never against a float, and never against
`round()`, which is banker's rounding in Python and half-away-from-zero in
`units.js`. `test_the_conversion_matches_python_decimal` is the general form;
the two named cases above it are the ones a reader can check by hand.

Run under Node for the reason `test_entry_destinations.py` gives at length: a
test that greps `units.js` for `BigInt` asserts that a word was typed, not that
a product is right. Node is the runner only and does not enter the stack
(`docs/architecture.md` §3) — there is still no build step and no
`package.json`.

**Mutation record.** Each of these was applied to `web/js/units.js` and the
named test watched to fail:

===============================================  =============================
Mutation                                         Killed by
===============================================  =============================
`formatParts` rounds with `>` not `>=`           `test_a_quarter_bin_rounds_up`
`toKg` back to `Number(a) * Number(b)`           `test_a_quarter_bin_rounds_up`
`formatParts` truncates instead of rounding      `test_three_quarters_of_a_bin`
`containerKg` lets `toKg`'s throw escape         `test_an_unknown_preset_is_empty`
`entryTotal` ignores `measureMode`               `test_a_container_entry_is_in_kilograms`
`entryTotal` returns tonnes for a container      `test_a_container_entry_is_in_kilograms`
===============================================  =============================
"""

from __future__ import annotations

import json
import shutil
import subprocess
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
UNITS_JS = ROOT / "web" / "js" / "units.js"

node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is required to execute web/js/units.js; the conversion is unverified without it",
)

#: The shipped presets, read from the fixture rather than retyped, so a seed
#: change that `tests/api/test_fixture_consistency.py` propagates here reaches
#: this file too. §10.2: the fixtures are the executable form of the contract.
PRESETS = json.loads(
    (ROOT / "tests" / "fixtures" / "taxonomy.json").read_text(encoding="utf-8")
)["unit_presets"]

#: `units.js` imports nothing and touches no browser global, so the harness is
#: the import and a JSON round trip.
HARNESS = """
import { readFileSync, writeFileSync } from 'node:fs'
const units = await import(process.argv[2])
const calls = JSON.parse(readFileSync(process.argv[3], 'utf8'))
const answers = calls.map(call => {
  try {
    return { ok: units[call.fn](...call.args) }
  } catch (error) {
    return { error: error.message }
  }
})
writeFileSync(process.argv[4], JSON.stringify(answers), 'utf8')
"""


@pytest.fixture(scope="module")
def call(tmp_path_factory):
    """Run a batch of `units.js` calls in one Node process."""
    directory = tmp_path_factory.mktemp("units")
    harness = directory / "harness.mjs"
    harness.write_text(HARNESS, encoding="utf-8")

    def run(*calls: tuple[str, list]) -> list[dict]:
        payload = directory / "calls.json"
        payload.write_text(
            json.dumps([{"fn": fn, "args": args} for fn, args in calls]),
            encoding="utf-8",
        )
        out = directory / "answers.json"
        completed = subprocess.run(
            [shutil.which("node"), str(harness), UNITS_JS.as_uri(),
             str(payload), str(out)],
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
        return json.loads(out.read_text(encoding="utf-8"))

    return run


def to_kg(call, count: str, code: str) -> dict:
    return call(("toKg", [count, code, PRESETS]))[0]


def kg_per_unit(code: str) -> Decimal:
    return Decimal(
        next(row["kg_per_unit"] for row in PRESETS if row["code"] == code)
    )


@node
def test_a_quarter_bin_rounds_up(call):
    """0.25 x 6.6700 = 1.6675 exactly, and 1.6675 rounds to 1.668.

    The double nearest 6.67 is below it, so a float implementation answers
    1.667 — a milligram, and the whole reason `qty_kg` is a DECIMAL.
    """
    assert kg_per_unit("food_scraps_bin_23l") == Decimal("6.6700")
    assert Decimal("0.25") * kg_per_unit("food_scraps_bin_23l") == Decimal("1.6675")
    assert to_kg(call, "0.25", "food_scraps_bin_23l") == {"ok": "1.668"}


@node
def test_three_quarters_of_a_bin(call):
    """0.75 x 6.6700 = 5.0025, which rounds to 5.003 and truncates to 5.002."""
    assert Decimal("0.75") * kg_per_unit("food_scraps_bin_23l") == Decimal("5.0025")
    assert to_kg(call, "0.75", "food_scraps_bin_23l") == {"ok": "5.003"}


@node
@pytest.mark.parametrize("count", ["0.01", "0.5", "1", "2", "3.33", "7.25", "10000"])
def test_the_conversion_matches_python_decimal(call, count):
    """Every shipped preset, at seven counts, against `Decimal`.

    Not against a float and not against `round()`: Python's `round` is
    banker's rounding, `units.js` rounds half away from zero, and the two
    disagree on exactly the ties this test is here to pin.
    """
    answers = call(*[("toKg", [count, row["code"], PRESETS]) for row in PRESETS])
    for row, answer in zip(PRESETS, answers):
        expected = (Decimal(count) * Decimal(row["kg_per_unit"])).quantize(
            Decimal("0.001"), rounding=ROUND_HALF_UP
        )
        assert answer == {"ok": f"{expected:.3f}"}, row["code"]


@node
def test_the_result_always_has_the_three_decimals_the_api_wants(call):
    """§6.2 refuses a fourth decimal place, and v0.14 §4 records why this is a
    live hazard: `kg_per_unit` is `DECIMAL(12,4)` and a non-integer count lands
    on six. Every answer is quantised to three before it leaves this module."""
    answers = call(*[("toKg", ["1.01", row["code"], PRESETS]) for row in PRESETS])
    for row, answer in zip(PRESETS, answers):
        _whole, _, fraction = answer["ok"].partition(".")
        assert len(fraction) == 3, (row["code"], answer)


@node
def test_an_unknown_preset_is_empty_rather_than_a_throw(call):
    """`containerKg` is called inside a render, so it may not throw.

    §6.1: a consumer must not assume the taxonomy is stable across a publish,
    and this page holds a preset chosen before one. `toKg` throwing is correct
    for `toKg`; a blank screen is not correct for the calculator.
    """
    entry = {"measureMode": "container", "unitPreset": "gone", "unitCount": "2"}
    assert call(("containerKg", [entry, PRESETS]))[0] == {"ok": ""}
    assert "error" in call(("toKg", ["2", "gone", PRESETS]))[0]


@node
@pytest.mark.parametrize("count", ["", "1.", "abc", "-2", "1e3"])
def test_a_count_that_is_not_a_decimal_has_no_total(call, count):
    """Half-typed and malformed alike: no total, no throw, no NaN reaching a
    figure. `1e3` is refused with the rest — a number input does not produce
    it, and admitting exponents admits `Infinity` by the same door."""
    entry = {"measureMode": "container", "unitPreset": "wheelie_bin_240l",
             "unitCount": count}
    assert call(("containerKg", [entry, PRESETS]))[0] == {"ok": ""}


@node
def test_a_container_entry_is_in_kilograms(call):
    """§6.2's mass conservation compares step 3's total with step 4's
    allocation, so both have to be in one unit — and a destination row reading
    "0.37 wheelie bins" is not enterable. Two 240 L bins is 139.200 kg, which
    is exactly what a visitor typing 139.2 kilograms would have sent."""
    container = {"measureMode": "container", "unitPreset": "wheelie_bin_240l",
                 "unitCount": "2", "totalAmount": "", "totalUnit": "kilograms"}
    typed = {"measureMode": "mass", "totalAmount": "139.2", "totalUnit": "kilograms"}
    answers = call(("entryTotal", [container, PRESETS]),
                   ("entryTotal", [typed, PRESETS]))
    assert answers[0] == {"ok": {"amount": "139.200", "unit": "kilograms"}}
    assert Decimal(answers[0]["ok"]["amount"]) == Decimal(answers[1]["ok"]["amount"])


@node
def test_a_mass_entry_is_untouched(call):
    """The other half of the same function: an entry that is not in container
    mode answers exactly the field the visitor typed in, in its own unit."""
    entry = {"measureMode": "mass", "totalAmount": "12.5", "totalUnit": "tonnes",
             "unitPreset": "wheelie_bin_240l", "unitCount": "9"}
    assert call(("entryTotal", [entry, PRESETS]))[0] == {
        "ok": {"amount": "12.5", "unit": "tonnes"}
    }
