"""Step 3 said two things that were false about the model behind it (#158, #159).

**Neither was a style complaint. Each named the wrong object.**

*#158, the production total.* The hint read *"Everything that went through this
stage over the same period, waste included."* A **stage** is the supply-chain
stage — the whole chain — and `total_input_kg` is a **per-leaf** field:
`leafPanel` draws one panel per leaf, each panel's box becomes that leaf's own
`EntryInput.total_input_kg`, and `engine/calculate.py` sums them for
`totals.production_share_percent` (§4.6). A visitor with three food types who
followed the sentence literally entered the whole stage's throughput three
times, so the denominator came out three times too large and the waste share
three times too small — and that share is printed on the results page, which is
what made it worse than the two money hints beside it (v1.85 fixed those, and
§7.3a's callout recorded this one rather than widening that PR).

*#159, the shared statistics-only tooltip.* It ended *"No figure on the results
page is calculated from it."* The results page prints **Share of value wasted**,
and §4.5 computes it as `wasted_value_nzd ÷ total_value_nzd × 100` — from
exactly the two boxes the tooltip sits on. The true claim, and the one the
client actually ruled on, is **O-2**: these figures do not enter the emissions
calculation and they are not the cost metric (`cost` is the waste levy plus
disposal cost and `const_FOOD_VALUE_PER_KG` stays at zero, contract v1.48).

**Why this file runs the front end under Node rather than reading its source.**
A test that greps `calculator.js` for a sentence asserts that a sentence was
typed. What has to be asserted is which *field* carries which sentence, at a
leaf count of three as well as of one, and `render()` is exported — so a stub
`main` whose `innerHTML` is just a string, plus `tests/fixtures/taxonomy.json`,
evaluates the tree `leafPanel` actually emits. It is also what makes the
measurement in #158 possible at all: three cards, read back one by one.

**The assertion trap this file is written around.** v1.85's own round found that
`"total value" in hint` was satisfied by **both** money hints, so swapping the
two left the test green — each was bound to the right element and neither
pinned *which* field's sentence it had read. The same trap is here twice: "for
this food type" is now in three hints, and the tooltip's new sentence shares
words with the hints beside it. Every pin below is therefore an **equality** on
the rendered text, or a phrase only that one element says.

**And the trap that is particular to #159**: a test that greps the tooltip's own
text passes whatever the sentence says. So the tooltip's claim is tied to what
`results.js` actually prints — `buildResultsReport` is run over the canonical
fixture in the **same** Node process, and the figure the tooltip must name is
read out of that report rather than written down here twice. If the results page
stops printing a figure derived from these two boxes, this file fails and says
the tooltip's claim needs revisiting; if the tooltip goes back to denying it,
this file fails too.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WEB_JS = ROOT / "web" / "js"
FIXTURES = ROOT / "tests" / "fixtures"

node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is required to execute web/js/; these sentences are unverified without it",
)

#: The three food categories of the three-card measurement. Codes out of
#: `tests/fixtures/taxonomy.json`, which §10 makes the executable form of the
#: contract - a hand-written taxonomy would let this file drift from the one
#: the browser is served.
LEAVES = ("dairy", "bakery_grains", "fruit")

#: **The sentences, in full, because equality is the pin.** A substring would be
#: satisfied by the hint one field along; see the module docstring.
PRODUCTION_TOTAL_HINT = (
    "Enter the total amount you produced for this food type over the same "
    "period, waste included."
)
PRODUCTION_VALUE_HINT = (
    "Enter the total value of everything you produced for this food type over "
    "the reporting period, not the value per unit."
)
WASTED_VALUE_HINT = (
    "Enter the total value of the waste for this food type over the same "
    "period, valued the same way as production above."
)
STATISTICS_ONLY_TIP = (
    "It joins the anonymous statistics, and the two together give the share of "
    "value wasted on the results page. No emissions or cost figure is "
    "calculated from it."
)

#: The one results-page figure §4.5 derives from the two NZ$ boxes, by the label
#: `moneySummary` and `moneyLines` print. **Not written down here as the thing
#: being asserted** - the assertion reads it out of the rendered report and only
#: then asks the tooltip to name it.
MONEY_DERIVED_RESULT_LABEL = "Share of value wasted"

#: The claim #159 is about, as a phrase. This is the weaker half of that test on
#: purpose: the positive tie above is what fails when the sentence reverts, and
#: this catches the one shape the positive tie cannot - a sentence that names the
#: figure and denies it in the same breath.
DENIAL = "no figure on the results page"

#: `calculator.js` and `results.js` both import `api.js` / `state.js`, which read
#: these two browser globals at module load. Nothing under test touches either.
#: `render()` writes a string into `main.innerHTML`; `bindResultsSectionSpy` is
#: called on every exit and is a no-op here - `root.querySelector` is absent, so
#: it takes its `!sections.length` return, and `bindEquivalenceOverlay` returns on
#: `typeof document === 'undefined'`.
HARNESS = """
globalThis.window = { location: { search: '' } }
globalThis.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} }

