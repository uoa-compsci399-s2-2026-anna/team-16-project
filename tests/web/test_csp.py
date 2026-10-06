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

**Both halves of the runtime configuration are exercised here, and they have to be.**
``connect-src`` and ``img-src`` are no longer literals in ``docker/nginx.conf``: the
entrypoint builds them from ``KAICALC_NEWS_ORIGIN`` and friends, and builds
``web/js/config.js`` from the same values so the policy and the page cannot name
different hosts. The stack this suite normally runs against is the *configured* case. The
*unconfigured* case is a different policy (``connect-src 'self'``, no host at all) served
to a different page (the home page removes its news section instead of fetching), and it
is the case most deployments of this calculator will actually be in — so a second
container is started for it below rather than reasoned about.

Requires the stack. Skipped, never failed, when it is not up.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tests.web import i18n_keys
from tests.web.base_url import ORIGIN

# The `browser` marker is applied by `conftest.py`, by location: every module
# here is a browser suite unless it is named in its `NOT_A_BROWSER_SUITE`.

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to enforce a CSP; parsing the header is not enforcement",
)

ROOT = Path(__file__).resolve().parents[2]
BASE = ORIGIN
STATS = json.loads((ROOT / "tests" / "fixtures" / "stats.json").read_text(encoding="utf-8"))

#: The policy is set on nginx's static `location /`, so it covers every file under
#: it - including `home.html`, which is retired but still served. It is listed here
#: for that reason and for no other: a page nobody links to is still a page a policy
#: has to be correct for, and it is the page whose news feed the `connect-src` and
#: `img-src` grants exist for. `/` is `index.html`; both are named because `/` is the
#: address a visitor types and the one whose headers a reverse proxy is most likely
#: to rewrite.
PAGES = ("/", "/index.html", "/home.html", "/stats.html", "/methodology.html")


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


#: `browser` itself now comes from `tests/web/conftest.py`, package-scoped
#: and shared across every file in this directory - see that module's
#: docstring for why a per-file fixture corrupted the rest of the run.
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


# ---------------------------------------------------------------------------------------
# The unconfigured deployment: a second container, because it is a different policy
# served to a different page and neither can be seen from the stack above.
# ---------------------------------------------------------------------------------------

IMAGE = "kaicalc-web:local"
NETWORK = os.environ.get("KAICALC_COMPOSE_NETWORK", "kaicalc_default")
UNCONFIGURED_NAME = "kaicalc-web-unconfigured-csp"
UNCONFIGURED_PORT = int(os.environ.get("KAICALC_UNCONFIGURED_PORT", "18099"))


def _docker_can_run_the_web_image() -> bool:
    if shutil.which("docker") is None:
        return False
    for argv in (
        ["docker", "image", "inspect", IMAGE],
        ["docker", "network", "inspect", NETWORK],
    ):
        if subprocess.run(argv, capture_output=True, text=True).returncode != 0:
            return False
    return True


@pytest.fixture(scope="module")
def unconfigured_base() -> str:
    """A web container with no news origin, on the stack's network so nginx can resolve.

    Its own name and its own port: ``docker/compose.yaml`` pins ``container_name`` for
    every service, so a second copy started through compose would replace the running
    stack rather than stand beside it. This is a bare ``docker run`` for that reason, and
    it is removed on the way out.
    """

    if not _docker_can_run_the_web_image():  # pragma: no cover - environment guard
        pytest.skip(
            f"needs docker, the {IMAGE} image and the {NETWORK} network; build with "
            "`docker compose -f docker/compose.yaml up -d --build web`"
        )
    subprocess.run(["docker", "rm", "-f", UNCONFIGURED_NAME], capture_output=True, text=True)
    started = subprocess.run(
        ["docker", "run", "-d", "--name", UNCONFIGURED_NAME, "--network", NETWORK,
         "-p", f"{UNCONFIGURED_PORT}:18080",
         # Empty, not absent: the point is that an operator who clears the value gets no
         # news feed rather than a fallback to somebody's default.
         "-e", "KAICALC_NEWS_ORIGIN=",
         "-e", "KAICALC_API_ORIGIN=",
         IMAGE],
        capture_output=True, text=True,
    )
    assert started.returncode == 0, f"could not start the unconfigured container: {started.stderr}"
    base = f"http://localhost:{UNCONFIGURED_PORT}"
    try:
        # A DEADLINE IN SECONDS WITH AN EXPLICIT SLEEP, AND THE REASON IS A
        # PLATFORM DIFFERENCE RATHER THAN A PREFERENCE. This was `for _ in
        # range(40)` with no sleep, which spends however long forty refused
        # connections take - and that is not a constant across platforms.
        # Measured on this project's Windows desktop against a closed localhost
        # port: 4.03s per refused attempt, so the loop ran for 161s and nginx
        # had long since come up. On the Linux runner a refused connection
        # returns at once, so the same forty attempts are over in a moment - the
        # log of 2026-10-06 shows it, quoting the entrypoint's FIRST startup
        # line (`/docker-entrypoint.d/ is not empty, will attempt to perform
        # configuration`) as its evidence that the container `never answered`.
        # It had not been given the chance to. The message reads like a broken
        # image and was a loop that never waited; it cost four cases, both of
        # the CSP policies this module exists to check, and it had passed on
        # every developer machine here.
        #
        # Sixty seconds because the image has to be pulled into a new container
        # and nginx's entrypoint templates its configuration first; the loop
        # leaves as soon as the page answers, so the budget costs nothing when
        # the container is quick.
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"{base}/", timeout=2) as response:
                    if response.status == 200:
                        break
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(0.5)
        else:  # pragma: no cover - environment guard
            logs = subprocess.run(["docker", "logs", UNCONFIGURED_NAME],
                                  capture_output=True, text=True)
            pytest.fail(
                f"the unconfigured container did not answer on {base}/ within "
                f"60s. docker logs {UNCONFIGURED_NAME}:\n"
                f"{logs.stdout}{logs.stderr}"
            )
        yield base
    finally:
        subprocess.run(["docker", "rm", "-f", UNCONFIGURED_NAME], capture_output=True, text=True)


