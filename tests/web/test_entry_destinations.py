"""The current-waste list excludes a prevention destination by flag (§6.1, §7.2).

`calculator.js` filtered `destination.code !== 'prevention'`, so §10.3's
`refed_prevention` — a prevention destination by every property that matters,
and one the deployed stack offers on the form today — sat on the list of
destinations a visitor allocates *current* waste across. §6.2 answers 400 for
it, so the form was offering a choice the server refuses; worse, before that
rule read the flag the line was accepted and became a public statistic.

Run under Node for the reason `test_results_export.py` gives at length: a test
that greps `calculator.js` for the string `is_prevention` asserts that a
property name was typed, not that a row leaves the list. Node is the runner
only and does not enter the stack (`docs/architecture.md` §3) — there is still
no build step and no `package.json`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CALCULATOR_JS = ROOT / "web" / "js" / "calculator.js"

node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is required to execute web/js/calculator.js; the filter is unverified without it",
)

#: `calculator.js` imports `api.js`, `state.js`, `results.js` and
#: `improvement.js` at module load; between them they read these two browser
#: globals. Nothing under test touches either. `crypto` is not stubbed: Node
#: defines it as a getter-only property, and it is real there anyway.
HARNESS = """
globalThis.window = { location: { search: '' } }
globalThis.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} }

import { readFileSync, writeFileSync } from 'node:fs'
const { entryDestinations } = await import(process.argv[2])
const { state } = await import(process.argv[3])
state.taxonomy = JSON.parse(readFileSync(process.argv[4], 'utf8'))
writeFileSync(process.argv[5], JSON.stringify(entryDestinations().map(d => d.code)), 'utf8')
"""


def offered(tmp_path: Path, destinations: list[dict]) -> list[str]:
    harness = tmp_path / "harness.mjs"
    harness.write_text(HARNESS, encoding="utf-8")
    taxonomy = tmp_path / "taxonomy.json"
    taxonomy.write_text(json.dumps({"destinations": destinations}), encoding="utf-8")
    out = tmp_path / "codes.json"
    completed = subprocess.run(
        [
            shutil.which("node"),
            str(harness),
            CALCULATOR_JS.as_uri(),
            (ROOT / "web" / "js" / "state.js").as_uri(),
            str(taxonomy),
            str(out),
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(out.read_text(encoding="utf-8"))


def _row(code: str, *, is_prevention: bool, sort_order: int = 10) -> dict:
    return {
        "code": code,
        "name": code.title(),
        "group": "reuse",
        "description": None,
        "is_prevention": is_prevention,
        "sort_order": sort_order,
    }


@node
def test_an_ordinary_destination_is_offered(tmp_path):
    codes = offered(tmp_path, [_row("landfill", is_prevention=False)])
    assert codes == ["landfill"]


@node
def test_every_flagged_destination_is_held_off_the_current_list(tmp_path):
    """Both of them, which is the defect: the second vocabulary's prevention
    row stayed on this list for as long as the filter named one code."""
    codes = offered(
        tmp_path,
        [
            _row("prevention", is_prevention=True, sort_order=5),
            _row("refed_prevention", is_prevention=True, sort_order=6),
            _row("landfill", is_prevention=False, sort_order=110),
        ],
    )
    assert codes == ["landfill"]


@node
def test_a_destination_named_prevention_but_unflagged_is_offered(tmp_path):
    """What proves the string is gone. A row *called* `prevention` with the
    tick cleared is an ordinary destination, and the server accepts it in a
    current scenario, so the form must offer it."""
    codes = offered(
        tmp_path,
        [
            _row("prevention", is_prevention=False, sort_order=5),
            _row("waste_avoided", is_prevention=True, sort_order=6),
        ],
    )
    assert codes == ["prevention"]
