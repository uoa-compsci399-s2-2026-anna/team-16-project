"""The leaf rule, and the one fan-out that turns chains into request entries.

**A chain is what the visitor built; a leaf is what the API receives.** One
supply-chain chain may name several food categories, each with its own amount
and its own destination allocation, and every element of the request body's
``entries[]`` is a leaf. The rule that says which leaves a chain has lives in
``web/js/state.js`` (``entryLeaves``), and ``web/js/submission.js``'s
``submissionLeaves`` is the single crossing between the two.

Run under Node for the reason ``test_entry_destinations.py`` gives at length: a
test that greps the source for ``entryLeaves`` asserts that a name was typed,
not that a chain forks. Node is the runner only and does not enter the stack
(``docs/architecture.md`` §3) - there is still no build step and no
``package.json``.

**Four modules must agree about the rule** - ``calculator.js``,
``submission.js``, ``improvement.js`` and ``results.js`` - so it is exported
from the one module all four can import without closing a cycle. The assertion
that it is not re-derived anywhere else is
``test_the_leaf_rule_lives_in_state_js_and_nowhere_else`` at the bottom.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web" / "js"

node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is required to execute web/js/state.js and web/js/submission.js",
)

HARNESS = """
globalThis.window = { location: { search: '' } }
globalThis.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} }

