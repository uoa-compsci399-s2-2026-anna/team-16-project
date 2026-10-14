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


def test_each_food_type_is_a_card_the_reader_can_fold(page):
    """Contract v1.100. **#134's rule on a fourth screen.**

    The owner's complaint was length: a chain of several foods draws a pie chart and a
    stack of fourteen sliders per food, so the panel runs to thousands of pixels and the
    foods below the fold go unadjusted with nothing saying so. Steps 2.5, 3 and 4 already
    answer that with one collapsible card each; this is the same chrome, from the same
    module, on the improvement panel.

    **Four claims, and the third is the one that makes folding safe here.**

    * every food type is a card, and they arrive shut;
    * the body is `hidden` while shut, so its sliders are out of the tab order rather
      than merely invisible — the thing `hidden` is defined to do and a class is not;
    * **`Total allocation` and `Total mass` stay readable with the card shut**, because
      they are in `collapsibleCard`'s `always` slot rather than its body. A fold that
      hid the one figure telling a visitor which food still needs work would have made
      the screen shorter and less usable, which is #142's rule for step 4's Remaining
      applied to the figure this screen is about;
    * opening one brings its editor back, and the panel grows by the amount the fold was
      saving — asserted as a relation rather than a pixel count, because the body's
      height is thirteen destinations' worth of rows and that number is data.
    """
    _forked_chain(page)
    page.click('[data-action="explore-improvements"]')
    page.wait_for_selector(".improvement-scenario", timeout=20000)
    page.wait_for_timeout(300)

    shut = page.evaluate("""() => {
      const cards = [...document.querySelectorAll('.improvement-leaf')];
      const panel = document.querySelector('#improvement-section');
      return {
        cards: cards.length,
        toggles: cards.filter(c => c.querySelector('.step-card__toggle[aria-expanded]')).length,
        expanded: cards.map(c => c.querySelector('.step-card__toggle')?.getAttribute('aria-expanded')),
        bodiesHidden: cards.map(c => c.querySelector('.step-card__body')?.hasAttribute('hidden')),
        slidersReachable: [...document.querySelectorAll('.improvement-allocation-list input')]
          .filter(i => i.checkVisibility()).length,
        totalsVisible: cards.map(c => !!c.querySelector('.improvement-total')?.checkVisibility()),
        totalsText: cards.map(c => c.querySelector('.improvement-total')?.textContent?.trim()),
        panelHeight: Math.round(panel.getBoundingClientRect().height),
        names: cards.map(c => c.querySelector('.step-card__name')?.textContent?.trim()),
      };
    }""")

    assert shut["cards"] == 2, f"the forked chain did not draw one card per food: {shut}"
    assert shut["toggles"] == 2, (
        f"a card without a toggle cannot be folded, which is the whole request: {shut}"
    )
    assert shut["expanded"] == ["false", "false"], (
        f"the cards did not arrive shut, so the panel is as long as it was: {shut}"
    )
    assert all(shut["bodiesHidden"]), (
        f"a shut card's body is not `hidden`, so its sliders are invisible and still in "
        f"the tab order: {shut}"
    )
    assert shut["slidersReachable"] == 0, (
        f"{shut['slidersReachable']} slider(s) are still reachable inside a shut card: {shut}"
    )
    assert all(shut["totalsVisible"]), (
        f"the running total is inside the fold, so a reader scanning a folded panel "
        f"cannot see which food still needs work: {shut}"
    )
    assert all("Total allocation" in (text or "") for text in shut["totalsText"]), shut
    assert all(name for name in shut["names"]), (
        f"a card without a name on its header is a fold with nothing to choose by: {shut}"
    )

    page.locator('.improvement-leaf .step-card__toggle[aria-expanded="false"]').first.click()
    page.wait_for_timeout(400)
    opened = page.evaluate("""() => {
      const cards = [...document.querySelectorAll('.improvement-leaf')];
      return {
        expanded: cards.map(c => c.querySelector('.step-card__toggle')?.getAttribute('aria-expanded')),
        slidersReachable: [...document.querySelectorAll('.improvement-allocation-list input')]
          .filter(i => i.checkVisibility()).length,
        pies: [...document.querySelectorAll('[data-improvement-pie]')]
          .filter(e => e.checkVisibility()).length,
        panelHeight: Math.round(
          document.querySelector('#improvement-section').getBoundingClientRect().height),
      };
    }""")

    assert opened["expanded"] == ["true", "false"], (
        f"pressing one card's toggle did not open exactly that card: {opened}"
    )
    assert opened["slidersReachable"] > 0, (
        f"the card opened and its editor did not come back: {opened}"
    )
    assert opened["pies"] == 1, (
        f"{opened['pies']} pie chart(s) are visible with one card open: {opened}"
    )
    assert opened["panelHeight"] > shut["panelHeight"], (
        f"opening a card did not make the panel taller, so the fold was saving nothing: "
        f"{shut['panelHeight']} -> {opened['panelHeight']}"
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


def _card_state(panel):
    """What one leaf's card says about itself: the strip, the figure and the badge."""
    return panel.evaluate(
        """panel => ({
             strayed: panel.querySelector('.improvement-total').classList.contains('invalid'),
             total: panel.querySelector('.improvement-total strong').textContent.trim(),
             state: panel.querySelector('[data-card-status]').dataset.state,
             word: panel.querySelector('[data-card-status-text]').textContent.trim(),
           })"""
    )


def test_a_leafs_card_states_its_own_allocation_and_never_the_panels(page):
    """A card at exactly 100% must not be told it is incomplete because another one is.

    **The defect, in one sentence.** The live keystroke path painted the total strip from
    ``improvementValidation(state)`` -- the whole *panel's* answer -- so a food driven to
    exactly ``100.00%`` was drawn in Beetroot and badged *Incomplete* for as long as any
    **other** food on the panel was unfinished. On a forked chain that is every card but
    the last one finished, and the figure printed inside the strip contradicts both the
    colour around it and the word above it.

    **And the badge was never rewritten at all.** This path patches the DOM instead of
    calling ``setState`` -- a re-render would take the caret out of the box mid-number --
    so nothing re-renders after a keystroke and the badge held whatever it said when the
    panel first opened. Steps 3 and 4 have ``updateCardBadges`` in ``calculator.js`` for
    exactly this; step 5 never got one.

    Both halves are measured here, so **nothing between the fill and the assertions may
    re-render**: the cards are opened before anything is typed, and the witness below
    fails the test if a re-render is what made the badge correct. Without it a future
    ``setState`` slipped into this path would make the assertions pass for the wrong
    reason, which is the failure mode this repository keeps finding.

    **The panel-wide guard has to survive.** Compare Impact is about the submission, not
    about one card, and must stay disabled with its sentence on screen while leaf two sits
    at 0%. A fix that made the card honest by making the button permissive would be a
    worse bug than the one it replaced, so the sentence is asserted to name the food that
    is actually unfinished -- and not the one that is done.
    """
    _forked_chain(page)
    page.click('[data-action="explore-improvements"]')
    page.wait_for_selector('[data-action="reset-improvement"]')
    #: Opened BEFORE anything is typed. A toggle calls `setState` and re-renders, and a
    #: re-render is precisely what must not be the thing that makes the badge correct.
    expand_step_cards(page)

    first = page.locator('[data-improvement-leaf-panel="0"]')
    second = page.locator('[data-improvement-leaf-panel="1"]')
    assert first.count() == 1 and second.count() == 1, (
        "the panel did not fork into two cards, so this test cannot measure one against "
        "the other"
    )
    names = page.evaluate(
        """() => [0, 1].map(i => document.querySelector(
             `[data-improvement-leaf-panel="${i}"] .step-card__name`).textContent.trim())"""
    )
    assert names[0] != names[1], names

    #: The number boxes carry no `id` of their own -- only the slider beside each one does
    #: (`DestinationAllocationRow`) -- so this is addressed through the card it belongs to,
    #: which is also what keeps the fill on leaf one and off leaf two.
    boxes = first.locator('.percentage-input input[type="number"]')
    assert boxes.count() >= 1, "leaf one has no destination box to type into"
    box = boxes.first

    #: The control for "it was not a re-render". `render()` replaces `main.innerHTML`
    #: wholesale, so a badge that survives with this attribute on it is the same node the
    #: first paint produced and was rewritten in place.
    page.evaluate(
        """() => { document.querySelector(
             '[data-improvement-leaf-panel="0"] [data-card-status]'
           ).dataset.witness = 'first-paint' }"""
    )

    #: Short of 100. This card really is the one that is wrong, and it says so -- the
    #: mutation that makes the strip never paint would pass the 100% case alone.
    box.fill("60")
    page.wait_for_timeout(150)
    assert _card_state(first) == {
        "strayed": True, "total": "60.00%", "state": "incomplete", "word": "Incomplete",
    }

    #: Exactly 100, with leaf two still untouched. This is the screenshot the owner sent.
    box.fill("100")
    page.wait_for_timeout(150)
    assert _card_state(first) == {
        "strayed": False, "total": "100.00%", "state": "complete", "word": "Complete",
    }, "a card holding exactly 100.00% was still being told it was incomplete"

    survived = page.evaluate(
        """() => document.querySelector(
             '[data-improvement-leaf-panel="0"] [data-card-status]')?.dataset.witness"""
    )
    assert survived == "first-paint", (
        "the badge is a different node, so the panel re-rendered and this test proves "
        "nothing about the live keystroke path"
    )

    #: Leaf two is untouched: 0% is not an error to paint, but it is not complete either.
    assert _card_state(second) == {
        "strayed": False, "total": "0.00%", "state": "incomplete", "word": "Incomplete",
    }

    #: ...and the submission-wide guard is exactly where it was.
    assert page.locator('[data-action="compare-improvement"]').is_disabled(), (
        "Compare Impact was enabled while one leaf of the submission was at 0%"
    )
    sentence = page.locator("#improvement-inline-error")
    assert sentence.is_visible()
    said = sentence.inner_text().strip()
    assert names[1] in said, (said, names)
    assert names[0] not in said, (
        "the panel is still complaining about the food that is finished: %s" % said
    )

    #: **Stricter than the comparison it replaced, and the reason it is.** The badge asks
    #: `leafAllocationProblem`, which is what Compare Impact asks, so boxes that SUM to 100
    #: with one of them left blank is not a finished card -- the figure is right and the
    #: allocation is not answered. The rule it replaced looked only at the total and called
    #: this *Complete* while the button beside it stayed disabled.
    first.locator('.percentage-input input[type="number"]').nth(1).fill("")
    page.wait_for_timeout(150)
    assert _card_state(first) == {
        "strayed": True, "total": "100.00%", "state": "incomplete", "word": "Incomplete",
    }, "a blank destination box was called complete because the rest of the card summed to 100"

    #: **And the first paint agrees with the keystroke.** Folding the card and opening it
    #: again is a `setState`, so this is the renderer's answer rather than the live path's.
    #: Both come out of `leafCardState`, which is what makes them one statement instead of
    #: two that can drift -- and drift is what this whole case is about.
    for _ in range(2):
        first.locator('[data-action="toggle-card"]').click()
        page.wait_for_timeout(150)
    assert _card_state(first) == {
        "strayed": True, "total": "100.00%", "state": "incomplete", "word": "Incomplete",
    }, "the re-rendered card disagrees with what the same card said while being typed into"

    #: **A keystroke patches the card it belongs to, and this is the case that says so.**
    #: Everything above types into leaf one, so a live path that looked up leaf ZERO
    #: whatever control was being edited would be indistinguishable from the fixed one --
    #: the mutation matrix found exactly that hole and nothing in this file noticed it.
    #: The two cards are put on different totals first, so the figure printed in the strip
    #: is a second witness beside the badge rather than a number both cards happen to share.
    first.locator('.percentage-input input[type="number"]').nth(1).fill("0")
    page.wait_for_timeout(120)
    box.fill("60")
    page.wait_for_timeout(120)
    second.locator('.percentage-input input[type="number"]').first.fill("100")
    page.wait_for_timeout(150)
    assert _card_state(second) == {
        "strayed": False, "total": "100.00%", "state": "complete", "word": "Complete",
    }, "typing into leaf two did not bring leaf two's own card up to date"
    assert _card_state(first) == {
        "strayed": True, "total": "60.00%", "state": "incomplete", "word": "Incomplete",
    }, "leaf one's card moved when the keystroke belonged to leaf two"


# ===========================================================================
# v1.120: the card's two halves, and the dialog's own two boxes.
#
# **Appended as separate functions on purpose.** Another branch is in this file
# at the same time; keeping these at the end and self-contained means the two
# sides merge beside each other rather than through each other. They share
# `_forked_chain` and nothing else.
# ===========================================================================


#: The header button and the `always` strip, read in one round trip.
#:
#: **Both halves, always**, because "they look the same" is a statement about a
#: PAIR: a probe that returned only the strip's background could not say whether
#: the header's was different, and the defect was exactly that the two were the
#: same. `read` is applied to each so a failure prints them side by side.
HALVES = """() => [...document.querySelectorAll('.improvement-leaf')].map(card => {
  const header = card.querySelector('.step-card__toggle');
  const strip = card.querySelector(':scope > .improvement-total');
  const read = element => {
    if (!element) return null;
    const style = getComputedStyle(element);
    const box = element.getBoundingClientRect();
    return {
      background: style.backgroundColor,
      borderWidth: style.borderTopWidth,
      borderStyle: style.borderTopStyle,
      borderColour: style.borderTopColor,
      shadow: style.boxShadow,
      top: Math.round(box.top),
      bottom: Math.round(box.bottom),
    };
  };
  return {
    name: card.querySelector('.step-card__name')?.textContent?.trim(),
    expanded: header.getAttribute('aria-expanded'),
    // Guarded rather than dereferenced, on `VERDICT`'s precedent in
    // test_card_folding_browser.py: a strip moved INSIDE the fold is one of the
    // things these cases are about, and a probe that threw on it would report a
    // TypeError instead of naming what moved.
    stripOutsideFold: Boolean(strip),
    stripInsideToggle: Boolean(strip && strip.closest('.step-card__toggle')),
    stripIsButton: Boolean(strip && (strip.tagName === 'BUTTON' || strip.closest('button'))),
    stripAction: strip ? strip.getAttribute('data-action') : null,
    header: read(header),
    strip: read(strip),
  };
})"""


#: The enlarged-chart dialog, its close button and its heading, as boxes.
#:
#: `elementFromPoint` as well as the two rectangles, because they are different
#: claims: rectangles that do not intersect are geometry, and "what does a
#: visitor's pointer actually land on at the first letter of the food's name" is
#: the question the complaint was about. The probe is taken at the heading's
#: LEADING edge, which is the left in English and the right in Arabic -- a probe
#: hard-coded to `left` would pass in RTL by measuring the END of the name, where
#: the button has never been.
DIALOG = """() => {
  const dialog = document.querySelector('.improvement-chart-expanded');
  const dir = getComputedStyle(document.documentElement).direction;
  if (!dialog) return {dir, dialog: false};
  const close = dialog.querySelector('.improvement-chart-close');
  const heading = dialog.querySelector('.improvement-leaf__heading');
  if (!close || !heading) {
    return {dir, dialog: true, close: Boolean(close), heading: Boolean(heading)};
  }
  const d = dialog.getBoundingClientRect();
  const c = close.getBoundingClientRect();
  const h = heading.getBoundingClientRect();
  const probeX = dir === 'rtl' ? h.right - 4 : h.left + 4;
  const probeY = h.top + h.height / 2;
  const hit = document.elementFromPoint(probeX, probeY);
  const box = r => ({
    left: Math.round(r.left), right: Math.round(r.right),
    top: Math.round(r.top), bottom: Math.round(r.bottom),
  });
  return {
    dir,
    dialog: true,
    close: true,
    heading: heading.textContent.trim(),
    overlapInline: Math.max(0, Math.min(c.right, h.right) - Math.max(c.left, h.left)),
    overlapBlock: Math.max(0, Math.min(c.bottom, h.bottom) - Math.max(c.top, h.top)),
    dialogBox: box(d),
    closeBox: box(c),
    headingBox: box(h),
    // How far the button sits from the dialog's own leading edge. The fix must
    // not have moved it: its corner is an established control location.
    closeFromLeadingEdge: Math.round(dir === 'rtl' ? d.right - c.right : c.left - d.left),
    probe: {x: Math.round(probeX), y: Math.round(probeY)},
    hitTag: hit ? hit.tagName : null,
    hitClass: hit ? String(hit.className) : null,
    hitIsHeading: Boolean(hit && hit.closest('.improvement-leaf__heading')),
    hitIsClose: Boolean(hit && hit.closest('.improvement-chart-close')),
  };
}"""


def _improvement_panel(page):
    """A two-leaf chain standing on the improvement panel, cards as drawn."""
    _forked_chain(page)
    page.click('[data-action="explore-improvements"]')
    page.wait_for_selector(".improvement-scenario", timeout=20000)
    page.wait_for_timeout(300)


def test_the_total_strip_does_not_read_as_the_cards_own_lower_half(page):
    """**v1.120. The card had two halves and one surface.**

    `collapsibleCard` draws three siblings inside the `<fieldset>`: the header
    button, the `always` strip, and the `hidden` body. On this panel the strip is
    `.improvement-total` -- *Total allocation / 0.00% / Total mass: 1.00 kg* -- and
    it was `rgba(255, 255, 255, 0.8)` with a 12px radius and no border, sitting
    18px below the header button on a card whose own ground is `#fff`. A
    0.8-alpha white fill on white is not a colour, so the radius it already had
    read as nothing and the two halves looked like one continuous card. The
    consequence is the owner's complaint: visitors press the lower half, which is
    not a control, and nothing happens.

    **Measured as a difference between the pair, not as a colour.** An assertion
    naming `rgb(223, 248, 237)` would pin a tint nobody promised and would fail
    the next time the brand's mint moves; what the defect WAS is that the two were
    indistinguishable, so what is asserted is that they differ -- in ground, and
    additionally in the border and the elevation that make the strip read as an
    object sitting ON the card rather than as part of it.

    Mutations that fail here: putting `background: rgba(255, 255, 255, 0.8)` back
    (the grounds match again); deleting the `box-shadow` (the island stops being
    lifted); deleting the `border`.
    """
    _improvement_panel(page)
    halves = page.evaluate(HALVES)

    assert len(halves) == 2, (
        f"the chain did not fork, so this measures a panel with no card on it and "
        f"proves nothing about a card's two halves: {halves}"
    )
    for index, card in enumerate(halves):
        assert card["stripOutsideFold"], (
            f"card {index} has no running-total strip outside its fold, so either the "
            f"strip moved into the body or `always` stopped being used: {card}"
        )
        header, strip = card["header"], card["strip"]
        assert strip["background"] != header["background"], (
            f"card {index}'s header and its running-total strip compute the same "
            f"background ({strip['background']}), so they are one surface again and a "
            f"visitor cannot tell which half opens the card: {card}"
        )
        #: **Opaque, and that is the one thing about the ground worth pinning.**
        #: Chromium serialises a fully opaque background as `rgb(...)` and anything
        #: less as `rgba(...)` -- the same distinction
        #: `test_step_navigation.py::test_the_allocation_summary_is_opaque_in_both_states`
        #: is built on. The defect was not "the wrong colour", it was a 0.8-alpha
        #: white over a white card, which is not a colour at all; and the assertion
        #: above cannot catch its return, because the header computes
        #: `rgba(0, 0, 0, 0)` and a translucent white is a different string from
        #: that. A specific tint is deliberately NOT asserted: a brand revision to
        #: `--mint` must not fail a case about distinguishability.
        assert not strip["background"].startswith("rgba("), (
            f"card {index}'s strip has a translucent ground ({strip['background']}) "
            f"again, which over the card's own white is what read as nothing: {card}"
        )
        assert strip["shadow"] != "none", (
            f"card {index}'s strip has no elevation, so it does not read as an island "
            f"sitting on the card: {card}"
        )
        assert header["shadow"] == "none", (
            f"card {index}'s HEADER grew a shadow of its own, which cancels the "
            f"distinction the strip's elevation is there to make: {card}"
        )
        assert strip["borderStyle"] == "solid" and strip["borderWidth"] != "0px", (
            f"card {index}'s strip has no edge of its own: {card}"
        )
        assert header["borderWidth"] == "0px", (
            f"card {index}'s header grew a border, so the two halves are two bordered "
            f"boxes now and the strip's edge no longer singles it out: {card}"
        )
        assert strip["top"] >= header["bottom"], (
            f"card {index}'s strip overlaps its header: {card}"
        )


def test_the_total_strip_is_not_a_control_and_pressing_it_does_not_open_the_card(page):
    """**The behaviour the owner observed, asserted so that nobody "fixes" it.**

    The complaint was that pressing the lower half does nothing. The wrong remedy
    is to make the strip clickable: it is an `aria-live` region holding two
    figures, the header is the card's one control and it carries the
    `aria-expanded` and the `aria-controls` that say so, and a second control over
    one disclosure is two things announcing one state. The right remedy is the one
    above -- stop the strip LOOKING like the half that opens the card.

    So this pins the status quo: the strip is outside the header button, is not a
    button, carries no `data-action`, and pressing it leaves every card shut.

    **And then the header is pressed**, because "pressing X changes nothing"
    passes trivially on a page where pressing anything changes nothing -- a
    broken toggle, a card list that never rendered, a module that threw at load.
    The second half is what makes the first half evidence.

    Mutation: giving the strip `data-action="toggle-card"` in `improvement.js`
    fails on the `stripAction` assertion and again on the press.
    """
    _improvement_panel(page)
    before = page.evaluate(HALVES)
    assert len(before) == 2, before
    assert [card["expanded"] for card in before] == ["false", "false"], (
        f"the cards did not arrive shut, so a press cannot be observed to leave them "
        f"shut: {before}"
    )
    assert not any(card["stripInsideToggle"] for card in before), (
        f"the running-total strip is inside the header button, so it IS the control and "
        f"`collapsibleCard`'s three siblings have become two: {before}"
    )
    assert not any(card["stripIsButton"] for card in before), before
    assert all(card["stripAction"] is None for card in before), (
        f"the strip carries a `data-action`, so it has been made a second control over "
        f"the card's one disclosure: {before}"
    )

    page.locator(".improvement-leaf").nth(0).locator(".improvement-total").click()
    page.wait_for_timeout(250)
    pressed = page.evaluate(HALVES)
    assert [card["expanded"] for card in pressed] == ["false", "false"], (
        f"pressing the running-total strip opened a card: the strip is a control now, "
        f"which is the remedy this project chose against: {pressed}"
    )

    page.locator('.improvement-leaf .step-card__toggle[aria-expanded="false"]').first.click()
    page.wait_for_timeout(350)
    opened = page.evaluate(HALVES)
    assert [card["expanded"] for card in opened] == ["true", "false"], (
        f"the HEADER does not open its card either, so the assertion above measured a "
        f"dead page rather than a strip that is correctly inert: {opened}"
    )


def _expanded_chart(browser, lang):
    """A two-leaf chain with one card's chart enlarged, in `lang`.

    Its own context rather than the module's `page` fixture, because the reading
    direction is what the RTL case is about and `i18n.js` sets `root.dir` from the
    catalogue -- so the language has to be chosen before the first paint.
    """
    context = browser.new_context(viewport={"width": 1278, "height": 983}, locale="en-NZ")
    opened = context.new_page()
    try:
        opened.goto(f"{BASE}?lang={lang}", wait_until="networkidle", timeout=15000)
    except Exception as error:  # pragma: no cover - environment guard
        context.close()
        pytest.skip(f"the front end is not being served at {BASE}: {error}")
    opened.wait_for_selector('[data-action="start"]', timeout=10000)
    _forked_chain(opened)
    opened.click('[data-action="explore-improvements"]')
    opened.wait_for_selector(".improvement-scenario", timeout=20000)
    #: The expand control sits in `.improvement-pie-wrap`, which is inside the card
    #: BODY -- and the cards arrive shut since v1.100, so the control is in the
    #: document and not pressable. `expand_step_cards` opens them through the
    #: toggle a visitor would press, which is the reason that helper exists.
    expand_step_cards(opened)
    opened.locator('[data-action="expand-improvement-chart"]').first.click()
    opened.wait_for_selector(".improvement-chart-expanded", timeout=10000)
    opened.wait_for_timeout(250)
    return context, opened


@pytest.mark.parametrize("lang,direction", [("en", "ltr"), ("ar", "rtl")])
def test_the_expanded_charts_close_button_does_not_cover_the_food_name(browser, lang, direction):
    """**v1.120. The dialog's close button was on top of its own heading.**

    `.improvement-chart-expanded` is `position: relative` with
    `padding: 34px 44px 28px`; `.improvement-chart-close` is 42px square at an
    inset of 14px, and an absolutely positioned box resolves its insets against
    its containing block's PADDING box -- so the button occupied 14-56px on the
    inline axis while the `<h3>` began at the content edge, 44px. Measured before
    the fix: the boxes overlapped by 12 x 20.8px and `document.elementFromPoint`
    at the heading's leading edge answered `improvement-chart-close`. The button
    was genuinely over the first characters of the food's name, not merely close
    to them.

    **The inline axis is the one that has to be clear, and the block axis is
    deliberately not asserted.** The button is level with the heading by design,
    so `overlapBlock` is about 20px, and asking for zero there would be asking for
    the button to be moved -- a different change that nobody has asked for. The
    last assertion pins the button where it is for that reason.

    **Both reading directions, because the fix is a logical property.** The space
    is reserved with `margin-inline-start`, so under `dir="rtl"` -- Arabic and Urdu
    ship -- it reserves on the right, which is the side `inset-inline-start` has
    moved the button to. A physical `padding-left` keeps the English case green and
    fails the Arabic one with the button back over the name;
    `test_i18n_web.py::test_the_stylesheet_carries_no_physical_direction_left`
    refuses the property outright and this is the case that says what it costs.

    A MARGIN and not a padding, which is measurable here too: padding sits inside
    the heading's border box, so `padding-inline-start: 26px` would move the text
    and leave `getBoundingClientRect()` starting at 44 -- still overlapping by 12px
    -- which `overlapInline` catches and `elementFromPoint` does not.

    A forked chain, because `improvement.js` draws the `<h3>` only when the panel
    has more than one leaf: on a single-leaf panel the card header carries the name
    and there is nothing in the dialog to collide with.
    """
    context, opened = _expanded_chart(browser, lang)
    try:
        measured = opened.evaluate(DIALOG)
    finally:
        context.close()

    assert measured["dialog"], f"the enlarged-chart dialog did not open: {measured}"
    assert measured["dir"] == direction, (
        f"the page rendered {measured['dir']} for lang={lang!r}, so nothing below is a "
        f"measurement of that reading direction: {measured}"
    )
    assert measured.get("close") and measured.get("heading"), (
        f"the dialog drew no close button or no food name, so there is nothing to "
        f"collide: a forked chain is what puts the <h3> there: {measured}"
    )
    assert measured["overlapInline"] == 0, (
        f"the close button and the food's name overlap by {measured['overlapInline']}px "
        f"on the inline axis in {direction}: button {measured['closeBox']}, heading "
        f"{measured['headingBox']}. The dialog has to leave the button's column to the "
        f"button: {measured}"
    )
    assert measured["hitIsHeading"], (
        f"in {direction} the point at the heading's first characters "
        f"({measured['probe']}) belongs to {measured['hitTag']}.{measured['hitClass']}, "
        f"not to the heading: {measured}"
    )
    assert not measured["hitIsClose"], (
        f"in {direction} the close button is still what a pointer lands on at the first "
        f"characters of the food's name: {measured}"
    )
    assert measured["closeFromLeadingEdge"] == 14, (
        f"the close button is {measured['closeFromLeadingEdge']}px from the dialog's "
        f"leading edge rather than its established 14px, so the overlap was resolved by "
        f"MOVING the control instead of reserving its space: {measured}"
    )


#: The header's ground and the island's, from one card, in one round trip. Read on the
#: improvement panel rather than on step 4 because **this** is where the island is: the
#: claim below is that the header's hover colour is not the island's, and step 4's slot
#: is `.allocation-summary` on its own opaque tint, so the comparison there is against
#: the wrong object and passes whatever the header does.
HOVER_GROUNDS = """() => {
  const card = document.querySelector('.improvement-leaf');
  const header = card.querySelector('.step-card__toggle');
  const island = card.querySelector(':scope > .improvement-total');
  return {
    header: getComputedStyle(header).backgroundColor,
    island: getComputedStyle(island).backgroundColor,
    isStatic: header.classList.contains('step-card__toggle--static'),
  };
}"""


def _transparent(colour):
    """`rgba(…, 0)` or `transparent`, however the browser spells it."""
    if colour in (None, "transparent"):
        return True
    alpha = colour.rsplit(",", 1)[-1].strip(" )") if colour.startswith("rgba(") else "1"
    return float(alpha) == 0


def test_the_header_takes_a_ground_under_the_pointer_and_never_the_islands(page):
    """The half that answers a press says so, in a colour that is not the island's.

    **v1.120 delivered this and nothing measured it.** The island tells a visitor that
    the lower half of a card is not a control; the header's hover ground is what tells
    them the upper half is. Three mutations were written for it in advance -- take the
    hover away, give it the island's own `--mint`, paint the header at rest instead --
    and all three were expected to survive the whole repository, which is the same as
    saying the affordance was written rather than delivered.

    **The second assertion is the one that needed moving to find.** It was first written
    against step 4's card, on the argument that `.step-card__toggle` is shared chrome and
    a hover measured on any consumer is a statement about all four. True of *whether*
    there is a hover; false of *which colour it must not be*. Step 4's slot is
    `.allocation-summary` on its own opaque `#e9faf3`, so comparing the header's hover
    against it passes while the header is painted `--mint` -- and `--mint` is exactly the
    colour that must not be used. Measured: that version of this case left the mutation
    alive. It is here now, where the island actually is.

    A header that took the island's own ground on hover would erase the distinction at
    the moment the visitor is reaching for one of the two halves: the pair would meet in
    one colour under the pointer, which is this change's own defect re-created in the
    hover state. `--zone` is the same green one step lighter and is already the
    stylesheet's quiet form ground.

    **At rest first.** A case that read only the hovered value would pass on a header
    painted permanently, which is a second colour and not an affordance.
    """
    _improvement_panel(page)
    at_rest = page.evaluate(HOVER_GROUNDS)
    assert not at_rest["isStatic"], (
        "the panel drew a lone, unfoldable card, so there is no control here to promise "
        "anything and this case would be measuring a <div>"
    )
    assert _transparent(at_rest["header"]), (
        f"the card header is painted at rest ({at_rest['header']}), so a hover ground is "
        "not an affordance -- it is a second permanent colour"
    )

    page.hover(".improvement-leaf .step-card__toggle")
    page.wait_for_timeout(150)
    hovered = page.evaluate(HOVER_GROUNDS)

    assert not _transparent(hovered["header"]), (
        "the card header takes no ground under the pointer, so nothing on screen says "
        "which half of the card answers a press -- the half the island cannot speak for"
    )
    assert hovered["header"] != hovered["island"], (
        f"the header's hover ground is the island's own colour ({hovered['header']}), so "
        "the two halves meet in one colour at exactly the moment the visitor is reaching "
        "for one of them: this change's defect, re-created in the hover state"
    )


def test_a_strayed_island_is_outlined_as_well_as_worded(page):
    """`.improvement-total.invalid` changes the island's edge, not only its text.

    **Added because a mutation survived.** Deleting `border-color: var(--error)` from the
    invalid rule left every case green: #193's card-state case asserts the `invalid`
    class is applied and the strip's own case measures the island against the header at
    rest, and neither looks at what the class does to the edge. A declaration nothing can
    kill is a decoration, and this one is not meant to be: the island's outline is
    `--border` while the figures add up and `--error` while they do not, so a strayed
    allocation is said by the shape as well as by the words inside it -- which is what
    carries the state to a reader who is scanning a folded panel rather than reading it.

    Measured as a *change* from the resting edge rather than against a named colour, for
    the reason the strip's own case gives: naming `rgb(135, 0, 90)` would pin a tint
    nobody promised, and the claim is that the edge answers the state, not that it is
    Beetroot.

    The radius and the shadow are asserted to survive the state too. A strayed strip that
    lost them would be a flat band flush with the card again -- the defect v1.120 fixed,
    arriving the moment the figures stop adding up, which is the worst possible moment.
    """
    _improvement_panel(page)
    #: The cards are drawn shut, and the box that has to be typed into is inside the
    #: fold. The strip is not -- it is the `always` slot, which is the whole point of it
    #: -- so this opens the card for the keystroke, not for the measurement.
    expand_step_cards(page)
    before = page.evaluate(HALVES)[0]
    assert before["strip"]["borderStyle"] != "none", (
        "the island has no edge at rest, so this case cannot say the state changed one"
    )

    #: Drive leaf one off 100% so the strip strays. 60 into the first destination is
    #: short of the total, which is what `.invalid` means.
    box = page.locator('[data-improvement-leaf-panel="0"] .percentage-input input[type="number"]').first
    box.fill("60")
    page.wait_for_timeout(200)

    after = page.evaluate(HALVES)[0]
    assert after["strip"]["borderColour"] != before["strip"]["borderColour"], (
        "a strayed allocation left the island's edge exactly as it was, so the state is "
        "carried by the text alone: %s then %s"
        % (before["strip"]["borderColour"], after["strip"]["borderColour"])
    )
    assert after["strip"]["shadow"] == before["strip"]["shadow"], (
        "the strayed island lost its elevation, so it is a flat band flush with the card "
        "again at the moment the figures stop adding up"
    )
