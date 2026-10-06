"""The two rules that keep `tests/web` tellable-not-to-run and pointable, enforced.

**Both rules were conventions, and both drifted.** Fifteen of the thirty-six
Playwright-driving modules here carried no `browser` marker, so
``pytest tests/web -m "not browser"`` swept the live stack while saying it would
not: 738 passed, 3 failed, 13m44s. And thirty-seven modules each held their own
base-URL constant, in two spellings that disagreed about whether the variable
named an origin or a page, so three agents in three worktrees could not point
their suites at their own containers and one of them spent a run reading another
worktree's build - 21 failures whose giveaway was a ``title="Percentage"``
attribute that existed nowhere in the failing tree (issue #149).

**A rule a human has to remember is a rule that drifts, so this file is the
rule.** It is the shape ``tests/web/test_i18n_web.py``'s ``IDENTICAL_BY_DESIGN``
and ``tests/api/test_fixture_consistency.py``'s
``METRICS_A_RESPONSE_FIXTURE_NEED_NOT_CARRY`` already set here: a declared
exemption list, each entry naming its file and its reason, and a test that fails
on an exemption which is not in fact needed - so the list can only grow
deliberately and cannot outlive the reason it was written for.

**This module reads source and runs one collection; it needs no browser and no
stack**, which is why it is in ``conftest.py``'s ``NOT_A_BROWSER_SUITE``. It has
to run in exactly the stack-free run whose correctness it asserts.

**Its neighbour is out of scope and still broken.**
``tests/admin/test_button_hint_browser.py`` and
``tests/admin/test_import_dialog_browser.py`` both hardcode
``http://localhost:18080``. They are correctly marked ``browser``, so half one
of #149 does not reach them, but they cannot be pointed at a private container
either. Fixing that is a one-line import each and belongs to whoever next has
``tests/admin`` open.
"""

from __future__ import annotations

import ast
import os
import pathlib
import re
import subprocess
import sys

import pytest

from tests.web import base_url
from tests.web.conftest import MIXED_BY_DESIGN, NOT_A_BROWSER_SUITE

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]

#: Anything that addresses this host. Narrow on purpose: an absolute URL is not
#: by itself a base URL - ``test_i18n_browser.py`` holds
#: ``http://www.w3.org/2000/svg``, which is a namespace and not an address, and
#: exempting it would be exempting the wrong thing. A loopback literal is always
#: somebody's attempt to name the stack.
LOOPBACK = re.compile(r"(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])")

#: How a module admits to driving a browser. ``importorskip`` is the route the
#: fifteen unmarked modules took, and the bare name covers a module that imports
#: it any other way.
PLAYWRIGHT = re.compile(r"\bplaywright\b")

#: The one module allowed to hold a loopback literal, because it is the place
#: this file exists to make everybody else use.
THE_ONE_PLACE = "base_url.py"

#: Modules that may name a host of their own, and why.
#:
#: **One entry, and it is not a base URL at all**, which is the distinction the
#: rule turns on: ``test_csp.py`` starts and removes a throwaway container of its
#: own in order to assert what the policy says when ``KAICALC_API_ORIGIN`` is
#: cleared, and it has to address that container directly. Pointing it at
#: :data:`tests.web.base_url.ORIGIN` would make it measure the stack under test
#: instead of the unconfigured one, which is the opposite of its subject.
#:
#: Read on the same terms as ``IDENTICAL_BY_DESIGN``: the entry names the file
#: and says why, and
#: ``test_every_declared_hardcoded_host_is_a_real_exception`` fails on an entry
#: whose file holds no loopback literal any more.
HOSTS_OF_THEIR_OWN = {
    "test_suite_isolation.py":
        "this file. It names :18080 to assert that the default is the port "
        "`docker/compose.yaml` publishes, and :18094 to assert that an "
        "environment variable really redirects - a rule about addresses cannot "
        "be written without writing addresses, and it holds no `page.goto`",
    "test_csp.py":
        "builds `http://localhost:{UNCONFIGURED_PORT}` for a container this "
        "file starts and removes itself, to read the CSP of a web image whose "
        "`KAICALC_API_ORIGIN` is empty. That container is deliberately not the "
        "stack under test, so `KAICALC_WEB_URL` must not reach it",
}