import { readFileSync, writeFileSync } from 'node:fs'
const { entryLeaves, leafKey } = await import(process.argv[2])
const { submissionLeaves, submissionPayload } = await import(process.argv[3])
const input = JSON.parse(readFileSync(process.argv[4], 'utf8'))
const chains = input.chains
const leaves = submissionLeaves(chains)
const body = submissionPayload({ taxonomy: { unit_presets: [] }, token: null, gwpHorizon: 100, timeFrame: '' }, chains)
writeFileSync(process.argv[5], JSON.stringify({
  perChain: chains.map(chain => entryLeaves(chain).map(leafKey)),
  leaves: leaves.map(leaf => ({
    sector: leaf.sector,
    foodCategory: leaf.foodCategory,
    foodItem: leaf.foodItem,
    totalAmount: leaf.totalAmount,
    totalUnit: leaf.totalUnit,
    totalInputKg: leaf.totalInputKg,
    wastedValueNzd: leaf.wastedValueNzd,
    current: (leaf.current || []).map(line => [line.destination, line.qtyInput]),
  })),
  body,
}), 'utf8')
"""


def fork(tmp_path: Path, chains: list[dict]) -> dict:
    harness = tmp_path / "harness.mjs"
    harness.write_text(HARNESS, encoding="utf-8")
    payload = tmp_path / "input.json"
    payload.write_text(json.dumps({"chains": chains}), encoding="utf-8")
    out = tmp_path / "out.json"
    completed = subprocess.run(
        [
            shutil.which("node"),
            str(harness),
            (WEB / "state.js").as_uri(),
            (WEB / "submission.js").as_uri(),
            str(payload),
            str(out),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"
    return json.loads(out.read_text(encoding="utf-8"))


def _leaf(**figures) -> dict:
    base = {
        "totalAmount": "",
        "totalUnit": "kilograms",
        "measureMode": "mass",
        "unitPreset": None,
        "unitCount": "",
        "totalInputKg": "",
        "totalValueNzd": "",
        "wastedValueNzd": "",
        "current": [],
    }
    base.update(figures)
    return base


def _chain(sector: str, categories: list[str], figures: dict, *, unspecified: bool = False) -> dict:
    return {
        "sector": sector,
        "foodCategories": categories,
        "foodUnspecified": unspecified,
        "foodItems": {},
        "totalUnit": "kilograms",
        "leafFigures": figures,
    }


def _key(category: str | None = None, item: str | None = None) -> str:
    return f"{category or ''}\x00{item or ''}"


# ----------------------------------------------------------------- the rule itself


@node
def test_a_chain_naming_nothing_is_one_category_less_leaf(tmp_path):
    """Skipping the optional step is still one entry, with `food_category: null`."""
    result = fork(tmp_path, [_chain("processing", [], {})])
    assert result["perChain"] == [[_key()]], result["perChain"]


@node
def test_i_do_not_know_is_the_same_submission_as_nothing_ticked(tmp_path):
    """§5.4's `unspecified` bucket means the user did not break their waste down by
    type, and skipping an optional step and saying so are the same answer. What the
    tick buys is a visible affirmative answer and a named row, not a third bucket."""
    nothing = fork(tmp_path, [_chain("processing", [], {})])
    said_so = fork(tmp_path, [_chain("processing", [], {}, unspecified=True)])
    assert nothing["perChain"] == said_so["perChain"]
    assert [leaf["foodCategory"] for leaf in said_so["leaves"]] == [None]


@node
def test_categories_plus_i_do_not_know_is_one_leaf_each(tmp_path):
    """"300 kg dairy and 200 kg I cannot identify" is two distinct rows under
    `COALESCE(food_category_id, 0)`, and would otherwise have to be forced into a
    named category."""
    result = fork(tmp_path, [_chain("processing", ["dairy"], {}, unspecified=True)])
    assert result["perChain"] == [[_key("dairy"), _key()]], result["perChain"]


@node
def test_the_leaf_order_is_the_ticking_order_not_the_sort_order(tmp_path):
    """The step-3 cards appear in the order they were created and do not reshuffle
    when a category is added, which is only true if the selection keeps its own
    order."""
    result = fork(tmp_path, [_chain("processing", ["vegetables", "dairy", "bakery"], {})])
    assert result["perChain"] == [[_key("vegetables"), _key("dairy"), _key("bakery")]]


# ----------------------------------------------------------------- the fan-out


@node
def test_each_leaf_carries_its_own_amount_and_its_own_allocation(tmp_path):
    """**The fork's whole point.** A submission must be able to say that the dairy
    went to landfill and the bakery went to animal feed; a shared split divided
    pro-rata cannot express that at all."""
    chain = _chain(
        "processing",
        ["dairy", "bakery"],
        {
            _key("dairy"): _leaf(totalAmount="600", current=[{"id": "a", "destination": "landfill", "qtyInput": "600"}]),
            _key("bakery"): _leaf(totalAmount="400", current=[{"id": "b", "destination": "animal_feed", "qtyInput": "400"}]),
        },
    )
    result = fork(tmp_path, [chain])
    got = [(leaf["foodCategory"], leaf["totalAmount"], leaf["current"]) for leaf in result["leaves"]]
    assert got == [
        ("dairy", "600", [["landfill", "600"]]),
        ("bakery", "400", [["animal_feed", "400"]]),
    ], got
    sent = [(entry["food_category"], entry["current"]) for entry in result["body"]["entries"]]
    assert sent == [
        ("dairy", [{"destination": "landfill", "qty_kg": "600.000"}]),
        ("bakery", [{"destination": "animal_feed", "qty_kg": "400.000"}]),
    ], sent


@node
def test_the_three_scalars_fork_and_are_not_copied_onto_every_leaf(tmp_path):
    """`engine/calculate.py:369` sums every entry's `total_input_kg` as the
    production-share denominator, so copying one chain's figure onto every leaf
    divides the share by N; `:506` derives `value_per_kg` per entry, so it overstates
    `saving_nzd` by roughly N - upward. Each leaf carries its own or nothing."""
    chain = _chain(
        "processing",
        ["dairy", "bakery"],
        {
            _key("dairy"): _leaf(totalAmount="600", totalInputKg="5000", wastedValueNzd="1200",
                                 current=[{"id": "a", "destination": "landfill", "qtyInput": "600"}]),
            _key("bakery"): _leaf(totalAmount="400",
                                  current=[{"id": "b", "destination": "landfill", "qtyInput": "400"}]),
        },
    )
    result = fork(tmp_path, [chain])
    got = [(entry["total_input_kg"], entry["wasted_value_nzd"]) for entry in result["body"]["entries"]]
    assert got == [("5000", "1200"), (None, None)], got


@node
def test_figures_typed_for_a_category_that_was_later_unticked_are_never_sent(tmp_path):
    """**Ghost figures.** `leafFigures` is keyed by leaf, so a record survives an
    untick unless something prunes it - and then it is fingerprinted, makes the
    back-out confirmation fire on a Back that discards nothing, and is silently
    restored on a re-tick. The fan-out reads `entryLeaves`, never the map's keys."""
    chain = _chain(
        "processing",
        ["dairy"],
        {
            _key("dairy"): _leaf(totalAmount="600", current=[{"id": "a", "destination": "landfill", "qtyInput": "600"}]),
            _key("bakery"): _leaf(totalAmount="999", current=[{"id": "b", "destination": "landfill", "qtyInput": "999"}]),
        },
    )
    result = fork(tmp_path, [chain])
    categories = [leaf["foodCategory"] for leaf in result["leaves"]]
    assert categories == ["dairy"], (
        f"a category that is no longer ticked still reached the request body: {categories}"
    )
    amounts = [entry["current"] for entry in result["body"]["entries"]]
    assert amounts == [[{"destination": "landfill", "qty_kg": "600.000"}]], amounts


@node
def test_two_chains_of_different_leaf_counts_flatten_in_submission_order(tmp_path):
    """The shape `entryResultsFrom` pairs against. Two chains of two and three leaves
    are five request entries, in chain order and then in leaf order."""
    first = _chain("processing", ["dairy", "bakery"], {
        _key("dairy"): _leaf(totalAmount="1"), _key("bakery"): _leaf(totalAmount="2"),
    })
    second = _chain("retail", ["fruit", "vegetables", "meat"], {
        _key("fruit"): _leaf(totalAmount="3"),
        _key("vegetables"): _leaf(totalAmount="4"),
        _key("meat"): _leaf(totalAmount="5"),
    })
    result = fork(tmp_path, [first, second])
    got = [(leaf["sector"], leaf["foodCategory"], leaf["totalAmount"]) for leaf in result["leaves"]]
    assert got == [
        ("processing", "dairy", "1"),
        ("processing", "bakery", "2"),
        ("retail", "fruit", "3"),
        ("retail", "vegetables", "4"),
        ("retail", "meat", "5"),
    ], got


