"""`period.js`'s rules, called directly under Node (contract v1.67, v1.68).

**Why this file exists beside the browser one.** `tests/web/test_period_picker_
browser.py` and `tests/web/test_period_submission_browser.py` drive the real
form, which is the only way to prove a keyboard, a focus ring or a request
body. But a rule is not the same thing as the screen that happens to show it,
and two of these rules have inputs the form cannot currently reach:

* `periodProblem(fields, timeFrame)` takes the time frame as an **argument**
  from v1.68, so that `handlePeriodInput` can ask about the answer a keystroke
  leaves behind rather than the one it is replacing. Swapping that ordering
  back leaves every browser test green — measured, and it is an equivalent
  mutation rather than a weak test, because emptying a preset-filled interval
  takes four edits and the demotion lands on the first of them. The argument
  is therefore asserted here, where both values can actually be passed.
* `periodValues` is what `state.periodStart` / `state.periodEnd` are set from,
  and the claim that a half-typed date leaves no stale instant behind is about
  a return value, not about a rendered field.

Run under Node for `tests/web/test_results_export.py`'s reason: executing the
module that ships beats reading its source with a regular expression, and this
project has lost defects to exactly that substitution.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PERIOD_JS = ROOT / "web" / "js" / "period.js"

node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is required to execute web/js/period.js; these rules are unverified without it",
)

#: `period.js` imports `state.js`, which reads `sessionStorage` at module load,
#: and `i18n.js`, which reads `window`. Neither is under test here; both are the
#: same two stubs `tests/web/test_results_export.py` installs.
HARNESS = """
globalThis.window = { location: { search: '' } }
globalThis.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} }

import { readFileSync, writeFileSync } from 'node:fs'
const period = await import(process.argv[2])
const cases = JSON.parse(readFileSync(process.argv[3], 'utf8'))
const out = cases.map(({ call, fields, timeFrame }) =>
  (call === 'problem'
    ? period.periodProblem(fields, timeFrame)
    : period.periodValues(fields)))
writeFileSync(process.argv[4], JSON.stringify(out), 'utf8')
"""

EMPTY = {"startDate": "", "startTime": "", "endDate": "", "endTime": ""}
SHIFT = {
    "startDate": "14/09/2026",
    "startTime": "08:10",
    "endDate": "14/09/2026",
    "endTime": "16:20",
}


def run(tmp_path: Path, cases: list[dict]) -> list[dict]:
    harness = tmp_path / "harness.mjs"
    harness.write_text(HARNESS, encoding="utf-8")
    payload = tmp_path / "cases.json"
    payload.write_text(json.dumps(cases), encoding="utf-8")
    out = tmp_path / "out.json"
    completed = subprocess.run(
        [shutil.which("node"), str(harness), PERIOD_JS.as_uri(), str(payload), str(out)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert completed.returncode == 0, (
        f"node could not run period.js's rules:\n{completed.stdout}\n{completed.stderr}"
    )
    return json.loads(out.read_text(encoding="utf-8"))


@node
def test_custom_with_no_interval_is_refused_and_a_preset_with_none_is_not(tmp_path):
    """**The v1.68 clause, and the whole reason `periodProblem` takes a time
    frame.**

    The same four empty strings are legal under one answer and refused under
    another, so nothing about the *fields* can decide it. §6.2 refuses
    `custom` with no interval (`period_custom_without_interval`) and
    deliberately allows a preset with none — clause five of
    `ck_submission_period` is "an interval requires a `time_frame`", not "an
    interval requires `custom`" — because a preset with no interval is every
    row written before v1.67 and every request from a client that predates the
    picker.

    A single-value test could not tell the clause from a blanket refusal of an
    empty period, which would block "Not stated" as well.
    """
    refused, preset, unstated = run(
        tmp_path,
        [
            {"call": "problem", "fields": EMPTY, "timeFrame": "custom"},
            {"call": "problem", "fields": EMPTY, "timeFrame": "one_week"},
            {"call": "problem", "fields": EMPTY, "timeFrame": ""},
        ],
    )
    assert "both a start and an end" in refused["message"], refused
    assert refused["field"] == "period-start-date"
    assert preset["message"] == "", (
        "a preset with no interval was refused; that is the pre-v1.67 shape and "
        "every row already in the database"
    )
    assert unstated["message"] == "", '"Not stated" is not a problem to be fixed'


@node
def test_a_complete_interval_is_accepted_under_every_answer(tmp_path):
    """The other half of the clause: it is about the *absence* of an interval,
    never about the interval itself. `custom` and a preset both carry the same
    shift without complaint — v1.67's designed normal case is the second."""
    results = run(
        tmp_path,
        [
            {"call": "problem", "fields": SHIFT, "timeFrame": frame}
            for frame in ("custom", "one_week", "one_month", "one_quarter", "one_year")
        ],
    )
    assert all(result["message"] == "" for result in results), results


@node
def test_a_half_typed_date_leaves_no_stale_instant_behind(tmp_path):
    """`periodValues` is what `state.periodStart` / `state.periodEnd` hold, and
    `submission.js` sends them verbatim. So a shape it returns while the period
    is *not* legal is a shape that can be sent.

    Both instants empty together, never one of them: half an interval is
    §6.2's `period_half_interval`, and the state must not be able to hold the
    surviving half of a period the visitor is in the middle of retyping.
    """
    complete, half_typed, backwards = run(
        tmp_path,
        [
            {"call": "values", "fields": SHIFT},
            {"call": "values", "fields": {**SHIFT, "endDate": "14/09/20"}},
            {"call": "values", "fields": {**SHIFT, "endTime": "08:00"}},
        ],
    )
    assert complete == {
        "periodStart": "2026-09-14T08:10",
        "periodEnd": "2026-09-14T16:20",
    }, complete
    for name, result in (("half-typed", half_typed), ("backwards", backwards)):
        assert result == {"periodStart": "", "periodEnd": ""}, (
            f"{name}: an illegal period left an instant in the state: {result}"
        )
