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

It also covers the pure functions behind the typed box's own
punctuation — `parseTimeText`'s widening, `maskPeriodText` and
`maskPeriodStep` — for the same reason. The mask's traps are behaviours of an
event handler and are asserted in a real browser in
`tests/web/test_period_typing_browser.py`; what is asserted here is the
*decision*, over the input shapes and the typed sequences a browser test cannot
enumerate one page load at a time.

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
const out = cases.map(entry => {
  if (entry.call === 'problem') return period.periodProblem(entry.fields, entry.timeFrame)
  if (entry.call === 'values') return period.periodValues(entry.fields)
  if (entry.call === 'time') return period.parseTimeText(entry.text)
  if (entry.call === 'date') return period.parseDateText(entry.text)
  if (entry.call === 'mask') return period.maskPeriodText(entry.text, entry.field)
  if (entry.call === 'typing') {
    // One box, typed one character at a time, exactly as `applyPeriodMask`
    // drives it: the value before the keystroke, the value the browser leaves
    // behind, what was inserted, and whether the separators already in the box
    // are the mask's own.
    let value = ''
    let masked = false
    for (const character of entry.keys) {
      const step = period.maskPeriodStep(
        { before: value, typed: value + character, inserted: character, masked },
        entry.field,
      )
      value = step.value
      masked = step.masked
    }
    return value
  }
  if (entry.call === 'step') return period.maskPeriodStep(entry.step, entry.field)
  throw new Error(`unknown call ${entry.call}`)
})
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


# ---------------------------------------------------------------------------
# What the box punctuates for itself
# ---------------------------------------------------------------------------


@node
def test_the_time_parser_widened_and_narrowed_nothing(tmp_path):
    """**Every shape the parser accepted before this change still parses to the same
    value**, and two more do.

    The widening exists so that `8` and `8:5` can be tidied into `08:00` and
    `08:05` when the caret leaves the box. It is done *in the parser* rather
    than in a blur-only second parser, so the same list has to hold for the
    typed path, the blur path and `chooseDay`'s "is there a time here already?"
    fallback — which is exactly why it is enumerated rather than spot-checked.

    `810` stays refused: it is five past eight to one reader and ten past eight
    to another, and this field guesses at nothing.
    """
    shapes = [
        # Accepted before this change, and unchanged. A regression here is a
        # narrowing, which the plan forbids outright.
        ("08:10", "08:10"), ("8:10", "08:10"), ("08.10", "08:10"),
        ("0810", "08:10"), ("00:00", "00:00"), ("23:59", "23:59"),
        (" 08:10 ", "08:10"),
        # The two new shapes.
        ("8:5", "08:05"), ("8", "08:00"), ("08", "08:00"), ("0", "00:00"),
        ("23", "23:00"), ("8.5", "08:05"),
        # Refused before and refused now.
        ("", None), ("810", None), ("25:00", None), ("08:60", None),
        ("24", None), ("abc", None), ("08:1a", None), ("-8", None),
    ]
    results = run(tmp_path, [{"call": "time", "text": text} for text, _ in shapes])
    wrong = [
        (text, expected, got)
        for (text, expected), got in zip(shapes, results)
        if got != expected
    ]
    assert not wrong, f"parseTimeText disagrees on {wrong}"


@node
def test_the_date_parser_still_accepts_every_shape_it_did(tmp_path):
    """Nothing in this change touches `parseDateText`, and this is the assertion that
    says so out loud.

    It is here because the mask's third trap is *about* these shapes: `-`, `.`,
    a space and year-first order are all accepted, which is why a mask that
    forced `dd/mm/yyyy` onto every keystroke would be a regression dressed as a
    feature. If this list ever shrinks, `ownedDigits` is refusing to keep its
    hands off something it should.
    """
    shapes = [
        ("14/09/2026", "2026-09-14"), ("1/1/2026", "2026-01-01"),
        ("14-09-2026", "2026-09-14"), ("14.09.2026", "2026-09-14"),
        ("14 09 2026", "2026-09-14"), ("2026-09-14", "2026-09-14"),
        ("2026/09/14", "2026-09-14"),
        ("31/02/2026", None), ("14/09/26", None), ("", None),
    ]
    results = run(tmp_path, [{"call": "date", "text": text} for text, _ in shapes])
    wrong = [
        (text, expected, got)
        for (text, expected), got in zip(shapes, results)
        if got != expected
    ]
    assert not wrong, f"parseDateText disagrees on {wrong}"