import { readFileSync, writeFileSync } from 'node:fs'
const [, , jsDir, taxonomyPath, resultStatePath, outPath, ...categories] = process.argv
const { render } = await import(jsDir + 'calculator.js')
const { buildResultsReport } = await import(jsDir + 'results.js')
const { state } = await import(jsDir + 'state.js')

const taxonomy = JSON.parse(readFileSync(taxonomyPath, 'utf8'))

function stepThree(foodCategories) {
  state.taxonomy = taxonomy
  state.step = 2
  state.sector = 'processing'
  state.foodCategories = foodCategories
  state.foodItems = {}
  const main = { className: '', innerHTML: '' }
  render(main)
  return main.innerHTML
}

const many = stepThree(categories)
const one = stepThree([categories[0]])
const report = buildResultsReport(JSON.parse(readFileSync(resultStatePath, 'utf8')))
writeFileSync(outPath, JSON.stringify({ many, one, report }), 'utf8')
"""


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _results_state() -> dict:
    """`build_state()` of `test_results_export.py`, in the one shape this file
    needs: the canonical response, whose `money` block is `complete` in all four
    fields and whose `wasted_share_percent` is `"21.50"` - so the report really
    does print the figure the tooltip has to name. Assembled through the same
    pairing `state.js`'s `entryResultsFrom` performs."""
    response = _fixture("calculate_response.json")
    entries = [
        {"sector": "processing", "foodCategory": "dairy", "totalAmount": "1500",
         "totalUnit": "kilograms",
         "current": [{"id": "a", "destination": "landfill", "qtyInput": "1200"},
                     {"id": "b", "destination": "animal_feed", "qtyInput": "300"}]},
        {"sector": "primary_production", "foodCategory": "vegetables",
         "totalAmount": "800", "totalUnit": "kilograms",
         "current": [{"id": "d", "destination": "not_harvested", "qtyInput": "800"}]},
    ]
    return {
        "taxonomy": _fixture("taxonomy.json"),
        "result": {
            **response,
            "entry_results": [
                {
                    "entry": entry,
                    "response": {
                        **(response.get("entries") or [])[index],
                        "factor_set": response.get("factor_set"),
                        "factor_source": response.get("factor_source"),
                        "gwp_horizon": response.get("gwp_horizon"),
                    },
                }
                for index, entry in enumerate(entries)
            ],
        },
        "improvementResult": None,
    }


