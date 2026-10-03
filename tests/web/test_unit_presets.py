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
named test watched to fail. All seven were killed:

===============================================  =====================================
Mutation                                         Killed by
===============================================  =====================================
`formatParts` rounds with `>` not `>=`           `test_a_quarter_bin_rounds_up`
`toKg` back to `Number(a) * Number(b)`           `test_a_quarter_bin_rounds_up`
`formatParts` truncates instead of rounding      `test_three_quarters_of_a_bin`
`containerKg` lets `toKg`'s throw escape         `..._an_unknown_preset_is_empty_...`
`entryTotal` ignores `measureMode`               `test_a_container_entry_is_in_kilograms`
`entryTotal` returns tonnes for a container      `test_a_container_entry_is_in_kilograms`
`DECIMAL_LITERAL` admits a sign again            `..._not_a_decimal_has_no_total[-2]`
===============================================  =====================================

The last row is not hypothetical tightening. `DECIMAL_LITERAL` was written
`/^[+-]?\\d+(\\.\\d+)?$/` and this file's `-2` case is what found it: a
`<input type="number">` hands over `"-2"` quite happily, and `containerKg`
answered `-139.200` kg.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

from tests.web.base_url import CALCULATOR
from tests.web.steps import press_continue


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


# ------------------------------------------------- the food-category filter
#
# `unit_preset.food_category_id` is nullable, and §2.1 says NULL means "applies
# to every category". What a **non-NULL** one means is per-food density: a bin
# of bread and a bin of potatoes do not weigh the same, so a preset naming a
# category is a conversion that is only true of that category.
#
# **No shipped preset names one**, because no measured per-food density exists
# (O-6) and inventing one is the move that item forbids. So nothing in a browser
# can exercise this filter — which is exactly why it is exercised here, against
# a taxonomy this file builds. A rule with no test is a rule that will be
# deleted by the next person who reads the seed and sees ten NULLs.

CALCULATOR_HARNESS = """
globalThis.window = { location: { search: '' } }
globalThis.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} }

import { readFileSync, writeFileSync } from 'node:fs'
const { containerPresets } = await import(process.argv[2])
const { state } = await import(process.argv[3])
const input = JSON.parse(readFileSync(process.argv[4], 'utf8'))
state.taxonomy = { unit_presets: input.presets }
// **The category of the CONTROL the list is offered on, not of the chain.** Since the
// fork a chain may name several categories at once, so there is no one chain category to
// filter against; a leaf's own unit select passes that leaf's category, and a step-4
// destination row - shared by no single food - passes nothing.
writeFileSync(process.argv[5], JSON.stringify(containerPresets(input.foodCategory).map(p => p.code)), 'utf8')
"""

#: Two generic containers and two that are only true of one food each.
MIXED_PRESETS = [
    {"code": "wheelie_bin_240l", "food_category": None, "kg_per_unit": "69.6000"},
    {"code": "bucket_20l_full", "food_category": None, "kg_per_unit": "5.8000"},
    {"code": "bread_crate_bakery", "food_category": "bakery_grains", "kg_per_unit": "4.1000"},
    {"code": "spud_bin_vegetables", "food_category": "vegetables", "kg_per_unit": "31.5000"},
]


