"""The improvement panel, forked.

``improvement.js``'s ``submissionEntries``, ``improvedLines`` and
``currentAllocationPercentages`` were all written around one allocation per
chain. Since the fork an entry is a **leaf**, each with its own destination
allocation, and the owner's own note calls this the largest single risk in the
landing: that panel is the round-three feature the client signed off.

Two things are measured here, end to end against the real API:

1. **The seeded allocation is taken over the leaves' own lines.** The chain below
   sends one leaf entirely to one destination and the other entirely to a
   different one - a split that a shared, pro-rated allocation could not express
   at all, because it would give both leaves the same shape. "Match the current
   allocation" must come back holding both destinations, in the proportion the
   two leaves' masses actually are.
2. **Mass is conserved per leaf, not per chain.** ``improvedLines`` takes
   ``sumQtyKg(requestLines(entry, presets))`` as its base, and the server checks
   ``current`` against ``alternative`` per entry. Anchored on the chain instead,
   a two-leaf chain would send both leaves an alternative describing the chain's
   whole mass and the submission would be refused.

Requires the stack: ``docker compose -f docker/compose.yaml up -d --build web``.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from tests.web.base_url import CALCULATOR
from tests.web.steps import expand_step_cards, press_continue


# The `browser` marker is applied by `conftest.py`, by location: every module
# here is a browser suite unless it is named in its `NOT_A_BROWSER_SUITE`.

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to drive Compare Impact against the real API",
)

BASE = CALCULATOR

#: One chain, two leaves, two masses that share no common factor with each other's
#: percentage - 100 and 200 are 33.33% and 66.67% of 300, so a rounding correction
#: is exercised as well as the split itself.
AMOUNTS = ("100", "200")


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 1278, "height": 983}, locale="en-NZ")
    opened = context.new_page()
    try:
        opened.goto(BASE + "?lang=en", wait_until="networkidle", timeout=15000)
    except Exception as error:  # pragma: no cover - environment guard
        context.close()
        pytest.skip(f"the front end is not being served at {BASE}: {error}")
    opened.wait_for_selector('[data-action="start"]', timeout=10000)
    yield opened
    context.close()


def _forked_chain(page):
    """Two leaves, each sent **entirely to a different destination**."""
    page.click('[data-action="start"]')
    page.wait_for_selector('input[name="sector"]')
    page.evaluate("document.querySelectorAll('input[name=sector]')[0].click()")
    page.wait_for_timeout(80)
    press_continue(page)
    page.wait_for_selector('input[name="food-category"]')
    boxes = page.locator('input[name="food-category"]')
    boxes.nth(0).click()
    page.wait_for_timeout(60)
    boxes.nth(1).click()
    page.wait_for_timeout(60)
    press_continue(page)
    page.wait_for_selector('[data-leaf-field="amount"]', state="attached")
    expand_step_cards(page)
    fields = page.evaluate(
        "() => [...document.querySelectorAll('[data-leaf-field=amount]')].map(e => e.id)"
    )
    assert len(fields) == 2, fields
    for field, amount in zip(fields, AMOUNTS):
        page.fill(f"#{field}", amount)
        page.wait_for_timeout(50)
    press_continue(page)
    #: Step 4 draws one collapsible card per food type since #142, and a shut
    #: card's body carries `hidden` - so its rows are in the document, not
    #: visible, and not fillable. Same two lines as step 3 above, same helper,
    #: and the same reason it exists (`tests/web/steps.py`).
    page.wait_for_selector('[data-line-field="amount"]', state="attached")
    expand_step_cards(page)
    # Leaf one's FIRST destination and leaf two's SECOND: the split differs by leaf,
    # which is the whole thing a per-leaf allocation can say and a shared one cannot.
    rows = page.evaluate(
        """() => {
          const byLeaf = {};
          for (const input of document.querySelectorAll('[data-line-field=amount]')) {
            (byLeaf[input.dataset.leaf] ||= []).push({id: input.id, destination: input.id});
          }
          const leaves = Object.values(byLeaf);
          return [leaves[0][0].id, leaves[1][1].id];
        }"""
    )
    for field, amount in zip(rows, AMOUNTS):
        page.fill(f"#{field}", amount)
        page.wait_for_timeout(80)
    press_continue(page)
    page.wait_for_selector('[data-action="calculate"]')
    page.click('.step-nav [data-action="calculate"]')
    page.wait_for_selector(".results-page", timeout=25000)


def _seeded_allocation(page):
    """*Match the current allocation*, read back **per leaf**.

    Returns one ``{destinationCode: value}`` map per leaf, in submission order.
    A submission-wide panel returns one map however many leaves there are, which
    is what the first test below fails on.
    """
    page.click('[data-action="explore-improvements"]')
    page.wait_for_selector('[data-action="reset-improvement"]')
    page.click('[data-action="reset-improvement"]')
    page.wait_for_timeout(250)
    return page.evaluate(
        """() => {
          const boxes = [...document.querySelectorAll('.percentage-input [data-improvement-code]')]
            .filter(el => el.tagName === 'INPUT' && el.type === 'number');
          const byLeaf = new Map();
          for (const box of boxes) {
            const leaf = box.dataset.improvementLeaf ?? '0';
            if (!byLeaf.has(leaf)) byLeaf.set(leaf, {});
            byLeaf.get(leaf)[box.dataset.improvementCode] = box.value;
          }
          return [...byLeaf.keys()].sort((a, b) => Number(a) - Number(b)).map(key => byLeaf.get(key));
        }"""
    )


def _used(allocation):
    """The destinations one leaf actually allocates to."""
    return {code: Decimal(value or "0") for code, value in allocation.items() if Decimal(value or "0") > 0}


def test_the_panel_forks_its_allocation_one_per_leaf(page):
    """**Owner decision 6: the improvement panel forks.**

    The chain below sends 100 kg of one food *entirely* to one destination and
    200 kg of another *entirely* to a second. That is a submission a single
    percentage split cannot express at all - it would have to give both leaves the
    same shape - so the two leaves are deliberately given *different* allocations
    and nothing submission-wide can accidentally satisfy this.

    Mutation: applying one submission-wide split fails here with the two leaves
    holding the same 33/67 pair, which describes neither of them.
    """
    _forked_chain(page)
    allocations = _seeded_allocation(page)
    assert len(allocations) == 2, (
        f"a two-leaf chain is offered {len(allocations)} allocation(s); the panel is "
        f"still submission-wide, so \"Match the current allocation\" cannot be an "
        f"identity on a forked chain: {allocations}"
    )
    first, second = (_used(allocation) for allocation in allocations)
    assert len(first) == 1 and len(second) == 1, (
        f"each leaf went entirely to ONE destination, so each leaf's own allocation "
        f"holds exactly one share; got {first} and {second}"
    )
    assert set(first) != set(second), (
        f"both leaves were seeded with the same destination, so the allocation is "
        f"still one split applied to every leaf: {first} vs {second}"
    )
    for index, used in enumerate((first, second)):
        share = next(iter(used.values()))
        assert share == Decimal("100"), (
            f"leaf {index} sends all of its waste to one destination, so that "
            f"destination's share is 100% of ITS mass, not of the submission's: {used}"
        )


def test_match_the_current_allocation_is_an_identity_on_a_forked_chain(page):
    """Pressing *Match the current allocation* and then *Compare Impact* must
    reproduce the current scenario exactly, leaf by leaf - that is what the button
    says it does, and the client signed the panel off on round three.

    Mutation: one submission-wide split makes the alternative of the 100 kg leaf
    land partly on the 200 kg leaf's destination, and this fails printing the two
    line sets it found.
    """
    bodies: list[str] = []
    page.on(
        "request",
        lambda request: bodies.append(request.post_data)
        if request.method == "POST" and request.url.endswith("/api/v1/calculate")
        else None,
    )
    _forked_chain(page)
    _seeded_allocation(page)
    page.click('[data-action="compare-improvement"]')
    page.wait_for_selector("#comparison-results", timeout=25000)

    entries = json.loads(bodies[-1])["entries"]
    assert len(entries) == 2, entries
    for index, entry in enumerate(entries):
        current = {line["destination"]: Decimal(line["qty_kg"]) for line in entry["current"]}
        alternative = {line["destination"]: Decimal(line["qty_kg"]) for line in entry["alternative"]}
        assert set(current) == set(alternative), (
            f"entry {index} ({entry['food_category']}) matched the current allocation "
            f"and its alternative names different destinations: current {current}, "
            f"alternative {alternative}"
        )
        for destination, qty in current.items():
            assert abs(alternative[destination] - qty) <= Decimal("0.01"), (
                f"entry {index} ({entry['food_category']}) matched the current "
                f"allocation and {destination} moved from {qty} kg to "
                f"{alternative[destination]} kg"
            )


def test_compare_impact_conserves_each_leafs_own_mass(page):
    """The alternative redistributes **the leaf's** current mass, and the server
    checks it per entry. `improvedLines` anchored on the chain would send both
    leaves an alternative of 300 kg against currents of 100 and 200.
    """
    bodies: list[str] = []
    page.on(
        "request",
        lambda request: bodies.append(request.post_data)
        if request.method == "POST" and request.url.endswith("/api/v1/calculate")
        else None,
    )
    _forked_chain(page)
    _seeded_allocation(page)
    button = page.locator('[data-action="compare-improvement"]')
    assert button.is_enabled(), (
        "Compare Impact stayed disabled after matching the current allocation, so the "
        "panel's own idea of the current allocation does not describe the same mass "
        "the request carries"
    )
    button.click()
    page.wait_for_selector("#comparison-results", timeout=25000)

    assert len(bodies) >= 2, f"the comparison posted nothing: {len(bodies)} calls"
    entries = json.loads(bodies[-1])["entries"]
    assert len(entries) == 2, (
        f"the comparison sent {len(entries)} entries for a two-leaf chain: {entries}"
    )
    for index, entry in enumerate(entries):
        current = sum(Decimal(line["qty_kg"]) for line in entry["current"])
        alternative = sum(Decimal(line["qty_kg"]) for line in entry["alternative"])
        assert abs(current - alternative) <= Decimal("0.01"), (
            f"entry {index} ({entry['food_category']}) sends {alternative} kg of "
            f"alternative against {current} kg of current, so the two scenarios do "
            f"not describe the same waste"
        )
        assert current == Decimal(AMOUNTS[index]) * 1, (
            f"entry {index} carries {current} kg, expected {AMOUNTS[index]}: {entry}"
        )


def test_the_comparison_renders_for_a_forked_chain(page):
    """End to end, because the panel is the round-three feature the client signed
    off and the fork rewrote every one of its three inputs."""
    _forked_chain(page)
    _seeded_allocation(page)
    page.click('[data-action="compare-improvement"]')
    page.wait_for_selector("#comparison-results", timeout=25000)
    text = page.locator("#comparison-results").inner_text()
    assert text.strip(), "the comparison panel rendered empty"
    assert page.locator(".improvement-error").count() == 0 or not page.locator(
        ".improvement-error"
    ).first.inner_text().strip(), (
        f"the comparison reported an error: "
        f"{page.locator('.improvement-error').first.inner_text()!r}"
    )


def test_one_unit_control_governs_every_row_of_every_leaf(page):
    """**#74 on a forked chain: one control, N donuts, one unit everywhere.**

    The client asked on 17 September for one consistent unit throughout the
    Improvement section. On a single-leaf panel that is a statement about thirteen
    rows; on a forked one it is a statement about thirteen rows times however many
    foods, and this is the shape that decided the design.

    **It is the case the rejected alternative could not have satisfied.** "Let each
    leaf inherit the unit it was measured in at step 3" reads like the same request
    and is not: ``entry.totalUnit`` is per leaf, so a chain whose foods were entered
    in different units produces a panel that is consistent down each card and
    inconsistent across them - which is #74's own complaint arriving by another door.
    One control for the panel is the only reading of the client's sentence that holds
    for a forked chain.

    Three assertions, in the order they would break:

    1. there is exactly **one** unit control on the whole panel, not one per card;
    2. no destination row anywhere carries a ``<select>`` of its own, on either card;
    3. every allocation control on every card reports the panel's unit. The row's
       visible unit suffix is gone since #74 (a container's staff-typed label does
       not fit a row-sized track), so the per-row surface that still names the unit
       is each control's own ``aria-label``, and that is what is read.

    The donuts are counted too, because a per-leaf editor is what makes this
    non-trivial: two cards, two charts, one control above both.

    A container rather than kilograms, because a container label is the one option
    that is neither translated nor a constant - it is staff text multiplied by
    ``kg_per_unit`` out of the taxonomy - so it is the value a stale per-row lookup
    would fail to follow.
    """
    _forked_chain(page)
    page.click('[data-action="explore-improvements"]')
    page.wait_for_selector("#improvement-mode")

    assert page.locator("#improvement-mode").count() == 1, (
        "a forked panel drew more than one unit control; #74 asks for one for the "
        "whole section"
    )
    assert page.locator(".improvement-leaf").count() == 2, (
        "the chain did not fork, so this measures a single-leaf panel and proves "
        "nothing about the across-card half of #74"
    )

    container = page.locator("#improvement-mode option").evaluate_all(
        "els => els.map(el => el.value).filter(value => value.startsWith('preset:'))"
    )
    assert container, "the one unit control offers no container at all"
    label = page.locator(
        '#improvement-mode option[value="%s"]' % container[0]
    ).inner_text()

    page.select_option("#improvement-mode", container[0])
    page.wait_for_timeout(250)

    assert page.locator(".improvement-allocation-row select").count() == 0, (
        "a destination row still carries a unit `<select>` of its own on a forked panel"
    )
    donuts = page.locator(".improvement-pie-chart").count()
    assert donuts >= 2, f"two leaves drew {donuts} donut(s)"

    #: Read per card, so a failure says which card disagreed rather than only that
    #: one did.
    per_leaf = page.evaluate(
        """() => [...document.querySelectorAll('[data-improvement-leaf-panel]')].map(panel => ({
             leaf: panel.dataset.improvementLeafPanel,
             units: [...new Set([...panel.querySelectorAll('input[type=range][data-improvement-code]')]
               .map(el => el.getAttribute('aria-label')))],
           }))"""
    )
    assert len(per_leaf) == 2, per_leaf
    for card in per_leaf:
        assert card["units"], f"card {card['leaf']} drew no allocation control"
        for name in card["units"]:
            assert name.endswith(label), (
                f"card {card['leaf']} has a control named {name!r} while the one unit "
                f"control says {label!r} - a per-row or per-leaf unit survived, which "
                "is #74 arriving by another door"
            )
