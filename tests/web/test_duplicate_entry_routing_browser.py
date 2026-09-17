"""A chain that repeats a saved one must be caught early, and must not land the
visitor on a step where nothing is wrong.

**The defect, and how it was found.** The owner reached step 5, noticed a
supply-chain stage was missing, and added one from step 1 — re-entering a
sector and food category an earlier chain already held. `POST
/api/v1/calculate` refuses that pair (`api/schemas.py`'s `duplicate_entry`,
which mirrors `uq_submission_entry` in the database), and the interface put
them on **step 4**, the destination step, whose fields were all valid. The
banner named `entries[0]`.

**Why every existing test passed.** `calculator.js`'s `detailStep` resolves a
detail's step by looking up where that field is rendered, and by its own
documented design it only knows the *draft* entry's paths: "a saved entry's
fields sit on a read-only `entryCard` with no box to highlight". A
`duplicate_entry` detail names `entries[1]` — a saved entry — so `detailStep`
returned `undefined` and `submitCalculation` fell through to its long-standing
`VALIDATION_ERROR` default of step 3 (step 4 on screen).

Nothing caught it because no test ever drove the duplicate path through the
interface. `tests/web/test_results_export.py:2599` has a helper named
`_submit_two_entries_of_different_sectors` whose docstring explains at length
that it picks two *distinct* sectors specifically to avoid tripping this
check — the constraint was known and routed around, never exercised.

**Three defects, in the order a visitor meets them** (spec §4 of
`.superpowers/sdd/2026-09-17-food-granularity/spec.md`):

1. Nothing warns at the point of choice, though the collision is knowable the
   moment the pair is picked.
2. The refusal arrives only after the whole chain has been built.
3. It lands on the wrong step.

The server refusal stays — the constraint is in the database, and a front-end
guard is a convenience, not an enforcement. So the guard below does not block
the visitor; continuing past it must still end somewhere that makes sense.

**Driven against the real API, not a stub**, for the reason
`test_amount_step_field_errors_browser.py` gives: a test that answers its own
POST proves only that the front end can parse a response it wrote itself. The
400 asserted here is the real one, from the real check.

**Running this**::

    pip install playwright && playwright install chromium
    docker compose -f docker/compose.yaml up -d --build web
    pytest tests/web/test_duplicate_entry_routing_browser.py

Restart ``kaicalc-api`` between this file and any other browser test file — see
``test_amount_limits_browser.py``'s header for why a sweep exhausts the
taxonomy rate limit.
"""

from __future__ import annotations

import os

import pytest

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to drive the real 400 this file asserts against",
)

BASE = os.environ.get("KAICALC_WEB_URL", "http://localhost:18080").rstrip("/") + "/index.html"

#: The destination step renders a rejection as a bare `.field-error[role=alert]`;
#: the amount and review steps add `.api-error` to it. Waiting on the narrower
#: class would make a wrongly-routed rejection fail these tests as a 15-second
#: timeout instead of an assertion naming the step it actually landed on.
ANY_BANNER = ".field-error[role=alert]"


@pytest.fixture
def page(browser):
    """A fresh context and a fresh session token, and no route interception —
    the whole point is what the real API says about a real duplicate."""
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


def _build_one_chain(page, *, then_add_another: bool):
    """One pass through steps 1-5, leaving the visitor on the review step.

    Every default is taken — the first sector radio and no food category — so
    that two passes produce the colliding pair on purpose. That is the whole
    subject of this file, and it is exactly what the helper in
    `test_results_export.py` goes out of its way to avoid.
    """
    page.click('[data-action="continue"]')
    page.wait_for_selector('input[name="food-category"]')
    page.click('[data-action="continue"]')
    page.wait_for_selector("#total-waste")
    page.fill("#total-waste", "1000")
    page.click('[data-action="continue"]')
    page.wait_for_selector('[data-line-field="amount"]')
    page.fill('[data-line-field="amount"] >> nth=0', "1000")
    page.wait_for_timeout(80)
    page.click('[data-action="continue"]')
    page.wait_for_selector('[data-action="calculate"]')
    if then_add_another:
        page.click('[data-action="add-entry"]')
        page.wait_for_selector('input[name="sector"]')


def _two_colliding_chains(page):
    """The owner's own path: build a chain, add another, repeat the same pair."""
    page.click('[data-action="start"]')
    page.wait_for_selector('input[name="sector"]')
    page.evaluate("document.querySelectorAll('input[name=sector]')[0].click()")
    page.wait_for_timeout(80)
    _build_one_chain(page, then_add_another=True)
    page.evaluate("document.querySelectorAll('input[name=sector]')[0].click()")
    page.wait_for_timeout(80)
    _build_one_chain(page, then_add_another=False)


