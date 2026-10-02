"""Contract tests for member D's public statistics and content work.

The public front end has no build step and the repository deliberately has no
JavaScript test runner.  These tests therefore check the stable, reviewable
boundary: page relationships, accessible HTML, local runtime assets, module
exports/data flow, safe rendering invariants, fixture ownership and nginx CSP.

They intentionally do *not* prescribe DOM ids, ``data-*`` hooks, CSS classes,
or a number of canvases/tables.  Chart drawing, focus movement and responsive
layout remain browser-acceptance work rather than being faked here.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from tests.support import red_line


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
FIXTURES = ROOT / "tests" / "fixtures"

#: The pages a visitor can reach. `home.html` is NOT one of them any more - see
#: `RETIRED_PAGES` - so it is out of every relationship asserted below: it is not a
#: link any page owes, and it is not a page that owes links.
PAGES = {
    "calculator": WEB / "index.html",
    "statistics": WEB / "stats.html",
    "documentation": WEB / "methodology.html",
}

#: In the tree, served if its address is typed, reachable from nothing.
#:
#: `home.html` was the landing page for one week. The team dropped it - it looked poor
#: and duplicated the client's own website, which already carries that material - and
#: `index.html`'s introduction screen came back in its place. It is retired rather than
#: deleted because the client has not decided about the news feed it carries.
#:
#: **Retired must not become rotted**, which is the whole reason this constant exists
#: rather than the file simply falling out of the suite. `test_the_retired_home_page…`
#: below holds it to the shape it was reviewed in, so that reviving it is a routing
#: decision rather than a repair job.
RETIRED_PAGES = {
    "home": WEB / "home.html",
}

REQUIRED_CSP = {
    "default-src": {"'self'"},
    "base-uri": {"'self'"},
    "object-src": {"'none'"},
    "frame-ancestors": {"'self'"},
    "form-action": {"'self'"},
    "script-src": {"'self'"},
    "style-src": {"'self'"},
    "style-src-attr": {"'unsafe-inline'"},
    "font-src": {"'self'"},
    # `img-src` and `connect-src` are PLACEHOLDERS in the template, and asserting the
    # placeholder is deliberately not the whole test. docker/web-config.sh fills both in
    # at container start from KAICALC_NEWS_ORIGIN, KAICALC_API_ORIGIN and
    # KAICALC_NEWS_IMAGE_ORIGINS, and builds web/js/config.js from the same values in the
    # same run so the policy and the page cannot name different hosts. What each renders
    # to - configured and unconfigured - is asserted against the real image in
    # tests/test_web_runtime_config.py, which is the only place that can see it.
    "img-src": {"${KAICALC_CSP_IMG_SRC}"},
    "connect-src": {"${KAICALC_CSP_CONNECT_SRC}"},
}

VOID_ELEMENTS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _json(path: Path):
    return json.loads(_read(path))


class _HTML(HTMLParser):
    """A small semantic inventory, not a snapshot of implementation hooks."""

    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self.elements: list[dict[str, object]] = []
        self._stack: list[int] = []
        self.feed(source)
        self.close()

    def handle_starttag(self, tag, attrs):
        element = {
            "tag": tag,
            "attrs": dict(attrs),
            "text": [],
            "ancestor_tags": tuple(self.elements[index]["tag"] for index in self._stack),
        }
        self.elements.append(element)
        if tag not in VOID_ELEMENTS:
            self._stack.append(len(self.elements) - 1)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_ELEMENTS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for position in range(len(self._stack) - 1, -1, -1):
            if self.elements[self._stack[position]]["tag"] == tag:
                del self._stack[position:]
                return

    def handle_data(self, data):
        for index in self._stack:
            self.elements[index]["text"].append(data)

    def matching(self, tag: str, **attrs: str) -> list[dict[str, object]]:
        return [
            element
            for element in self.elements
            if element["tag"] == tag
            and all(element["attrs"].get(key) == value for key, value in attrs.items())
        ]

    def values(self, tag: str, attribute: str) -> list[str]:
        return [
            element["attrs"][attribute]
            for element in self.matching(tag)
            if attribute in element["attrs"]
        ]


def _page(path: Path) -> tuple[str, _HTML]:
    source = _read(path)
    return source, _HTML(source)


def _text(element: dict[str, object]) -> str:
    return " ".join("".join(element["text"]).split())


def _linked_page_names(page: _HTML) -> set[str]:
    names = set()
    for href in page.values("a", "href"):
        parsed = urlsplit(href)
        if not parsed.scheme and not parsed.netloc:
            name = Path(unquote(parsed.path)).name
            if name:
                names.add(name)
    return names


def _without_js_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"(^|\s)//[^\r\n]*", r"\1", source)


def _js_code_without_comments_or_strings(source: str) -> str:
    """Keep only executable token positions for a conservative direct-call check."""

    output = list(source)
    index = 0
    while index < len(source):
        character = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if character == "/" and following in {"/", "*"}:
            end = source.find("\n", index + 2) if following == "/" else source.find("*/", index + 2)
            end = len(source) if end < 0 else end + (0 if following == "/" else 2)
            output[index:end] = " " * (end - index)
            index = end
            continue
        if character in {"'", '"', "`"}:
            quote = character
            end = index + 1
            escaped = False
            while end < len(source):
                current = source[end]
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == quote:
                    end += 1
                    break
                end += 1
            output[index:end] = " " * (end - index)
            index = end
            continue
        index += 1
    return "".join(output)


def _bracket_span(source: str, opening: int) -> str:
    """The text from ``source[opening]`` to its matching bracket, inclusive.

    Counts every bracket kind, so an arrow-function body inside a call is
    included rather than being cut off at the first ``)`` — which is exactly
    what the regex this replaces did.
    """
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack: list[str] = []
    for index in range(opening, len(source)):
        character = source[index]
        if character in pairs:
            stack.append(pairs[character])
        elif stack and character == stack[-1]:
            stack.pop()
            if not stack:
                return source[opening:index + 1]
    return source[opening:]


def _brace_block(source: str, opening: int) -> tuple[str, int]:
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[opening:index + 1], index + 1
    raise AssertionError("Unclosed nginx block")


def _exported_function(source: str, name: str) -> str:
    match = re.search(rf"export\s+(?:async\s+)?function\s+{re.escape(name)}\s*\(", source)
    assert match, f"Missing exported function {name}"
    opening = source.find("{", match.end())
    assert opening >= 0, f"Exported function {name} has no body"

    depth = 0
    quote = None
    escaped = False
    index = opening
    while index < len(source):
        character = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if quote:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
        elif character in {"'", '"', "`"}:
            quote = character
        elif character == "/" and following == "/":
            newline = source.find("\n", index + 2)
            index = len(source) if newline < 0 else newline
        elif character == "/" and following == "*":
            closing = source.find("*/", index + 2)
            index = len(source) if closing < 0 else closing + 1
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return source[match.start():index + 1]
        index += 1
    raise AssertionError(f"Exported function {name} has an unclosed body")


def _resolve_local(owner: Path, reference: str, *, allow_image_data: bool = False) -> Path | None:
    parsed = urlsplit(reference)
    if parsed.scheme == "data":
        assert allow_image_data and reference.lower().startswith("data:image/"), (
            f"{owner.relative_to(ROOT)} uses data: outside an image source"
        )
        return None
    if not parsed.path and parsed.fragment:
        return None
    assert not (parsed.scheme or parsed.netloc or reference.startswith("//")), (
        f"{owner.relative_to(ROOT)} loads a third-party runtime asset: {reference}"
    )
    path = unquote(parsed.path)
    target = (WEB / path.lstrip("/")) if path.startswith("/") else (owner.parent / path)
    resolved = target.resolve()
    resolved.relative_to(WEB.resolve())
    return resolved


#: The two pages that carry the public navigation in their header. It was three
#: pages and four links until `home.html` was retired; the calculator has never
#: been one of them.
#:
#: **The calculator is not one of them, and that is a measurement.** Its header
#: row already carries the language chooser §7.7.4 admitted to it, and its whole
#: vertical slack on the shortest step at 1278x983 is 32px against the 44px a
#: touch-target navigation block needs. Both arrangements were measured on a
#: real build and both broke
#: `tests/web/test_step_navigation.py::test_a_short_step_is_not_floored_by_a_stale_min_height`
#: — in the header by 37px, in the footer by 26px. The full reasoning, with the
#: numbers, is in the comment in `web/index.html`.
#:
#: The exemption is bounded rather than granted: the calculator still has to be
#: linked from every one of these three, and still has to reach the
#: documentation page itself. That is what `test_the_calculator_is_reachable…`
#: below asserts, so "the calculator carries no nav" can never quietly become
#: "the calculator links nowhere".
NAVIGATED_PAGES = ("statistics", "documentation")


def test_public_page_relationships_and_accessible_shells():
    """The user ruling keeps index as Calculator and adds Home beside it."""

    expected_links = {path.name for path in PAGES.values()}
    for role, path in PAGES.items():
        assert path.is_file(), f"Missing {role} page: {path.relative_to(ROOT)}"
        source, page = _page(path)

        html = page.matching("html")
        assert html and html[0]["attrs"].get("lang") == "en-NZ"
        assert page.matching("meta", charset="UTF-8") or page.matching("meta", charset="utf-8")
        assert page.matching("meta", name="viewport")
        assert page.matching("title")
        # The existing calculator renders its step heading from calculator.js;
        # the three content pages have a useful heading before JavaScript runs.
        if role != "calculator":
            assert page.matching("h1")
        assert len(page.matching("main")) == 1
        assert page.matching("header") and page.matching("footer")

        main_id = page.matching("main")[0]["attrs"].get("id")
        skip_links = [link for link in page.matching("a") if _text(link)]
        assert main_id and any(
            link["attrs"].get("href") == f"#{main_id}" for link in skip_links
        ), f"{path.name}: skip link must target its main content"

        navs = page.matching("nav")
        assert all(
            nav["attrs"].get("aria-label") or nav["attrs"].get("aria-labelledby")
            for nav in navs
        ), f"{path.name}: every nav needs an accessible name"

        if role in NAVIGATED_PAGES:
            assert navs, f"{path.name}: needs the public navigation"
            assert len(page.matching("a", **{"aria-current": "page"})) == 1
            linked = _linked_page_names(page)
            if any(urlsplit(href).path == "/" for href in page.values("a", "href")):
                linked.add("index.html")
            missing = expected_links - linked
            assert not missing, f"{path.name}: public navigation is missing {sorted(missing)}"
        else:
            # The calculator marks no current page because it carries no nav to
            # mark it in; a stray `aria-current` here would mean one came back.
            assert not page.matching("a", **{"aria-current": "page"}), (
                f"{path.name}: carries no public navigation, so nothing may claim to be "
                "the current page — see NAVIGATED_PAGES for the measurement"
            )

        scripts = page.matching("script")
        assert scripts and any(script["attrs"].get("type") == "module" for script in scripts)
    assert "calculator" in _read(PAGES["calculator"]).lower()


def test_the_retired_home_page_is_still_in_the_tree_and_still_whole():
    """Retired, not deleted - and not rotted either.

    `home.html` is reachable from nothing. That is the decision, and it means no other
    test in this file looks at the file at all, which is exactly how a retired page
    becomes a broken one nobody notices until the client asks for it back.

    So the three things that must survive the retirement are asserted here: the file
    exists, it still carries the news section that is the reason it was kept, and it
    still links to the calculator - so a reader who reaches it from a bookmark or a
    search result is not stranded on a page with no way into the tool.

    **It is deliberately NOT asserted to be linked from anywhere.** If a link back to
    it ever reappears, `test_no_reachable_page_links_to_a_retired_one` below fails.
    """
    home = RETIRED_PAGES["home"]
    assert home.is_file(), "home.html was deleted; it is retired pending the client's "        "decision on the news feed, which has not been given"
    text = _read(home)
    # **The MARKUP, not the word.** Written as `"news" in text.lower()` this passed a
    # mutation that deleted the whole `<section class="home-news">` - because the
    # retirement note at the top of the file says "news feed" several times, and a
    # comment about a section is not a section. `web/js/home.js` keys on `#news-feed`
    # and removes `.home-news` when no origin is configured, so those are the two
    # names the page has to keep for the module above it to still have a page.
    assert 'class="home-news"' in text, (
        "the news section is gone from home.html. It is the reason this page is kept "
        "rather than deleted; without it the file is a duplicate of the client's own "
        "site and nothing else"
    )
    assert 'id="news-feed"' in text, (
        "`#news-feed` is what web/js/home.js fills and what it removes when no origin "
        "is configured; without the element the retired module has no page"
    )
    assert "index.html" in text, "the retired page must still reach the calculator"


def test_no_reachable_page_links_to_a_retired_one():
    """The other half of retirement, and the half a comment cannot enforce.

    A page is retired when nothing links to it. Restoring one row to the drawer, one
    entry to a header navigation or one `href` on a wordmark quietly un-retires it -
    each is a one-line change that reads as a fix, and the client has not asked for it.
    """
    for name in (path.name for path in RETIRED_PAGES.values()):
        for role, path in PAGES.items():
            linked = _linked_page_names(_page(path)[1])
            assert name not in linked, (
                f"{path.name} links to the retired page {name}. It is in the tree "
                "pending the client's decision on the news feed; it is not a "
                "destination. See RETIRED_PAGES."
            )


def test_the_calculator_is_reachable_from_every_page_and_reaches_the_documentation():
    """What the calculator's navigation exemption is bounded by.

    The calculator carries no header navigation — see ``NAVIGATED_PAGES`` for
    the measurement — so the two things that exemption must not cost are
    asserted here directly: a visitor can always get *to* the calculator, and a
    visitor *on* the calculator can always reach the page that publishes the
    factors behind the number they are being shown.

    §7.6.2 and §6.3 are why the second half matters more than it looks. The
    factor set is mock, every results view says so, and the page that explains
    what that means is the documentation page.
    """
    for role in NAVIGATED_PAGES:
        linked = _linked_page_names(PAGES[role] and _page(PAGES[role])[1])
        if any(urlsplit(href).path == "/" for href in _page(PAGES[role])[1].values("a", "href")):
            linked.add("index.html")
        assert "index.html" in linked, f"{PAGES[role].name}: does not link to the calculator"

    calculator = _page(PAGES["calculator"])[1]
    assert "methodology.html" in _linked_page_names(calculator), (
        "index.html must reach the documentation page; it is the only public surface "
        "that publishes the provenance of the factors its results are computed from"
    )


def test_every_declared_runtime_asset_is_local_and_present():
    """News article links may be external; scripts, CSS, fonts and images may not."""

    css_files: set[Path] = set()
    for path in (*PAGES.values(), *RETIRED_PAGES.values()):
        _, page = _page(path)
        references = [
            *(page.values("script", "src")),
            *(
                link["attrs"]["href"]
                for link in page.matching("link")
                if set(link["attrs"].get("rel", "").split())
                & {"stylesheet", "icon", "preload", "manifest"}
            ),
        ]
        for reference in references:
            asset = _resolve_local(path, reference)
            if asset is not None:
                assert asset.is_file(), f"{path.name}: missing runtime asset {reference}"
                if asset.suffix == ".css":
                    css_files.add(asset)

        image_references = [
            *(page.values("img", "src")),
            *(page.values("source", "src")),
            *(page.values("video", "poster")),
        ]
        for srcset in page.values("img", "srcset") + page.values("source", "srcset"):
            image_references.extend(part.strip().split()[0] for part in srcset.split(",") if part.strip())
        for reference in image_references:
            asset = _resolve_local(path, reference, allow_image_data=True)
            if asset is not None:
                assert asset.is_file(), f"{path.name}: missing runtime image {reference}"

    for css in css_files:
        source = _read(css)
        assert not re.search(r"@import\s+(?:url\()?\s*['\"]?https?://", source)
        for match in re.finditer(r"url\(\s*(['\"]?)(.*?)\1\s*\)", source):
            asset = _resolve_local(css, match.group(2), allow_image_data=True)
            if asset is not None:
                assert asset.is_file(), f"{css.relative_to(ROOT)}: missing {match.group(2)}"
        for match in re.finditer(r"@import\s+(?:url\()?\s*(['\"])(.*?)\1", source):
            asset = _resolve_local(css, match.group(2))
            assert asset is not None and asset.is_file(), (
                f"{css.relative_to(ROOT)}: missing imported stylesheet {match.group(2)}"
            )

    # ES-module dependencies are runtime assets too, even though HTML does not list them.
    for module in (WEB / "js").rglob("*.js"):
        source = _without_js_comments(_read(module))
        imports = re.findall(
            r"(?:\bfrom\s*|\bimport\s*(?:\(\s*)?)['\"]([^'\"]+)['\"]",
            source,
        )
        for reference in imports:
            asset = _resolve_local(module, reference)
            assert asset is not None and asset.is_file(), (
                f"{module.relative_to(ROOT)}: missing module import {reference}"
            )


def test_api_js_owns_all_direct_fetch_calls_and_the_wordpress_url():
    """Static scope: direct ``fetch(...)`` tokens; browser QA covers aliases/other APIs.

    ``js/i18n.js`` is the one permitted exception and it arrived after this test
    was written — D branched at contract v1.17 and interface translation landed
    at v1.24-v1.27 underneath. It fetches **catalogues**, which are static files
    on this origin under ``web/locales/``, not API resources; routing them
    through ``api.js`` would put the language layer behind the module that
    reports API errors in the language the language layer has not chosen yet.

    The exemption is bounded rather than granted: i18n.js has to resolve every
    catalogue against a base declared relative to its own module URL, and may
    name no absolute origin and no ``/api/`` path at all.  §7.1's rule — *one*
    module owns the API — is what is being defended, not the token ``fetch``.
    """

    js_files = sorted((WEB / "js").rglob("*.js"))
    callers = []
    for path in js_files:
        if re.search(r"(?<![.\w$])fetch\s*\(", _js_code_without_comments_or_strings(_read(path))):
            callers.append(path.relative_to(WEB).as_posix())
    assert callers == ["js/api.js", "js/i18n.js"], (
        f"Only api.js and the catalogue loader may directly call fetch(), found {callers}"
    )

    i18n = _js_code_without_comments_or_strings(_read(WEB / "js" / "i18n.js"))
    assert re.search(
        r"new\s+URL\(\s*['\"]\.\./locales/['\"]\s*,\s*import\.meta\.url\s*\)",
        _read(WEB / "js" / "i18n.js"),
    ), "i18n.js must resolve catalogues against its own module URL"
    # Prose about the API is not a call to it, so these read the executable
    # token positions only — the same conservative source the scan above uses.
    i18n_literals = _without_js_comments(_read(WEB / "js" / "i18n.js"))
    assert "/api/" not in i18n_literals, "i18n.js must not reach the API; api.js owns it"
    # **ONE EXEMPTION, AND IT IS NOT AN ORIGIN.** `http://www.w3.org/2000/svg` is
    # the XML namespace a `<svg>` element has to be *created* in — pass anything
    # else to `createElementNS` and the browser builds an HTML element named
    # "svg" that renders nothing at all. It is an identifier compared by string;
    # nothing is ever fetched from it, and the chooser's globe is drawn in the
    # page precisely because `img-src 'self' data:` forbids fetching an icon.
    #
    # Removed exactly once and by exact text, so the scan below still fails on a
    # second occurrence, on a different w3.org path, or on any other host.
    scanned = i18n_literals.replace("http://www.w3.org/2000/svg", "", 1)
    for scheme in ("http://", "https://", "//cdn"):
        assert scheme not in scanned, (
            f"i18n.js names an absolute origin ({scheme}); catalogues are same-origin"
        )
    assert re.search(r"(?<![.\w$])fetch\s*\(\s*url\b", i18n), (
        "i18n.js's one fetch must take the URL built from that base"
    )

    api = _read(WEB / "js" / "api.js")
    news_request = _exported_function(api, "getNewsPosts")
    assert "limit" in news_request
    assert re.search(r"per_page\s*=", api)
    assert "_embed" in api

    # THE ORIGIN COMES FROM CONFIGURATION, AND THE WORDPRESS ROUTE DOES NOT.
    # WordPress fixes `/wp-json/wp/v2/posts`; only the host is a deployment fact, and it
    # arrives through config.js so that the same value builds the `connect-src` this
    # fetch has to satisfy. A literal host here is half of a pair that has to agree with
    # docker/nginx.conf, which is the defect this arrangement removed.
    assert "/wp-json/wp/v2/posts" in api
    assert re.search(r"import\s*\{[^}]*\bNEWS_ORIGIN\b[^}]*\}\s*from\s*'\./config\.js'", api), (
        "api.js must take the news origin from config.js"
    )
    assert re.search(r"import\s*\{[^}]*\bAPI_ORIGIN\b[^}]*\}\s*from\s*'\./config\.js'", api), (
        "api.js must take the API origin from config.js"
    )
    assert re.search(r"API_BASE\s*=\s*`\$\{API_ORIGIN\}/api/v1`", api), (
        "API_BASE must stay relative when API_ORIGIN is empty - same origin is the default"
    )
    # `?mock=1` reads `../../tests/fixtures/`, which is a path and not an origin.
    api_literals = _without_js_comments(api)
    for scheme in ("http://", "https://", "//cdn"):
        assert scheme not in api_literals, (
            f"api.js names an absolute origin ({scheme}); origins come from config.js"
        )

    # An unset origin must not become a request to a guessed host. Most deployments of
    # this calculator have no WordPress, and `null` is what the home page reads to drop
    # the section rather than report an outage nobody caused.
    assert re.search(r"if\s*\(\s*!\s*NEWS_ORIGIN\s*\)\s*return\s+null", news_request), (
        "getNewsPosts must return null, and fetch nothing, when no news origin is set"
    )

    # Existing mock mode continues to consume B's canonical fixtures.
    assert "../../tests/fixtures/" in api
    assert "stats.json" in api and "factors.json" in api


def test_news_module_exposes_the_safe_object_and_failure_contract():
    """Malicious markup, URL protocols and remote-image DOM are browser acceptance tests."""

    path = WEB / "js" / "news.js"
    assert path.is_file()
    source = _read(path)
    fetch_news = _exported_function(source, "fetchNews")

    assert "getNewsPosts" in source and "./api.js" in source
    assert "catch" in fetch_news and re.search(r"return\s+\[\s*\]", fetch_news)
    for field in ("title", "excerpt", "link", "date", "imageUrl"):
        assert re.search(rf"\b{field}\b", fetch_news), f"News objects omit {field}"
    assert ".rendered" in source

    home_script = WEB / "js" / "home.js"
    assert home_script.is_file()
    assert "fetchNews" in _read(home_script) and "./news.js" in _read(home_script)


def test_charts_module_has_exact_public_exports_and_vendored_chartjs():
    path = WEB / "js" / "charts.js"
    assert path.is_file()
    source = _read(path)
    exported = set(re.findall(r"export\s+function\s+([A-Za-z_$][\w$]*)\s*\(", source))
    assert exported == {"renderDonut", "renderPie", "renderBar", "renderLine"}
    assert re.search(r"\bnew\s+Chart\s*\(", source)

    chart_sources = []
    for candidate in WEB.rglob("*.js"):
        candidate_source = _read(candidate)
        if "Chart.js v4.5.1" in candidate_source:
            chart_sources.append((candidate, candidate_source))
    assert len(chart_sources) == 1, "Exactly one first-party Chart.js 4.5.1 runtime is required"
    vendor_path, vendor_source = chart_sources[0]
    nearby_licences = [
        candidate for candidate in vendor_path.parent.iterdir()
        if candidate.is_file() and "license" in candidate.name.lower()
    ]
    assert "MIT" in vendor_source[:2000] or any("MIT" in _read(path) for path in nearby_licences), (
        f"{vendor_path.relative_to(ROOT)} lacks its MIT licence notice"
    )
    stats_html = _read(PAGES["statistics"])
    assert vendor_path.name in source or vendor_path.name in stats_html, (
        "charts.js or stats.html must actually load the vendored Chart.js runtime"
    )
    assert "https://cdn" not in source.lower()


def test_documented_statistics_visual_contract_matches_the_public_modules():
    """Catch a chart contract that omits the shipped selection and rendering rules."""

    contract = _read(ROOT / "docs" / "interfaces.md")
    section = contract.split("## 7.4 `charts.js` (written by D)", 1)[1].split("## 7.5", 1)[0]
    changelog = contract.split("## 0.1 Change Log", 1)[1].split("### v1.78", 1)[0]
    entry = re.search(r"(?ms)^### v(\d+)\.(\d+) [^\n]*statistics chart selection[^\n]*\n.*?(?=^### v|\Z)", changelog)
    assert entry, "the statistics presentation change needs its own changelog entry"

    versions = [(int(major), int(minor)) for major, minor in re.findall(
        r"(?m)^### v(\d+)\.(\d+)\b", contract,
    )]
    header = re.search(r'(?m)^date: "[^"]+ \(v(\d+)\.(\d+)\)"$', contract)
    assert header
    assert len(versions) == len(set(versions))
    assert (int(entry[1]), int(entry[2])) > (1, 78)
    assert (int(header[1]), int(header[2])) >= (int(entry[1]), int(entry[2]))

    charts_source = _read(WEB / "js" / "charts.js")
    actual = set(re.findall(r"(?m)^export (?:function|const)\s+(\w+)", charts_source))
    documented = set(re.findall(r"(?m)^export (?:function|const)\s+(\w+)", section))
    assert documented == actual == {"PALETTE", "renderDonut", "renderPie", "renderBar", "renderLine"}
    assert len(re.findall(r"(?m)^export function\s+\w+", section)) == 4

    for token in (
        "labelKey", "valueKey", "formatValue", "allowNegative", "String(value)",
        "RangeError", "finite negative", "count-ranked", "Other", "not a time trend",
        "pie", "bar", "line", "latestStats", "latestFailure", "rerenderInActiveLanguage",
        "fitLegend", "reduced-motion", "16", "§6.4", "§7.7.7", "en-NZ",
    ):
        assert token in section, f"§7.4 omits {token}"
    for token in ("REST", "wire", "fixture", "no-op", "team", "PR"):
        assert token in entry[0], f"statistics changelog omits {token}"

    architecture = _read(ROOT / "docs" / "architecture.md")
    statistics_row = re.search(r"(?m)^\| Statistics \|.*$", architecture)
    assert statistics_row and all(chart in statistics_row[0] for chart in ("Pie", "Bar", "Line"))
    readme = _read(WEB / "README.md")
    assert "Chart.js adapters for doughnut, pie, bar and line charts" in readme
    assert "count-ranked" in readme and "Other" in readme


#: The provenance note beside the runtime, and the two files it vouches for.
VENDOR = WEB / "vendor"
SOURCE_NOTE = VENDOR / "chart.js.SOURCE.md"


def test_the_recorded_chartjs_hashes_are_the_hashes_of_the_files_on_disk():
    """The provenance note is checked, not merely written.

    ``chart.js.SOURCE.md`` records a runtime SHA-256, a licence SHA-256 and the
    npm integrity string the tarball was verified against, and all three were
    correct byte for byte against ``registry.npmjs.org/chart.js/4.5.1``. Nothing
    kept them correct: the strongest claim any test made about the vendored
    runtime was that some file contained the literal ``Chart.js v4.5.1``, which
    a one-line file would satisfy.

    That matters more here than a version pin usually would. There is no build
    step, no lockfile and no package manager anywhere near ``web/`` — this
    206KB blob is committed as source, and the note beside it is the only record
    of where it came from. A hash nobody recomputes is a claim about a file
    rather than a fact about it.

    The hashes are read **out of the note** rather than repeated here, so the
    note stays the single record and editing it to match a swapped file is the
    same edit either way — one that has to be made deliberately, in the file
    whose whole job is to say what was downloaded.
    """
    assert SOURCE_NOTE.is_file(), "the vendored runtime has no provenance note"
    note = _read(SOURCE_NOTE)

    recorded = {
        label: match
        for label, match in re.findall(
            r"^-\s+(Runtime|Licence|License)\s+SHA-256:\s*`([0-9a-f]{64})`\s*$",
            note,
            flags=re.M,
        )
    }
    assert set(recorded) & {"Runtime"}, "SOURCE.md records no runtime SHA-256"
    assert set(recorded) & {"Licence", "License"}, "SOURCE.md records no licence SHA-256"

    licence_key = "Licence" if "Licence" in recorded else "License"
    for filename, key in (
        ("chart.umd.min.js", "Runtime"),
        ("chart.js.LICENSE.md", licence_key),
    ):
        path = VENDOR / filename
        assert path.is_file(), f"{filename} is recorded in SOURCE.md but is not on disk"
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == recorded[key], (
            f"web/vendor/{filename} does not match the SHA-256 recorded in "
            f"chart.js.SOURCE.md: recorded {recorded[key]}, on disk {actual}. "
            "Either the file was replaced without updating its provenance, or the "
            "provenance was updated without replacing the file."
        )

    # The npm integrity string is the third recorded fact and the one that ties
    # the pair to a published release. It is not recomputable from these two
    # files - it covers the whole tarball - so what is asserted is that it is
    # still recorded, in the registry's own format, for the version claimed.
    assert re.search(r"`sha512-[A-Za-z0-9+/]{86}==`", note), (
        "SOURCE.md no longer records the npm integrity string the tarball was verified against"
    )
    assert "4.5.1" in note and "registry.npmjs.org/chart.js" in note


#: The brand palette, from `Kai Commitment_Brand Guidelines_v1-Oct25.pdf`.
#: White is excluded: it is the page ground and the segment border, so it is not
#: available as a fill. The guidelines misprint Blueberry's RGB as 0/90/130; the
#: hex is authoritative.
KALE = "#003223"
WHITE = "#FFFFFF"

#: The guidelines' own dark-ground/light-ground classification. Beetroot is not
#: classified there; at 9.64:1 against White and 1.47:1 against Kale it is a
#: dark ground by any reading, so it is listed with the ones that are.
BRAND_COLOURS = {
    "#003223": ("Kale", WHITE),
    "#FF5032": ("Orange", WHITE),
    "#005AE6": ("Blueberry", WHITE),
    "#87005A": ("Beetroot", WHITE),
    "#28C882": ("Pea", KALE),
    "#FFD76E": ("Banana", KALE),
    "#E6BEFF": ("Lavender", KALE),
}

#: Orange on White is 3.26:1, below the 4.5:1 body-text minimum. It is the
#: brand's own pairing, named here as the single stated exception rather than
#: lowering the bar for every entry.
BRAND_CONTRAST_EXCEPTIONS = {"#FF5032"}


def _palette() -> list[tuple[str, str]]:
    """`PALETTE`'s `{fill, ink}` pairs, read out of charts.js in order."""
    source = _read(WEB / "js" / "charts.js")
    match = re.search(r"export\s+const\s+PALETTE\s*=\s*\[", source)
    assert match, "charts.js no longer exports PALETTE"
    body = _bracket_span(source, source.index("[", match.end() - 1))
    entries = re.findall(
        r"\{\s*fill:\s*'(#[0-9A-Fa-f]{6})'\s*,\s*ink:\s*(WHITE|KALE|'#[0-9A-Fa-f]{6}')\s*\}",
        body,
    )
    assert entries, "PALETTE's entries are no longer {fill, ink} pairs"
    resolved = {"WHITE": WHITE, "KALE": KALE}
    return [(fill.upper(), resolved.get(ink, ink.strip("'")).upper()) for fill, ink in entries]