def offered(tmp_path, food_category, presets=None):
    harness = tmp_path / "calculator_harness.mjs"
    harness.write_text(CALCULATOR_HARNESS, encoding="utf-8")
    payload = tmp_path / "input.json"
    payload.write_text(
        json.dumps({"presets": MIXED_PRESETS if presets is None else presets,
                    "foodCategory": food_category}),
        encoding="utf-8",
    )
    out = tmp_path / "codes.json"
    completed = subprocess.run(
        [shutil.which("node"), str(harness),
         (ROOT / "web" / "js" / "calculator.js").as_uri(),
         (ROOT / "web" / "js" / "state.js").as_uri(),
         str(payload), str(out)],
        capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(out.read_text(encoding="utf-8"))


@node
def test_a_preset_with_no_food_category_is_always_offered(tmp_path):
    """NULL is "a wheelie bin is a wheelie bin" and shows whatever step 2 said —
    including when step 2 was skipped, which is the normal case."""
    for category in (None, "vegetables", "dairy"):
        codes = offered(tmp_path, category)
        assert "wheelie_bin_240l" in codes, category
        assert "bucket_20l_full" in codes, category


@node
def test_a_preset_naming_a_category_appears_only_for_that_category(tmp_path):
    """The whole point of the column. Offering a bread crate's conversion to
    somebody weighing potatoes applies a density that is not true of what they
    have — silently, since the form would show a plausible number either way."""
    assert offered(tmp_path, "bakery_grains") == [
        "wheelie_bin_240l", "bucket_20l_full", "bread_crate_bakery",
    ]
    assert offered(tmp_path, "vegetables") == [
        "wheelie_bin_240l", "bucket_20l_full", "spud_bin_vegetables",
    ]


@node
def test_a_control_that_belongs_to_no_one_food_is_offered_the_generic_ones_only(tmp_path):
    """**The rule the fork made necessary, stated as a test.**

    Before the fork there was one food category per chain and one list. Now a leaf's
    own unit select on step 3 is about one food and gets that food's presets, while a
    step-4 destination row belongs to a leaf whose category may be any of several - and
    a container measured against one food is a density that is not true of the others.
    `containerPresets()` with no argument is that control's list, and it must hold
    nothing but the NULL-category presets.

    The union rule - offer every selected category's presets everywhere - is the wrong
    answer for exactly this reason, and it would also let a visitor pick the dairy crate
    for the bakery leaf.
    """
    assert offered(tmp_path, None) == ["wheelie_bin_240l", "bucket_20l_full"]


@node
def test_skipping_the_food_step_offers_the_generic_containers_alone(tmp_path):
    """Step 2 is optional (§6.2 resolves a null `food_category` to the standard
    mix), and a visitor who skipped it has not told the calculator which food
    this is. Offering a bread crate then would be guessing on their behalf."""
    assert offered(tmp_path, None) == ["wheelie_bin_240l", "bucket_20l_full"]


@node
def test_the_served_order_is_preserved_and_not_re_sorted(tmp_path):
    """§6.1 orders the presets smallest first and the form renders that order as
    given. Re-sorting in the browser could only disagree with the server, and
    would mean `Number()` on an API decimal for something that is not display."""
    reversed_presets = list(reversed(MIXED_PRESETS))
    assert offered(tmp_path, "vegetables", reversed_presets) == [
        "spud_bin_vegetables", "bucket_20l_full", "wheelie_bin_240l",
    ]


# --------------------------------------------------------- item ⑥: a unit per row
#
# Driven in a real browser against a running stack, for the reason every other browser
# file in this directory gives: a test that greps the markup for `data-line-field="unit"`
# asserts that a `<select>` was typed, not that a visitor can use it to say one row is in
# tonnes and another is in kilograms. `test_container_input_browser.py`'s and
# `test_step_navigation.py`'s `page_at`/`browser` fixtures are not imported from here —
# every browser file in this project is self-contained, run one at a time, against its own
# fresh page — and this section follows that convention rather than reaching across files.

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to drive the per-row unit select; it is unverified without it",
)

WEB_BASE = CALCULATOR

#: `html { scroll-behavior: smooth }` otherwise animates `scrollTo`, which nothing here
#: measures — but `test_step_navigation.py`'s note that a mid-animation read can misfire
#: is a reason to disable it everywhere it is not the thing under test, not only where it is.
FORCE_AUTO = "html { scroll-behavior: auto !important; }"


