"""Shared Playwright driver for every `tests/web` browser test.

Each file used to open its own `sync_playwright()` context through a
locally-defined `browser` fixture: five at `scope="module"`
(`test_csp.py`, `test_horizontal_overflow.py`, `test_i18n_browser.py`,
`test_site_drawer.py`, `test_statistics_browser.py`) and three at
`scope="session"` (`test_amount_limits_browser.py`,
`test_container_input_browser.py`, `test_step_navigation.py`).

**Why that combination corrupts the whole suite.** Playwright's sync API
does not run its event loop in a background thread; it runs
`loop.run_until_complete(...)` on a greenlet, and every blocking call
(`page.goto`, `new_context`, …) is a greenlet-switch back to the caller, not
a return from `run_until_complete`. That coroutine frame is only ever
*suspended*, never unwound, for as long as the `sync_playwright()` context
stays open (see `playwright/sync_api/_context_manager.py`'s `__enter__`).
Consequently `asyncio.get_running_loop()` reports a running loop on that OS
thread for the fixture's *entire lifetime*, not just while a Playwright call
is in flight.

A `scope="session"` `browser` fixture therefore never releases that running
loop until the whole pytest session ends. The moment a second file tries
`sync_playwright()` on the same thread - another `tests/web` module, or (if
collection order ever put `tests/web` first) an `asyncio`-marked test in
`tests/api` reaching for its own `Runner.run()` - it trips over a loop that
is still "running" and never will stop being. Playwright raises "It looks
like you are using Playwright Sync API inside the asyncio loop"; an asyncio
`Runner` raises "Runner.run() cannot be called from a running event loop".
Same fault, reported from whichever side goes second. **This reproduces from
`tests/web` alone** - `test_amount_limits_browser.py` collects first
alphabetically, opens a session-scoped browser that never closes, and every
other browser-driving file after it fails at `browser` fixture setup for the
rest of the run. `tests/api` sharing the session just adds a second way to
observe the identical bug.

**The fix: one Playwright driver for the whole package.** `scope="package"`
opens `_playwright`/`browser` on first use and closes them as soon as the
last `tests/web` test finishes - not deferred to the end of the whole
session, which would still poison `tests/api` if `tests/web` happened to
collect first (`session` scope ties teardown to session end regardless of
which package asked for it first; `package` scope ties it to the package
that owns the fixture). Every file below other than
`test_horizontal_overflow.py` now gets `browser` from here automatically,
by no longer defining its own. Each test already opens its own
`browser.new_context()` per case, so nothing here changes test isolation -
only the number of Chromium processes launched (previously up to eight;
now one, or two counting the scrollbar exception below).

`test_horizontal_overflow.py` launches Chromium with
`ignore_default_args=["--hide-scrollbars"]` - it needs scrollbars actually
drawn, which is the whole point of that file (see its own module
docstring) - so it cannot share the `browser` instance below. It keeps a
local `browser` override, but built on `_playwright` rather than a second
`sync_playwright()` of its own: two Chromium instances launched from the
*same* Playwright driver coexist fine; two `sync_playwright()` contexts on
one thread are exactly the conflict this file exists to close.
"""

from __future__ import annotations

import pathlib

import pytest

HERE = pathlib.Path(__file__).resolve().parent

#: Every module under `tests/web` that does **not** need the stack up, with what
#: it reads instead.
#:
#: **The list is of the non-browser files on purpose, and the direction is the
#: whole point.** Fifteen of the thirty-six Playwright-driving modules here
#: carried no `browser` marker, so `pytest tests/web -m "not browser"` with the
#: stack stopped was observed doing a full sweep against the live containers -
#: 738 passed, 3 failed, 13m44s - while the command said `not browser` and the
#: output said 738 passed (issue #149). The reason the marker drifted is that it
#: was a line each author had to remember to type, and the browser list is the
#: one that grows: a new browser file is written most weeks and a new
#: source-reading one two or three times a semester. Enumerating the browser
#: files would put the forgettable side of the rule back where it was. So the
#: default for a new file is `browser`, and a file that genuinely needs no stack
#: has to say so here and say why - which is a line in a review diff rather than
#: a thing nobody notices.
#:
#: Read on the same terms as `tests/web/test_i18n_web.py`'s
#: `IDENTICAL_BY_DESIGN`: an exemption names the file and gives its reason, and
#: `test_suite_isolation.py` fails on an entry whose file does in fact reach
#: Playwright - so the list cannot outlive the reason it was written for.
NOT_A_BROWSER_SUITE = {
    "test_consent_copy.py":
        "greps `web/*.html` and the catalogues for the sentences that describe "
        "where a calculation goes; the copy is in the files, not on a screen",
    "test_entry_destinations.py":
        "runs `calculator.js::entryDestinations` under Node against a "
        "hand-built taxonomy; no page is rendered",
    "test_i18n_negotiation.py":
        "runs `i18n.js`'s negotiator under Node over `navigator.languages` "
        "lists it supplies itself",
    "test_i18n_web.py":
        "compares `web/locales/` and `api/assets/locales/` as files on disk, "
        "including the SHA-256 identity the PDF depends on",
    "test_js_syntax.py":
        "`node --check` over every module in `web/js/`; the question is whether "
        "the file parses, which a served page cannot answer more cheaply",
    "test_leaf_rule.py":
        "runs the leaf fan-out under Node; the rule is a function and is tested "
        "as one",
    "test_methodology_columns.py":
        "reads `methodology.js` and the §6.3 fixtures as source text",
    "test_period_form_bounds.py":
        "reads `period.js`'s `MAX_HOURS_AHEAD` and `api/schemas.py`'s "
        "`PERIOD_CEILING_HOURS` as source, because the claim is that neither "
        "number was copied onto the other side",
    "test_period_rules.py":
        "calls `period.js`'s rules directly under Node",
    "test_snapshot.py":
        "runs `snapshot.js` under Node over `sessionStorage` states it builds",
    "test_suite_isolation.py":
        "reads the modules in this package as source and collects them in a "
        "subprocess; it has to run in exactly the stack-free run whose "
        "correctness it asserts",
}

