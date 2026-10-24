"""Every file in `web/js/` parses. Nothing else in this repository checks that.

**The defect this exists for is not hypothetical.** PR #96 shipped

    cost: '<p>… excluding the food's purchase or retail value.</p>…'

-- an unescaped ASCII apostrophe closing a single-quoted string at
`web/js/results.js:273`. `results.js` then never evaluates, so the results page
renders nothing at all: no summary cards, no equivalences, no export, and **no
`is_mock` placeholder banner**, which §7.6 makes mandatory and non-dismissible
while open item O-1 stands. A results page that silently stops saying its
numbers are placeholders is the worst failure this front end has.

It reached a pull request because nothing looks. `grep -rn "node --check"` over
`tests/` and `.github/` found nothing before this file. The only suite that
notices is `tests/web/test_results_export.py`, and it notices by collapsing --
nineteen failures and fourteen errors, all the same SyntaxError -- which is a
diagnosis somebody has to perform rather than a message that names the file and
the line.

**`node --check` on a `.js` path does not do this job, and that is measured.**
Node picks its parse goal from the extension, and the two goals are not the
same language: **a module is always strict-mode code**, so the module goal
rejects constructs the script goal accepts. `with` is the plainest of them --
the spec says strict mode code may not contain a `WithStatement` -- and the
difference is therefore the language's, not Node's:

    printf 'with (Math) { }\n' > b.js
    cp b.js b.mjs
    node --check b.js    -> exit 0      # a legal sloppy-mode script
    node --check b.mjs   -> exit 1      # SyntaxError: strict mode code may not
                                        #   include a with statement

So this file copies each source to a `.mjs` and checks that -- the goal the
browser actually uses for `<script type="module">`, which is what
`web/index.html` loads. Checking `web/js/*.js` under its own name would use
the other goal and would pass a file the browser refuses.

**The example above used to be the unterminated string instead, and that is
why this file went red on 2026-10-24.** `node --check` on a `.js` path used to
let PR #96's own defect through: `.js` exited 0 where `.mjs` exited 1 and
named `results.js:273`. It no longer does. The GitHub runner's `ubuntu-latest`
moved to `ubuntu-26.04` on 2026-10-19, which carries **node 24.21.0**, and
there an unterminated string is refused under both spellings. Measured on both
versions, same files, same commands:

    construct                  24.14.1 .js/.mjs   24.21.0 .js/.mjs
    unterminated string         0 / 1              1 / 1   <- the hole closed
    with (Math) { }             0 / 1              0 / 1
    const n = 010;              0 / 1              0 / 1
    var x = 1; delete x;        0 / 1              0 / 1
    function f(a, a) { }        0 / 1              0 / 1
    var eval = 1;               0 / 1              0 / 1

An unterminated string is a **lexical** error, and lexing does not depend on
the parse goal at all, so it was never the right probe for this question -- it
only happened to expose a hole in Node's `.js` path, and that hole was a bug
to be fixed rather than a property to rely on. The five strict-mode-only
constructs are goal differences by specification, which is what this file now
asserts. `test_the_module_parses` was never affected either way: `.mjs` has
always refused both kinds, which is why nothing about `web/js/` was in doubt
on the day the harness test failed.

**A skip here is not a pass.** Without Node nothing is verified, and the reason
says so rather than reporting green.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
JS = ROOT / "web" / "js"

#: Node is not a dependency of this project and never becomes one -- `web/` has
#: no build step and `docs/architecture.md` §3 rules Node out of the stack. It
#: is only ever the *runner* here, the way a browser is, and it is preinstalled
#: on every GitHub-hosted runner, so this skip does not fire in CI.
node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is required to parse web/js/*.js; without it the front end's "
           "syntax is unverified rather than known good",
)


def _sources() -> list[Path]:
    return sorted(JS.glob("*.js"))


def test_there_are_sources_to_check():
    """A glob that matches nothing passes every parametrised test below and
    proves nothing -- the shape of failure this repository has already met in
    `tests/golden/test_golden.py::test_the_suite_is_not_empty`."""
    found = _sources()
    assert len(found) >= 10, [path.name for path in found]


@node
@pytest.mark.parametrize("source", _sources(), ids=lambda path: path.name)
def test_the_module_parses(source: Path, tmp_path: Path):
    """Parsed as a MODULE, which is how the browser loads it.

    `web/index.html` and the other three pages use `<script type="module">`, so
    the module goal is the one that decides whether a visitor gets a working
    page. Checking the file under its own `.js` name would use the other goal
    and would miss exactly the errors that matter (see this module's docstring).
    """
    copy = tmp_path / f"{source.stem}.mjs"
    copy.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    completed = subprocess.run(
        [shutil.which("node"), "--check", str(copy)],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )

    assert completed.returncode == 0, (
        f"web/js/{source.name} does not parse as a module, so the browser will "
        f"not run it:\n{completed.stderr.strip()}"
    )


#: A construct the SCRIPT goal accepts and the MODULE goal must reject.
#:
#: `with` is forbidden in strict-mode code by the language itself, and a module
#: is always strict, so this is a difference between the two parse goals rather
#: than a quirk of one Node build -- which is the property this file needs and
#: the one the unterminated string turned out not to have. The module docstring
#: carries the measurement on both Node versions, and the four other
#: strict-mode-only constructs that behave identically.
_SLOPPY_ONLY_SCRIPT = "with (Math) { }\n"

#: The shape of the defect this file was written for: PR #96's unescaped
#: apostrophe, reduced. Checked under `.mjs` only -- what the `.js` spelling
#: does with it has changed once already and is no longer anybody's premise.
_LEXICALLY_BROKEN = "export const a = 'unterminated\nconst b = 1\n"


def _check(path: Path) -> int:
    return subprocess.run(
        [shutil.which("node"), "--check", str(path)],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    ).returncode


@node
def test_the_check_actually_rejects_a_broken_module(tmp_path: Path):
    """**The half that keeps the test above from being decorative.**

    A gate that cannot fail is not a gate, and this one has a plausible way of
    never failing: run it on the wrong extension and every file passes forever,
    including the one that took the results page down. So three things are
    asserted -- that the module goal refuses what it must, that the `.js`
    spelling does *not*, which is the whole reason every source is copied to a
    `.mjs` first, and that the lexical shape of PR #96's own defect is still
    caught.

    If the second assertion ever fails, read the module docstring: it means
    Node has stopped distinguishing the goals for `with`, which would be a
    change in the language rather than in Node, and the probe would need
    replacing with one of the four alternatives recorded there.
    """
    sloppy_module = tmp_path / "sloppy.mjs"
    sloppy_script = tmp_path / "sloppy.js"
    sloppy_module.write_text(_SLOPPY_ONLY_SCRIPT, encoding="utf-8")
    sloppy_script.write_text(_SLOPPY_ONLY_SCRIPT, encoding="utf-8")
    lexically_broken = tmp_path / "broken.mjs"
    lexically_broken.write_text(_LEXICALLY_BROKEN, encoding="utf-8")

    assert _check(sloppy_module) != 0, (
        "node --check accepted a `with` statement in a .mjs file. A module is "
        "strict-mode code and strict mode forbids `with`, so either this is "
        "not being parsed as a module - in which case test_the_module_parses "
        "cannot fail and is checking nothing - or the language has changed"
    )
    assert _check(sloppy_script) == 0, (
        "node --check refused a `with` statement under the .js spelling. That "
        "is a legal sloppy-mode script, so the two parse goals are no longer "
        "being distinguished the way this file's docstring records. Read it: "
        "`const n = 010;`, `delete x`, a duplicate parameter name and "
        "`var eval = 1;` are the recorded alternatives, all measured on node "
        "24.14.1 and 24.21.0"
    )
    assert _check(lexically_broken) != 0, (
        "node --check accepted an unterminated string in a .mjs file, which is "
        "the shape of the PR #96 defect this whole file exists for"
    )
