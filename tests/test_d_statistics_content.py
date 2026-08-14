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

import json
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
FIXTURES = ROOT / "tests" / "fixtures"

PAGES = {
    "home": WEB / "home.html",
    "calculator": WEB / "index.html",
    "statistics": WEB / "stats.html",
    "documentation": WEB / "methodology.html",
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
    "img-src": {"'self'", "data:"},
    "connect-src": {"'self'", "https://kaicommitment.org.nz"},
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
        assert navs and all(
            nav["attrs"].get("aria-label") or nav["attrs"].get("aria-labelledby")
            for nav in navs
        )
        assert len(page.matching("a", **{"aria-current": "page"})) == 1
        linked = _linked_page_names(page)
        if any(urlsplit(href).path == "/" for href in page.values("a", "href")):
            linked.add("index.html")
        missing = expected_links - linked
        assert not missing, f"{path.name}: public navigation is missing {sorted(missing)}"

        scripts = page.matching("script")
        assert scripts and any(script["attrs"].get("type") == "module" for script in scripts)
    home_text = _read(PAGES["home"]).lower()
    assert "news" in home_text
    assert "calculator" in home_text and "index.html" in home_text
    assert "calculator" in _read(PAGES["calculator"]).lower()


def test_every_declared_runtime_asset_is_local_and_present():
    """News article links may be external; scripts, CSS, fonts and images may not."""

    css_files: set[Path] = set()
    for path in PAGES.values():
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
    for scheme in ("http://", "https://", "//cdn"):
        assert scheme not in i18n_literals, (
            f"i18n.js names an absolute origin ({scheme}); catalogues are same-origin"
        )
    assert re.search(r"(?<![.\w$])fetch\s*\(\s*url\b", i18n), (
        "i18n.js's one fetch must take the URL built from that base"
    )

    api = _read(WEB / "js" / "api.js")
    news_request = _exported_function(api, "getNewsPosts")
    assert "limit" in news_request
    assert "https://kaicommitment.org.nz/wp-json/wp/v2/posts" in api
    assert re.search(r"per_page\s*=", api)
    assert "_embed" in api

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
    assert exported == {"renderDonut", "renderBar"}
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


def test_statistics_source_consumes_the_stats_contract_without_nz_generalisation():
    """Independent empty states, negative axes and destroy timing stay in browser QA."""

    html = _read(PAGES["statistics"])
    source = _read(WEB / "js" / "stats.js")
    combined = (html + "\n" + source).lower()

    assert "getstats" in source.lower() and "./api.js" in source
    assert "self-selected" in combined
    assert not re.search(r"(?:distribution|statistics|picture)\s+of\s+(?:food waste\s+)?(?:in\s+)?new zealand", combined)
    assert "total_calculations" in source
    assert "generated_at" in source and "suppression_threshold" in source
    assert "share" in source, "API-provided shares are the preferred public chart values"
    assert "tonnes recorded" not in combined

    for breakdown in ("by_destination", "by_sector", "by_food_category"):
        assert breakdown in source
    assert re.search(r"\bcatch\b", source)

    # `other` and `unspecified` are ordinary API buckets, not client-side filters.
    assert not re.search(r"\.filter\s*\([^)]*(?:other|unspecified)", source)

    assert "renderDonut" in source and "renderBar" in source and "./charts.js" in source


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
        assert "aggregate statistics" in copy
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
