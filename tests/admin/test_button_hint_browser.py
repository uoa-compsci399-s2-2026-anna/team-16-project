"""What `tests/admin/test_button_hint.py` cannot prove: that a person can
actually SEE the description, not merely that the markup is wired correctly.

`tests/admin/test_guidance.py`'s own docstring names the trap this file
exists to avoid: a folded warning once passed that suite for months because
the test only checked the markup *contained* the phrase, never that a reader
could see it without a click nobody told them to make. The same trap applies
here in a new shape - a `.help-tip__body` collapsed to a 1px clipped box is,
by every markup-only measure, "there": the text is in the DOM, the id
matches, `aria-describedby` resolves. None of that is evidence a mouse or a
keyboard actually reveals it.

**Not `Locator.is_visible()`, on purpose.** Playwright's own definition of
it is "has a non-empty bounding box and is not `visibility: hidden`" - which
a correctly-collapsed `.help-tip__body` also satisfies: it is deliberately
1px x 1px, not zero, so `aria-describedby` can still resolve it before any
reveal (see `_help_tip.html`'s header comment for why). `is_visible()` is
therefore `true` in BOTH the collapsed and the revealed state and cannot
tell this file's two cases apart - confirmed empirically while writing it:
every reveal test below passed against a `.help-tip__body` still nested
inside a closed `<details>`, where Chromium does not render the element AT
ALL, because `is_visible()` was reading the *box*, which existed regardless,
never the *paint*. So this file measures `getBoundingClientRect().width`
directly, the same way `tests/web/test_horizontal_overflow.py` measures
`scrollWidth` rather than trusting a layout heuristic: collapsed is ~1px,
revealed is the width of a sentence, and the two are never close enough to
confuse.

**Why security.html and not a sweep of every button.** It is the one page in
this task's scope that carries both shapes: three glyph=true reveals it had
before this task (`add-authenticator-hint`, `remove-authenticator-hint`,
`change-password-hint`) and six glyph=false ones this task added
(`rename-authenticator-hint` among them). One authenticated page measured at
five widths in two languages is already the matrix `tests/web/
test_horizontal_overflow.py` runs its own representative pages at, rather
than a sweep of the panel's every page at every width - the same trade that
file's own docstring explains, made the same way here.

Requires the stack rebuilt: `docker compose -f docker/compose.yaml up -d
--build admin`, and a throwaway admin account this file creates and removes
itself (see `_hint_account`) - never `admin`, `admin2` or `test123`. Skipped
rather than failed when the stack is not up.
"""

from __future__ import annotations

import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import pyotp

pytestmark = pytest.mark.browser

BASE = "http://localhost:18080"
COMPOSE = ["docker", "compose", "-f", "docker/compose.yaml"]
CSS_PATH = Path(__file__).resolve().parents[2] / "admin" / "static" / "brand.css"

#: A username namespaced so it cannot collide with a real account and is
#: obviously this file's own if anyone sees it mid-run.
_USERNAME = "kctest_button_hint"
_DISPLAY_NAME = "Button hint browser test"
_NEW_PASSWORD = "a-throwaway-password-not-reused-anywhere-else"


def _stack_is_up() -> bool:
    try:
        with urllib.request.urlopen(f"{BASE}/", timeout=3) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

if not _stack_is_up():  # pragma: no cover - environment guard
    pytest.skip(
        f"the stack is not answering on {BASE}; run "
        "`docker compose -f docker/compose.yaml up -d --build admin`",
        allow_module_level=True,
    )


