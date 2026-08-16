"""What a browser is allowed to reuse from the last release, asserted on the wire.

**The defect.** ``docker/nginx.conf``'s ``location /`` emitted no cache directive
at all, and a response with neither ``Cache-Control`` nor ``Expires`` is not
uncacheable: RFC 9111 section 4.2.2 lets a cache invent a freshness lifetime from
``Last-Modified``, and browsers use a tenth of the document's age. Nothing under
``web/`` is fingerprinted - there is no build step, so ``calculator.js`` keeps that
name in every release - so a returning browser could run the previous release's
modules for days without asking.

**Why that is more than staleness.** ``web/js/config.js`` is written by
``docker/web-config.sh`` at container start from ``KAICALC_API_ORIGIN``, and the
public ``connect-src`` is built from the same variable in the same run. The two
are generated together so they cannot disagree; heuristic freshness pulled them
apart from the other end, pairing the previous release's API origin with this
release's policy. The result is a fetch the browser refuses and the page cannot
explain.

**This file asks the running stack, not the template.** A test that greps
``docker/nginx.conf`` asserts that a directive was typed. Whether the header
reaches a client is a property of the container, and this repository's defect
list already holds a health check that reported healthy over a socket the real
server had not bound.

**Mutation record.** Deleting the ``add_header Cache-Control`` line from
``location /`` and rebuilding ``web`` fails
``test_every_static_asset_forbids_silent_reuse`` on all six paths at once. The
``no-store`` variant - stronger, and wrong - fails
``test_revalidation_is_cheap_because_no_cache_is_not_no_store``, which is the
assertion that stops the fix from being over-applied.

No browser required, and no Playwright. Skipped, never failed, when the stack is
not up::

    docker compose -f docker/compose.yaml up -d --build web
    pytest tests/web/test_cache_headers.py
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.browser

#: The ORIGIN, not a page - every path below is appended to it.
BASE = os.environ.get("KAICALC_WEB_URL", "http://localhost:18080").rstrip("/")

#: One of each kind of thing ``location /`` serves, because the rule is the
#: block's and not any one file's.
#:
#: ``/js/config.js`` leads deliberately: it is the generated module, the one
#: whose staleness is guaranteed to contradict the policy rather than merely
#: likely to. ``/locales/fr.json`` stands for the twenty catalogues, and the
#: woff2 face is the asset a narrower fix would have excepted.
STATIC_PATHS = [
    "/js/config.js",
    "/js/calculator.js",
    "/index.html",
    "/home.html",
    "/css/styles.css",
    "/locales/fr.json",
]


def _head(path: str, headers: dict[str, str] | None = None):
    """The response to a GET of ``path``, or a skip if nothing is listening."""
    request = urllib.request.Request(BASE + path, headers=headers or {})
    try:
        return urllib.request.urlopen(request, timeout=10)
    except urllib.error.HTTPError as error:  # 304 arrives here, and is wanted
        return error
    except (urllib.error.URLError, OSError) as error:  # pragma: no cover - guard
        pytest.skip(
            f"the stack is not answering on {BASE}: {error}. Run "
            "`docker compose -f docker/compose.yaml up -d --build web`"
        )


@pytest.mark.parametrize("path", STATIC_PATHS)
def test_every_static_asset_forbids_silent_reuse(path):
    """No asset may be reused without asking the origin first.

    ``no-cache`` is the directive that says exactly that. The assertion is on
    the *absence of a licence to skip revalidation*, so it is written against
    what the header must not permit as well as what it says: a positive
    ``max-age``, or ``immutable``, would each reinstate the defect while
    leaving a ``Cache-Control`` header in place for a shallower test to find.
    """
    response = _head(path)
    directive = (response.headers.get("Cache-Control") or "").lower()
    assert directive, f"{path} carries no Cache-Control at all: {dict(response.headers)}"
    assert "no-cache" in directive, f"{path} -> {directive!r}"
    assert "immutable" not in directive, f"{path} -> {directive!r}"
    for token in directive.replace(" ", "").split(","):
        if token.startswith("max-age="):
            assert token == "max-age=0", f"{path} may be reused unasked: {directive!r}"


def test_the_generated_module_is_covered_by_the_same_rule_as_the_policy_it_pairs_with():
    """`config.js` and the CSP that names the same origin must age together.

    They are written by one script from one variable. This asserts the third
    thing needed for them to stay agreed in a browser: that the module cannot
    outlive the header. Both are read from one response so the pairing is
    observed rather than assumed.
    """
    module = _head("/js/config.js")
    page = _head("/index.html")
    assert "no-cache" in (module.headers.get("Cache-Control") or "").lower()
    assert page.headers.get("Content-Security-Policy"), dict(page.headers)
    assert "no-cache" in (page.headers.get("Cache-Control") or "").lower()


def test_revalidation_is_cheap_because_no_cache_is_not_no_store():
    """The fix must cost a conditional request, not a re-download.

    ``no-store`` would also stop silent reuse and is the wrong answer: it
    forbids keeping the bytes, so every navigation re-downloads the modules,
    the stylesheet, both fonts and a catalogue. ``no-cache`` keeps them and
    revalidates, and nginx answers that from the ETag with a bodiless 304 -
    which is what this asserts, on the real server, with the real validator.
    """
    first = _head("/js/calculator.js")
    etag = first.headers.get("ETag")
    directive = (first.headers.get("Cache-Control") or "").lower()
    assert "no-store" not in directive, directive
    assert etag, f"no validator to revalidate against: {dict(first.headers)}"
    again = _head("/js/calculator.js", {"If-None-Match": etag})
    assert getattr(again, "status", getattr(again, "code", None)) == 304, (
        f"revalidation re-sent the body: {getattr(again, 'status', None)}"
    )


def test_the_static_rule_does_not_reach_the_api():
    """`location /`'s directive must not leak onto `/api/v1/`.

    nginx does not inherit an ``add_header`` from a sibling location, which is
    why the CSP above it is scoped the way it is. The API answers a POST that
    calculates and persists; a cache directive written for static files has no
    business being asserted of it, and this is the test that notices if the
    directive is ever hoisted to the server block instead.
    """
    response = _head("/api/v1/taxonomy")
    assert "no-cache" not in (response.headers.get("Cache-Control") or "").lower(), (
        dict(response.headers)
    )