def _relative_luminance(hex_colour: str) -> float:
    """WCAG 2.x relative luminance, computed here rather than imported from the
    module under test, so the pairing is checked independently of the table."""
    channels = []
    for offset in (1, 3, 5):
        value = int(hex_colour[offset:offset + 2], 16) / 255
        channels.append(value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4)
    red, green, blue = channels
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(a: str, b: str) -> float:
    high, low = sorted((_relative_luminance(a), _relative_luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_the_palette_covers_the_taxonomy_without_repeating_a_colour():
    """The palette was nine long and indexed modulo its own length, so a tenth
    bucket drew the first one's colour — measured, `Landfill` and
    `Other (sample too small)` were both `#005f73` in the same doughnut.

    The ceiling is not hypothetical and not a guess: it is every destination the
    taxonomy defines, plus the `other` bucket §5.4 merges the suppressed ones
    into, which is an ordinary bucket alongside them rather than instead of one.
    Pinning the length against the fixture is what keeps the modulo in
    `paletteEntry` unreachable — if B adds destinations, this fails before a
    doughnut repeats a colour on a public page.
    """
    palette = _palette()
    fills = [fill for fill, _ in palette]
    assert len(fills) == len(set(fills)), (
        f"the palette repeats a colour: {sorted({f for f in fills if fills.count(f) > 1})}"
    )

    taxonomy = _json(FIXTURES / "taxonomy.json")
    ceiling = len(taxonomy["destinations"]) + 1
    assert len(palette) >= ceiling, (
        f"the palette has {len(palette)} colours for a ceiling of {ceiling} buckets "
        f"({len(taxonomy['destinations'])} destinations plus `other`), so the modulo in "
        "paletteEntry() would repeat one"
    )


def test_the_palette_is_built_only_from_brand_colours():
    """Not one of Kale, Orange, Pea, Blueberry, Beetroot, Banana or Lavender
    appeared in the palette this replaces; `renderBar` drew `#0a9396` on
    `#005f73`, neither of which is anywhere in the guidelines, on a page whose
    every other colour comes from a `--kai-*` token.

    Sixteen colours cannot all be brand colours — there are seven — so the rest
    are mixes toward White and toward Kale. What is asserted is that every entry
    is *on a line between two brand colours*: either one of the seven exactly,
    or a mix of one of them with White or with Kale. That refuses an invented
    hue while allowing the tints and shades the count needs.
    """
    palette = _palette()
    for fill, _ in palette:
        if fill in BRAND_COLOURS:
            continue
        target = tuple(int(fill[i:i + 2], 16) for i in (1, 3, 5))
        derived = False
        for base_hex in BRAND_COLOURS:
            base = tuple(int(base_hex[i:i + 2], 16) for i in (1, 3, 5))
            for other in ((255, 255, 255), (0, 50, 35)):
                for step in range(1, 100):
                    mixed = tuple(round(b + (o - b) * step / 100) for b, o in zip(base, other))
                    if max(abs(m - t) for m, t in zip(mixed, target)) <= 1:
                        derived = True
                        break
                if derived:
                    break
            if derived:
                break
        assert derived, (
            f"{fill} is not a brand colour and is not a mix of one with White or Kale; "
            "the brand palette is White, Kale, Orange, Pea, Blueberry, Beetroot, Banana "
            "and Lavender"
        )

    assert set(BRAND_COLOURS) <= {fill for fill, _ in palette}, (
        "every brand colour usable as a fill should appear in the palette before any mix does"
    )


def test_every_palette_ink_follows_the_brand_rule():
    """Dark grounds take white text, light grounds take Kale.

    `ink` is load-bearing rather than recorded: the current Statistics default,
    `renderPie`, paints the tooltip on the hovered segment's own fill and takes
    that segment's ink. `renderDonut` remains a compatible adapter with no
    Statistics caller. An `ink`
    edited out of step with its `fill` draws white text on Banana. The contrast
    ratio is recomputed here from the hex, independently of the table, so this
    fails on the entry that drifted rather than on the rule being restated.
    """
    for fill, ink in _palette():
        assert ink in {WHITE, KALE}, f"{fill} takes {ink}, which is neither White nor Kale"
        against_white = _contrast(fill, WHITE)
        against_kale = _contrast(fill, KALE)

        if fill in BRAND_COLOURS:
            # The guidelines classify these seven, and the guidelines win. Orange
            # is the case that proves the two rules differ: it is a *dark* ground
            # taking white text, while raw contrast would pair it with Kale.
            name, expected = BRAND_COLOURS[fill]
            assert ink == expected, (
                f"{name} ({fill}) is a "
                f"{'dark' if expected == WHITE else 'light'} ground in the brand "
                f"guidelines and takes {expected}, not {ink}"
            )
        else:
            expected = WHITE if against_white >= against_kale else KALE
            assert ink == expected, (
                f"{fill} takes {ink}: contrast is {against_white:.2f} against White and "
                f"{against_kale:.2f} against Kale, so it is a "
                f"{'dark' if expected == WHITE else 'light'} ground and takes {expected}"
            )

        if fill not in BRAND_CONTRAST_EXCEPTIONS:
            assert _contrast(fill, ink) >= 4.5, (
                f"{fill} reaches only {_contrast(fill, ink):.2f}:1 against its own ink, "
                "below the 4.5:1 body-text minimum the tooltip needs"
            )

    # The exception has to stay an exception: an entry listed here that now
    # clears 4.5 is a stale exemption, and one that is not in the palette at all
    # is a note about a colour nobody uses.
    palette_fills = {fill for fill, _ in _palette()}
    for fill in BRAND_CONTRAST_EXCEPTIONS:
        assert fill in palette_fills, f"{fill} is exempted but is not in the palette"
        ink = dict(_palette())[fill]
        assert _contrast(fill, ink) < 4.5, (
            f"{fill} now reaches {_contrast(fill, ink):.2f}:1 and no longer needs its exemption"
        )


def test_statistics_source_consumes_the_stats_contract_without_nz_generalisation():
    """Independent empty states, negative axes and destroy timing stay in browser QA."""

    html = _read(PAGES["statistics"])
    source = _read(WEB / "js" / "stats.js")
    combined = (html + "\n" + source).lower()

    assert "getstats" in source.lower() and "./api.js" in source
    assert "self-selected" in combined
    assert "total_calculations" in source
    assert "generated_at" in source and "suppression_threshold" in source
    assert "tonnes recorded" not in combined

    # **The API's own `share`, read as a field, not the substring "share".**
    # `assert "share" in source` was satisfied by the function name
    # `sharePercent` and by nothing else needing to be true: every reference to
    # the field could have been deleted and the assertion would still have
    # passed. Anchored on a property read instead.
    assert re.search(r"\brow\s*\??\.\s*share\b|\[\s*['\"]share['\"]\s*\]|valueKey:\s*['\"]share['\"]", source), (
        "the statistics page must read the API-provided `share`, not derive one"
    )

    for breakdown in ("by_destination", "by_sector", "by_food_category"):
        assert breakdown in source
    assert re.search(r"\bcatch\b", source)

    # **Suppression is the service's, and the browser may not repeat it.**
    # The guard here was `\.filter\s*\([^)]*(?:other|unspecified)`, which stops
    # at the first `)` and so never sees the body of an arrow function. It does
    # not catch `rows.filter(r => r.count < stats.suppression_threshold)` — the
    # exact client-side re-suppression it exists to forbid, and the one that
    # would silently drop the `other` bucket §6.4 says can be the largest row
    # in the breakdown. Every `.filter(` call is now read to its matching
    # bracket and refused if it mentions a bucket field, a threshold or a
    # bucket code.
    code = _js_code_without_comments_or_strings(source)
    forbidden = ("count", "share", "total_kg", "suppression_threshold", "code", "label")
    for match in re.finditer(r"\.filter\s*\(", code):
        opening = code.index("(", match.start())
        body = _bracket_span(code, opening)
        named = sorted(word for word in forbidden if re.search(rf"\b{word}\b", body))
        assert not named, (
            f"stats.js filters buckets in the browser on {named}: suppression and bucket "
            f"membership are the service's ({source[opening - 40:opening + len(body)]!r})"
        )

    assert all(name in source for name in ("renderPie", "renderBar", "renderLine", "./charts.js"))


def test_the_statistics_page_never_makes_new_zealand_the_subject():
    """§6.4's copy constraint, at word level over the page's own text.

    The predicate, why the phrase-order regex it replaces was decorative, and
    the four sentences that passed it are all in ``tests/support/red_line.py``.
    This asserts the page against it; ``test_the_red_line_guard_can_fail``
    below asserts the predicate against those four.

    The text scanned is the statistics page's static copy plus every string
    literal ``stats.js`` can put on the screen — the module builds its DOM with
    ``textContent``, so its literals are its rendered text. The same predicate
    is run over the genuinely rendered ``innerText`` in
    ``tests/web/test_statistics_browser.py``, which is the form that also sees
    what the API sends.
    """
    html = _read(PAGES["statistics"])
    literals = " ".join(
        "".join(group) for group in re.findall(
            r"`([^`]*)`|'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\"",
            _read(WEB / "js" / "stats.js"),
        )
    )
    offenders = red_line.violations(f"{html}\n{literals}")
    assert not offenders, f"the statistics page makes New Zealand its subject: {offenders}"


@pytest.mark.parametrize("sentence", red_line.COUNTEREXAMPLES)
def test_the_red_line_guard_can_fail(sentence):
    """Each of the four sentences the previous guard let through is caught.

    Without this the rewrite would be an untested rewrite, which is the same
    defect one layer up.
    """
    assert red_line.violations(sentence), f"the red-line guard does not catch {sentence!r}"


@pytest.mark.parametrize("sentence", red_line.PERMITTED)
def test_the_red_line_guard_is_not_merely_a_word_ban(sentence):
    """And each sentence that may name the country is left alone.

    A guard that refused the words "New Zealand" outright would pass every
    counterexample above and be useless: the page's own copy names the country,
    in a negation, and that sentence is the reason it passes at all.
    """
    assert not red_line.violations(sentence), f"the red-line guard over-reaches on {sentence!r}"


def test_statistics_fixture_exercises_the_public_semantics():
    stats = _json(FIXTURES / "stats.json")
    assert set(stats) == {
        "generated_at", "total_calculations", "suppression_threshold",
        "by_destination", "by_sector", "by_food_category",
    }
    assert isinstance(stats["total_calculations"], int)
    assert isinstance(stats["suppression_threshold"], int)
    assert isinstance(stats["generated_at"], str)

    for breakdown in ("by_destination", "by_sector", "by_food_category"):
        rows = stats[breakdown]
        assert isinstance(rows, list) and rows
        for row in rows:
            assert set(row) == {"code", "label", "count", "share", "total_kg"}
            assert isinstance(row["count"], int)
            assert isinstance(row["share"], str) and isinstance(row["total_kg"], str)
        assert "other" in {row["code"] for row in rows}
    assert "unspecified" in {row["code"] for row in stats["by_food_category"]}


def test_methodology_source_consumes_collections_metadata_and_nullable_fields():
    """Mock true/false, null, empty and hostile-text rendering stay in browser QA."""

    html = _read(PAGES["documentation"])
    source = _read(WEB / "js" / "methodology.js")
    combined = (html + "\n" + source).lower()

    assert "getFactors" in source and "./api.js" in source
    for collection in ("constants", "formulas", "upstream", "downstream", "equivalences"):
        assert re.search(
            rf"(?:\.|\[['\"]){collection}(?:\b|['\"]\])|"
            rf"\{{[^}}]*\b{collection}\b[^}}]*\}}\s*=",
            source,
        ), f"Documentation does not read {collection} from the factors response"
    for metadata in ("version_label", "published_at", "notes"):
        assert re.search(
            rf"factor_set(?:\.|\?\.){metadata}\b|factor_set\[['\"]{metadata}['\"]\]|"
            rf"\{{[^}}]*\b{metadata}\b[^}}]*\}}\s*=\s*[^;\n]*factor_set",
            source,
        ), (
            f"Documentation omits factor_set.{metadata}"
        )
    assert "source_note" in source and "data_quality" in source
    assert "food_category" in source
    assert "is_mock" in source
    assert "placeholder" in combined and "warning" in combined
    assert "approved background reading has not yet been provided" in combined
    assert re.search(r"\bcatch\b", source)


def test_the_documentation_page_publishes_only_the_contract_s_factor_set_fields():
    """§1.1 makes `code` the cross-layer identifier and §7 forbids the front end
    learning a database primary key.

    `METADATA_FIELDS` in `methodology.js` was written to print one. It listed
    ``['id', 'ID', factor_set => factor_set.id]``, and §6.3's `factor_set`
    carries `version_label`, `is_mock`, `published_at` and `notes` — so
    `Object.hasOwn` filtered it, nothing rendered, and the page looked correct.
    It would have begun printing a primary key on a public page the day B added
    `id` to the projection, with nothing failing.

    A projection is not a permission, so the list is asserted to be exactly
    §6.3's four fields rather than merely free of `id`. `name`, `version` and
    `effective_from` were equally dead, and a page that renders whatever the
    response happens to carry is a page that publishes whatever the response
    happens to carry.
    """
    source = _read(WEB / "js" / "methodology.js")
    match = re.search(r"const\s+METADATA_FIELDS\s*=\s*\[", source)
    assert match, "methodology.js no longer declares METADATA_FIELDS"
    body = _bracket_span(source, source.index("[", match.end() - 1))

    keys = re.findall(r"\[\s*'([a-z_]+)'", body)
    assert keys, "METADATA_FIELDS no longer names its fields as string keys"
    assert set(keys) == {"version_label", "published_at", "notes", "is_mock"}, (
        f"the documentation page publishes {sorted(keys)}; §6.3's factor_set carries "
        "version_label, is_mock, published_at and notes, and nothing else may be rendered "
        "from it without a contract change"
    )

    # Belt and braces across the whole module, because the field list is not the
    # only way to reach a primary key.
    code = _js_code_without_comments_or_strings(source)
    assert not re.search(r"factor_set\s*(?:\?)?\.\s*id\b", code), (
        "methodology.js reads factor_set.id; the front end never learns a primary key"
    )
    for module in sorted((WEB / "js").glob("*.js")):
        module_code = _js_code_without_comments_or_strings(_read(module))
        offenders = re.findall(r"\b(?:factor_set|row|entry|bucket)\s*(?:\?)?\.\s*id\b", module_code)
        assert not offenders, (
            f"{module.relative_to(ROOT)} reads a database primary key: {offenders}"
        )


def test_factors_fixture_carries_negative_generic_and_nullable_rows():
    factors = _json(FIXTURES / "factors.json")
    assert set(factors) == {
        "factor_set", "constants", "formulas", "upstream", "downstream", "equivalences",
    }
    assert factors["factor_set"]["is_mock"] is True
    for collection in ("constants", "formulas", "upstream", "downstream", "equivalences"):
        assert factors[collection], f"Canonical fixture needs demo data for {collection}"
    for row in factors["upstream"]:
        assert "source_note" in row and "data_quality" in row
    for row in factors["downstream"]:
        assert "source_note" in row and "data_quality" in row
    for row in factors["equivalences"]:
        assert {"name", "sort_order", "source_note"} <= row.keys()
    assert any(row["food_category"] is None for row in factors["downstream"])
    assert any(float(row["value_per_kg"]) < 0 for row in factors["downstream"])
    assert any(
        row.get("source_note") is None or row.get("data_quality") is None
        for rows in (factors["upstream"], factors["downstream"])
        for row in rows
    ) or any(row.get("source_note") is None for row in factors["equivalences"])


def test_every_public_footer_has_transparency_copy_and_what_we_record_link():
    for path in PAGES.values():
        _, page = _page(path)
        footers = page.matching("footer")
        assert len(footers) == 1
        copy = _text(footers[0]).lower()
        assert "sector" in copy and "food categor" in copy and "quantit" in copy
        # **"public statistics", not "aggregate statistics", and the choice with
        # it.** Item 13 made the aggregate opt-in, and this notice renders in
        # `index.html`'s footer - which is the calculator, so a visitor met it on
        # the results page a few centimetres above the contribute control, being
        # told their calculation was already in the statistics and then asked to
        # opt in to exactly that. `tests/web/test_consent_copy.py` holds the rule
        # this line now checks one instance of.
        assert "public statistics" in copy
        assert "choose to offer" in copy, (
            f"{path.name}: the footer states the statistics as a fact rather than "
            "as the visitor's choice"
        )
        assert "nothing" in copy and "identif" in copy and "business" in copy
        record_links = [
            link for link in page.matching("a")
            if "footer" in link["ancestor_tags"]
            and "what we record" in _text(link).lower()
        ]
        assert record_links, f"{path.name}: footer needs a What we record link"
        for link in record_links:
            href = link["attrs"].get("href", "")
            parsed = urlsplit(href)
            assert href and not parsed.scheme and not parsed.netloc
            if not parsed.path:
                target = path
            elif parsed.path.startswith("/"):
                target = (WEB / unquote(parsed.path).lstrip("/")).resolve()
            else:
                target = (path.parent / unquote(parsed.path)).resolve()
            target.relative_to(WEB.resolve())
            assert target.is_file(), f"{path.name}: What we record target does not exist"
            assert parsed.fragment, f"{path.name}: What we record must target a section"
            _, target_page = _page(target)
            assert any(
                element["attrs"].get("id") == parsed.fragment
                for element in target_page.elements
            ), f"{path.name}: missing What we record fragment #{parsed.fragment}"


def test_nginx_public_static_location_has_the_required_csp_semantics():
    source = re.sub(r"(?m)^\s*#.*$", "", _read(ROOT / "docker" / "nginx.conf"))
    location_match = re.search(r"\blocation\s+/\s*\{", source)
    assert location_match, "nginx needs the public static `location /` block"
    public_block, public_end = _brace_block(source, source.find("{", location_match.start()))
    matches = re.findall(
        r"add_header\s+Content-Security-Policy\s+\"([^\"]+)\"\s+always\s*;",
        public_block,
        flags=re.DOTALL,
    )
    assert len(matches) == 1, "Public static pages need one CSP with `always`"
    other_source = source[:location_match.start()] + source[public_end:]
    assert not re.search(r"add_header\s+Content-Security-Policy\b", other_source), (
        "The public-page CSP must not be applied to /api or /admin"
    )

    actual: dict[str, set[str]] = {}
    for raw_directive in matches[0].split(";"):
        tokens = raw_directive.split()
        if not tokens:
            continue
        name, *values = tokens
        assert name not in actual, f"Duplicate CSP directive: {name}"
        actual[name] = set(values)
    for directive, required_values in REQUIRED_CSP.items():
        assert actual.get(directive) == required_values, (
            f"CSP {directive} must be exactly {sorted(required_values)}; "
            f"found {sorted(actual.get(directive, set()))}"
        )

    # NO DEPLOYMENT DOMAIN GOES BACK INTO THIS FILE. The client's production domain was
    # written here and again in web/js/api.js, and the two had to agree; they fail
    # asymmetrically, so nothing would have caught the drift. Both now derive from one
    # environment variable, and re-adding a literal host to any directive - the natural
    # fix when a resource is refused - silently re-creates the pair.
    # The whole template, not only the policy: a host added to a `proxy_pass`, a
    # `sub_filter` or a new `add_header` is the same defect wearing a different hat. The
    # comments were stripped at the top of this function, so prose about the client's site
    # is not what this reads.
    #
    # A DOT IS WHAT SEPARATES THE TWO KINDS OF HOST HERE. `proxy_pass http://api:18000`
    # and `http://admin:18001` name compose services - single labels that resolve only on
    # the internal network, are not deployment facts, and are the routing this file exists
    # to express. Anything with a dot in it is a public name or an address: a domain, or
    # an IP somebody wrote down.
    for origin in re.findall(r"(?i)\bhttps?://[A-Za-z0-9][A-Za-z0-9-]*(?:\.[A-Za-z0-9-]+)+", source):
        assert False, (
            f"docker/nginx.conf names the literal origin {origin}. Origins reach this file "
            "from KAICALC_NEWS_ORIGIN / KAICALC_API_ORIGIN / KAICALC_NEWS_IMAGE_ORIGINS "
            "through docker/web-config.sh, which builds web/js/config.js from the same "
            "values - a host written here is a second copy the front end does not follow, "
            "and the two fail asymmetrically: a wrong policy means the news quietly does "
            "not load, a wrong URL means the browser asks a domain nobody chose."
        )


def test_stats_and_factors_have_one_canonical_fixture_each():
    excluded = {
        ".git", ".codex", ".venv", ".pytest_cache", ".superpowers", ".claude",
        "__pycache__", "cache",
    }
    tracked = {
        (ROOT / line).resolve()
        for line in subprocess.run(
            ["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True,
        ).stdout.splitlines()
    }
    product_roots = [
        WEB, ROOT / "api", ROOT / "admin", ROOT / "db", ROOT / "engine", ROOT / "tests",
    ]
    for name in ("stats.json", "factors.json"):
        copies = {path for path in tracked if path.name == name}
        for product_root in product_roots:
            if product_root.is_dir():
                copies.update(
                    path.resolve() for path in product_root.rglob(name)
                    if not excluded.intersection(path.relative_to(ROOT).parts)
                )
        assert copies == {(FIXTURES / name).resolve()}, (
            f"{name} must exist only in tests/fixtures; found "
            f"{sorted(path.relative_to(ROOT).as_posix() for path in copies)}"
        )