#: The module that needs the container and drives no browser, and why it keeps
#: the marker rather than being declared non-browser.
#:
#: ``test_cache_headers.py``'s subject is what the shared stack's own nginx
#: sends: ``Cache-Control: no-cache`` on ``location /`` and on ``/admin``, and a
#: 304 off the ETag. That is a property of the **container**, not of
#: ``docker/nginx.conf``'s text - this repository's defect list already holds a
#: health check that reported healthy over a socket the real server had not
#: bound - and it is the evidence that let PR #150 delete four hand-maintained
#: ``?v=`` cache keys. So it is a ``browser`` suite in the only sense the marker
#: means, "needs the stack up", and the `-m "not browser"` run must not try it.
#: Named here so that the next reader asking why a Playwright-free file is
#: marked finds the answer rather than removing the marker.
NEEDS_THE_STACK_WITHOUT_A_BROWSER = "test_cache_headers.py"


def _clean_env() -> dict[str, str]:
    """This process's environment with any pointing already applied stripped out.

    A subprocess collection has to answer for the *default*, so a batch already
    running against a private container does not change what this file asserts.
    """
    return {
        key: value for key, value in os.environ.items()
        if key not in ("KAICALC_WEB_URL", "KAICALC_API_URL")
    }


def _collect(target: str, expression: str) -> set[str]:
    """The module basenames `pytest --collect-only -m <expression>` selects.

    A subprocess rather than `request.session.items`, for two reasons. The
    session only ever holds what *this* invocation collected, so running this
    file on its own would make every assertion below vacuous - the exact
    "passing for the wrong reason" shape this repository keeps finding. And the
    claim is about what `-m` does after `_pytest.mark` has had its turn, which
    is a property of a whole pytest run and not of an item list.
    """
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", target, "--collect-only", "-m", expression],
        cwd=REPO, capture_output=True, text=True, timeout=900, env=_clean_env(),
    )
    assert completed.returncode == 0, (
        f"collecting {target!r} under -m {expression!r} failed:\n"
        f"{completed.stdout[-4000:]}{completed.stderr[-2000:]}"
    )
    return {
        line.split("::")[0].replace("\\", "/").rsplit("/", 1)[1]
        for line in completed.stdout.splitlines()
        if "::" in line and line.startswith("tests/web")
    }


@pytest.fixture(scope="module")
def stack_free_modules() -> set[str]:
    """Which modules `pytest tests/web -m "not browser"` would actually run.

    Module-scoped because the collection costs a subprocess and three tests ask
    the same question of it.
    """
    selected = _collect("tests/web", "not browser")
    assert selected, "nothing at all was collected under -m 'not browser'"
    return selected


def _modules() -> list[pathlib.Path]:
    return sorted(HERE.glob("*.py"))