def _docker_exec(*args: str, input_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [*COMPOSE, "exec", "-T", "admin", *args],
        input=input_text,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _create_throwaway_account() -> str:
    """Create `_USERNAME` as a throwaway admin account and return its
    one-time password.

    Never `admin`, `admin2` or `test123` - see this file's module docstring.
    `SECRET_KEY` is read from the same file `docker/entrypoint.sh` reads it
    from; a fresh `exec` session does not inherit the export the container's
    own entrypoint made for PID 1.
    """
    result = _docker_exec(
        "sh", "-c",
        'export SECRET_KEY=$(cat /var/lib/kaicalc/secret_key) && '
        f'python -m admin.cli create-staff {_USERNAME} "{_DISPLAY_NAME}" --admin',
    )
    assert result.returncode == 0, (
        f"could not create the throwaway account (is one already left over "
        f"from an interrupted run? clean it up with the SQL in "
        f"_delete_throwaway_account and retry): {result.stdout}\n{result.stderr}"
    )
    match = re.search(r"Initial password: (\S+)", result.stdout)
    assert match, f"create-staff did not print an initial password: {result.stdout}"
    return match.group(1)


def _delete_throwaway_account() -> None:
    """Remove `_USERNAME` and every audit_log row it wrote, by raw SQL - the
    same shape tests/admin/conftest.py's own `_cleanup_staff` uses, and for
    the same reason `delete-staff` is not used here: that path requires the
    account deactivated first, which is one more real HTTP round trip this
    teardown has no need to make for an account nothing else will ever look
    for again.
    """
    script = (
        "import os\n"
        "from sqlalchemy import create_engine, text\n"
        "engine = create_engine(os.environ['DATABASE_URL'])\n"
        "with engine.begin() as conn:\n"
        "    conn.execute(text(\"DELETE FROM audit_log WHERE actor = :u\"), {'u': %r})\n"
        "    conn.execute(text(\"DELETE FROM staff WHERE username = :u\"), {'u': %r})\n"
    ) % (_USERNAME, _USERNAME)
    _docker_exec("python", input_text=script)


def _log_in(page, password: str) -> None:
    """Drive the real first-login flow: password, forced change, TOTP
    enrolment, recovery codes - the same sequence `admin/backend.py::login`
    documents. There is no shortcut through this from outside the process
    the stack runs in: this file is exercising the deployed panel over HTTP,
    not the app object tests/admin/conftest.py's own `_login` can reach into
    directly.
    """
    page.goto(f"{BASE}/admin/login", wait_until="networkidle")
    page.fill('input[name="username"]', _USERNAME)
    page.fill('input[name="password"]', password)
    # Scoped to the text on the login form's own button: the page also
    # carries a language chooser with its own type="submit" button earlier
    # in the DOM, and a bare `button[type="submit"]` selector clicks that one
    # instead.
    page.click('button:has-text("Continue")')
    page.wait_for_load_state("networkidle")

    assert "/admin/change-password" in page.url, (
        f"expected the forced password-change step, landed on {page.url} instead"
    )
    page.fill("#password", _NEW_PASSWORD)
    page.fill("#confirm", _NEW_PASSWORD)
    page.click('button:has-text("Set password")')
    page.wait_for_load_state("networkidle")

    assert "/admin/enrol" in page.url, (
        f"expected the MFA enrolment step, landed on {page.url} instead"
    )
    secret = page.inner_text("code.key").replace(" ", "").strip()
    page.fill("#code", pyotp.TOTP(secret).now())
    page.click('button:has-text("Confirm")')
    page.wait_for_load_state("networkidle")

    page.click('a:has-text("I have saved them")')
    page.wait_for_load_state("networkidle")
    assert page.url.rstrip("/") == f"{BASE}/admin", (
        f"expected to land on the panel index after enrolling, got {page.url}"
    )


# --- Playwright, MODULE-scoped, and only here -----------------------------
#
# Not tests/admin/conftest.py, and not `scope="package"`: this file is the
# only place in tests/admin/ that drives a browser, but the *package* also
# holds every one of tests/admin's async, pytest-asyncio-backed HTTP tests -
# `admin_client`, `staff_client`, the lot. A `scope="package"` fixture here
# would open its `sync_playwright()` context on this module's first test and
# not close it until the LAST test in the WHOLE tests/admin package finishes,
# not the last test that actually requested it. Playwright's sync API keeps
# an asyncio loop "running" on this OS thread for as long as that context
# stays open (tests/web/conftest.py's docstring explains why in full), so
# every async test collected after this module - which is most of
# tests/admin, alphabetically - failed at fixture setup with "Runner.run()
# cannot be called from a running event loop" the first time this ran as
# part of the whole suite, not in isolation. `scope="module"` closes the
# context as soon as this module's own last test finishes, before pytest
# ever moves on to collect the next file.


@pytest.fixture(scope="module")
def _playwright():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="module")
def browser(_playwright):
    instance = _playwright.chromium.launch()
    yield instance
    instance.close()