@pytest.fixture
def page_at(browser):
    """A page at a given viewport, English, on the introduction screen."""
    contexts = []

    def open_page(width, height, dpr=1.0):
        ctx = browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=dpr,
            locale="en-NZ",
            bypass_csp=True,
        )
        contexts.append(ctx)
        page = ctx.new_page()
        url = WEB_BASE + ("&" if "?" in WEB_BASE else "?") + "lang=en"
        try:
            page.goto(url, wait_until="networkidle", timeout=20000)
        except Exception as error:  # pragma: no cover - environment guard
            pytest.skip(f"the front end is not being served at {WEB_BASE}: {error}")
        page.add_style_tag(content=FORCE_AUTO)
        page.click('[data-action="start"]')
        page.wait_for_selector('input[name="sector"]', timeout=10000)
        return page

    yield open_page
    for ctx in contexts:
        ctx.close()


def to_amount_step(page):
    """Walk to step 3 (`#total-waste`, `#total-unit`) and stop.

    **Not `advance_to(page, 3)`.** `tests/web/test_step_navigation.py`'s `walk()` yields
    index 2 on arrival at this screen and index 3 on arrival at the *next* one, the
    destination-allocation screen this file's tests actually want to reach from — the
    amount step's own defect. This helper is `test_container_input_browser.py`'s
    `to_amount_step`, transcribed rather than counted past.
    """
    page.wait_for_selector('input[name="sector"]')
    page.evaluate("document.querySelector('input[name=sector]').click()")
    page.wait_for_timeout(60)
    press_continue(page)
    page.wait_for_selector('input[name="food-category"]')
    press_continue(page)
    page.wait_for_selector("#total-waste")
    return page


def test_each_destination_row_carries_its_own_unit(page_at):
    """Item ⑥. Every row currently borrows `state.totalUnit`, so a visitor
    who measured one destination in tonnes and another in buckets cannot say
    so.

    **No contract change.** §7.6.1 gives the front end exactly one
    calculation - unit conversion in `units.js` - and this is that. The wire
    still carries `qty_kg` and the API never learns a unit was chosen.
    """
    page = to_amount_step(page_at(1278, 983, 1.25))
    page.fill("#total-waste", "5")
    page.select_option("#total-unit", "tonnes")
    press_continue(page)
    page.wait_for_selector(".destination-row")

    rows = page.locator(".destination-row")
    assert rows.count() >= 2, "need two rows to tell a per-row unit from a shared one"

    selects = page.locator('.destination-row select[data-line-field="unit"]')
    assert selects.count() == rows.count(), (
        "not every destination row has its own unit selector"
    )

    #: Each row starts on the unit chosen for the entry, so a visitor who
    #: wants one unit throughout types nothing extra.
    assert selects.nth(0).input_value() == "tonnes"


def test_changing_one_row_s_unit_does_not_change_the_others(page_at):
    """The assertion that distinguishes a per-row control from a shared one
    wearing several hats. Without it, a single `state.totalUnit` rendered
    N times passes every other test here."""
    page = to_amount_step(page_at(1278, 983, 1.25))
    page.fill("#total-waste", "5")
    page.select_option("#total-unit", "tonnes")
    press_continue(page)
    page.wait_for_selector(".destination-row")

    selects = page.locator('.destination-row select[data-line-field="unit"]')
    selects.nth(0).select_option("kilograms")

    assert selects.nth(0).input_value() == "kilograms"
    assert selects.nth(1).input_value() == "tonnes", (
        "changing one row's unit changed another's - the state is still shared"
    )

    #: The native `<select>`'s own value above is unbinding-blind: Playwright's
    #: `select_option` sets it whether or not any `change` handler ever runs, and
    #: nothing re-renders to contradict it. `rowUnitLabel(rowUnit)` only reaches the
    #: page through `state.current[i].unit` and `destinationRows()`'s own read of it,
    #: so the amount input's `aria-label` is a state-derived witness a DOM-only
    #: mutation cannot fake.
    amounts = page.locator('.destination-row input[data-line-field="amount"]')
    assert "kilograms" in (amounts.nth(0).get_attribute("aria-label") or ""), (
        "row 0's aria-label still names the old unit - the change did not reach state"
    )
    assert "tonnes" in (amounts.nth(1).get_attribute("aria-label") or ""), (
        "row 1's aria-label changed too - the state is still shared"
    )