def _source(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Every string node that is a docstring, by identity.

    Needed because the prose in this package talks about
    ``http://localhost:18080`` constantly - this file's own header does - and a
    scan that flagged prose would be a scan nobody could keep green. Comments do
    not appear in an AST at all, so they need no handling.
    """
    nodes = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            nodes.add(id(body[0].value))
    return nodes


def _loopback_literals(path: pathlib.Path) -> list[str]:
    """``line: literal`` for every non-docstring string literal naming this host."""
    tree = ast.parse(_source(path))
    docstrings = _docstring_nodes(tree)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docstrings:
            continue
        if LOOPBACK.search(node.value):
            found.append(f"line {node.lineno}: {node.value!r}")
    return sorted(set(found))


# ---------------------------------------------------------------------------
# Rule one: a module that reaches Playwright is a browser suite
# ---------------------------------------------------------------------------


def test_no_playwright_module_is_declared_stack_free():
    """A Playwright module may not sit in `NOT_A_BROWSER_SUITE`.

    This is the exact failure #149 reported, read off the declaration rather
    than off a run: `conftest.py` marks every module here `browser` unless it is
    named in that list, so a module that reaches Playwright and is named in it is
    a module `-m "not browser"` will drive a real browser for.

    The message names the files, because "the marker drifted" is useless and
    "these four files" is a diff.
    """
    offenders = sorted(
        path.name for path in _modules()
        if path.name in NOT_A_BROWSER_SUITE and PLAYWRIGHT.search(_source(path))
    )
    assert not offenders, (
        f"these modules reach Playwright and are declared stack-free in "
        f"conftest.py's NOT_A_BROWSER_SUITE, so `pytest tests/web -m \"not "
        f"browser\"` will launch a browser for them: {offenders}"
    )


def test_every_mixed_module_really_holds_both_kinds():
    """`MIXED_BY_DESIGN` is the one list that can hide an unmarked browser case.

    A module in it is exempt from the blanket, so only `conftest.py`'s fixture
    rule marks its browser half. That is correct for a file whose two halves are
    one subject and wrong for a file that simply forgot the marker - so each
    entry has to be a module that genuinely reaches Playwright **and**
    contributes cases to the stack-free run. The second half is measured in
    `test_the_stack_free_selection_is_exactly_what_is_declared` below; this is
    the first.
    """
    by_name = {path.name: path for path in _modules()}
    missing = sorted(name for name in MIXED_BY_DESIGN if name not in by_name)
    assert not missing, f"MIXED_BY_DESIGN names files that do not exist: {missing}"
    not_mixed = sorted(
        name for name in MIXED_BY_DESIGN if not PLAYWRIGHT.search(_source(by_name[name]))
    )
    assert not not_mixed, (
        f"these modules are declared MIXED_BY_DESIGN but reach no Playwright, so "
        f"they are plain stack-free suites and belong in NOT_A_BROWSER_SUITE "
        f"where the blanket cannot mark them: {not_mixed}"
    )


def test_every_declared_stack_free_module_exists_and_says_why():
    """An exemption with no reason is a convention with extra steps."""
    by_name = {path.name for path in _modules()}
    missing = sorted(name for name in NOT_A_BROWSER_SUITE if name not in by_name)
    assert not missing, (
        f"NOT_A_BROWSER_SUITE names files that do not exist, so the list has "
        f"outlived what it was written for: {missing}"
    )
    silent = sorted(
        name for name, reason in {**NOT_A_BROWSER_SUITE, **MIXED_BY_DESIGN}.items()
        if len(str(reason).split()) < 5
    )
    assert not silent, (
        f"these exemptions carry no usable reason; the next reader cannot tell "
        f"whether the entry is still true: {silent}"
    )


def test_the_playwright_free_module_that_still_needs_the_stack_is_named_and_marked():
    """`test_cache_headers.py` is a deliberate exception and stays one.

    It drives no browser, so rule one says nothing about it; it needs the
    container, so it must not be in `NOT_A_BROWSER_SUITE`. Both halves are
    asserted, because each on its own would be satisfied by deleting the file.
    """
    path = HERE / NEEDS_THE_STACK_WITHOUT_A_BROWSER
    assert path.exists(), (
        f"{NEEDS_THE_STACK_WITHOUT_A_BROWSER} is gone; it was the evidence that "
        f"let PR #150 delete four `?v=` cache keys, and this declaration should "
        f"go with it"
    )
    assert not PLAYWRIGHT.search(_source(path)), (
        f"{path.name} now reaches Playwright, so it is an ordinary browser suite "
        f"and this declaration is stale"
    )
    assert path.name not in NOT_A_BROWSER_SUITE and path.name not in MIXED_BY_DESIGN, (
        f"{path.name} has been declared stack-free, but every assertion in it "
        f"reads a header off the running container - the stack-free run would "
        f"skip or fail all twenty cases"
    )


def test_every_playwright_free_module_is_declared_rather_than_silently_skipped():
    """The direction the other five tests above do not cover (v1.98).

    **Everything else here guards the declaration against the code. This guards
    the code against the declaration's absence**, and that gap is not
    hypothetical: `test_class_rules.py` sat outside `NOT_A_BROWSER_SUITE` from
    v1.93 until v1.98, and `test_step_three_copy_truth.py` from v1.92 until
    v1.98. Both are pure static analysis. The blanket marked them `browser`,
    `-m "not browser"` deselected all of their cases, and nothing said so --
    `test_the_stack_free_selection_is_exactly_what_is_declared` is satisfied
    because the selection and the declaration **agreed on excluding them**. Two
    sets can be equal and both wrong.

    What it cost: PR #170 added a class with no rule, its author ran the
    stack-free suite, saw green, and pushed a build that CI would have failed.
    The guard that should have caught it had been switched off by a rule written
    to protect it.

    The rule, and it is the smallest one that closes the direction: a module that
    reaches no Playwright is a module the stack-free run can have, so it must be
    declared -- in `NOT_A_BROWSER_SUITE`, or in `MIXED_BY_DESIGN`, or by being
    the one named file that needs the container without driving a browser. A new
    stack-free file therefore fails here until its author writes the one line
    that says why, which is the same bargain `NOT_A_BROWSER_SUITE`'s own header
    strikes for the other direction.

    **This does not re-litigate which default is right.** `conftest.py` defaults
    a new file to `browser` on the reasoning that the browser list is the one
    that grows, and that reasoning stands -- a file that forgets to declare
    itself is skipped, which is slow and safe, rather than driving a browser in a
    run that asked for none. What was missing is that nothing ever reported the
    skipping.
    """
    declared = set(NOT_A_BROWSER_SUITE) | set(MIXED_BY_DESIGN)
    #: Test modules only. `_modules()` is every `.py` in the package, and the
    #: helpers beside them -- `base_url.py`, `i18n_keys.py`, `steps.py`,
    #: `__init__.py` -- carry no cases for `-m` to select or deselect, so a
    #: declaration for one would be a note about nothing. Measured: without this
    #: filter the assertion names all four of them.
    undeclared = sorted(
        path.name for path in _modules()
        if path.name.startswith("test_")
        and not PLAYWRIGHT.search(_source(path))
        and path.name not in declared
        and path.name != NEEDS_THE_STACK_WITHOUT_A_BROWSER
    )
    assert not undeclared, (
        f"these modules drive no browser and are declared nowhere, so "
        f"`conftest.py`'s blanket marks them `browser` and `pytest tests/web -m "
        f"\"not browser\"` silently skips every case in them: {undeclared}. "
        f"Add each to NOT_A_BROWSER_SUITE with its reason -- or, if one really "
        f"does need the running stack without driving a browser, it is a second "
        f"NEEDS_THE_STACK_WITHOUT_A_BROWSER and that constant has to become a "
        f"set with a reason apiece, like every other exemption in this package"
    )


# ---------------------------------------------------------------------------
# Rule two: there is one base URL and it is in one place
# ---------------------------------------------------------------------------


def test_no_module_hardcodes_a_base_url():
    """Thirty-seven constants became one, and this is what keeps it one.

    Docstrings are excluded and comments are invisible to an AST, so prose about
    ``http://localhost:18080`` - this file's own header, `base_url.py`'s, half a
    dozen others' - is not caught. What is caught is a *value*: the thing a
    `page.goto` would use.
    """
    offenders = {}
    for path in _modules():
        if path.name == THE_ONE_PLACE or path.name in HOSTS_OF_THEIR_OWN:
            continue
        literals = _loopback_literals(path)
        if literals:
            offenders[path.name] = literals
    assert not offenders, (
        "these modules hardcode an address for the stack instead of taking it "
        "from `tests.web.base_url`, so they cannot be pointed at a worktree's "
        "own container and will read whichever image happens to be on :18080: "
        + "; ".join(f"{name} ({', '.join(lines)})" for name, lines in sorted(offenders.items()))
    )


def test_every_declared_hardcoded_host_is_a_real_exception():
    """An exemption for a file that no longer needs it is a licence lying around."""
    by_name = {path.name: path for path in _modules()}
    missing = sorted(name for name in HOSTS_OF_THEIR_OWN if name not in by_name)
    assert not missing, f"HOSTS_OF_THEIR_OWN names files that do not exist: {missing}"
    unnecessary = sorted(
        name for name in HOSTS_OF_THEIR_OWN if not _loopback_literals(by_name[name])
    )
    assert not unnecessary, (
        f"these files are exempted from the base-URL rule and hold no address of "
        f"their own any more, so the exemption permits a future one silently: "
        f"{unnecessary}"
    )


def test_the_one_place_defaults_to_the_shipped_port():
    """The default is the whole compatibility story, so it is asserted.

    Every file used to default to `http://localhost:18080`, which is what
    `docker/compose.yaml` publishes `web` on. A checkout that sets no
    environment variable has to behave exactly as it did, and the page spelling
    has to be derived from the origin rather than defaulted separately - nine
    files defaulted to `.../index.html` and so meant something different by the
    same variable.
    """
    assert base_url.DEFAULT_ORIGIN == "http://localhost:18080", base_url.DEFAULT_ORIGIN
    assert base_url.CALCULATOR == base_url.ORIGIN + "/index.html", base_url.CALCULATOR
    assert base_url.page("home.html") == base_url.page("/home.html"), "a leading slash doubles"
    # And the default is the port compose actually publishes, read off compose
    # rather than restated: `web` is the only published service and its host
    # port is `${KAICALC_WEB_PORT:-18080}`, so the default in `base_url.py` and
    # the default in that expansion are the same number or the shipped
    # `docker compose up` answers nothing on it.
    compose = (REPO / "docker" / "compose.yaml").read_text(encoding="utf-8")
    port = base_url.DEFAULT_ORIGIN.rsplit(":", 1)[1]
    assert f"${{KAICALC_WEB_PORT:-{port}}}:18080" in compose, (
        f"docker/compose.yaml no longer publishes web on :{port} by default, so "
        f"the default in tests/web/base_url.py points at nothing"
    )


def test_an_environment_variable_redirects_every_module():
    """The redirect is a property of the module, not of the one that read it last.

    Re-imported in a subprocess with the variable set, because `base_url` reads
    the environment once at import and this process has already imported it.
    """
    probe = (
        "import tests.web.base_url as b;"
        "print(b.ORIGIN, b.CALCULATOR, b.API_ORIGIN)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO, capture_output=True, text=True, timeout=120,
        env={**_clean_env(), "KAICALC_WEB_URL": "http://localhost:18094/"},
    )
    assert completed.returncode == 0, completed.stderr
    origin, calculator, api = completed.stdout.split()
    assert origin == "http://localhost:18094", origin
    assert calculator == "http://localhost:18094/index.html", calculator
    # The API follows the web origin rather than a second literal: nginx proxies
    # `/api/v1/` out of the same container, so a suite pointed at a private one
    # must not keep asking the shared stack for its taxonomy.
    assert api == "http://localhost:18094", api


# ---------------------------------------------------------------------------
# What the two rules are for: the stack-free run
# ---------------------------------------------------------------------------


def test_the_stack_free_selection_is_exactly_what_is_declared(stack_free_modules):
    """`-m "not browser"` must select the declared modules and nothing else.

    **This is the one assertion that measures the hook rather than the
    declaration.** Everything above reads `NOT_A_BROWSER_SUITE` and
    `MIXED_BY_DESIGN` and checks they are honest; none of it would notice if
    `conftest.py`'s `pytest_collection_modifyitems` stopped being called, or ran
    after `_pytest.mark` had already done the deselection, in which case the
    marker would be added too late for `-m` to see it and the sweep would be
    back.

    Collected in a subprocess so the answer is pytest's own and not a
    re-derivation of it, and `--collect-only` so no case is run twice.
    """
    declared = set(NOT_A_BROWSER_SUITE) | set(MIXED_BY_DESIGN)
    undeclared = sorted(stack_free_modules - declared)
    assert not undeclared, (
        f"these modules put cases into `pytest tests/web -m \"not browser\"` "
        f"without being declared stack-free in conftest.py, so a run meant to "
        f"skip the browser will drive one: {undeclared}"
    )
    # And the other direction: a declaration for a module that contributes
    # nothing is a declaration that has outlived its file's contents.
    idle = sorted(declared - stack_free_modules)
    assert not idle, (
        f"these modules are declared stack-free and contribute no case to the "
        f"stack-free run, so the exemption is doing nothing: {idle}"
    )


def test_no_selected_module_drives_playwright_unless_it_is_declared_mixed(stack_free_modules):
    """The same selection, read against rule one.

    A module may contribute to the stack-free run and still reach Playwright -
    the three in `MIXED_BY_DESIGN` do - but only those three, and only because
    `conftest.py`'s fixture rule marks their browser halves. Any other such
    module is the #149 defect exactly.
    """
    offenders = sorted(
        name for name in stack_free_modules
        if name not in MIXED_BY_DESIGN and PLAYWRIGHT.search(_source(HERE / name))
    )
    assert not offenders, (
        f"these modules reach Playwright and are selected by `-m \"not "
        f"browser\"`: {offenders}. Either they belong under the marker, or they "
        f"are mixed and must say so in conftest.py's MIXED_BY_DESIGN"
    )


@pytest.mark.parametrize("name", sorted(MIXED_BY_DESIGN))
def test_a_mixed_modules_browser_half_is_marked_by_the_fixture_rule(name):
    """Named per module, so a failure says which one stopped being marked.

    The fixture rule is the whole reason a mixed module is safe to exempt from
    the blanket, so each exemption is asserted to still be carrying its weight:
    the module must put cases into `-m browser` as well as into
    `-m "not browser"`.
    """
    assert _collect(f"tests/web/{name}", "browser") == {name}, (
        f"{name} is declared MIXED_BY_DESIGN but contributes no case to "
        f"`-m browser`, so either its browser half has gone - and it belongs in "
        f"NOT_A_BROWSER_SUITE - or nothing is marking it any more"
    )