@pytest.fixture(scope="module")
def _hint_account():
    """Create the throwaway account once for the whole module, remove it
    once, whatever else in the module fails."""
    password = _create_throwaway_account()
    yield password
    _delete_throwaway_account()


@pytest.fixture(scope="module")
def authed_storage_state(browser, _hint_account):
    """Cookies for an already-logged-in session, captured once and reused by
    every test below - re-running the four-step login flow (password,
    change-password, enrol, recovery codes) per test would multiply this
    file's cost by however many tests it has for no coverage gained; nothing
    below exercises login itself, only what the security page does once
    signed in.
    """
    context = browser.new_context()
    page = context.new_page()
    _log_in(page, _hint_account)
    state = context.storage_state()
    context.close()
    return state


@pytest.fixture
def security_page(browser, authed_storage_state):
    context = browser.new_context(storage_state=authed_storage_state)
    page = context.new_page()
    page.goto(f"{BASE}/admin/security", wait_until="networkidle")
    yield page
    context.close()


# --- Reveal mechanics --------------------------------------------------

#: A collapsed `.help-tip__body` is `box-sizing: border-box` at exactly
#: 1px x 1px (`admin/static/brand.css`'s own comment on the rule explains
#: the border-box part); revealed, it holds a full sentence and is at the
#: very least tens of pixels wide. The two are never close enough for a
#: reasonable threshold to confuse - seed
#: `test_the_collapsed_and_revealed_widths_are_nowhere_near_this_threshold`
#: checks that assumption rather than leaving it implicit.
_REVEALED_WIDTH_THRESHOLD = 20


def _rendered_width(page, selector: str) -> float:
    """`getBoundingClientRect().width` for one element - measured geometry,
    the same kind of fact `tests/web/test_horizontal_overflow.py` reads
    `scrollWidth` as, and not `Locator.is_visible()`. See this file's module
    docstring for why `is_visible()` cannot tell this file's two states
    apart: a correctly-collapsed `.help-tip__body` is a non-empty box on
    purpose, so `is_visible()` reads `true` in both states.
    """
    return page.evaluate(
        "(sel) => document.querySelector(sel).getBoundingClientRect().width", selector
    )


def test_the_description_is_hidden_until_hover_or_focus(security_page):
    """The starting state every test below assumes: collapsed to ~1px, not
    merely styled to look collapsed while actually occupying real space -
    that was this mechanism's actual first bug, caught only by measuring
    this width (see `_help_tip.html`'s header comment)."""
    glyph_width = _rendered_width(security_page, "#add-authenticator-hint")
    bare_width = _rendered_width(security_page, "#rename-authenticator-hint")
    assert glyph_width < _REVEALED_WIDTH_THRESHOLD, (
        f"the glyph body is {glyph_width}px wide before any interaction"
    )
    assert bare_width < _REVEALED_WIDTH_THRESHOLD, (
        f"the bare-shape body is {bare_width}px wide before any interaction"
    )


def test_the_description_appears_on_hover(security_page):
    """The half this task's owner actually asked for."""
    security_page.hover("#rename-authenticator")
    width = _rendered_width(security_page, "#rename-authenticator-hint")
    assert width >= _REVEALED_WIDTH_THRESHOLD, (
        f"hovering the button did not reveal its description ({width}px wide)"
    )
    assert "label this list uses" in security_page.inner_text("#rename-authenticator-hint")


