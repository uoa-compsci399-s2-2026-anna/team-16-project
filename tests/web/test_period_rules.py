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
import math
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
  // WP3's clock. `dx`/`dy` are an offset from the centre of the face, in
  // whatever units the caller measured in — the ring test divides by `radius`,
  // so only the ratio matters and a test may write user units or pixels.
  if (entry.call === 'angle') return period.clockAngle(entry.dx, entry.dy)
  if (entry.call === 'hour') return period.hourFromPointer(entry.dx, entry.dy, entry.radius)
  if (entry.call === 'minute') return period.minuteFromPointer(entry.dx, entry.dy)
  if (entry.call === 'mode') return period.openClockMode(entry.event)
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


# ---------------------------------------------------------------------------
# WP3's clock: the pointer arithmetic, called directly
# ---------------------------------------------------------------------------
#
# **Here as well as in the browser, and the division is the same one the rest of
# this file makes.** `tests/web/test_period_clock_browser.py` drives real
# `pointerdown`/`pointermove`/`pointerup` at real coordinates, which is the only
# way to prove that a drag works, that the capture survives leaving the face and
# that the hand ends up where the finger did. What it cannot afford is the
# *decision*: 96 ring-and-angle cases is 96 page loads, and §6.5 caps a caller at
# 600 GETs an hour. The angle, the two rings and the one-minute snap are
# arithmetic, so they are asserted where arithmetic can be enumerated.


def _polar(degrees: float, radius: float) -> tuple[float, float]:
    """A point at `degrees` clockwise from twelve, `radius` from the centre.

    The inverse of `period.js`'s `facePoint`, written out here rather than
    imported so that the test states the convention independently: x grows to
    the right and **y grows downwards**, which is what both a client rectangle
    and an SVG `viewBox` do and is the whole reason `clockAngle` negates `dy`.
    """
    radians = math.radians(degrees)
    return (radius * math.sin(radians), -radius * math.cos(radians))


@node
def test_twelve_oclock_is_up_and_the_angle_grows_the_way_a_clock_does(tmp_path):
    """The convention the entire dial rests on, in four presses.

    `atan2` measures from the positive x axis and anticlockwise; a clock
    measures from straight up and clockwise. `clockAngle` swaps its arguments
    and negates `dy` to make that translation, and a sign lost anywhere in it
    gives a dial that reads three o'clock as nine.
    """
    up, right, down, left = run(
        tmp_path,
        [{"call": "angle", "dx": dx, "dy": dy} for dx, dy in
         [(0, -10), (10, 0), (0, 10), (-10, 0)]],
    )
    assert (up, right, down, left) == (0, 90, 180, 270), (
        f"the face is not a clock: up={up}, right={right}, down={down}, left={left}"
    )


@node
def test_the_outer_ring_is_one_to_twelve_and_the_inner_ring_is_the_other_twelve(tmp_path):
    """**The two rings are what makes this a 24-hour dial**, and the top of each
    is the position the two disagree about: twelve o'clock outside, midnight
    inside.

    Every other inner position is its outer twin plus twelve — 1 becomes 13, 11
    becomes 23 — which is Material's layout and the only one that puts 00:00 and
    12:00 where a reader of a 24-hour clock looks for them. Enumerated over all
    twelve positions of both rings rather than sampled, because an off-by-one in
    the `step === 0` branch is invisible at any single position but 12.
    """
    outer = run(
        tmp_path,
        [{"call": "hour", "dx": _polar(index * 30, 80)[0], "dy": _polar(index * 30, 80)[1],
          "radius": 96} for index in range(12)],
    )
    assert outer == [12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11], outer

    inner = run(
        tmp_path,
        [{"call": "hour", "dx": _polar(index * 30, 52)[0], "dy": _polar(index * 30, 52)[1],
          "radius": 96} for index in range(12)],
    )
    assert inner == [0, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23], inner