#: Modules that hold **both** kinds of case, with what the non-browser half is.
#:
#: These are exempt from the blanket above and nothing else: the fixture rule in
#: `pytest_collection_modifyitems` still marks every case in them that asks for
#: a browser, so their Playwright half is marked and their Node half stays
#: selectable under `-m "not browser"` exactly as it is today. Marking them
#: wholesale would have moved 85 passing source-level cases out of the
#: stack-free run, which is a loss of coverage dressed up as a fix.
#:
#: A module belongs here only because splitting it would separate two halves of
#: one subject - `test_unit_presets.py`'s Node conversion cases and its browser
#: cases are the same arithmetic asserted at two altitudes, and a reader
#: checking that they agree needs them in one file.
MIXED_BY_DESIGN = {
    "test_results_export.py":
        "60 cases render the export from a fixture under Node and compare the "
        "text to the screen's own wording; 88 drive the real download. Its "
        "browser half carries `@pytest.mark.browser` per case already",
    "test_step_navigation.py":
        "two cases read the improvement panel's and the allocation rows' "
        "breakpoints out of `styles.css`; the other 126 drive the step bar",
    "test_unit_presets.py":
        "23 cases run `units.js`'s container conversion under Node; six drive "
        "the select the conversion is offered on",
}


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    """Give every `tests/web` case that needs the stack the `browser` marker.

    Two rules, and the first is the one that cannot be forgotten:

    1. **Any case whose fixture closure contains `browser`** is marked, wherever
       it lives. That is read off what the test asked for, so a Playwright case
       added to a mixed module - or to `tests/web` under a name nobody thought
       to list - is marked the moment it is written.
    2. **Any case in a module not named in `NOT_A_BROWSER_SUITE` or
       `MIXED_BY_DESIGN`** is marked. This is what covers the shape rule 1
       cannot see: `test_cache_headers.py` drives no browser at all and still
       needs the container, because its subject is the headers nginx sends.

    `tryfirst` because `-m` is applied by `_pytest.mark`'s own
    `pytest_collection_modifyitems`, and a marker added after that hook has run
    is a marker `-m "not browser"` never sees.

    **It is belt rather than braces, and that was measured rather than
    assumed.** Removing the decorator leaves the selection byte-identical -
    440/1351 collected under `-m "not browser"` either way - because conftest
    plugins are registered later than the builtins and non-wrapper hooks are
    called last-registered-first. So the ordering already favours us and
    `tryfirst` only says so out loud, for the reader who would otherwise have to
    know that. The claim that matters is measured in `test_suite_isolation.py`'s
    `test_the_stack_free_selection_is_exactly_what_is_declared`, which collects
    in a subprocess and would fail if the ordering ever changed under us.
    """
    for item in items:
        path = pathlib.Path(str(item.fspath)).resolve()
        if path.parent != HERE:
            continue
        if "browser" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.browser)
            continue
        if path.name in NOT_A_BROWSER_SUITE or path.name in MIXED_BY_DESIGN:
            continue
        item.add_marker(pytest.mark.browser)


@pytest.fixture(scope="package")
def _playwright():
    """The one `sync_playwright()` context for the whole `tests/web` run.

    The import is deferred to fixture setup, not made at module level: a
    checkout without Playwright installed still needs `tests/web` to
    *collect* cleanly, because each test file does its own
    `pytest.importorskip("playwright.sync_api", ...)` and skips at module
    level before any test in it ever requests `browser` - collection must
    not fail here first.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="package")
def browser(_playwright):
    instance = _playwright.chromium.launch()
    yield instance
    instance.close()