def test_the_description_appears_on_keyboard_focus_not_only_on_hover(security_page):
    """**The half a hover-only build always drops, per this task's own
    brief and per `_help_tip.html`'s header comment: Tab moves focus onto a
    button and nothing then shows the text.** Focused without any mouse
    movement at all - `.focus()` is the DOM API a keyboard-only user's Tab
    key drives, not a synthesised hover.

    Mutation-tested: removing the `:focus` half of `admin/static/brand.css`'s
    reveal selector (leaving `:hover` alone) makes this test fail by name
    while `test_the_description_appears_on_hover` above keeps passing - see
    `.superpowers/sdd/reviews/2026-09-15-admin-button-descriptions.md` for
    the run that confirmed it.
    """
    security_page.focus("#rename-authenticator")
    width = _rendered_width(security_page, "#rename-authenticator-hint")
    assert width >= _REVEALED_WIDTH_THRESHOLD, (
        f"focusing the button did not reveal its description ({width}px wide)"
    )
    assert "label this list uses" in security_page.inner_text("#rename-authenticator-hint")


def test_the_glyph_shape_still_opens_on_click_for_a_reader_with_no_hover_or_keyboard(security_page):
    """The touch path `_help_tip.html`'s header comment argues for keeping:
    unaffected by hover or focus being added, on the buttons that still
    carry a "?"."""
    security_page.click("#add-authenticator + .help-tip summary")
    width = _rendered_width(security_page, "#add-authenticator-hint")
    assert width >= _REVEALED_WIDTH_THRESHOLD, (
        f"clicking the glyph did not open its description ({width}px wide)"
    )


def test_the_collapsed_and_revealed_widths_are_nowhere_near_this_threshold():
    """Guards `_REVEALED_WIDTH_THRESHOLD` itself: the collapsed state is
    defined as exactly 1px (`box-sizing: border-box`, `inline-size: 1px` in
    `admin/static/brand.css`), so a threshold anywhere under a genuinely
    revealed sentence's width is safe. Static, no browser needed - this is
    a fact about the constant, not about a rendered page."""
    css = CSS_PATH.read_text(encoding="utf-8")
    assert "inline-size: 1px" in css, (
        "the collapsed .help-tip__body is no longer declared at 1px - "
        "re-check _REVEALED_WIDTH_THRESHOLD against whatever it is now"
    )
    assert _REVEALED_WIDTH_THRESHOLD > 1


# --- aria-describedby ----------------------------------------------------


@pytest.mark.parametrize(
    "button_id,expected_substring",
    [
        ("add-authenticator", "lost phone"),
        ("remove-authenticator", "immediately"),
        ("change-password", "signs out every other session"),
        ("rename-authenticator", "label this list uses"),
    ],
)
def test_aria_describedby_points_at_an_element_that_exists_and_carries_the_text(
    security_page, button_id, expected_substring
):
    """`text_content()`, not `inner_text()`: this reads the description
    BEFORE any reveal, in its collapsed state, on purpose - the whole point
    of the clip-rect technique over `display: none` is that the text is
    still there for `aria-describedby` (and a screen reader) to find before
    a sighted reveal ever happens, and `inner_text()` (which approximates
    what a sighted reader sees) reads empty for a collapsed element for
    exactly that reason.
    """
    described_by = security_page.get_attribute(f"#{button_id}", "aria-describedby")
    assert described_by, f"#{button_id} carries no aria-describedby"
    target = security_page.locator(f"#{described_by}")
    assert target.count() == 1, (
        f"#{button_id}'s aria-describedby names {described_by!r}, which does "
        f"not resolve to exactly one element on the page"
    )
    assert expected_substring in target.text_content()


