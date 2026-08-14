"""Translation, in a browser, on the running stack, with the language varied.

**A test that asserts a string exists does not assert anyone can read it.**
Six defects have reached this repository through green markup tests - 87 field
descriptions asserted present while four were invisible behind a widget's CSS
class, five guidance blocks asserted present while absent from the built image.
`test_i18n_web.py` proves the catalogues are complete; it cannot prove a single
one of them reaches a screen, because the calculator renders from template
literals and a literal that was never wrapped in `t()` looks exactly like one
that was.

So this file drives Chromium against http://localhost:18080, sets
`navigator.languages` and `Accept-Language` to what a real visitor would send,
and asserts **a specific string inside the specific element that carries it**.
"The page contains Chinese characters" would pass against a page with one
translated word in it and is written nowhere here.

Requires the stack rebuilt: `docker compose -f docker/compose.yaml up -d
--build web admin`. Skipped rather than failed when it is not up.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from tests.web import i18n_keys

pytestmark = pytest.mark.browser

BASE = "http://localhost:18080"

#: Set through `Page.add_init_script` before any of the page's own scripts run,
#: because `web/js/i18n.js` reads `navigator.languages` at module evaluation.
#: Playwright's `locale=` only sets `Accept-Language` and `navigator.language`;
#: it does not set the ordered list, which is the thing under test.
_LANGUAGES_SHIM = """
Object.defineProperty(navigator, 'languages', {{ get: () => {languages} }});
Object.defineProperty(navigator, 'language', {{ get: () => {first} }});
"""


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
        "`docker compose -f docker/compose.yaml up -d --build web admin`",
        allow_module_level=True,
    )

from playwright.sync_api import sync_playwright  # noqa: E402


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        yield instance
        instance.close()


def open_page(browser, languages, path="/", query=""):
    """A page whose browser claims `languages`, in preference order."""
    context = browser.new_context(
        extra_http_headers={"Accept-Language": ",".join(languages)}
    )
    page = context.new_page()
    page.add_init_script(
        _LANGUAGES_SHIM.format(
            languages=json.dumps(languages), first=json.dumps(languages[0])
        )
    )
    page.goto(f"{BASE}{path}{query}", wait_until="networkidle")
    return context, page


# ---------------------------------------------------------------------------
# The built image
# ---------------------------------------------------------------------------


def test_every_catalogue_is_actually_in_the_built_image():
    """`web/locales/` is a new runtime directory, which is the exact shape of a
    defect this repository has already paid for: five guidance blocks passed
    every test in the checkout and were absent from the image. Nothing but a
    request to the running container proves a file shipped.

    A miss here does not 404, either - nginx redirects an unknown path to `/`
    (`@not_a_page`), so a missing catalogue arrives as a 200 carrying HTML.
    That is why the body is parsed rather than the status checked.
    """
    with urllib.request.urlopen(f"{BASE}/locales/index.json", timeout=10) as response:
        manifest = json.loads(response.read().decode("utf-8"))
    assert [entry["language"] for entry in manifest["catalogues"]] == (
        i18n_keys.catalogue_languages()
    )
    for entry in manifest["catalogues"]:
        url = f"{BASE}/locales/{entry['language']}.json"
        with urllib.request.urlopen(url, timeout=10) as response:
            catalogue = json.loads(response.read().decode("utf-8"))
        assert catalogue["language"] == entry["language"]
        assert len(catalogue["strings"]) == len(i18n_keys.source_strings())


# ---------------------------------------------------------------------------
# The calculator: negotiated from navigator.languages, with no query string
# ---------------------------------------------------------------------------


def test_the_intro_renders_in_chinese_for_a_chinese_browser(browser):
    """The whole of change 1 on the public side: no picker, no cookie, no
    query string, and the first paint is already in the visitor's language."""
    context, page = open_page(browser, ["zh-CN", "zh", "en"])
    try:
        assert page.get_attribute("html", "lang") == "zh"
        # The element, not the page. A heading asserted "somewhere in the
        # markup" would pass against a string rendered into a comment.
        assert page.inner_text("h1#page-title") == "食物浪费影响计算器"
        assert page.inner_text('[data-action="start"]') == "启动计算器"
        # Static HTML the browser parsed before any module ran.
        assert "Skip to calculator" not in page.inner_text(".skip-link")
        assert page.inner_text("footer .transparency-notice") != ""
    finally:
        context.close()


def test_en_nz_reaches_english_and_nothing_is_translated(browser):
    """`en-NZ` has no catalogue of its own and must reach the source language
    by truncation rather than by falling off the end of the negotiation."""
    context, page = open_page(browser, ["en-NZ", "en"])
    try:
        assert page.get_attribute("html", "lang") == "en-NZ"
        assert page.inner_text("h1#page-title") == "Food Waste Impact Calculator"
        assert page.locator("#machine-translation-notice").count() == 0
    finally:
        context.close()


def test_only_the_first_language_is_consulted_and_the_rest_is_not_walked(browser):
    """v1.26, in a real browser, with a real `navigator.languages`.

    `sv` has no catalogue, so this visitor reads English — the Korean behind
    it does not get a turn. A browser's second and third entries are
    frequently residue (a preinstalled locale, an input method added once)
    rather than a second language, and English is the floor every reader of
    this tool has.

    **The second half is the test.** The first assertion alone would pass
    against a calculator that had lost the ability to render Korean at all, so
    the same list with Korean at the head is asserted beside it, on the same
    element, against Korean's own catalogue entry.
    """
    context, page = open_page(browser, ["sv-SE", "ko", "en"])
    try:
        assert page.get_attribute("html", "lang") == "en-NZ"
        assert page.inner_text("h1#page-title") == "Food Waste Impact Calculator"
    finally:
        context.close()

    korean = i18n_keys.catalogue("ko")["strings"]["Food Waste Impact Calculator"]
    context, page = open_page(browser, ["ko", "sv-SE", "en"])
    try:
        assert page.get_attribute("html", "lang") == "ko"
        assert page.inner_text("h1#page-title") == korean
    finally:
        context.close()


