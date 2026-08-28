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

import pytest


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