@node
def test_the_item_dimension_is_carried_and_now_sent(tmp_path):
    """`foodItem` travels on the leaf so the review step and the results page can
    label it, and **reaches the request body**.

    It was carried and deliberately withheld until contract v1.58: `EntryPayload`
    is a Pydantic model with `extra="forbid"`, so an unknown key was a 400 rather
    than an ignored field, and this test asserted the withholding. v1.58 gave the
    model a `food_item`, which inverted it -- the assertion that used to protect
    the boundary would now hold the front end one revision behind the API it
    talks to.
    """
    chain = _chain("processing", ["dairy"], {
        _key("dairy", "cheese"): _leaf(totalAmount="10", current=[{"id": "a", "destination": "landfill", "qtyInput": "10"}]),
    })
    chain["foodItems"] = {"dairy": ["cheese"]}
    result = fork(tmp_path, [chain])
    assert [leaf["foodItem"] for leaf in result["leaves"]] == ["cheese"]
    assert result["body"]["entries"][0]["food_item"] == "cheese"


@node
def test_a_leaf_that_names_no_food_sends_null_rather_than_omitting_the_key(tmp_path):
    """§6.2: absent and `null` mean the same thing to the API, and that thing is
    "named a category and no food". Sending `null` explicitly is the shape that
    cannot be mistaken for a client written before the field existed -- and it is
    what every leaf sends today, because nothing can name a food until step 2.5.
    """
    chain = _chain("processing", ["dairy"], {
        _key("dairy", None): _leaf(totalAmount="10", current=[{"id": "a", "destination": "landfill", "qtyInput": "10"}]),
    })
    entry = fork(tmp_path, [chain])["body"]["entries"][0]
    assert "food_item" in entry, entry
    assert entry["food_item"] is None


# ----------------------------------------------------------------- one definition


#: Modules that consume leaves. None of them may rebuild the list itself.
_CONSUMERS = ("calculator.js", "submission.js", "improvement.js", "results.js")


def test_no_front_end_source_file_carries_a_raw_control_byte():
    """`leafKey` joins its two halves with U+0000, and it was written with an
    **actual NUL byte inside the template literal** rather than the `\\u0000`
    escape `calculator.js`'s own `savedLeafKey` uses two lines of reasoning away.

    It is not a cosmetic difference. `git` calls the file binary and stops
    producing a diff for it, so every later change to `state.js` reviews as
    "Binary files differ"; `grep` skips it; and an editor or a tool that
    normalises encodings can drop or replace the byte without anything on screen
    changing, at which point two leaves whose codes concatenate alike collide and
    share one figures record. The escape renders the same character and survives
    all three.

    Asserted over the whole directory rather than over the one line, because the
    next person reaching for a NUL separator will write it the same way.
    """
    offenders = []
    for path in sorted(WEB.glob("*.js")):
        data = path.read_bytes()
        for index, byte in enumerate(data):
            if byte < 9 or byte in (11, 12) or 14 <= byte < 32:
                line = data[:index].count(b"\n") + 1
                offenders.append(f"{path.name}:{line} (0x{byte:02x})")
    assert not offenders, (
        f"these front-end sources carry a raw control byte, which makes git treat "
        f"them as binary and stop diffing them: {offenders}"
    )


def test_the_leaf_rule_lives_in_state_js_and_nowhere_else():
    """**Four modules must agree about it**, and a second copy is where the next
    divergence goes. `state.js` imports nothing at all, so it is the only module all
    four can import without closing a cycle - `calculator.js` already imports
    `improvement.js` and `results.js`, which is why `publicError` has to be *passed*
    into `compareImprovement` rather than imported.

    Asserted by looking for the function body, not the name: a consumer that calls
    `entryLeaves` is correct, and one that writes its own `for (const category of ...
    foodCategories)` loop is the defect.
    """
    source = (WEB / "state.js").read_text(encoding="utf-8")
    assert "export function entryLeaves(chain)" in source, (
        "the leaf rule is not exported from web/js/state.js"
    )
    derived = []
    for name in _CONSUMERS:
        text = (WEB / name).read_text(encoding="utf-8")
        # The rule's own shape: iterating `foodCategories` to build a list.
        for match in re.finditer(r"of\s*\(?\s*(?:chain|state|entry)[^\n]{0,30}foodCategories", text):
            line = text[: match.start()].count("\n") + 1
            derived.append(f"{name}:{line}")
    assert not derived, (
        f"these modules re-derive the leaf list instead of calling entryLeaves(): {derived}"
    )
