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
Node picks its parse goal from the extension, and the CommonJS path lets a
fatal syntax error through:

    printf "export const a = 'unterminated\\nconst b = 1\\n" > b.js
    cp b.js b.mjs
    node --check b.js    -> exit 0      # passes
    node --check b.mjs   -> exit 1      # SyntaxError, with the line

The same pair on PR #96's own `results.js`: `.js` exits 0, `.mjs` exits 1 and
names line 273. So this file copies each source to a `.mjs` and checks that --
the goal the browser actually uses for `<script type="module">`, which is what
`web/index.html` loads.

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


@node
def test_the_check_actually_rejects_a_broken_module(tmp_path: Path):
    """**The half that keeps the test above from being decorative.**

    A gate that cannot fail is not a gate, and this one had a plausible way of
    never failing: run it on the wrong extension and every file passes forever,
    including the one that took the results page down. So the harness is aimed
    at a file that is definitely broken, and at the `.js` spelling that is
    definitely not enough -- if Node's behaviour changes and the `.js` path
    starts rejecting this too, the second assertion fails and this docstring is
    where to start reading.
    """
    broken = "export const a = 'unterminated\nconst b = 1\n"
    as_module = tmp_path / "broken.mjs"
    as_script = tmp_path / "broken.js"
    as_module.write_text(broken, encoding="utf-8")
    as_script.write_text(broken, encoding="utf-8")

    def check(path: Path) -> int:
        return subprocess.run(
            [shutil.which("node"), "--check", str(path)],
            capture_output=True, text=True, timeout=60,
        ).returncode

    assert check(as_module) != 0, (
        "node --check accepted an unterminated string in a .mjs file, so "
        "test_the_module_parses cannot fail and is checking nothing"
    )
    assert check(as_script) == 0, (
        "node --check now rejects this under the .js spelling too. That is an "
        "improvement, not a failure -- but this file exists because it did "
        "not, and the docstring above should be corrected before this "
        "assertion is relaxed"
    )
