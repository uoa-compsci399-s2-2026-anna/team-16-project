"""The results page attaches each leaf's figures to that leaf, and to no other.

**This is the highest-severity line in the fork.** `entryResultsFrom`
(``web/js/state.js``) pairs purely by index against ``response.entries``. Before
the fork ``submitCalculation`` handed it ``[...state.entries, draftEntry()]`` -
the chains - and that was right, because one chain was one entry. Since the fork
one chain carries several ``entries[]``, so handing it chains attaches every
figure on the results page, in the text export and in the PDF to the wrong
entry, **with no error and no warning anywhere**.

So the journey below builds **two chains with different leaf counts** - two
leaves and then three - and gives every leaf a distinct amount. A naive
chain-index pairing cannot accidentally line up against that: it runs out of
chains after two, and every leaf's amount is unique, so a shift of any size
shows.

The assertion is made inside one entry block of the downloaded report, where
two independent sources meet:

* ``Waste amount`` and ``Destinations`` are printed from **the entry** - what
  the visitor typed, as ``submissionLeaves`` shaped it;
* ``Impact by destination`` prints ``(111.000 kg)`` from **the response** - what
  the engine was given.

They are the same number when the pairing is right and different numbers when it
is not. Nothing else on the results page can tell: ``stageFoodLabel`` and
``sectorName`` both read ``response.food_category ?? entry.foodCategory``, so the
labels come out right whichever object is attached.

Requires the stack: ``docker compose -f docker/compose.yaml up -d --build web``.
"""

from __future__ import annotations

import json
import re

import pytest

from tests.web.base_url import CALCULATOR
from tests.web.steps import expand_step_cards, press_continue


pytestmark = pytest.mark.browser

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to drive a real calculation through the wizard",
)

BASE = CALCULATOR

#: Two chains, of two and of three leaves, each leaf with a distinct amount.
#: The counts differ on purpose - with two chains of two, a chain-index pairing
#: would line up for the first two entries by accident.
FIRST = ((0, 1), ("111", "222"))
SECOND = ((2, 3, 4), ("333", "444", "555"))


@pytest.fixture
def page(browser):
    context = browser.new_context(
        viewport={"width": 1278, "height": 983}, locale="en-NZ", accept_downloads=True
    )
    opened = context.new_page()
    try:
        opened.goto(BASE + "?lang=en", wait_until="networkidle", timeout=15000)
    except Exception as error:  # pragma: no cover - environment guard
        context.close()
        pytest.skip(f"the front end is not being served at {BASE}: {error}")
    opened.wait_for_selector('[data-action="start"]', timeout=10000)
    yield opened
    context.close()


def _pick_sector(page, index):
    page.wait_for_selector('input[name="sector"]')
    page.evaluate(f"document.querySelectorAll('input[name=sector]')[{index}].click()")
    page.wait_for_timeout(80)
    press_continue(page)


def _build_chain(page, categories, amounts):
    """One chain: tick N categories, give each its own amount and its own split."""
    page.wait_for_selector('input[name="food-category"]')
    boxes = page.locator('input[name="food-category"]')
    for index in categories:
        boxes.nth(index).click()
        page.wait_for_timeout(60)
    press_continue(page)
    page.wait_for_selector('[data-leaf-field="amount"]', state="attached")
    expand_step_cards(page)
    ids = page.evaluate(
        "() => [...document.querySelectorAll('[data-leaf-field=amount]')].map(e => e.id)"
    )
    assert len(ids) == len(categories), (
        f"step 3 rendered {len(ids)} amount fields for {len(categories)} ticked "
        f"categories: {ids}"
    )
    for field, amount in zip(ids, amounts):
        page.fill(f"#{field}", amount)
        page.wait_for_timeout(50)
    press_continue(page)
    #: Step 4 draws one collapsible card per food type since #142, and a shut
    #: card's body carries `hidden` - so its rows are in the document, not
    #: visible, and not fillable. Same two lines as step 3 above, same helper,
    #: and the same reason it exists (`tests/web/steps.py`).
    page.wait_for_selector('[data-line-field="amount"]', state="attached")
    expand_step_cards(page)
    first_row = page.evaluate(
        """() => {
          const byLeaf = {};
          for (const input of document.querySelectorAll('[data-line-field=amount]')) {
            (byLeaf[input.dataset.leaf] ||= []).push(input.id);
          }
          return Object.values(byLeaf).map(ids => ids[0]);
        }"""
    )
    assert len(first_row) == len(categories), (
        f"step 4 rendered destination rows for {len(first_row)} leaves, expected "
        f"{len(categories)} - the allocation is per leaf"
    )
    for field, amount in zip(first_row, amounts):
        page.fill(f"#{field}", amount)
        page.wait_for_timeout(70)
    press_continue(page)
    page.wait_for_selector('[data-action="calculate"]')


def _two_chains(page):
    page.click('[data-action="start"]')
    _pick_sector(page, 0)
    _build_chain(page, *FIRST)
    page.click('[data-action="add-entry"]')
    _pick_sector(page, 1)
    _build_chain(page, *SECOND)


def _report(page) -> str:
    """The downloaded text report.

    **The failure is named rather than left to a timeout.** `buildResultsReport`
    reads `entry.current` on every paired entry, and a chain has no `current` - so a
    pairing that hands it chains throws inside the click handler and no download is
    ever offered. Waiting thirty seconds for an event that cannot arrive says
    nothing; the page error does.
    """
    thrown: list[str] = []
    page.on("pageerror", lambda error: thrown.append(str(error)))
    try:
        with page.expect_download(timeout=8000) as download:
            page.click('[data-action="download-results"]')
    except Exception:
        raise AssertionError(
            "the text export produced no file. The report is built from "
            "`state.result.entry_results`, so this is what a mis-paired result looks "
            f"like from the outside; the page reported: {thrown or 'nothing'}"
        ) from None
    path = download.value.path()
    with open(path, encoding="utf-8") as handle:
        return handle.read()