def test_a_button_with_both_a_glyph_and_a_hover_description_shows_the_same_text_in_both(
    security_page,
):
    """The one thing `_help_tip.html`'s header comment insists on: a button
    must not be able to say one thing on hover and another once its "?" is
    opened. Checked by actually exercising both triggers on the same
    button - `#add-authenticator` - and reading the same element's rendered
    text back each time, rather than assumed from the two paths sharing one
    `id` in the markup.
    """
    selector = "#add-authenticator-hint"

    security_page.click("#add-authenticator + .help-tip summary")
    assert _rendered_width(security_page, selector) >= _REVEALED_WIDTH_THRESHOLD
    via_glyph = security_page.inner_text(selector)

    security_page.click("#add-authenticator + .help-tip summary")  # closes it
    assert _rendered_width(security_page, selector) < _REVEALED_WIDTH_THRESHOLD

    security_page.hover("#add-authenticator")
    assert _rendered_width(security_page, selector) >= _REVEALED_WIDTH_THRESHOLD
    via_hover = security_page.inner_text(selector)

    assert via_glyph == via_hover, (
        f"the glyph shows {via_glyph!r} but hover shows {via_hover!r} for the "
        f"same button"
    )


# --- No horizontal overflow, revealed, in five widths and two languages --

WIDTHS = (320, 390, 700, 938, 1278)
LANGUAGES = ("ar", "de")

_OVERFLOW = """
() => {
  const de = document.documentElement;
  return {scroll: de.scrollWidth, client: de.clientWidth, inner: window.innerWidth};
}
"""


@pytest.fixture(scope="module")
def scrollbar_browser(_playwright):
    """A **second** Chromium instance, built on the same `_playwright`
    driver as the `browser` fixture above (two Chromium instances from one
    driver coexist; two `sync_playwright()` contexts on one thread do not -
    see tests/web/conftest.py's docstring) - launched with real scrollbars
    drawn.

    `tests/web/test_horizontal_overflow.py`'s own module docstring is the
    reason this is not reused from the plain `browser` fixture: headless
    Chromium's default `--hide-scrollbars` makes `clientWidth == innerWidth`
    exactly, so `scrollWidth <= clientWidth` reads true against a `50vw`-style
    bleed that overflows every desktop browser actually drawing a scrollbar.
    A suite that cannot see the scrollbar cannot see what the scrollbar
    would have caused.
    """
    instance = _playwright.chromium.launch(ignore_default_args=["--hide-scrollbars"])
    yield instance
    instance.close()


def _open_security(scrollbar_browser, storage_state, language, width):
    context = scrollbar_browser.new_context(
        storage_state=storage_state,
        viewport={"width": width, "height": 780},
        locale=language,
        extra_http_headers={"Accept-Language": f"{language},en;q=0.5"},
    )
    page = context.new_page()
    page.goto(f"{BASE}/admin/security?lang={language}", wait_until="networkidle")
    page.wait_for_selector("#rename-authenticator", timeout=15_000)
    return context, page


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("width", WIDTHS)
def test_no_horizontal_overflow_with_a_description_revealed(
    scrollbar_browser, authed_storage_state, language, width
):
    """The reveal is what this task added, so the reveal is what has to be
    measured under - not the page at rest, which every existing test on this
    branch already covers. Both shapes are focused at once (a keyboard user
    tabbing through would reveal at most one at a time, so this is the more
    demanding case, not a realistic one) to measure the wider of the two
    possible layouts in a single page load rather than doubling the matrix.
    """
    context, page = _open_security(scrollbar_browser, authed_storage_state, language, width)
    try:
        page.focus("#rename-authenticator")
        page.evaluate(
            "document.querySelector('#add-authenticator + .help-tip summary').click()"
        )
        measured = page.evaluate(_OVERFLOW)
    finally:
        context.close()

    assert measured["scroll"] <= measured["client"], (
        f"/admin/security scrolls sideways at {width}px in {language} with a "
        f"description revealed: scrollWidth {measured['scroll']} against "
        f"clientWidth {measured['client']} (innerWidth {measured['inner']})"
    )


def test_the_scrollbar_browser_is_actually_drawing_a_scrollbar(
    scrollbar_browser, authed_storage_state
):
    """The precondition for the matrix above - see `scrollbar_browser`'s own
    docstring and `tests/web/test_horizontal_overflow.py`'s equivalent for
    what it means for this to read false."""
    context, page = _open_security(scrollbar_browser, authed_storage_state, "en", 390)
    try:
        measured = page.evaluate(_OVERFLOW)
    finally:
        context.close()
    assert measured["client"] < measured["inner"], (
        f"the browser is not drawing a scrollbar: clientWidth "
        f"{measured['client']} equals innerWidth {measured['inner']}"
    )