def test_a_regional_first_tag_still_truncates_to_its_catalogue(browser):
    """`de-AT, xx` reads German. The rule took away the walk between tags, not
    the lookup inside one — and this is the case that tells the two apart: a
    negotiator that had stopped truncating would answer English here and still
    pass every assertion above."""
    german = i18n_keys.catalogue("de")["strings"]["Food Waste Impact Calculator"]
    context, page = open_page(browser, ["de-AT", "xx"])
    try:
        assert page.get_attribute("html", "lang") == "de"
        assert page.inner_text("h1#page-title") == german
    finally:
        context.close()


def test_zh_tw_reaches_traditional_chinese_and_not_simplified(browser):
    """The one case a plain RFC 4647 implementation gets wrong.

    Anchored on a character that differs between the scripts, so a
    Traditional page that had silently fallen back to Simplified fails here.
    """
    context, page = open_page(browser, ["zh-TW", "zh", "en"])
    try:
        assert page.get_attribute("html", "lang") == "zh-Hant"
        heading = page.inner_text("h1#page-title")
        assert "計算" in heading, heading
        assert "计算" not in heading, "zh-TW fell through to Simplified Chinese"
    finally:
        context.close()


def test_zh_hk_also_reaches_traditional(browser):
    context, page = open_page(browser, ["zh-HK", "en"])
    try:
        assert page.get_attribute("html", "lang") == "zh-Hant"
    finally:
        context.close()


def test_fil_reaches_the_tagalog_catalogue(browser):
    """Browsers send `fil` for Filipino; the catalogue is `tl`."""
    context, page = open_page(browser, ["fil", "en"])
    try:
        assert page.get_attribute("html", "lang") == "tl"
    finally:
        context.close()


def test_an_unknown_tag_falls_all_the_way_to_english(browser):
    context, page = open_page(browser, ["xx-YY", "zz"])
    try:
        assert page.get_attribute("html", "lang") == "en-NZ"
        assert page.inner_text("h1#page-title") == "Food Waste Impact Calculator"
    finally:
        context.close()


def test_lang_forces_a_language_over_the_browser_s_own(browser):
    context, page = open_page(browser, ["en-NZ"], query="?lang=th")
    try:
        assert page.get_attribute("html", "lang") == "th"
    finally:
        context.close()


def test_an_unrecognised_lang_is_ignored_and_the_browser_still_decides(browser):
    context, page = open_page(browser, ["zh-CN", "en"], query="?lang=qq")
    try:
        assert page.get_attribute("html", "lang") == "zh"
    finally:
        context.close()


def test_nothing_is_stored_until_a_choice_is_made(browser):
    """**This test replaced its own opposite**, and the bound is what changed.

    It used to assert `context.cookies() == []` outright, so that storing
    anything had to be a deliberate act rather than a quiet one. It was. What
    it asserts now is narrower and more useful: **merely visiting stores
    nothing.** Negotiation still keeps nothing, and the cookie appears only
    when somebody uses the control.

    `localStorage` stays empty in both cases - the choice is a cookie because
    the panel has to read it server-side, and a second copy in `localStorage`
    would be a second thing to keep in step.
    """
    context, page = open_page(browser, ["zh-CN", "en"])
    try:
        assert context.cookies() == [], "visiting the page stored something"
        assert page.evaluate("Object.keys(localStorage)") == []
        assert "lang" not in " ".join(page.evaluate("Object.keys(sessionStorage)"))
    finally:
        context.close()


def _choose(page, value):
    """Operate the control the way a person does, and wait for the result.

    `select_option` fires `change`, which is what the chooser listens for. The
    wait is on the rendered text rather than on a timeout: the handler fetches
    a catalogue, so asserting immediately would race it.
    """
    page.select_option("#language-chooser", value)
    page.wait_for_function(
        "(want) => document.getElementById('language-chooser')?.value === want",
        arg=value,
    )
    # The handler repaints the chooser after the catalogue arrives, so wait for
    # the document to be announced in the language that was asked for rather
    # than for the select alone - the select's value updates synchronously on
    # `change` and would let every assertion below race the fetch.
    page.wait_for_function(
        "(want) => want === 'auto' || document.documentElement.lang.startsWith("
        "want === 'zh-Hant' ? 'zh' : want)",
        arg=value,
    )


def test_the_chooser_is_present_usable_and_at_the_top_inline_start(browser):
    """Present is not usable, and this project has shipped that six times.

    So: in the viewport without scrolling, big enough to touch, actually
    reachable by keyboard, and carrying a real accessible name rather than a
    bare `<select>` a screen reader announces as nothing.
    """
    context, page = open_page(browser, ["en-NZ"])
    try:
        box = page.locator("#language-chooser").bounding_box()
        assert box is not None, "the chooser is not rendered at all"
        assert box["height"] >= 44, f"below the 44px touch target: {box}"
        assert box["y"] < page.evaluate("window.innerHeight"), "below the fold"

        # A real accessible name, from a real <label for>.
        assert page.evaluate(
            """() => document.querySelector('label[for="language-chooser"]')
                 ?.textContent.trim()"""
        ) == "Language"

        # Keyboard reachable: focus it and confirm it took focus.
        page.locator("#language-chooser").focus()
        assert page.evaluate("document.activeElement.id") == "language-chooser"

        # **First item in the header's own row, and it must cost no height.**
        # A strip of its own above the header cost 57px on every page, which
        # this calculator cannot afford - it deleted an 87px step-indicator band
        # to stop short steps scrolling. So the chooser joins the row that is
        # already there, ahead of the brand, and the row must not have grown.
        assert page.evaluate(
            """() => {
              const bar = document.querySelector('.language-bar');
              const row = document.querySelector('.header-inner');
              const brand = document.querySelector('.brand');
              return bar.parentElement === row &&
                (bar.compareDocumentPosition(brand) &
                 Node.DOCUMENT_POSITION_FOLLOWING) !== 0;
            }"""
        ), "the chooser is not the first thing in the header row"
        assert page.evaluate(
            "() => Math.round("
            "document.querySelector('.header-inner').getBoundingClientRect().height)"
        ) <= 96, "the header grew to make room for the chooser"
    finally:
        context.close()