@node
def test_the_ring_boundary_is_halfway_between_the_two_rows_of_numbers(tmp_path):
    """Which ring a press lands on is a distance, and the distance has to be
    where the numbers say it is.

    The two label rings are at 52 and 80 of a 96 radius, so the boundary is 66.
    A press at 65 is inner and a press at 67 is outer; a boundary that had
    drifted towards either ring would read a press aimed squarely at a printed
    number as the other one's.

    **The centre is included because it caught a real defect the first time it
    was run.** It has no angle, and `Math.atan2(0, -0)` is π — negating a zero
    `dy` produces negative zero, which `atan2` reads as the negative x axis — so
    the dead centre of the face came back as six o'clock, and on the inner ring
    that is 18:00. `clockAngle` now answers 0 there, which is midnight on the
    ring a centre press lands on and surprises nobody.
    """
    just_inside, just_outside, centre, far_outside = run(
        tmp_path,
        [
            {"call": "hour", "dx": 0, "dy": -65, "radius": 96},
            {"call": "hour", "dx": 0, "dy": -67, "radius": 96},
            {"call": "hour", "dx": 0, "dy": 0, "radius": 96},
            {"call": "hour", "dx": 0, "dy": -140, "radius": 96},
        ],
    )
    assert just_inside == 0, "a press at 65 of 96 is inside the inner ring"
    assert just_outside == 12, "a press at 67 of 96 is on the outer ring"
    assert centre == 0, (
        "the dead centre of the face has no angle; it must be given one rather "
        "than left to `atan2(0, -0)`, which is π and reads as six o'clock"
    )
    assert far_outside == 12, (
        "a press beyond the face — which pointer capture makes reachable on "
        "every drag — still reads as the outer ring"
    )


@node
def test_a_minute_is_a_minute_and_not_a_multiple_of_five(tmp_path):
    """**The single most consequential number in this control.**

    Only every fifth minute is labelled, and the obvious implementation snaps to
    the labels. A roster that says 08:07 is a real shift, and a dial that
    answered 08:05 or 08:10 would refuse a period somebody actually worked —
    while looking, on screen, exactly like one that worked.

    So: all sixty positions, each read back as itself. If this ever returns
    `round(value / 5) * 5` the assertion below names the first minute it lost.
    """
    minutes = run(
        tmp_path,
        [{"call": "minute", "dx": _polar(value * 6, 80)[0], "dy": _polar(value * 6, 80)[1]}
         for value in range(60)],
    )
    assert minutes == list(range(60)), (
        f"the minute ring does not resolve to one minute: {minutes}"
    )


@node
def test_the_minute_ring_ignores_how_far_from_the_centre_the_pointer_is(tmp_path):
    """There is one minute ring, so the distance means nothing on this stage.

    It matters because a drag wanders: somebody sweeping towards "ten past"
    pulls inwards and outwards across the gesture, and a minute that changed
    with the radius would flicker while the angle held still.
    """
    near, on, far = run(
        tmp_path,
        [{"call": "minute", "dx": _polar(42, radius)[0], "dy": _polar(42, radius)[1]}
         for radius in (20, 80, 200)],
    )
    assert (near, on, far) == (7, 7, 7), (near, on, far)


@node
def test_a_keyboard_press_opens_the_keyboard_mode_and_a_pointer_press_the_dial(tmp_path):
    """**One character carrying the whole accessibility story**, so it is
    asserted where it can be stated rather than only implied by a browser.

    `UIEvent.detail` on a click is the click count. A real press is 1, the
    second of a double-click is 2, and a click *synthesised* from `Enter` or
    `Space` on a focused button is 0, because no button was clicked any number
    of times. A dial has no keyboard route into it, so getting this backwards
    hands a keyboard user a control they cannot drive at all.

    `undefined` — a caller with no event to offer — opens the dial, which is the
    safe default: the dial is the visible half, and the toggle inside the dialog
    reaches the other one in one press either way.
    """
    keyboard, single, double, missing = run(
        tmp_path,
        [
            {"call": "mode", "event": {"detail": 0}},
            {"call": "mode", "event": {"detail": 1}},
            {"call": "mode", "event": {"detail": 2}},
            {"call": "mode"},
        ],
    )
    assert keyboard == "keyboard", "a keyboard-synthesised click must open the number boxes"
    assert single == "dial", "a pointer press must open the dial"
    assert double == "dial", "the second click of a double-click is still a pointer press"
    assert missing == "dial"