@node
def test_the_mask_places_a_separator_after_the_group_it_closes(tmp_path):
    """`maskPeriodText`, which is the placement half and nothing else.

    The separator goes in **when the next group opens**, never eagerly after the
    last digit of a group: `14` stays `14` and becomes `14/0` on the third
    digit. An eager trailing `/` would put the caret on the wrong side of a
    character that is not there yet.

    Nothing is truncated either. A ninth digit spills visibly into the year
    rather than being eaten, because a box that silently drops a keystroke is
    worse than one showing a value the visitor can see is wrong.
    """
    cases = [
        ("date", "", ""), ("date", "1", "1"), ("date", "14", "14"),
        ("date", "140", "14/0"), ("date", "1409", "14/09"),
        ("date", "14092", "14/09/2"), ("date", "14092026", "14/09/2026"),
        ("date", "14/09/2026", "14/09/2026"),
        ("date", "140920261", "14/09/20261"),
        ("time", "0", "0"), ("time", "08", "08"), ("time", "081", "08:1"),
        ("time", "0810", "08:10"), ("time", "08:10", "08:10"),
        ("time", "08101", "08:101"),
    ]
    results = run(
        tmp_path,
        [
            {"call": "mask", "text": text, "field": f"start{kind.capitalize()}"}
            for kind, text, _ in cases
        ],
    )
    wrong = [
        (kind, text, expected, got)
        for (kind, text, expected), got in zip(cases, results)
        if got != expected
    ]
    assert not wrong, f"maskPeriodText disagrees on {wrong}"


@node
def test_typing_a_separator_of_your_own_switches_the_mask_off_for_that_value(tmp_path):
    """**The third trap, driven key by key, which is the only way it shows.**

    `parseDateText` accepts `-`, `.`, a space and year-first order and
    `parseTimeText` accepts a full stop, so a mask that forced `dd/mm/yyyy` onto
    every keystroke would refuse input shapes this field takes today — a
    regression dressed as a feature.

    **`2026-09-14` is the case that switching off alone cannot get right**, and
    it is why `maskPeriodStep` also takes back out what it had already put in.
    The visitor types four digits before they type the `-`, and four bare digits
    are indistinguishable from `dd/mm`: by the time the character that says
    "year-first" arrives, the box already reads `20/26`. Measured in Chromium at
    one key per event — `page.fill` never shows it, because a fill is one event
    with the `-` already in the string.
    """
    cases = [
        # Bare digits: the mask's own, and punctuated.
        ("date", "14092026", "14/09/2026"),
        ("date", "01012026", "01/01/2026"),
        ("time", "0810", "08:10"),
        ("time", "1620", "16:20"),
        # A separator of the visitor's own: hands off from that keystroke on.
        ("date", "1/1/2026", "1/1/2026"),
        ("date", "14-09-2026", "14-09-2026"),
        ("date", "14.09.2026", "14.09.2026"),
        ("date", "14 09 2026", "14 09 2026"),
        ("date", "14/09/2026", "14/09/2026"),
        ("time", "8:10", "8:10"),
        ("time", "08.10", "08.10"),
        ("time", "08:10", "08:10"),
        # Four digits first, then the character that says year-first: what the
        # mask had already put in comes back out.
        ("date", "2026-09-14", "2026-09-14"),
        ("date", "2026/09/14", "2026/09/14"),
        # Mixed separators parse today, so the `/` the visitor typed is theirs
        # and stays: only a separator at one of the mask's own break points and
        # put there by the mask is taken back out.
        ("date", "14/09-2026", "14/09-2026"),
    ]
    results = run(
        tmp_path,
        [
            {"call": "typing", "keys": keys, "field": f"start{kind.capitalize()}"}
            for kind, keys, _ in cases
        ],
    )
    wrong = [
        (kind, keys, expected, got)
        for (kind, keys, expected), got in zip(cases, results)
        if got != expected
    ]
    assert not wrong, f"typing produced the wrong text: {wrong}"