def test_choosing_a_language_survives_a_reload_and_another_page(browser):
    """The whole point of the feature, driven the way a person drives it.

    Select, reload, navigate to a second page - and only then assert. A test
    that asserted straight after the click would prove the DOM updated, not
    that anything was remembered.
    """
    context, page = open_page(browser, ["en-NZ"])
    try:
        _choose(page, "zh")
        assert page.inner_text("h1#page-title") == "食物浪费影响计算器"

        stored = [c for c in context.cookies() if c["name"] == "kaicalc_lang"]
        assert len(stored) == 1 and stored[0]["value"] == "zh"
        assert stored[0]["path"] == "/", "the panel could not read it at any other path"
        assert stored[0]["httpOnly"] is False, "web/js/i18n.js has to read it"

        # **The chooser translates itself, not only the page around it.**
        # `i18n_keys.py` finds these three strings by reading the constants they
        # are declared as, so a chooser that stopped passing them through `t()`
        # would keep every catalogue complete and every file-level test green -
        # a mutation that did exactly that survived the whole suite until this
        # assertion existed. Only a rendered page catches it.
        assert page.eval_on_selector(
            'label[for="language-chooser"]', "el => el.textContent.trim()"
        ) == "语言"
        assert page.eval_on_selector(
            '#language-chooser option[value="auto"]', "el => el.textContent.trim()"
        ) == "跟随系统"
        assert "机器翻译" in page.eval_on_selector(
            '#language-chooser option[value="th"]', "el => el.textContent"
        )

        page.reload(wait_until="networkidle")
        assert page.inner_text("h1#page-title") == "食物浪费影响计算器"
        assert page.eval_on_selector("#language-chooser", "el => el.value") == "zh"

        page.goto(f"{BASE}/methodology.html", wait_until="networkidle")
        assert page.eval_on_selector("#language-chooser", "el => el.value") == "zh"
        assert "透明" in page.inner_text("body") or "方法" in page.inner_text("body")
    finally:
        context.close()


def test_follow_the_system_reverts_and_is_stored_rather_than_deleted(browser):
    """Going back is a write, not a deletion, and the page follows the browser again.

    The `value == "auto"` assertion is the one that kills the tempting
    implementation: clearing the cookie would also make the page revert, and
    would then be indistinguishable from never having chosen - and a cookie
    deletion that misses on path or domain silently leaves the old value.
    """
    context, page = open_page(browser, ["zh-CN", "en"])
    try:
        _choose(page, "en")
        assert page.inner_text("h1#page-title") == "Food Waste Impact Calculator"

        _choose(page, "auto")
        # Back to the browser's own language, which is Chinese here.
        assert page.inner_text("h1#page-title") == "食物浪费影响计算器"

        stored = [c for c in context.cookies() if c["name"] == "kaicalc_lang"]
        assert len(stored) == 1, "follow-the-system deleted the cookie"
        assert stored[0]["value"] == "auto"
    finally:
        context.close()


def test_lang_does_not_write_the_choice(browser):
    """A shared support link must not re-language the recipient for good.

    This is what keeps `?lang=` and the chooser from being confused for one
    another: one renders a page, the other remembers.
    """
    context, page = open_page(browser, ["en-NZ"], query="?lang=th")
    try:
        assert page.get_attribute("html", "lang") == "th"
        assert [c for c in context.cookies() if c["name"] == "kaicalc_lang"] == []
    finally:
        context.close()


def test_a_stored_choice_beats_the_browser_and_the_notice_follows(browser):
    """Chinese browser, English chosen - the case the chooser was asked for.

    The notice assertions are the second half: switching into a machine
    translated language must raise it, and switching out must remove it. A
    notice that outlived the language it warned about would be a false
    statement about a reviewed page.
    """
    context, page = open_page(browser, ["zh-CN", "en"])
    try:
        assert page.locator("#machine-translation-notice").count() == 0

        _choose(page, "th")
        assert page.locator("#machine-translation-notice").count() == 1
        assert page.evaluate(
            "document.body.firstElementChild.id"
        ) == "machine-translation-notice", "the chooser displaced the notice"

        _choose(page, "en")
        assert page.inner_text("h1#page-title") == "Food Waste Impact Calculator"
        assert page.locator("#machine-translation-notice").count() == 0, (
            "the notice outlived the language it was warning about"
        )
    finally:
        context.close()


def test_switching_twice_does_not_strand_the_page_in_the_first_language(browser):
    """The defect `applyToDocument`'s key-pinning exists to prevent.

    `data-i18n` with no value means "my own text is the key". After one switch
    that text is Chinese, so a second switch looks the Chinese up as a key,
    finds nothing, and leaves the page stuck. Two switches and a return to
    English is the shortest sequence that catches it; the footer is asserted
    because it is static HTML translated by `applyToDocument` rather than
    re-rendered by the wizard.
    """
    context, page = open_page(browser, ["en-NZ"])
    try:
        english = page.inner_text("footer")
        _choose(page, "zh")
        chinese = page.inner_text("footer")
        assert chinese != english

        _choose(page, "ja")
        japanese = page.inner_text("footer")
        assert japanese not in (english, chinese), "stranded in the first language"

        _choose(page, "en")
        assert page.inner_text("footer") == english, "cannot get back to English"
    finally:
        context.close()