# --- The pages this file used to not visit ---------------------------------
#
# Everything above measures /admin/security, and /admin/security is the one
# page in scope where the reveal mechanism happened to work. Three controls
# elsewhere revealed nothing at all on hover while every test here was green,
# for a reason no stylesheet reading finds: the collapsed description is an
# absolutely positioned 1px box, and inside a `position: sticky` ancestor
# Chromium's hover hit-test resolved to IT rather than to the button 40px
# away. The button never entered `:hover`, so the reveal rule never matched -
# and the button's own hover styling died with it.
#
# A reveal test that only ever visits the page where the mechanism works is
# the same shape of gap as the closed-`<details>` bug this file was written to
# catch. These cases visit the pages that broke.


@pytest.fixture
def dry_run_page(browser, authed_storage_state):
    context = browser.new_context(storage_state=authed_storage_state)
    page = context.new_page()
    page.goto(f"{BASE}/admin/try", wait_until="networkidle")
    yield page
    context.close()


def _box(page, selector):
    """Document-relative geometry, so a page that scrolls under Playwright's
    own scroll-into-view does not read as a layout change."""
    return page.evaluate(
        "(sel) => { const r = document.querySelector(sel).getBoundingClientRect();"
        "  return [Math.round(r.width), Math.round(r.height),"
        "          Math.round(r.x + window.scrollX), Math.round(r.y + window.scrollY)]; }",
        selector,
    )


#: The Reset link in the sticky dry-run bar - one of the three controls that
#: showed nothing at all, on the page whose `position: sticky` bar caused it.
_RESET = "[aria-describedby=dry-run-reset-hint]"


def test_a_description_in_the_sticky_dry_run_bar_appears_on_hover(dry_run_page):
    """Was 1x1 - no reveal at all - while every /admin/security case passed."""
    dry_run_page.hover(_RESET)
    width = _rendered_width(dry_run_page, "#dry-run-reset-hint")
    assert width >= _REVEALED_WIDTH_THRESHOLD, (
        f"hovering Reset in the sticky bar revealed nothing ({width}px wide)"
    )


def test_a_description_in_the_sticky_dry_run_bar_appears_on_keyboard_focus(dry_run_page):
    dry_run_page.focus(_RESET)
    width = _rendered_width(dry_run_page, "#dry-run-reset-hint")
    assert width >= _REVEALED_WIDTH_THRESHOLD, (
        f"focusing Reset revealed nothing ({width}px wide)"
    )


def test_the_control_keeps_its_own_hover_state(dry_run_page):
    """The half of this defect that is not about descriptions at all.

    With the hit-test landing on the collapsed description, the control never
    entered `:hover`, so its own hover styling stopped working - measured on
    the dry-run result page's primary action, which stayed Kale under the
    pointer instead of going Blueberry. Adding descriptions to this panel had
    silently removed a hover affordance three of its buttons already had.
    """
    dry_run_page.hover(_RESET)
    assert dry_run_page.evaluate(
        f"document.querySelector('{_RESET}').matches(':hover')"
    ), (
        "the control is not in :hover while the pointer is on it - the "
        "description beside it is taking the hit-test, which kills both the "
        "reveal and the control's own hover styling"
    )


def test_revealing_a_description_does_not_move_the_buttons_in_a_sticky_bar(dry_run_page):
    """The sticky bar is pinned to the foot of the page, so an in-flow reveal
    grew it upwards and pushed the buttons out from under the pointer that had
    just arrived - which unhovers the control, collapses the card, shrinks the
    row and starts again. Playwright called that "element is not stable" and
    gave up after 60 retries, on a submit button."""
    before = _box(dry_run_page, ".dry-run-primary")
    dry_run_page.hover(_RESET)
    dry_run_page.wait_for_timeout(150)
    assert _box(dry_run_page, ".dry-run-primary") == before, (
        "revealing Reset's description moved the Run test button beside it"
    )