@pytest.fixture(scope="module")
def rendered(tmp_path_factory) -> dict:
    tmp_path = tmp_path_factory.mktemp("step-three-copy")
    harness = tmp_path / "harness.mjs"
    harness.write_text(HARNESS, encoding="utf-8")
    state_file = tmp_path / "result-state.json"
    state_file.write_text(json.dumps(_results_state()), encoding="utf-8")
    out = tmp_path / "rendered.json"
    completed = subprocess.run(
        [
            shutil.which("node"),
            str(harness),
            WEB_JS.as_uri() + "/",
            str(FIXTURES / "taxonomy.json"),
            str(state_file),
            str(out),
            *LEAVES,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert completed.returncode == 0, (
        f"node could not render step 3 and the report:\n{completed.stdout}\n"
        f"{completed.stderr}"
    )
    return json.loads(out.read_text(encoding="utf-8"))


def _hint(markup: str, field_id: str) -> str:
    """The `.field-hint` paragraph that follows `label[for="<field_id>"]`.

    Exact rather than approximate, and that is a property of the emitter:
    `leafPanel` writes `</label><p class="field-hint">` with no node in between.
    """
    import re

    match = re.search(
        r'<label for="' + re.escape(field_id) + r'">.*?</label>'
        r'<p class="field-hint">(.*?)</p>',
        markup,
        re.S,
    )
    assert match, f"no label/hint pair for {field_id!r} in the rendered step 3"
    return match.group(1)


def _tooltip(markup: str, tip_id: str) -> list[str]:
    """The paragraphs of the `role="tooltip"` panel `term()` emits."""
    import re

    match = re.search(
        r'<span class="tip" id="' + re.escape(tip_id) + r'" role="tooltip">(.*?)</span>',
        markup,
        re.S,
    )
    assert match, f"no tooltip {tip_id!r} in the rendered step 3"
    return re.findall(r"<p>(.*?)</p>", match.group(1), re.S)


def _slug(category: str) -> str:
    return category.replace("_", "-")


# --------------------------------------------------------------------- #158


@node
def test_each_card_asks_for_that_cards_own_production_total(rendered):
    """**The three-card measurement, and the whole of #158.**

    Three food types, three `#total-input` boxes, three hints. Before this
    change every one of them read *"Everything that went through this stage over
    the same period, waste included"* — identical on all three, and each asking
    for a figure about the **chain**. §4.6 sums the three boxes, so a visitor who
    answered all three honestly supplied three times the throughput: the
    denominator of `totals.production_share_percent` came out three times too
    large and the share three times too small.

    Each hint now names **the food type the card is for**, which is the scoping
    v1.85 settled for the two money hints and the same scoping the waste label
    one zone above already carries as `Waste amount for %(food)s`. It still
    reads correctly at a leaf count of one, which is the case that draws no card
    at all (v1.81 decision 2) — asserted below as well, because that is the
    render nearly every other test in `tests/web/` exercises.

    **Equality, not a substring.** "for this food type" is now in three of the
    five hints on this card, so a substring pin would survive a swap. See
    `test_no_step_three_hint_can_be_swapped_for_its_neighbour`.
    """
    #: **The three named words come before the equality, and the order is the
    #: point.** An equality fails with a diff and leaves the reader to work out
    #: which property of the sentence mattered; these three say it. "stage" is the
    #: word that was wrong - it is the supply chain, and this box is one leaf of
    #: it. "this food type" is the scoping that stops three cards collecting the
    #: same figure three times. "waste included" is what makes the figure a
    #: denominator at all, and it is also the phrase no other hint on this card
    #: carries, so it is what distinguishes this sentence from its neighbours.
    for category in LEAVES:
        hint = _hint(rendered["many"], f"total-input--{_slug(category)}")
        assert "this stage" not in hint, (
            f"the production-total hint on {category} asks for the whole stage's "
            f"throughput on a box §4.6 sums once per leaf: {hint!r}"
        )
        assert "this food type" in hint, (
            f"the production-total hint on {category} is not scoped to the card "
            f"that holds it, so three cards collect the same figure three times: {hint!r}"
        )
        assert "waste included" in hint, (
            "the production-total hint has lost the clarification that the figure "
            f"includes the waste, which is what makes it a denominator: {hint!r}"
        )

    #: And then the whole sentence, because the three above are each satisfied by
    #: a hint one field along (see
    #: `test_no_step_three_hint_can_be_swapped_for_its_neighbour`).
    for category in LEAVES:
        field_id = f"total-input--{_slug(category)}"
        hint = _hint(rendered["many"], field_id)
        assert hint == PRODUCTION_TOTAL_HINT, (
            f"{field_id} does not carry the production-total hint: {hint!r}"
        )

    #: A leaf count of one: no card, no legend, bare `#total-input` (v1.81
    #: decision 2), and the same sentence has to read correctly there.
    assert _hint(rendered["one"], "total-input") == PRODUCTION_TOTAL_HINT


@node
def test_no_step_three_hint_can_be_swapped_for_its_neighbour(rendered):
    """**The mutation this file exists to fail on.**

    v1.85 found `"total value" in hint` true of both money hints, so swapping
    them left every assertion green. Three hints now share "for this food type",
    which widens that hole rather than closing it: the scoping phrase is
    deliberately the same on all three and therefore pins none of them.

    So each of the three is pinned twice - by equality on its own sentence, and
    by a phrase **only** that sentence carries:

    * `total-input`  — "total amount", and it is the only one saying "waste included"
    * `total-value`  — "per unit", which only the production-value hint rules out
    * `wasted-value` — "production above", the §4.5 shared-basis constraint

    Swapping any two fails here naming both fields that received the wrong
    sentence, which is the measurement rather than the intention.
    """
    expected = {
        "total-input": PRODUCTION_TOTAL_HINT,
        "total-value": PRODUCTION_VALUE_HINT,
        "wasted-value": WASTED_VALUE_HINT,
    }
    #: Distinct from one another, which is what makes the equalities above a pin
    #: at all - three identical sentences would satisfy every one of them.
    assert len(set(expected.values())) == 3, "two of the three hints are the same sentence"

    for category in LEAVES:
        for base, sentence in expected.items():
            field_id = f"{base}--{_slug(category)}"
            assert _hint(rendered["many"], field_id) == sentence, (
                f"{field_id} carries the wrong sentence: "
                f"{_hint(rendered['many'], field_id)!r} - expected {sentence!r}"
            )

    only = {
        "total-input": ("total amount", "waste included"),
        "total-value": ("per unit",),
        "wasted-value": ("production above",),
    }
    for base, phrases in only.items():
        hint = _hint(rendered["one"] if base == "total-input" else rendered["many"],
                     base if base == "total-input" else f"{base}--dairy")
        for phrase in phrases:
            assert phrase in hint, f"{base} no longer says {phrase!r}: {hint!r}"
        for other, other_phrases in only.items():
            if other == base:
                continue
            for phrase in other_phrases:
                assert phrase not in hint, (
                    f"{base} carries {other}'s sentence - {phrase!r} is in {hint!r}"
                )


# --------------------------------------------------------------------- #159


@node
def test_the_money_tooltip_names_the_results_figure_the_page_computes_from_it(rendered):
    """**#159, tied to the results page rather than to the tooltip's own text.**

    The sentence said *"No figure on the results page is calculated from it."*
    The results page prints `Share of value wasted`, which §4.5 computes as
    `wasted_value_nzd ÷ total_value_nzd × 100` — from exactly the two boxes this
    tooltip sits on. It also echoes both sums back (`Total value of food
    handled`, `Value of food wasted`) and prints `saving_nzd`, which §4.5 derives
    from `wasted_value_nzd`. "Statistics only" is true in the sense that none of
    them is an **impact** number; it is false as a claim about the page.

    **The shape being avoided is a test that greps the sentence**, which passes
    whatever the sentence says. The figure is therefore read out of the report
    `results.js` actually builds, in the same Node process, over the canonical
    fixture — and only then is the tooltip asked to name it. The first assertion
    is the tie: were the results page to stop deriving a figure from these two
    boxes, it fails, and the tooltip's claim is what would need revisiting.
    """
    report = rendered["report"]
    assert MONEY_DERIVED_RESULT_LABEL in report, (
        f"the results page no longer prints {MONEY_DERIVED_RESULT_LABEL!r}, so the "
        "figure this tooltip names is not on it - either the fixture stopped "
        "supplying the two money figures or §4.5's share was removed, and this "
        "tooltip's sentence has to be re-decided either way:\n" + report
    )

    for base in ("total-value", "wasted-value"):
        paragraphs = _tooltip(rendered["many"], f"term-tip-{base}--dairy")
        assert len(paragraphs) == 2, f"{base}: {len(paragraphs)} tooltip paragraphs, not two"
        shared = paragraphs[-1]
        #: **The tie comes before the equality**, so the failure reads as what it
        #: is: the results page computes a figure from this box and the tooltip
        #: does not name it. An equality first would report a diff and leave the
        #: reader to work out which half of the sentence was the claim.
        assert MONEY_DERIVED_RESULT_LABEL.casefold() in shared.casefold(), (
            f"{base}'s tooltip does not name {MONEY_DERIVED_RESULT_LABEL!r}, which "
            f"the report above shows the results page computing from this very "
            f"box (§4.5 `wasted_value_nzd / total_value_nzd * 100`): {shared!r}"
        )
        assert DENIAL not in shared.casefold(), (
            f"{base}'s tooltip still denies that the results page computes anything "
            f"from it, and the report above prints {MONEY_DERIVED_RESULT_LABEL!r}: "
            f"{shared!r}"
        )
        #: **O-2 stays decided, and this is the claim the client did rule on.**
        #: `cost` is the waste levy plus disposal cost, computed from factors;
        #: `const_FOOD_VALUE_PER_KG` stays at zero permanently (v1.48). So the
        #: sentence has to state that limit, and nothing here may imply the money
        #: boxes feed the cost metric.
        assert "emissions or cost" in shared, (
            f"{base}'s tooltip no longer states the O-2 limit - that no emissions "
            f"and no cost figure is calculated from it: {shared!r}"
        )

    #: One key, said twice (§7.7.1: the English source string **is** the key).
    assert _tooltip(rendered["many"], "term-tip-total-value--dairy")[-1] == (
        _tooltip(rendered["many"], "term-tip-wasted-value--dairy")[-1]
    ), "the two money tooltips no longer share the one statistics sentence"

    #: And then the whole sentence, last, for the reason the hint test gives: the
    #: phrases above are each satisfiable by a sentence that is not this one.
    for base in ("total-value", "wasted-value"):
        shared = _tooltip(rendered["many"], f"term-tip-{base}--dairy")[-1]
        assert shared == STATISTICS_ONLY_TIP, (
            f"{base}'s second tooltip paragraph is not the shared statistics "
            f"sentence: {shared!r}"
        )


@node
def test_the_statistics_sentence_is_not_the_hint_beside_it(rendered):
    """The tooltip and the hints it sits among may not say each other's thing.

    `leafPanel`'s own note: *a hint that a tooltip repeats is a hint the visitor
    has been made to hover for twice* - and `aria-describedby` makes the tooltip
    the label's description, so a screen reader would read the duplicate twice in
    a row. The new sentence shares words with the hints beside it ("value",
    "wasted", "results page"), which is exactly the overlap that makes a
    substring assertion on either one worthless.
    """
    shared = _tooltip(rendered["many"], "term-tip-total-value--dairy")[-1]
    for base in ("total-input", "total-value", "wasted-value"):
        hint = _hint(rendered["many"], f"{base}--dairy")
        assert hint != shared, f"{base}'s hint is the tooltip's sentence verbatim"
        assert "anonymous statistics" not in hint, (
            f"{base}'s hint has taken the tooltip's sentence: {hint!r}"
        )
    #: And the zone's own sub-line still carries the fact about the pair, which is
    #: where it belongs - the tooltip states the limit, the zone states the
    #: category. Neither is the other's wording.
    assert "Optional. They never enter the emissions calculation." in rendered["many"]