def test_the_machine_translated_options_are_marked_before_anyone_picks(browser):
    """The warning belongs on the option too, not only after the choice.

    Chinese and English carry no mark - English is hand-written and Chinese is
    reviewed by its users - and asserting that is what stops the marker being
    applied to everything.
    """
    context, page = open_page(browser, ["en-NZ"])
    try:
        labels = page.evaluate(
            "() => Object.fromEntries(Array.from("
            "document.querySelectorAll('#language-chooser option')"
            ").map(o => [o.value, o.textContent]))"
        )
        assert "machine translated" in labels["th"]
        assert "machine translated" in labels["ar"]
        assert "machine translated" not in labels["zh"]
        assert "machine translated" not in labels["en"]
        # The endonym itself is never translated away.
        assert "中文" in labels["zh"] and labels["en"] == "English"
        assert labels["auto"] == "Follow the system"
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1280])
def test_the_chooser_is_usable_at_every_width(browser, width):
    """390px is a phone. A control that overflows there is not a control.

    The horizontal-overflow assertion is the one that matters: the language bar
    is a new row at the top of every page and is the most likely thing to widen
    the document.
    """
    context = browser.new_context(
        viewport={"width": width, "height": 800},
        extra_http_headers={"Accept-Language": "en-NZ"},
    )
    try:
        page = context.new_page()
        page.goto(f"{BASE}/", wait_until="networkidle")
        box = page.locator("#language-chooser").bounding_box()
        assert box is not None and box["height"] >= 44, f"{width}px: {box}"
        assert box["x"] >= 0 and box["x"] + box["width"] <= width + 1, (
            f"{width}px: the chooser is off screen: {box}"
        )
        assert not page.evaluate(
            "document.documentElement.scrollWidth > "
            "document.documentElement.clientWidth + 1"
        ), f"{width}px: the page scrolls sideways"
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1280])
def test_the_chooser_mirrors_in_a_right_to_left_page(browser, width):
    """"Top left" is physical, and Arabic renders right-to-left.

    **Measured, not trusted.** `dir="rtl"` on its own proves nothing - the
    whole point of the logical-property conversion is that the bar moves. So
    the chooser's distance from each edge is measured in both directions and
    the two must swap: inline-start in English is the left edge, and in Arabic
    it is the right one.
    """
    def measure(languages):
        context = browser.new_context(
            viewport={"width": width, "height": 800},
            extra_http_headers={"Accept-Language": ",".join(languages)},
        )
        try:
            page = context.new_page()
            page.add_init_script(
                _LANGUAGES_SHIM.format(
                    languages=json.dumps(languages), first=json.dumps(languages[0])
                )
            )
            page.goto(f"{BASE}/", wait_until="networkidle")
            # Measured against the BAR's own edges rather than the viewport's.
            # The bar is full width, so a viewport measurement says the same
            # thing, but only by coincidence - the claim being tested is that
            # the content sits at the bar's reading-start edge.
            return page.evaluate(
                """() => {
                  // The ROW, not the bar. The bar now hugs its own contents
                  // inside the header row, so measuring the gap against the bar
                  // would read zero in both directions and prove nothing.
                  const bar = document.querySelector('.header-inner');
                  const el = document.getElementById('language-chooser');
                  const label = document.querySelector('.language-bar__label');
                  const b = bar.getBoundingClientRect();
                  const s = el.getBoundingClientRect();
                  const l = label.getBoundingClientRect();
                  return {
                    dir: document.documentElement.dir,
                    // Gap between the bar's edge and the first thing in it.
                    gapLeft: Math.round(Math.min(s.left, l.left) - b.left),
                    gapRight: Math.round(b.right - Math.max(s.right, l.right)),
                    // The label is visually hidden below 720px, so its box is
                    // 1px and off-flow; compare it only where it is drawn.
                    labelLeftOfSelect: l.width > 2 ? l.left < s.left : null,
                    overflows:
                      document.documentElement.scrollWidth >
                      document.documentElement.clientWidth + 1,
                  };
                }"""
            )
        finally:
            context.close()

    rtl = measure(["ar"])
    ltr = measure(["en-NZ"])

    assert ltr["dir"] == "ltr" and rtl["dir"] == "rtl"
    # The content hugs the reading-start edge, which is the left in English and
    # the right in Arabic. Asserted as a swap rather than against a constant, so
    # that changing the bar's padding does not require editing this test.
    assert ltr["gapLeft"] < ltr["gapRight"], f"{width}px: not at the start in ltr"
    assert rtl["gapRight"] < rtl["gapLeft"], f"{width}px: not mirrored in rtl"
    assert ltr["gapLeft"] == rtl["gapRight"], (
        f"{width}px: the two directions are not mirror images: {ltr} vs {rtl}"
    )
    # The label leads the control in reading order in both directions, which is
    # the half that `dir` alone would not give us. Skipped where the label is
    # visually hidden, which is a real state and not a failure.
    if ltr["labelLeftOfSelect"] is not None:
        assert ltr["labelLeftOfSelect"] and not rtl["labelLeftOfSelect"]
    assert not rtl["overflows"], f"{width}px: the right-to-left page scrolls sideways"