@node
def test_the_mask_stays_on_through_an_edit_in_the_middle(tmp_path):
    """**`masked`, and why it is a flag rather than a reading of the text.**

    A visitor editing the middle of `14/09/2026` leaves the separators one place
    out of position — `154/09/2026` — so the value after the edit looks like
    somebody else's punctuation. A rule read off the characters alone would
    switch the mask off on the first correction and never turn it back on, and
    the box would quietly stop punctuating after one fix.

    The second case is the same shape in a box the mask never owned, which must
    stay hands off: the flag is what tells the two apart, and without it they
    are the same string.

    The value the first case produces is nonsense — it is a 40th month — and
    that is deliberate: the mask punctuates, the parser judges, and the error
    line says so. What is asserted is which of the two answers the mask gave.
    """
    edit, foreign = run(
        tmp_path,
        [
            {
                "call": "step",
                "field": "startDate",
                "step": {
                    "before": "14/09/2026",
                    "typed": "154/09/2026",
                    "inserted": "5",
                    "masked": True,
                },
            },
            # The same shape in a box the mask never owned: hands off, and it
            # stays hands off.
            {
                "call": "step",
                "field": "startDate",
                "step": {
                    "before": "1/1/2026",
                    "typed": "15/1/2026",
                    "inserted": "5",
                    "masked": False,
                },
            },
        ],
    )
    assert edit == {"value": "15/40/92026", "masked": True}, edit
    assert foreign == {"value": "15/1/2026", "masked": False}, foreign


@node
def test_a_paste_the_browser_will_not_describe_is_left_alone(tmp_path):
    """`event.data` is `null` for a paste whose content is only on
    `dataTransfer`. It is read as "not digits" rather than guessed at.

    Guessing is what would hurt. `2026-09-14` pasted into an empty box has no
    keystroke history to switch the mask off, so a mask that took a null `data`
    for digits would reformat it to `20/26/0914` — a well-formed string naming a
    different day, which the visitor never typed and would have no reason to
    re-read.
    """
    (result,) = run(
        tmp_path,
        [
            {
                "call": "step",
                "field": "startDate",
                "step": {
                    "before": "",
                    "typed": "2026-09-14",
                    "inserted": None,
                    "masked": False,
                },
            }
        ],
    )
    assert result == {"value": "2026-09-14", "masked": False}, result


@node
def test_typing_never_changes_what_a_value_means(tmp_path):
    """**The claim the whole feature rests on: this is punctuation, not
    meaning.**

    For every shape either box already accepts, typing it one key at a time and
    then parsing the result gives what parsing the typed characters gives. The
    failure it guards is a mask that quietly reinterprets a value the field
    accepts today — `2026-09-14` read as the 20th of the 26th month is a
    well-formed string that says a different day, and it would reach the wire
    without a word.

    Bare digits are the one case where the mask *creates* a meaning rather than
    preserving one — `14092026` parses to nothing at all until the separators go
    in — so those are asserted by name, in `dd/mm/yyyy` order, which is what the
    field's placeholder and its hint both promise.
    """
    dates = ["14092026", "14/09/2026", "1/1/2026", "2026-09-14", "14-09-2026", "01012026"]
    times = ["0810", "08:10", "8:10", "08.10", "8", "8:5", "1620"]
    typed = run(
        tmp_path,
        [{"call": "typing", "keys": text, "field": "startDate"} for text in dates]
        + [{"call": "typing", "keys": text, "field": "startTime"} for text in times],
    )
    date_typed, time_typed = typed[: len(dates)], typed[len(dates):]
    parsed = run(
        tmp_path,
        [{"call": "date", "text": text} for text in dates + list(date_typed)]
        + [{"call": "time", "text": text} for text in times + list(time_typed)],
    )
    date_source = parsed[: len(dates)]
    date_result = parsed[len(dates): 2 * len(dates)]
    rest = parsed[2 * len(dates):]
    time_source, time_result = rest[: len(times)], rest[len(times):]

    reinterpreted = [
        (text, source, result)
        for text, source, result in zip(dates + times, date_source + time_source, date_result + time_result)
        if source is not None and source != result
    ]
    assert not reinterpreted, (
        f"typing changed what an already-valid value means: {reinterpreted}"
    )
    assert dict(zip(dates, date_result))["14092026"] == "2026-09-14"
    assert dict(zip(dates, date_result))["01012026"] == "2026-01-01"
    assert dict(zip(times, time_result))["0810"] == "08:10"