def test_a_refused_duplicate_lands_on_the_review_step(page):
    """Step 5, where the offending chain is actually on screen — not step 4.

    Asserted through the two headings rather than the eyebrow's "Step N" text,
    because the eyebrow is translated and the heading ids are not.
    """
    _two_colliding_chains(page)
    page.click('[data-action="calculate"]')
    page.wait_for_selector(ANY_BANNER, timeout=15000)
    assert page.locator("#review-title").is_visible(), (
        "a duplicate entry names a saved chain, which lives on the review step; "
        "step 4's fields are all valid and there is nothing to fix there"
    )
    assert page.locator("#destination-title").count() == 0


def test_the_offending_chain_is_marked_where_it_is_shown(page):
    """A banner naming an entry, while every entry on the step looks identical,
    leaves the visitor counting blocks. The named entry carries the message.

    **It is the draft that gets marked here, not a saved card.** `duplicate_entry`
    flags the *later* of the two colliding entries, and the request body is
    `[...state.entries, draftEntry()]` — so with one saved chain and a draft, the
    later one is the draft, shown on this step as the "Current entry" blocks. An
    earlier version of this test asserted `.saved-entry-card.has-error` and was
    wrong about which entry the API names.
    """
    _two_colliding_chains(page)
    page.click('[data-action="calculate"]')
    page.wait_for_selector(ANY_BANNER, timeout=15000)
    flagged = page.locator(".entry-problem")
    assert flagged.count() == 1, (
        f"exactly the entry the API named should carry the message, got {flagged.count()}"
    )
    assert page.locator(".current-entry-heading.has-error").count() == 1, (
        "the current entry is the one the API named; marking a saved card instead "
        "points the visitor at the entry that is allowed to stay"
    )
    assert "entry 1" in flagged.inner_text().lower(), (
        f"the message should name the entry this one collides with: {flagged.inner_text()!r}"
    )


def test_the_visitor_is_never_shown_a_request_path(page):
    """`entries[0]` is a path into the request body. It means nothing to a
    visitor, and today it is printed to them verbatim because the front end
    passes the API's English `message` straight through."""
    _two_colliding_chains(page)
    page.click('[data-action="calculate"]')
    page.wait_for_selector(ANY_BANNER, timeout=15000)
    banner = page.locator(ANY_BANNER).first.inner_text()
    assert "entries[" not in banner, f"request path leaked to the visitor: {banner!r}"


def test_the_collision_is_named_at_the_point_of_choice(page):
    """Step 2 knows the pair is already taken the moment it is chosen. Saying so
    there is the difference between one more click and rebuilding a whole chain
    to be refused at the end."""
    page.click('[data-action="start"]')
    page.wait_for_selector('input[name="sector"]')
    page.evaluate("document.querySelectorAll('input[name=sector]')[0].click()")
    page.wait_for_timeout(80)
    _build_one_chain(page, then_add_another=True)
    page.evaluate("document.querySelectorAll('input[name=sector]')[0].click()")
    page.wait_for_timeout(80)
    page.click('[data-action="continue"]')
    page.wait_for_selector('input[name="food-category"]')
    notice = page.locator(".duplicate-notice")
    assert notice.is_visible(), (
        "choosing a sector and food category a saved chain already holds is "
        "knowable here, and is refused by the API if it is not said here"
    )


def test_the_notice_opens_the_chain_it_names(page):
    """The offer has to work. A notice that names entry 1 and does not take the
    visitor there leaves them to find it themselves."""
    page.click('[data-action="start"]')
    page.wait_for_selector('input[name="sector"]')
    page.evaluate("document.querySelectorAll('input[name=sector]')[0].click()")
    page.wait_for_timeout(80)
    _build_one_chain(page, then_add_another=True)
    page.evaluate("document.querySelectorAll('input[name=sector]')[0].click()")
    page.wait_for_timeout(80)
    page.click('[data-action="continue"]')
    page.wait_for_selector(".duplicate-notice")
    page.click('.duplicate-notice [data-action="edit-entry"]')
    page.wait_for_timeout(200)
    assert page.locator("#stage-title").is_visible(), (
        "editing an entry reopens it from step 1, the way the review list's own "
        "Edit button does"
    )


def test_continuing_past_the_notice_is_still_allowed(page):
    """The guard is a convenience, not an enforcement — the constraint lives in
    the database. A visitor who continues anyway must still be able to, and must
    still land somewhere that makes sense (the two tests above)."""
    page.click('[data-action="start"]')
    page.wait_for_selector('input[name="sector"]')
    page.evaluate("document.querySelectorAll('input[name=sector]')[0].click()")
    page.wait_for_timeout(80)
    _build_one_chain(page, then_add_another=True)
    page.evaluate("document.querySelectorAll('input[name=sector]')[0].click()")
    page.wait_for_timeout(80)
    page.click('[data-action="continue"]')
    page.wait_for_selector(".duplicate-notice")
    page.click('[data-action="continue"]')
    page.wait_for_selector("#total-waste", timeout=5000)