def test_the_calculator_says_it_needs_scripting_rather_than_offering_a_dead_control(
    browser,
):
    """With JavaScript off there is no chooser at all, and a note saying why.

    The note is about the calculator, not about the language control: this page
    is ES modules end to end and renders nothing without scripting, so a
    chooser that needed JavaScript added no degradation. What would have been
    wrong is a `<select>` sitting in the static HTML doing nothing.
    """
    context = browser.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(f"{BASE}/", wait_until="domcontentloaded")
        assert page.locator("#language-chooser").count() == 0, (
            "a language control is present with scripting off and cannot work"
        )
        notice = page.locator(".noscript-notice")
        assert notice.count() == 1 and notice.is_visible()
        assert "JavaScript" in notice.inner_text()
    finally:
        context.close()


# ---------------------------------------------------------------------------
# The notice, and what it says about which promise is being made
# ---------------------------------------------------------------------------


def test_a_machine_translated_page_says_so_at_the_top_in_both_languages(browser):
    """The notice cannot live on a switcher any more, so it lives on the page.

    Asserted at the top of `<body>` and in two languages: the sentence a
    machine-translated page most has to get right went through the same
    machine as the rest of the file, so the English half is not decoration.
    """
    context, page = open_page(browser, ["th", "en"])
    try:
        notice = page.locator("#machine-translation-notice")
        assert notice.count() == 1
        assert notice.is_visible()
        # The first element of the body, before the header - not a footnote.
        assert page.evaluate(
            "document.body.firstElementChild.id"
        ) == "machine-translation-notice"
        english = notice.locator(".machine-translation-notice-en")
        assert "machine translated" in english.inner_text()
        assert english.get_attribute("lang") == "en"
        # And the Thai half is not the English one repeated.
        assert notice.inner_text().replace(english.inner_text(), "").strip()
    finally:
        context.close()


def test_chinese_carries_no_notice_because_it_has_readers(browser):
    context, page = open_page(browser, ["zh-CN", "en"])
    try:
        assert page.locator("#machine-translation-notice").count() == 0
    finally:
        context.close()


# ---------------------------------------------------------------------------
# Right to left
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("language", ["ar", "ur"])
def test_arabic_and_urdu_render_right_to_left(browser, language):
    context, page = open_page(browser, [language, "en"])
    try:
        assert page.get_attribute("html", "dir") == "rtl"
        assert page.get_attribute("html", "lang") == language
    finally:
        context.close()


def _mirror_measurements(page):
    return page.evaluate(
        """() => {
        const item = document.querySelector('.check-list li');
        const label = document.querySelector('.prototype-label');
        const bullet = getComputedStyle(item, '::before');
        return {
          dir: document.documentElement.dir,
          itemPadLeft: getComputedStyle(item).paddingLeft,
          itemPadRight: getComputedStyle(item).paddingRight,
          bulletLeft: parseFloat(bullet.left),
          bulletRight: parseFloat(bullet.right),
          labelBorderLeft: getComputedStyle(label).borderLeftWidth,
          labelBorderRight: getComputedStyle(label).borderRightWidth,
          overflows: document.body.scrollWidth > window.innerWidth + 1,
        };
      }"""
    )


def test_the_right_to_left_layout_is_actually_mirrored(browser):
    """`dir="rtl"` on its own proves nothing.

    A page whose stylesheet is full of physical `margin-left` rules carries
    the attribute and lays out exactly as it did before - which is the
    half-mirrored page this project decided was worse than not shipping
    Arabic and Urdu at all. So this is measured rather than asserted: the
    check-list's indent, the tick drawn before each item, and the accent
    border beside the wordmark all have to change sides, and the page must
    not gain a horizontal scrollbar doing it.
    """
    context, page = open_page(browser, ["ar"])
    try:
        rtl = _mirror_measurements(page)
    finally:
        context.close()

    context, page = open_page(browser, ["en-NZ"])
    try:
        ltr = _mirror_measurements(page)
    finally:
        context.close()

    assert rtl["dir"] == "rtl" and ltr["dir"] == "ltr"
    assert ltr["itemPadLeft"] == rtl["itemPadRight"] != "0px"
    assert rtl["itemPadLeft"] == ltr["itemPadRight"] == "0px"
    assert ltr["bulletLeft"] == 0 and ltr["bulletRight"] > 0
    assert rtl["bulletRight"] == 0 and rtl["bulletLeft"] > 0
    assert ltr["labelBorderLeft"] == rtl["labelBorderRight"] != "0px"
    assert not rtl["overflows"], "the right-to-left page scrolls sideways"


# ---------------------------------------------------------------------------
# Past the first screen: the steps, the form, and what stays English
# ---------------------------------------------------------------------------


def test_the_wizard_is_translated_past_the_first_screen(browser):
    """The intro is one function of six. A page that translates its hero and
    reverts on step 1 is what a test of the landing page alone would miss."""
    context, page = open_page(browser, ["zh-CN", "en"])
    try:
        page.click('[data-action="start"]')
        page.wait_for_selector("#stage-title")
        assert page.inner_text("#stage-title") == "这些浪费发生在食物供应链的哪个环节？"
        # The step bar, which composes its label from a placeholder.
        assert page.inner_text(".step-nav-label") == "第 1 步，共 6 步"
        assert page.inner_text(".step-nav-name") == "供应链环节"
        assert page.inner_text('.step-nav [data-action="continue"]') == "继续"
    finally:
        context.close()


def test_the_taxonomy_stays_in_the_language_staff_typed_it(browser):
    """The honest half of the result, asserted rather than left to be noticed.

    Destination names, food categories and sector names are staff-typed rows
    (O-8 rule 1), so a Chinese page lists them in English. This test is what
    makes that a decision the repository holds rather than a gap somebody
    finds.
    """
    context, page = open_page(browser, ["zh-CN", "en"])
    try:
        page.click('[data-action="start"]')
        page.wait_for_selector(".stage-card")
        names = page.locator(".stage-title").all_inner_texts()
        assert names, "no sectors rendered - is the API up?"
        assert any(name.isascii() for name in names), names
        # while the chrome around them is not English
        assert page.inner_text(".stage-fieldset legend").startswith("供应链")
    finally:
        context.close()