#: The Add-an-authenticator dialog's own submit, inside `.dialog__actions` -
#: a `flex-wrap: nowrap` row that cannot put the card anywhere but in line
#: with the buttons.
_DIALOG_SUBMIT = "[aria-describedby=confirm-add-authenticator-hint]"


def test_revealing_a_description_in_a_dialog_moves_neither_button_nor_dialog(security_page):
    """Measured before the fix: the hovered submit went 192x50 -> 114x142,
    moved 28px inline-start and 46px up, both labels wrapped to three lines,
    and the dialog itself grew 92px and re-centred - all while the reader was
    aiming at the button."""
    security_page.click("#add-authenticator")
    security_page.wait_for_timeout(200)
    button_before = _box(security_page, _DIALOG_SUBMIT)
    dialog_before = _box(security_page, "dialog[open]")

    security_page.hover(_DIALOG_SUBMIT)
    security_page.wait_for_timeout(150)

    assert _rendered_width(security_page, "#confirm-add-authenticator-hint") >= (
        _REVEALED_WIDTH_THRESHOLD
    ), "the dialog's description did not appear at all"
    assert _box(security_page, _DIALOG_SUBMIT) == button_before, (
        "the description crushed or moved the submit button under the pointer"
    )
    assert _box(security_page, "dialog[open]") == dialog_before, (
        "the description resized the dialog"
    )


def test_a_bare_button_element_reveals_its_description(browser, authed_storage_state):
    """`block_ip.html`'s Block carries no `button`/`button--quiet` class, so it
    matched none of the reveal selectors and was the one described button in
    the panel that answered neither hover nor focus - visibly inconsistent
    with every other one."""
    context = browser.new_context(storage_state=authed_storage_state)
    page = context.new_page()
    try:
        page.goto(f"{BASE}/admin/ip-block/block", wait_until="networkidle")
        page.hover("[aria-describedby=block-hint]")
        width = _rendered_width(page, "#block-hint")
        assert width >= _REVEALED_WIDTH_THRESHOLD, (
            f"hovering Block revealed nothing ({width}px wide)"
        )
    finally:
        context.close()


# --- The actions sqladmin draws itself -------------------------------------


@pytest.mark.parametrize("path,expected", [
    ("/admin/factor-set/list", 7),
    ("/admin/staff/list", 6),
    ("/admin/sector/list", 2),
    ("/admin/ip-block/list", 1),
])
def test_every_action_in_the_menu_shows_its_description(
    browser, authed_storage_state, path, expected
):
    """Publish, Roll back, Archive, Clone and the rest live in sqladmin\'s own
    dropdown, where `brand/`\'s help-tip mechanism cannot reach them; their
    description travels inside the label instead (`admin/modelviews.py`\'s
    `described()`). A menu is already a disclosure, so there is no second
    gesture here - the description is simply rendered, and this measures that
    it is rendered with a HEIGHT rather than merely present in the DOM,
    because Tabler\'s `.dropdown-item` is `white-space: nowrap` and a sentence
    inside one is exactly the kind of thing that collapses or overflows.
    """
    context = browser.new_context(storage_state=authed_storage_state)
    page = context.new_page()
    try:
        page.goto(f"{BASE}{path}", wait_until="networkidle")
        page.click("#dropdownMenuButton")
        page.wait_for_timeout(200)
        notes = page.evaluate(
            "() => [...document.querySelectorAll(\'.dropdown-item .action-item__note\')]"
            "        .map(n => Math.round(n.getBoundingClientRect().height))"
        )
        assert len(notes) == expected, (
            f"{path}: expected {expected} described actions, found {len(notes)}"
        )
        assert all(height > 0 for height in notes), (
            f"{path}: a description rendered with no height: {notes}"
        )
        assert page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        ), f"{path}: the open menu pushed the page sideways"
    finally:
        context.close()
