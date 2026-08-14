"""The Content-Security-Policy, enforced by a browser rather than parsed.

`tests/test_d_statistics_content.py` asserts the header's directives, which is
the right test for "the policy says what we meant". It cannot tell whether the
policy *breaks the page*, because a CSP failure is silent: the browser refuses
the resource, logs a violation to a console nobody is reading, and renders a
page that is merely missing something.

That is not hypothetical here. `style-src 'self'` refuses Playwright's
`add_style_tag`, which is how every layout test in this suite injects
`FORCE_AUTO` and `KAICALC_MUTATION_CSS`; all twenty tests in
`test_step_navigation.py` errored on the injection the moment the policy landed.
Those files now set `bypass_csp`, which is correct for instrumentation and is
exactly why this file exists: **it is the one browser test that runs with the
policy on**, so something still fails when a directive breaks a real page.

Requires the stack. Skipped, never failed, when it is not up.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.browser

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to enforce a CSP; parsing the header is not enforcement",
)

ROOT = Path(__file__).resolve().parents[2]
BASE = os.environ.get("KAICALC_WEB_URL", "http://localhost:18080")
STATS = json.loads((ROOT / "tests" / "fixtures" / "stats.json").read_text(encoding="utf-8"))

PAGES = ("/", "/home.html", "/stats.html", "/methodology.html")


def _stack_is_up() -> bool:
    try:
        with urllib.request.urlopen(f"{BASE}/", timeout=3) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):  # pragma: no cover - environment guard
        return False


if not _stack_is_up():  # pragma: no cover - environment guard
    pytest.skip(
        f"the stack is not answering on {BASE}; run "
        "`docker compose -f docker/compose.yaml up -d --build web`",
        allow_module_level=True,
    )


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        instance = p.chromium.launch()
        yield instance
        instance.close()


@pytest.mark.parametrize("path", PAGES)
def test_no_public_page_violates_its_own_policy(browser, path):
    """Load each public page with the policy enforced and collect what it refused.

    Chromium reports a CSP refusal twice: as a `securitypolicyviolation` event
    on the document, and as a console error. The event is listened for because
    it carries the directive and the blocked URI; the console is read as well
    because a violation in a worker or a stylesheet does not always raise the
    event on the document.
    """
    context = browser.new_context(viewport={"width": 1278, "height": 983}, locale="en-NZ")
    page = context.new_page()
    violations: list[dict] = []
    console: list[str] = []
    try:
        page.expose_function("__kaicalcViolation", lambda detail: violations.append(detail))
        page.add_init_script(
            """
            document.addEventListener('securitypolicyviolation', (event) => {
              window.__kaicalcViolation({
                directive: event.effectiveDirective || event.violatedDirective,
                blocked: event.blockedURI,
                source: event.sourceFile || '',
                line: event.lineNumber || 0,
              });
            });
            """
        )
        page.on(
            "console",
            lambda message: console.append(message.text)
            if "Content Security Policy" in message.text
            else None,
        )
        page.route(
            "**/api/v1/stats",
            lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps(STATS)
            ),
        )
        page.goto(f"{BASE}{path}", wait_until="networkidle", timeout=20000)
        page.wait_for_timeout(1200)

        assert not violations, (
            f"{path} violates its own Content-Security-Policy: {violations}. "
            "The page is rendering without something it asked for."
        )
        assert not console, f"{path} logged a CSP refusal: {console}"
    finally:
        context.close()


def test_the_policy_is_actually_enforced_and_not_merely_advertised(browser):
    """The guard on the guard.

    Every assertion above passes on a page that is served with no policy at all,
    and passes on a page whose policy is `Content-Security-Policy-Report-Only`.
    So one thing that the policy *should* refuse is attempted, and it has to be
    refused: an inline `<style>`, which `style-src 'self'` forbids and which is
    the exact mechanism the rest of the suite has to bypass.
    """
    context = browser.new_context(viewport={"width": 1278, "height": 983}, locale="en-NZ")
    page = context.new_page()
    try:
        page.goto(f"{BASE}/stats.html", wait_until="networkidle", timeout=20000)
        with pytest.raises(playwright_api.Error) as refusal:
            page.add_style_tag(content="body { outline: 1px solid red; }")
        assert "Content Security Policy" in str(refusal.value), str(refusal.value)
    finally:
        context.close()


@pytest.mark.parametrize("path", PAGES)
def test_every_public_page_is_served_the_policy(browser, path):
    """The header reaches the browser on every public path, not only on `/`.

    D scoped the policy to nginx's `location /`, and a location-level
    `add_header` stops the server-level ones being inherited - which is why the
    three baseline headers are repeated inside that block. A path that fell
    through to a different location would lose all four silently.
    """
    context = browser.new_context()
    page = context.new_page()
    try:
        response = page.goto(f"{BASE}{path}", wait_until="domcontentloaded", timeout=20000)
        headers = {name.lower(): value for name, value in response.headers.items()}
        assert "content-security-policy" in headers, f"{path} is served no CSP"
        for header in ("x-content-type-options", "x-frame-options", "referrer-policy"):
            assert header in headers, (
                f"{path} lost the baseline header {header}: a location-level add_header "
                "stops the server-level ones being inherited"
            )
    finally:
        context.close()