def test_a_validation_message_is_translated(browser):
    """Error copy is the text somebody reads when they are already stuck."""
    context, page = open_page(browser, ["zh-CN", "en"])
    try:
        page.click('[data-action="start"]')
        page.wait_for_selector("#stage-title")
        page.click('.step-nav [data-action="continue"]')
        page.wait_for_selector(".field-error")
        assert page.inner_text(".field-error") == "请选择浪费发生在食物供应链的哪个环节。"
    finally:
        context.close()


# ---------------------------------------------------------------------------
# The panel, which negotiates on the server
# ---------------------------------------------------------------------------


def _admin_login(browser, accept_language=None, query=""):
    """The panel, through a real browser.

    `urllib` cannot be used here and the reason is E-8: `admin/protection.py`
    refuses a headless client outright, so a raw request to /admin/login
    answers 403 whatever language it asks for. That refusal is itself worth
    knowing about - it is the response most likely to be cached, and it
    carries `Vary` because the middleware that sets it is the outermost one.
    """
    context = browser.new_context(
        extra_http_headers=(
            {"Accept-Language": accept_language} if accept_language else {}
        )
    )
    page = context.new_page()
    response = page.goto(
        f"{BASE}/admin/login{query}", wait_until="domcontentloaded"
    )
    return context, page, response


def test_the_panel_negotiates_from_accept_language_and_says_it_varies(browser):
    """Verified against the RUNNING stack rather than against the application.

    A response can carry `Vary` out of FastAPI and still reach a visitor
    without it if something upstream rewrote the headers, and nginx proxies
    this route. This is the check that would notice.
    """
    context, page, response = _admin_login(browser, "zh-CN,zh;q=0.9,en;q=0.8")
    try:
        assert response.status == 200
        assert "accept-language" in response.headers.get("vary", "").lower()
        assert page.get_attribute("html", "lang") == "zh"
        assert page.inner_text('button[type="submit"], .gate__card button') != ""
        assert context.cookies() == []
    finally:
        context.close()

    context, page, response = _admin_login(browser)
    try:
        assert "accept-language" in response.headers.get("vary", "").lower()
        assert page.get_attribute("html", "lang") == "en-NZ"
    finally:
        context.close()


def test_the_panel_ranks_the_header_by_quality(browser):
    """`zh;q=0.8, en;q=0.9` is a request for English, in that written order."""
    context, page, _ = _admin_login(browser, "zh;q=0.8, en;q=0.9")
    try:
        assert page.get_attribute("html", "lang") == "en-NZ"
    finally:
        context.close()


def test_a_wildcard_at_the_head_is_no_preference_and_renders_english(browser):
    """`*` says "anything", which under v1.26 is a statement that the visitor
    has expressed no preference — and no preference is English.

    Paired with the same wildcard *behind* a real preference, which decides
    nothing: `*` is ranked like any other entry rather than dropped, precisely
    so that it can hold the one slot that matters when it is at the head.
    """
    context, page, _ = _admin_login(browser, "*, zh;q=0.9")
    try:
        assert page.get_attribute("html", "lang") == "en-NZ"
    finally:
        context.close()

    context, page, _ = _admin_login(browser, "zh, *;q=0.9")
    try:
        assert page.get_attribute("html", "lang") == "zh"
    finally:
        context.close()


def test_the_panel_consults_only_the_highest_priority_tag(browser):
    """`fr-CA, fr, zh` reaches **English**, not Chinese (v1.26).

    The same rule as the calculator, on the surface that reads
    `Accept-Language` instead of `navigator.languages`, because two surfaces
    that answered one visitor differently would be the defect.

    Asserted in a pair: the second request is the same header with a supported
    tag at its head, and it still reaches Chinese. Without it this file would
    pass against a panel whose catalogue had stopped loading.
    """
    context, page, _ = _admin_login(browser, "fr-CA,fr;q=0.9,zh;q=0.5")
    try:
        assert page.get_attribute("html", "lang") == "en-NZ"
    finally:
        context.close()

    context, page, _ = _admin_login(browser, "zh-CN,fr;q=0.9,en;q=0.5")
    try:
        assert page.get_attribute("html", "lang") == "zh"
    finally:
        context.close()


def test_the_panel_stores_nothing_and_ignores_an_unrecognised_lang(browser):
    context, page, _ = _admin_login(browser, "en-NZ", query="?lang=zh")
    try:
        assert page.get_attribute("html", "lang") == "zh"
        assert [c for c in context.cookies() if "lang" in c["name"]] == []
    finally:
        context.close()

    context, page, _ = _admin_login(browser, "zh-CN", query="?lang=qq")
    try:
        assert page.get_attribute("html", "lang") == "zh"
    finally:
        context.close()