#: ``Entry 3: Processing and manufacturing`` ... up to the next blank-line gap.
_BLOCK = re.compile(r"^Entry (\d+): .*?(?=\n\n|\Z)", re.M | re.S)
_TYPED = re.compile(r"^Waste amount: ([\d,]+\.\d\d) ", re.M)
_FROM_RESPONSE = re.compile(r"\(([\d,]+\.\d{3}) kg\)")
_FOOD = re.compile(r"^Food type: (.+)$", re.M)


def test_every_entry_block_pairs_the_food_it_names_with_its_own_figures(page):
    """**The pairing, measured where the two sources meet.**

    Five entries from two chains. In each block the amount printed from the entry
    and the kilograms printed from the response must be the same number - they are
    two views of one leaf, and they agree only if `entryResultsFrom` was handed the
    leaves the request carried.

    Mutation: pairing by chain index instead (`entryResultsFrom(chains, response)`)
    leaves `entry.current` undefined for every element and the report cannot be
    built at all; pairing against a shifted leaf array puts 222.00 kg of typed
    fruit beside 111.000 kg of engine-computed mixed waste, and the equality below
    is what says so.
    """
    _two_chains(page)
    page.click('.step-nav [data-action="calculate"]')
    page.wait_for_selector(".results-page", timeout=25000)
    report = _report(page)

    blocks = _BLOCK.findall(report)
    assert len(blocks) == 5, (
        f"two chains of two and three leaves are five request entries, and the report "
        f"has {len(blocks)} entry blocks. A chain-index pairing produces two."
    )

    paired = []
    for match in _BLOCK.finditer(report):
        block = match.group(0)
        typed = _TYPED.search(block)
        computed = _FROM_RESPONSE.search(block)
        food = _FOOD.search(block)
        assert typed and computed and food, (
            f"entry block {match.group(1)} is missing one of its three lines:\n{block}"
        )
        paired.append(
            (
                food.group(1),
                typed.group(1).replace(",", ""),
                computed.group(1).replace(",", ""),
            )
        )

    mismatched = [row for row in paired if float(row[1]) != float(row[2])]
    assert not mismatched, (
        "an entry block prints one leaf's typed amount beside another leaf's "
        f"engine figures, so the results page is attributing numbers to the wrong "
        f"food: {mismatched} (all blocks: {paired})"
    )
    assert [row[1] for row in paired] == ["111.00", "222.00", "333.00", "444.00", "555.00"], (
        f"the five leaves did not reach the report in submission order: {paired}"
    )


def test_the_export_request_carries_exactly_the_entries_the_calculation_carried(page):
    """`exportPayload` builds the PDF body from `state.result.entry_results`, not
    from `state.entries` - so it is a second, independent reading of the same
    pairing. The two bodies must hold the same entries in the same order.

    Handed chains, `entryPayload` reads `entry.foodCategory` (a chain has
    `foodCategories`) and `entry.current` (a chain has none), so the export would
    either throw or post a body describing nothing the visitor entered.
    """
    bodies: dict[str, str] = {}
    page.on(
        "request",
        lambda request: bodies.setdefault(request.url.rsplit("/", 1)[-1], request.post_data)
        if "/api/v1/" in request.url and request.method == "POST"
        else None,
    )
    _two_chains(page)
    page.click('.step-nav [data-action="calculate"]')
    page.wait_for_selector(".results-page", timeout=25000)
    page.click('[data-action="download-pdf"]')
    page.wait_for_timeout(2500)

    assert "calculate" in bodies, "no calculation was posted"
    assert "pdf" in bodies, (
        f"the PDF export posted nothing; requests seen: {sorted(bodies)}. An export "
        f"built from mis-paired entries throws before it reaches the network."
    )
    calculated = json.loads(bodies["calculate"])["entries"]
    exported = json.loads(bodies["pdf"])["entries"]
    strip = lambda entries: [  # noqa: E731 - one expression, read once
        {"sector": e["sector"], "food_category": e["food_category"], "current": e["current"]}
        for e in entries
    ]
    assert strip(exported) == strip(calculated), (
        "the export describes different entries from the calculation it is an "
        f"export of.\ncalculated: {strip(calculated)}\nexported:   {strip(exported)}"
    )


def test_the_stage_tab_tells_two_leaves_of_one_chain_apart(page):
    """Three leaves of one chain are three rows in one sector, and the accumulator
    that used to merge colliding labels was removed deliberately (§7.6.1: it summed
    engine-computed figures in the browser). So they must not be merged - and they
    must not be three rows reading "Processing and manufacturing" and nothing else.
    """
    _two_chains(page)
    page.click('.step-nav [data-action="calculate"]')
    page.wait_for_selector(".results-page", timeout=25000)
    page.click('[data-action="breakdown-tab"][data-tab="stage"]')
    page.wait_for_timeout(200)
    labels = page.evaluate(
        """() => [...document.querySelectorAll('#breakdown-panel-stage tbody tr th[scope=row]')]
             .map(cell => cell.textContent.trim())"""
    )
    assert len(labels) == 5, f"the stage tab shows {len(labels)} rows, expected 5: {labels}"
    assert len(set(labels)) == 5, (
        "two rows of the stage breakdown carry the same label, so a reader cannot "
        f"tell which food each figure belongs to: {labels}"
    )
