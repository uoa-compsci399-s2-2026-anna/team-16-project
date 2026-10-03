"""Step 1's sector cards are the size of what they hold, measured in a browser.

**The complaint, in the owner's words:** on step 1 the text "looks like it
floats on the card rather than being centred inside it". It was addressed once
before and did not take, and the reason it did not take is in this directory
rather than in the stylesheet - *nothing asserted a number*. A test that reads
"the card is visible" or "`.stage-copy` exists" passes identically against the
defect and against the fix, so the defect came back unobserved. Every
assertion in this file is a geometry in CSS pixels.

**Two mechanisms produced the one symptom, and both are measured here.**

*Wide.* `.stage-select` carried `min-height: 92px` with `align-items:
flex-start`. Copy shorter than the box pinned to the top and the remainder
showed as empty space underneath: 23.5px on all six cards at 1280px. The 92
was not a design constant. It is the arithmetic of a card carrying one line of
description - 20 padding + 22.5 title + 6 `.stage-copy` row gap + 24 for one
line + 20 padding = 92.5 - so it was a floor sized for content the client has
not supplied yet (open item O-1). It held that space open against a payload
that has never contained it.

*Narrow, selected only.* `.selected-label` ("Selected") is a third flex child
of `.stage-select`. Below 480px `flex-wrap: wrap` puts it on its own line, and
at that moment `gap: 15px` - a shorthand written for the horizontal space
between the radio and the copy - starts paying out on the block axis as well.
The pill's own authored spacing is `margin: 8px 0 0`; the browser drew 23. The
author budgeted 8 and a shorthand charged nearly three times it.

**Why `align-items: center` is not the fix, and why this file asserts the
radio.** It is the reading the complaint most obviously invites, and it is
wrong in a way that only shows up in a state the calculator cannot currently
render. `align-items` centres *every* flex child, the radio included, so as
the copy grows the radio slides away from the line it labels. Measured, the
radio's centre against the centre of the title's first line box::

    content state     as shipped   align-items: center
    empty (today)          2.5           4.7
    one-line               2.5          16.7   (1280px)
    fixture prose          2.5          52.7   (1280px)
    fixture prose          2.5         136.7   (320px, selected)

A radio 136px below its own title, beside the fifth line of body text. It
would have looked like a fix for exactly as long as the descriptions stayed
empty. `test_the_radio_stays_on_the_line_of_the_title_it_labels` is the
assertion that refuses it, and it is the reason this file measures the *first
line box* via `Range.getClientRects()[0]` rather than the title's bounding
box - against a wrapped title a bounding box averages the lines away and reads
"fine".

`.stage-copy { align-self: center }` fails the same assertion from the other
side: it measures -9.3 today, the radio floating above its own title, correct
again only once a description arrives to fill the box.

**Descriptions are supplied by this file, not by the server.** All six sectors
return `description` of length 0 today, so the state the fix has to survive is
not reachable from the running stack. `GET /api/v1/taxonomy` is fulfilled in
the browser from three bodies - empty, one line, and the prose already sitting
in `tests/fixtures/taxonomy.json` (150-276 characters, six sectors) - which is
also what keeps these numbers meaningful *after* O-1 resolves: the empty-state
geometry stays measurable when the real payload stops being empty. Fulfilling
the route locally also keeps this file off the taxonomy rate limit.

**The rhythm is asserted, not assumed.** The supervisor's rule on this project
is to be wide where it should be wide and narrow where it should be narrow,
and never to fix one problem by adding pixels in a way that regresses the
spacing inside a card - a previous change here halved a 50px rhythm to 25px by
altering how elements related to each other, and only measurement caught it.
So `test_the_rhythm_between_and_inside_cards_is_untouched` pins the 14px
`.stage-card-list` gap and the 6px title-to-description gap, and
`test_the_card_is_not_inflated_with_block_padding_instead` pins the authored
20px/18px block padding - which is the assertion that refuses buying today's
roomy card back with `padding-block`, a fix that would pay for the absent
description at every content length forever.

**Every assertion here was checked by mutation**, because a rule no test kills
is a rule no test is checking, and on this project "the test passed for the
wrong reason" has a long enough history to be assumed rather than hoped
against. Restoring the 92px floor, restoring the row gap, `align-items:
center`, `padding-block: 35px`, halving the card-list gap and halving the
title-to-description gap each fail a named test here. The padding assertion in
particular exists *because* the mutation run found that the slack assertions
alone let `padding-block: 35px` through: they measure the content box, and an
inflated card has a perfectly clean content box.

Requires the stack up: `docker compose -f docker/compose.yaml up -d --build
web`. Skipped rather than failed when it is not.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tests.web.base_url import CALCULATOR

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to measure where the copy sits inside a card; the spacing is unverified without it",
)

ROOT = Path(__file__).resolve().parents[2]
BASE = CALCULATOR
TAXONOMY_URL = BASE.rsplit("/", 1)[0] + "/api/v1/taxonomy"

#: Injected as a stylesheet after load, the same hook `test_step_navigation.py`
#: uses: every rule this file depends on can be knocked out and the test
#: watched to fail. A rule no test kills is a rule no test is really checking.
MUTATION_CSS = os.environ.get("KAICALC_MUTATION_CSS", "")

#: One line of description - the shortest payload that makes the card's natural
#: height exceed the floor that used to be declared on it.
ONE_LINE = "Food waste arising at this stage of the supply chain."


def _live_taxonomy() -> dict:
    try:
        with urllib.request.urlopen(TAXONOMY_URL, timeout=10) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError) as error:  # pragma: no cover - environment guard
        pytest.skip(f"the taxonomy is not being served at {TAXONOMY_URL}: {error}")


def _fixture_descriptions() -> "dict[str, str]":
    document = json.loads((ROOT / "tests" / "fixtures" / "taxonomy.json").read_text(encoding="utf-8"))
    return {s["code"]: s["description"] for s in document["sectors"] if s.get("description")}


#: The measurement, in one place because every test below wants the same
#: numbers about a different card.
#:
#: `boxTop`/`boxHeight` are `.stage-select`'s *content* box - the padding is
#: deliberately excluded, because the padding is the thing doing the centring
#: and a measurement that swallowed it could not tell slack from spacing.
MEASURE = """() => [...document.querySelectorAll('.stage-card')].map((card, i) => {
  const select = card.querySelector('.stage-select');
  const copy = card.querySelector('.stage-copy');
  const title = card.querySelector('.stage-title');
  const description = card.querySelector('.stage-description');
  const radio = card.querySelector('input[type=radio]');
  const pill = card.querySelector('.selected-label');
  const details = card.querySelector('.details-button');
  const style = getComputedStyle(select);
  const padTop = parseFloat(style.paddingTop), padBottom = parseFloat(style.paddingBottom);
  const S = select.getBoundingClientRect(), C = copy.getBoundingClientRect();
  const R = radio.getBoundingClientRect(), T = title.getBoundingClientRect();
  const boxTop = S.top + padTop, boxHeight = S.height - padTop - padBottom;
  // The centre of the title's FIRST LINE BOX, not of the title element: a
  // wrapped title would otherwise average its lines and hide the drift.
  const range = document.createRange();
  range.selectNodeContents(title);
  const firstLine = range.getClientRects()[0];
  const D = description ? description.getBoundingClientRect() : null;
  const P = pill ? pill.getBoundingClientRect() : null;
  const round = n => Math.round(n * 10) / 10;
  return {
    index: i,
    selected: card.classList.contains('selected'),
    title: title.textContent,
    cardHeight: round(card.getBoundingClientRect().height),
    selectHeight: round(S.height),
    contentBoxHeight: round(boxHeight),
    copyHeight: round(C.height),
    gapAbove: round(C.top - boxTop),
    // SLACK, not "whatever is under the copy". Below 480px the wrapped pill
    // lives under the copy and is content, so the emptiness is measured from
    // the lowest flex child rather than from the copy - otherwise mechanism
    // (b) would be double-counted here and mechanism (a) could be "fixed" by
    // pushing something into the hole.
    slackBelow: round((boxTop + boxHeight) - Math.max.apply(null,
      [C.bottom, R.bottom].concat(P ? [P.bottom] : []))),
    copyToBoxBottom: round((boxTop + boxHeight) - (C.top + C.height)),
    radioVsFirstLine: firstLine ? round((R.top + R.height / 2) - (firstLine.top + firstLine.height / 2)) : null,
    pillGap: P ? round(P.top - (C.top + C.height)) : null,
    titleToDescription: (D && D.height > 0) ? round(D.top - T.bottom) : null,
    detailsHeight: details ? round(details.getBoundingClientRect().height) : null,
    // The padding is deliberately OUTSIDE every measurement above, so it has
    // to be asserted in its own right - a card inflated with `padding-block`
    // has no slack in its content box at all and would otherwise read clean.
    padBlockStart: round(padTop),
    padBlockEnd: round(padBottom),
  };
})"""

CARD_GAPS = """() => {
  const cards = [...document.querySelectorAll('.stage-card')];
  const round = n => Math.round(n * 10) / 10;
  return cards.slice(1).map((card, i) =>
    round(card.getBoundingClientRect().top - cards[i].getBoundingClientRect().bottom));
}"""

SIDEWAYS = "() => [document.documentElement.scrollWidth, window.innerWidth]"


@pytest.fixture(scope="module")
def taxonomy_bodies():
    """The three content states, as whole `/api/v1/taxonomy` response bodies.

    Rewriting the response rather than the DOM is what makes the `empty` state
    survive O-1: when the client finally supplies descriptions this file keeps
    measuring a card whose description is absent, which is the state the
    stylesheet still has to be right about for any sector nobody has written
    copy for yet.
    """
    live = _live_taxonomy()
    fixture = _fixture_descriptions()

    def rewritten(pick):
        document = json.loads(json.dumps(live))
        for sector in document["sectors"]:
            sector["description"] = pick(sector)
        return json.dumps(document)

    return {
        "empty": rewritten(lambda s: ""),
        "one-line": rewritten(lambda s: ONE_LINE),
        "fixture": rewritten(lambda s: fixture.get(s["code"], ONE_LINE)),
    }


@pytest.fixture
def step_one(browser, taxonomy_bodies):
    """Step 1, at a width, in a content state, optionally with a card selected.

    `bypass_csp=True` is for the mutation hook alone: `style-src 'self'` is
    correct, and `tests/web/test_csp.py` is what enforces it unbypassed.
    """
    contexts = []

    def open_step(width, state="empty", select=False, lang="en"):
        context = browser.new_context(
            viewport={"width": width, "height": 900},
            locale="en-NZ" if lang == "en" else "ar-SA",
            bypass_csp=True,
        )
        contexts.append(context)
        page = context.new_page()
        body = taxonomy_bodies[state]
        page.route(
            "**/api/v1/taxonomy*",
            lambda route, request, body=body: route.fulfill(
                status=200, content_type="application/json", body=body),
        )
        url = BASE + ("&" if "?" in BASE else "?") + f"lang={lang}"
        try:
            page.goto(url, wait_until="networkidle", timeout=15000)
        except Exception as error:  # pragma: no cover - environment guard
            pytest.skip(f"the front end is not being served at {BASE}: {error}")
        page.wait_for_selector('[data-action="start"]', timeout=10000)
        page.click('[data-action="start"]')
        page.wait_for_selector(".stage-card", timeout=10000)
        if select:
            # The list re-renders wholesale on selection, so this has to settle
            # before anything is measured.
            page.evaluate("() => document.querySelectorAll('input[name=sector]')[0].click()")
            page.wait_for_selector(".stage-card.selected", timeout=10000)
        if MUTATION_CSS:
            page.add_style_tag(content=MUTATION_CSS)
        page.wait_for_timeout(120)
        return page

    yield open_step
    for context in contexts:
        context.close()


def _report(cards, note):
    """Every failure below prints the whole grid it measured.

    A geometry assertion that reports only `False != True` costs the next
    person the entire measuring run again.
    """
    lines = [note, f"{'#':>2} {'sel':>5} {'card':>7} {'box':>7} {'copy':>7} "
                   f"{'above':>6} {'slack':>6} {'c>box':>6} {'radio':>6} {'pill':>6}  title"]
    for card in cards:
        lines.append(
            f"{card['index']:>2} {str(card['selected']):>5} {card['cardHeight']:>7} "
            f"{card['contentBoxHeight']:>7} {card['copyHeight']:>7} {card['gapAbove']:>6} "
            f"{card['slackBelow']:>6} {card['copyToBoxBottom']:>6} {str(card['radioVsFirstLine']):>6} "
            f"{str(card['pillGap']):>6}  {card['title'][:30]}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The two mechanisms.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("width", [1280, 938, 481, 320])
@pytest.mark.parametrize("select", [False, True], ids=["plain", "selected"])
def test_the_copy_is_not_pinned_to_the_top_of_a_taller_box(step_one, width, select):
    """Whatever slack `.stage-select`'s content box has, the copy sits in the
    MIDDLE of it - equal above and below, never all of it underneath.

    Asserting *both* edges is the point, and asserting their EQUALITY rather
    than their absence is the owner's decision: the 92px floor stays, because
    the card at that height reads comfortably, so the fix centres the copy in
    the box rather than shrinking the box onto the copy. Zero above and 23.5
    below was the complaint; 11.8 and 11.8 is the fix.

    **This is not `align-items: center`, and the difference is load-bearing.**
    That declaration centres every flex child independently, so the radio drifts
    down the block as the copy grows - measured on this branch at 16.7px with
    one line of description and 136.7px at 320px with the fixture prose, a radio
    beside the fifth line of its own body text. The stylesheet instead centres
    the flex LINE as a unit (`flex-wrap: wrap` with `align-content: center`)
    while `align-items: flex-start` holds the radio at the top of that line, so
    the radio travels with the copy and stays on the title.
    `test_the_radio_stays_on_the_line_of_the_title_it_labels` is what holds the
    two apart in all three content states: if this test is ever made to pass by
    reaching for `align-items: center`, that one goes red.

    `.stage-copy` carries `flex: 1 1 0` for the same reason. Turning on
    `flex-wrap: wrap` at wide widths without it let a long description take its
    max-content width, overflow the line and wrap BELOW the radio - putting the
    radio 21.5px ABOVE the first line of the title it labels, the same defect by
    another route.
    """
    page = step_one(width, state="empty", select=select)
    cards = page.evaluate(MEASURE)
    assert cards, "step 1 rendered no sector cards"
    # Symmetry, not absence: at 320px there is no floor and both edges read 0,
    # which satisfies this as surely as 11.8 and 11.8 do at 1280.
    offenders = [c for c in cards if abs(c["gapAbove"] - c["slackBelow"]) > 4]
    assert not offenders, _report(cards, f"slack inside .stage-select at {width}px (empty descriptions):")


def test_the_selected_card_at_320_does_not_strand_the_pill_a_line_below_the_copy(step_one):
    """Mechanism (b): the 49px case, which has its own cause.

    Below 480px the pill wraps to its own flex line and `gap: 15px` - written
    for the horizontal space between the radio and the copy - starts paying
    out vertically too, on top of the pill's own authored `margin: 8px 0 0`.
    This asserts that the authored 8 is what gets drawn.
    """
    page = step_one(320, state="empty", select=True)
    cards = page.evaluate(MEASURE)
    selected = [c for c in cards if c["selected"]]
    assert len(selected) == 1, _report(cards, "expected exactly one selected card at 320px:")
    card = selected[0]
    assert card["pillGap"] is not None, "the selected card rendered no Selected pill"
    assert card["pillGap"] <= 10, _report(
        cards, f"the Selected pill sits {card['pillGap']}px below the copy at 320px "
               f"while its own margin declares 8:")


# ---------------------------------------------------------------------------
# The state the fix has to survive.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("state", ["one-line", "fixture"])
@pytest.mark.parametrize("width", [1280, 320])
@pytest.mark.parametrize("select", [False, True], ids=["plain", "selected"])
def test_real_descriptions_do_not_reintroduce_slack(step_one, state, width, select):
    """The card must still be the size of its content once O-1 resolves.

    A fix correct only for an empty card is not the fix: `min-height: 92px`
    was itself a number that looked right against the content of the day, and
    the whole reason it was wrong is that the content changed underneath it.

    Note what this does *not* catch, because it measures the content box: a
    card inflated with `padding-block` has no slack here at all.
    `test_the_card_is_not_inflated_with_block_padding_instead` is what refuses
    that, and the two are only a pair - neither alone says "the card is the
    size of what it holds".
    """
    page = step_one(width, state=state, select=select)
    cards = page.evaluate(MEASURE)
    # Symmetry, not absence: at 320px there is no floor and both edges read 0,
    # which satisfies this as surely as 11.8 and 11.8 do at 1280.
    offenders = [c for c in cards if abs(c["gapAbove"] - c["slackBelow"]) > 4]
    assert not offenders, _report(cards, f"slack inside .stage-select at {width}px with {state} descriptions:")


@pytest.mark.parametrize("state", ["empty", "one-line", "fixture"])
@pytest.mark.parametrize("width,expected", [(1280, 20), (320, 18)])
def test_the_card_is_not_inflated_with_block_padding_instead(step_one, state, width, expected):
    """Symmetric block padding is what centres the copy, so the padding is the
    one thing this fix was not allowed to touch.

    Every other assertion in this file measures `.stage-select`'s *content*
    box, which makes them all blind to padding by construction: a card given
    `padding-block: 35px` has no slack in its content box whatsoever and reads
    perfectly clean. That is not hypothetical - it is the shape of the
    tempting alternative fix, which preserves today's roomy 94px card by
    paying for the absent description in padding rather than in `min-height`,
    and then goes on paying for it at every content length forever (measured:
    124.5px with one line of description where the card should be 94.5, and
    196.5px with the real prose where it should be 166.5).

    So the authored 20px/18px is pinned here, symmetric, at both widths and in
    all three content states. The supervisor's rule on this project is never
    to fix a spacing complaint by adding pixels; this is that rule as an
    assertion rather than as an intention.
    """
    page = step_one(width, state=state, select=False)
    cards = page.evaluate(MEASURE)
    wrong = [c for c in cards
             if abs(c["padBlockStart"] - expected) > 0.6 or abs(c["padBlockEnd"] - expected) > 0.6]
    assert not wrong, _report(
        cards, f"block padding at {width}px with {state} descriptions is not a symmetric {expected}px: "
               + str([(c["padBlockStart"], c["padBlockEnd"]) for c in cards]))


@pytest.mark.parametrize("state", ["empty", "one-line", "fixture"])
@pytest.mark.parametrize("width", [1280, 320])
@pytest.mark.parametrize("select", [False, True], ids=["plain", "selected"])
def test_the_radio_stays_on_the_line_of_the_title_it_labels(step_one, state, width, select):
    """`.stage-select input` carries `margin: 2px 0 0` - a deliberate optical
    nudge that drops the 22px control onto the cap height of the bold title
    rather than onto its line box. Measured, that is +2.5px in every state.

    This is the assertion a slack measurement alone would not make, and it is
    the one that disqualifies both intuitive fixes: `align-items: center`
    reads 4.7 / 16.7 / 52.7 / 136.7 as the copy grows, and
    `.stage-copy { align-self: center }` reads -9.3 today. Either would have
    been scored "centred" by eye and by a slack measurement alone.
    """
    page = step_one(width, state=state, select=select)
    cards = page.evaluate(MEASURE)
    # `or 0` would coerce a null into the middle of the window and pass. A null
    # means `Range.getClientRects()` returned nothing for the title's first line,
    # which is a measurement that did not happen rather than one that succeeded.
    blind = [c for c in cards if c["radioVsFirstLine"] is None]
    assert not blind, _report(
        cards, f"the first line of the title could not be measured at {width}px:")
    offenders = [c for c in cards if not (0 <= c["radioVsFirstLine"] <= 6)]
    assert not offenders, _report(
        cards, f"the radio has left the first line of its title at {width}px with {state} descriptions:")


# ---------------------------------------------------------------------------
# The supervisor's rule: nothing inside or between the cards may move.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("state", ["empty", "one-line", "fixture"])
@pytest.mark.parametrize("width", [1280, 320])
def test_the_rhythm_between_and_inside_cards_is_untouched(step_one, state, width):
    """14px between cards, 6px between a title and its description.

    `.stage-card-list` declares `gap: 14px` and `.stage-copy` declares
    `gap: 6px`. A previous change on this project halved a 50px rhythm to 25px
    by altering how elements related to one another, and only measurement
    caught it; these are the two rhythms a change to `.stage-select` could
    plausibly reach.
    """
    page = step_one(width, state=state, select=False)
    gaps = page.evaluate(CARD_GAPS)
    assert gaps and all(abs(gap - 14) <= 0.6 for gap in gaps), f"card-to-card gaps at {width}px: {gaps}"
    cards = page.evaluate(MEASURE)
    rendered = [c["titleToDescription"] for c in cards if c["titleToDescription"] is not None]
    if state == "empty":
        assert not rendered, f"an empty description still drew a box at {width}px: {rendered}"
    else:
        assert len(rendered) == len(cards), f"a description failed to render at {width}px: {rendered}"
        assert all(abs(gap - 6) <= 0.6 for gap in rendered), f"title-to-description gaps at {width}px: {rendered}"


@pytest.mark.parametrize("select", [False, True], ids=["plain", "selected"])
def test_the_details_button_keeps_a_reachable_target(step_one, select):
    """`.details-button` stretches to the card's grid row, so shrinking the
    card shrinks the button. WCAG 2.5.5 wants 44x44, and the button's own
    `min-height: 48px` is the floor that has to still be doing the work once
    the card stops declaring one of its own.
    """
    page = step_one(1280, state="empty", select=select)
    cards = page.evaluate(MEASURE)
    short = [c for c in cards if (c["detailsHeight"] or 0) < 44]
    assert not short, _report(cards, "the Details button fell below a 44px target:")


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_step_one_does_not_scroll_sideways_at_320(step_one, lang):
    """Both directions. `column-gap` and `row-gap` are axis-logical rather
    than physical, so RTL has to measure identically rather than merely not
    crash.
    """
    page = step_one(320, state="empty", select=True, lang=lang)
    # The RTL half of this test is worth nothing if `?lang=ar` silently fell back
    # to English: it would then be the LTR case run twice under two names.
    direction = page.evaluate("() => document.documentElement.dir")
    assert direction == ("rtl" if lang == "ar" else "ltr"), (
        f"{lang} rendered dir={direction!r}, so this is not the direction it claims to test")
    scroll_width, inner_width = page.evaluate(SIDEWAYS)
    assert scroll_width <= inner_width, f"step 1 scrolls sideways in {lang}: {scroll_width} > {inner_width}"