def test_vary_survives_the_proxy_on_a_response_no_browser_rendered():
    """`Vary` is set by the OUTERMOST middleware, so it reaches responses the
    inner ones short-circuit - and it has to survive nginx, which proxies this
    route.

    A bare urllib request is what `admin/protection.py` calls a headless
    client, so this may come back 403 rather than 200. Either is fine and the
    assertion is the same: the response a shared cache is most likely to hold
    is the one that must not be servable to the next visitor in the wrong
    language.
    """
    request = urllib.request.Request(
        f"{BASE}/admin/login", headers={"Accept-Language": "zh-CN"}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            headers, status = response.headers, response.status
    except urllib.error.HTTPError as refusal:
        headers, status = refusal.headers, refusal.code
    assert status in (200, 403), status
    assert "accept-language" in headers.get("Vary", "").lower(), dict(headers)


def test_the_static_origin_does_not_claim_to_vary():
    """Stated as a test because it looks like an omission and is a decision:
    `Vary` on a near-unique header would fragment a shared cache across every
    asset for a page that does not negotiate at all."""
    with urllib.request.urlopen(f"{BASE}/css/styles.css", timeout=10) as response:
        assert "accept-language" not in response.headers.get("Vary", "").lower()


# ---------------------------------------------------------------------------
# Every catalogue, on a real page
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("language", i18n_keys.catalogue_languages())
def test_every_catalogue_reaches_the_page_it_was_written_for(browser, language):
    """Twenty languages, each asserted against its OWN catalogue entry, inside
    the element that carries it.

    The expectation is read from `web/locales/<lang>.json` by Python and
    compared with what Chromium rendered after fetching the same file over
    HTTP and running `t()` on it - two independent paths to the same string,
    which is what makes this an end-to-end assertion rather than a tautology.

    Parametrised over the manifest rather than over a list here, so a
    twenty-first language is covered by adding its file.
    """
    catalogue = i18n_keys.catalogue(language)
    context, page = open_page(browser, [language, "en"])
    try:
        assert page.get_attribute("html", "lang") == language
        assert page.get_attribute("html", "dir") == catalogue.get("dir", "ltr")
        assert (
            page.inner_text("h1#page-title")
            == catalogue["strings"]["Food Waste Impact Calculator"]
        )
        assert (
            page.inner_text('[data-action="start"]')
            == catalogue["strings"]["Start calculator"]
        )
        # Static HTML, translated by `applyToDocument` rather than by a render.
        assert (
            page.inner_text(".skip-link") == catalogue["strings"]["Skip to calculator"]
        )
        # An attribute, which a text-only walk would miss.
        assert (
            page.get_attribute("#home-button", "aria-label")
            == catalogue["strings"][
                "Clear calculator data and return to the introduction"
            ]
        )
        # `<title>`, which is neither text nor attribute.
        assert page.title() == catalogue["strings"][
            "Food Waste Impact Calculator | Kai Commitment"
        ]
        # And the notice says what the catalogue says about itself.
        expected = 1 if catalogue["machine_translated"] else 0
        assert page.locator("#machine-translation-notice").count() == expected
    finally:
        context.close()


# ---------------------------------------------------------------------------
# The panel's chooser, driven with scripting switched OFF
# ---------------------------------------------------------------------------


def _gate(browser, scripting, width=1280, accept_language="en-NZ"):
    """The panel's login page, which is where the chooser can be driven
    without credentials.

    Deliberately the gate rather than a signed-in screen. It is the page a
    person who does not read English needs most, it is the one page a
    locked-out account can still reach, and reaching it costs no password -
    a wrong one charges the shared login throttle and locks an account for
    fifteen minutes, so no test here may guess at one.

    `urllib` cannot be used against this path at all: admin/protection.py
    refuses headless clients, which is why this goes through a real browser.
    """
    context = browser.new_context(
        viewport={"width": width, "height": 800},
        java_script_enabled=scripting,
        extra_http_headers={"Accept-Language": accept_language},
    )
    page = context.new_page()
    page.goto(f"{BASE}/admin/login", wait_until="domcontentloaded")
    return context, page


@pytest.mark.parametrize("width", [390, 1280])
def test_the_panels_chooser_works_with_scripting_off(browser, width):
    """**The whole reason the panel's chooser is a form.**

    Driven with `java_script_enabled=False`, so nothing here can be carried by
    a change handler: the visitor picks an option, presses a real button, and
    the server answers. Then a reload and a second page, because a control that
    only appears to work until you navigate is the defect this is guarding.

    Run at 390px as well as desktop - a control that overflows a phone is not a
    control - and finished by returning to "Follow the system" and confirming it
    reverts rather than sticking.
    """
    context, page = _gate(browser, scripting=False, width=width)
    try:
        chooser = page.locator("#language-chooser")
        assert chooser.count() == 1, "no chooser on the gate page"
        box = chooser.bounding_box()
        assert box["height"] >= 44, f"{width}px: below the touch target: {box}"
        assert box["x"] >= 0 and box["x"] + box["width"] <= width + 1, (
            f"{width}px: the chooser is off screen: {box}"
        )
        assert page.locator("form.language-bar button[type=submit]").count() == 1

        # English to begin with, because that is what the browser asked for.
        assert "Sign in" in page.content() or "Continue" in page.content()

        # Pick Chinese and submit. No script is involved in either step.
        page.select_option("#language-chooser", "zh")
        page.click("form.language-bar button[type=submit]")
        page.wait_for_load_state("domcontentloaded")
        assert "登录" in page.content(), (
            f"{width}px: the panel did not change language without scripting"
        )

        stored = [c for c in context.cookies() if c["name"] == "kaicalc_lang"]
        assert len(stored) == 1 and stored[0]["value"] == "zh"
        assert stored[0]["path"] == "/", "the calculator could not read it"

        # It survives a reload...
        page.reload(wait_until="domcontentloaded")
        assert "登录" in page.content()
        # The chooser shows the choice it is honouring, not the first option.
        assert 'value="zh" lang="zh" selected' in page.content()

        # ...and a different page of the panel. /admin/security is behind the
        # session, so its refusal is what a signed-out visitor gets - and the
        # refusal has to be readable too.
        page.goto(f"{BASE}/admin/security", wait_until="domcontentloaded")
        assert "登录" in page.content(), (
            f"{width}px: a second page of the panel lost the language"
        )

        # Back to following the system, which is a write and not a deletion.
        page.goto(f"{BASE}/admin/login", wait_until="domcontentloaded")
        page.select_option("#language-chooser", "auto")
        page.click("form.language-bar button[type=submit]")
        page.wait_for_load_state("domcontentloaded")
        assert "登录" not in page.content(), "follow-the-system did not revert"
        reverted = [c for c in context.cookies() if c["name"] == "kaicalc_lang"]
        assert len(reverted) == 1 and reverted[0]["value"] == "auto", (
            "follow-the-system deleted the cookie instead of writing it"
        )
    finally:
        context.close()


def test_the_panel_names_a_language_it_cannot_render(browser):
    """Tamil chosen on the calculator, then the panel opened.

    The cookie is set through the calculator's own origin - the same cookie,
    which is the point - and the panel is then asked for. It must render
    English, name தமிழ், offer no Tamil option, and **leave the cookie alone**.
    """
    context = browser.new_context(extra_http_headers={"Accept-Language": "en-NZ"})
    try:
        page = context.new_page()
        page.goto(f"{BASE}/", wait_until="networkidle")
        _choose(page, "ta")
        assert [c["value"] for c in context.cookies() if c["name"] == "kaicalc_lang"] == ["ta"]

        page.goto(f"{BASE}/admin/login", wait_until="domcontentloaded")
        body = page.content()
        assert "தமிழ்" in body, "the panel did not name the language it lacks"
        assert 'value="ta"' not in body, "a dead Tamil entry is in the list"
        assert "登录" not in body, "the panel should be English here"

        after = [c["value"] for c in context.cookies() if c["name"] == "kaicalc_lang"]
        assert after == ["ta"], (
            f"the panel rewrote the shared cookie to {after}; the calculator's "
            "language has been destroyed from an unrelated screen"
        )
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1280])
def test_choosing_a_right_to_left_language_survives_and_reverts(browser, width):
    """The owner's acceptance run, on the calculator, in Arabic, at both widths.

    Every other test here either chooses a language OR checks the mirroring.
    This does both in one session, because the interesting failures live in the
    join: a chooser that mirrors on a page the browser negotiated into Arabic
    but not on one the visitor chose into Arabic, or a stored right-to-left
    choice that survives a reload and loses `dir` on the second page.

    Ends by returning to "Follow the system" and confirming the page comes back
    to English on an English browser - a revert that leaves `dir="rtl"` behind
    is the failure this last third exists for.
    """
    context = browser.new_context(
        viewport={"width": width, "height": 800},
        extra_http_headers={"Accept-Language": "en-NZ"},
    )
    try:
        page = context.new_page()
        # The shim is not optional here. `extra_http_headers` does not touch
        # `navigator.languages`, and this machine's own locale is Chinese - so
        # without it the revert at the end follows the real browser to Chinese
        # and the test fails on correct behaviour. `open_page` exists for this
        # reason; it is inlined because this test needs its own viewport.
        page.add_init_script(
            _LANGUAGES_SHIM.format(
                languages=json.dumps(["en-NZ"]), first=json.dumps("en-NZ")
            )
        )
        page.goto(f"{BASE}/", wait_until="networkidle")
        assert page.get_attribute("html", "dir") == "ltr"
        assert page.get_attribute("html", "lang") == "en-NZ"

        _choose(page, "ar")
        assert page.get_attribute("html", "dir") == "rtl"
        assert page.get_attribute("html", "lang") == "ar"

        # Survives a reload.
        page.reload(wait_until="networkidle")
        assert page.get_attribute("html", "dir") == "rtl"
        assert page.eval_on_selector("#language-chooser", "el => el.value") == "ar"

        # Survives navigation to another page.
        page.goto(f"{BASE}/methodology.html", wait_until="networkidle")
        assert page.get_attribute("html", "dir") == "rtl"
        assert page.eval_on_selector("#language-chooser", "el => el.value") == "ar"

        # The control is still reachable and still at the reading-start edge,
        # which is the RIGHT here - a mirrored page that puts its own language
        # control off screen is the trap this whole parametrisation is for.
        geometry = page.evaluate(
            """(w) => {
              const el = document.getElementById('language-chooser');
              const b = el.getBoundingClientRect();
              return {onScreen: b.left >= 0 && b.right <= w + 1,
                      height: Math.round(b.height),
                      fromRight: Math.round(w - b.right),
                      fromLeft: Math.round(b.left)};
            }""",
            width,
        )
        assert geometry["onScreen"], f"{width}px: off screen in rtl: {geometry}"
        assert geometry["height"] >= 44, geometry
        assert geometry["fromRight"] < geometry["fromLeft"], (
            f"{width}px: not at the reading-start edge in rtl: {geometry}"
        )
        # NOT asserted here: that this page does not scroll sideways. It does,
        # in Arabic at 390px, and it did before the chooser existed - the
        # published-formulas table and the factor-set definition list overflow
        # on their own, measured identically with the language bar hidden
        # (scrollWidth 875 either way). It is a real defect and it is recorded
        # rather than smuggled into this test: asserting it here would make a
        # chooser test fail for a reason that has nothing to do with the
        # chooser, and deleting the assertion later would look like a fix.
        # The calculator page IS held to it, in
        # `test_the_chooser_is_usable_at_every_width` and the mirroring test.

        # And back again.
        _choose(page, "auto")
        assert page.get_attribute("html", "dir") == "ltr", (
            "reverting left the document in right-to-left"
        )
        assert page.get_attribute("html", "lang") == "en-NZ"
        stored = [c for c in context.cookies() if c["name"] == "kaicalc_lang"]
        assert len(stored) == 1 and stored[0]["value"] == "auto"
    finally:
        context.close()