def test_the_unconfigured_policy_names_no_host(unconfigured_base):
    """``connect-src 'self'`` and nothing else — the domain is gone from the image."""

    with urllib.request.urlopen(f"{unconfigured_base}/home.html", timeout=10) as response:
        policy = response.headers["Content-Security-Policy"]
    assert policy, "the unconfigured container serves no CSP"
    directives = {}
    for raw in policy.split(";"):
        tokens = raw.split()
        if tokens:
            directives[tokens[0]] = tokens[1:]
    assert directives["connect-src"] == ["'self'"], directives["connect-src"]
    assert directives["img-src"] == ["'self'", "data:"], directives["img-src"]


def test_an_unconfigured_home_page_removes_its_news_section_and_asks_nobody(browser,
                                                                           unconfigured_base):
    """The decision this makes visible: an unset origin renders nothing, and requests nothing.

    The alternative — leaving the heading with "Latest news is temporarily unavailable"
    underneath it — reports an outage for a service that was never configured, and a
    reader has no way to tell that apart from a real one. The alternative that is worse
    still is guessing a domain and asking it.

    Both halves are asserted, because either alone passes on the wrong page: a section
    that is gone but a request that still went out is a browser asking a host nobody
    chose, and a request that did not go out but a section that is still standing is the
    misleading notice.
    """

    context = browser.new_context(viewport={"width": 1278, "height": 983}, locale="en-NZ")
    page = context.new_page()
    violations: list[dict] = []
    external: list[str] = []
    try:
        page.expose_function("__kaicalcViolation", lambda detail: violations.append(detail))
        page.add_init_script(
            """
            document.addEventListener('securitypolicyviolation', (event) => {
              window.__kaicalcViolation({
                directive: event.effectiveDirective || event.violatedDirective,
                blocked: event.blockedURI,
              });
            });
            """
        )
        page.on(
            "request",
            lambda request: external.append(request.url)
            if not request.url.startswith(unconfigured_base) else None,
        )
        page.goto(f"{unconfigured_base}/home.html", wait_until="networkidle", timeout=20000)
        page.wait_for_timeout(1200)

        assert not violations, f"the unconfigured home page violates its own policy: {violations}"
        assert page.locator("#news-feed").count() == 0, (
            "the news feed is still on the page with no origin configured"
        )
        assert page.locator(".home-news").count() == 0, (
            "the news heading and standfirst are still standing over a feed that will "
            "never arrive - a reader cannot tell that from an outage"
        )
        assert page.locator("main").count() == 1, "the rest of the home page went with it"
        assert not external, (
            f"the unconfigured page went off-origin: {external}. An unset news origin must "
            "mean no request, not a request to a guessed host."
        )
    finally:
        context.close()


@pytest.mark.parametrize("language", ["zh", "ar"])
def test_an_unconfigured_home_page_still_chooses_its_language(browser, unconfigured_base,
                                                              language):
    """A deployment with no news origin is still a deployment in twenty languages.

    **Filed here rather than with the rest of the translation tests because of the
    container.** The only unconfigured web container this suite starts is the one
    ``unconfigured_base`` above owns, and this is a question about that deployment:
    the news feed is the *one* thing an unset origin is allowed to cost, and the
    language chooser, ``<html lang>`` and the page's own prose are not on that list.

    Everything expected is read out of ``web/locales/<language>.json`` by Python and
    compared with what Chromium rendered after fetching the same file over HTTP, so
    the assertion cannot agree with itself. Arabic is included because it is also the
    direction check: a chooser that is present but renders the page left-to-right has
    not applied the catalogue, only found it.

    The control is *used*, not merely counted. A `<select>` that exists and does
    nothing is the defect this project has recorded six times over, so the last two
    assertions switch languages through it and read the heading back.
    """
    catalogue = i18n_keys.catalogue(language)
    strings = catalogue["strings"]
    context = browser.new_context(
        viewport={"width": 1278, "height": 983},
        extra_http_headers={"Accept-Language": f"{language},en"},
    )
    page = context.new_page()
    page.add_init_script(
        "Object.defineProperty(navigator, 'languages', {get: () => %s});"
        "Object.defineProperty(navigator, 'language', {get: () => %s});"
        % (json.dumps([language, "en"]), json.dumps(language))
    )
    try:
        page.goto(f"{unconfigured_base}/home.html", wait_until="networkidle", timeout=20000)
        page.wait_for_timeout(600)

        assert page.locator(".home-news").count() == 0, (
            "the premise of this test is gone: this container has a news section"
        )
        assert page.get_attribute("html", "lang") == language, (
            "an unconfigured home page is announced in the wrong language, so a screen "
            "reader pronounces it with English phonetics"
        )
        assert page.get_attribute("html", "dir") == catalogue.get("dir", "ltr")
        assert page.inner_text("h1#home-title") == strings[
            "Turn food waste information into action"
        ], "the home page did not translate with no news origin configured"

        chooser = page.locator("#language-chooser")
        assert chooser.count() == 1, "no language chooser on an unconfigured home page"
        assert chooser.is_visible(), (
            "the language chooser is in the document but not on the screen"
        )
        chooser.select_option("en")
        page.wait_for_timeout(400)
        assert page.inner_text("h1#home-title") == (
            "Turn food waste information into action"
        ), "the chooser is present but choosing a language changes nothing"
        assert page.get_attribute("html", "lang") == "en-NZ"
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
