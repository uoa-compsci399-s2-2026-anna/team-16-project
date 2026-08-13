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


def test_nothing_is_stored_about_the_language(browser):
    """No cookie, no localStorage, no sessionStorage key. A language is a
    property of a request, and the only thing this calculator keeps in the
    browser is the section 2.3 session token."""
    context, page = open_page(browser, ["zh-CN", "en"])
    try:
        assert context.cookies() == []
        assert page.evaluate("Object.keys(localStorage)") == []
        assert "lang" not in " ".join(page.evaluate("Object.keys(sessionStorage)"))
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