def test_the_review_step_shows_each_row_in_the_unit_it_was_typed_in(page_at):
    """And the kilograms beside it, which is what actually goes on the wire.

    Showing only the typed figure would hide the conversion at the one moment
    a visitor can still check it; showing only kilograms would throw away
    what they typed.
    """
    page = to_amount_step(page_at(1278, 983, 1.25))
    page.fill("#total-waste", "5")
    page.select_option("#total-unit", "tonnes")
    press_continue(page)
    page.wait_for_selector(".destination-row")

    page.locator('.destination-row select[data-line-field="unit"]').nth(0).select_option("kilograms")
    page.locator('.destination-row input[data-line-field="amount"]').nth(0).fill("250")
    press_continue(page)
    page.wait_for_selector(".review-destinations")

    #: An exact pair in one `dd`, not a substring scan of the whole section. `"250" in
    #: text` matches `"250000.000 kg"` just as happily as `"250.000 kg"` - it is the
    #: assertion that let the pure-conversion mutation (`line.unit` replaced by
    #: `state.totalUnit`) through in review: 250 *tonnes* misreported as "250.00
    #: kilograms" beside a correct-looking but wrong "(250000.000 kg)".
    dds = page.locator(".review-destinations dd").all_inner_texts()
    assert any("250.00 kilograms" in text and "(250.000 kg)" in text for text in dds), dds


def test_two_rows_in_different_units_convert_to_different_kilograms(page_at):
    """The test that actually tells a per-row unit from the behaviour it replaces.

    One row of "5" in kilograms and one row of "5" in tonnes must reach two
    different kilogram figures on review — a single shared unit, or a select
    that renders without binding to state, both collapse this to one figure
    repeated twice.
    """
    page = to_amount_step(page_at(1278, 983, 1.25))
    page.fill("#total-waste", "6000")
    page.select_option("#total-unit", "kilograms")
    press_continue(page)
    page.wait_for_selector(".destination-row")

    amounts = page.locator('.destination-row input[data-line-field="amount"]')
    units = page.locator('.destination-row select[data-line-field="unit"]')
    amounts.nth(0).fill("5")
    units.nth(0).select_option("kilograms")
    amounts.nth(1).fill("5")
    units.nth(1).select_option("tonnes")
    page.wait_for_timeout(80)

    press_continue(page)
    page.wait_for_selector(".review-destinations")
    lines = page.locator(".review-destinations dd").all_inner_texts()

    #: `formatNumber` is locale-formatted (`en-NZ`), so 5,000 kilograms prints with a
    #: thousands separator - the same figure `test_container_input_browser.py` checks for
    #: German and Arabic. The two rows must disagree, not merely both be present.
    assert any("(5.000 kg)" in text for text in lines), lines
    assert any("5,000.000 kg" in text for text in lines), lines


@pytest.mark.parametrize("width", [390, 700])
def test_the_amount_input_stays_usable_beside_the_unit_select(page_at, width):
    """Item ⑥'s `<select>` sits in the same two-column row `.amount-with-unit` always
    had, and a track that lets the select claim the whole row leaves the amount input
    a number field nobody can type into.

    **`count() == 1` and a value assertion both pass at 28px.** Neither measures a
    dimension, and 28px is worse than merely cramped - `test_amount_limits_browser.py`
    and this file's other assertions never fail on it, because nothing here reads a
    box. `.destination-row`'s narrower single-column layout applies at 480px and
    below, so 390 and 700 are the two widths that exercise, respectively, the stacked
    and the side-by-side `.amount-with-unit` template.
    """
    page = to_amount_step(page_at(width, 800))
    page.fill("#total-waste", "1000")
    press_continue(page)
    page.wait_for_selector('.destination-row input[data-line-field="amount"]')

    box = page.locator('.destination-row input[data-line-field="amount"]').first.bounding_box()
    assert box is not None and box["width"] >= 80, (
        f"the amount input is {box['width'] if box else None}px wide at {width}px - "
        "too narrow to type an amount into"
    )
