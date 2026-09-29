"""The 24-hour rule lives in the form; the 38-hour rule lives in the API; and
neither number may be copied onto the other side.

**Why this file exists at all.** `api/schemas.py` refuses a period instant more
than **38** hours past the server's own UTC clock, and `web/js/period.js`
refuses one more than **24** hours past the visitor's. Two different numbers for
what reads like one rule is exactly the shape a tidy-minded reader "fixes", and
both fixes are wrong:

* tightening the server to 24 refuses a shift somebody in Auckland entered
  correctly, at the time it actually happened, with nothing in the payload they
  could change to make it pass - a zoneless `2026-09-22T17:55` is 13 hours ahead
  of UTC before anything has gone wrong;
* loosening the form to 38 lets a visitor enter a period a day and a half in the
  future, which is the thing the rule exists to refuse, and the server would
  accept it because the server is the loose half by design.

The arithmetic is `24 + 14`: the form's allowance plus the widest civil UTC
offset in use anywhere (Kiritimati). It is written out in the comment over
`PERIOD_CEILING_HOURS` and again in `period.js`'s header; this file is what
makes the two files fail together rather than drift apart quietly.

No browser is needed: both numbers are read out of the source text, so this runs
in CI beside the schema tests and does not depend on Playwright or on the stack
being up.
"""

from __future__ import annotations

import re
from pathlib import Path

from api.schemas import PERIOD_CEILING_HOURS

ROOT = Path(__file__).resolve().parents[2]
PERIOD_JS = ROOT / "web" / "js" / "period.js"


def _source() -> str:
    return PERIOD_JS.read_text(encoding="utf-8")


def _declared_max_hours() -> int:
    """`MAX_HOURS_AHEAD`'s value, read out of the module that enforces it.

    The exported constant rather than the ceiling it computes, because the
    ceiling is a function of `Date.now()` and this test has to be able to run at
    any hour.
    """
    match = re.search(r"export const MAX_HOURS_AHEAD = (\d+)", _source())
    assert match, (
        "web/js/period.js no longer exports MAX_HOURS_AHEAD. The form's own "
        "bound is the only exact one - see the module header - so it has to "
        "stay findable, not fold into an expression."
    )
    return int(match.group(1))


def test_the_form_refuses_more_than_twenty_four_hours_ahead():
    assert _declared_max_hours() == 24, (
        "the form's ceiling moved. 24 hours is the client's rule: a shift "
        "entered at 17:55 may end at 22:00 and may cross midnight, and beyond "
        "a day it is not a period anybody is reporting."
    )


def test_the_server_stays_loose_and_the_two_are_not_the_same_number():
    assert PERIOD_CEILING_HOURS == 38
    assert PERIOD_CEILING_HOURS != _declared_max_hours(), (
        "the API's ceiling and the form's are now the same number. They must "
        "not be: the stored instants carry no zone, so the server cannot tell "
        "which side of the date line a value was typed on and has to allow the "
        "widest civil UTC offset on top of the form's allowance."
    )


def test_the_server_ceiling_is_the_form_s_plus_the_widest_civil_offset():
    """The relationship, not the two numbers - so that moving one on purpose
    fails here until the other is moved with it and the arithmetic still holds."""
    widest_civil_utc_offset_hours = 14  # Kiritimati, UTC+14.
    assert PERIOD_CEILING_HOURS == _declared_max_hours() + widest_civil_utc_offset_hours


def test_the_browser_does_not_carry_the_server_s_number():
    """**A grep, and it is the assertion that would actually have caught the
    copy.** The two tests above compare declared constants; this one catches the
    likelier mistake, which is a second 38 appearing somewhere in the module as
    a literal - in a fallback, a clamp, or a comment that has started to read
    like code - while `MAX_HOURS_AHEAD` stays innocently at 24.

    Prose is stripped first, because the module header explains 38 at length and
    must go on being allowed to.
    """
    source = re.sub(r"/\*.*?\*/", "", _source(), flags=re.S)
    source = re.sub(r"^\s*//.*$", "", source, flags=re.M)
    assert "38" not in source, (
        "web/js/period.js carries the number 38 in its code. That is the "
        "API's deliberately loose ceiling and it is wrong in a browser, where "
        "'now' is the visitor's own now: see PERIOD_CEILING_HOURS in "
        "api/schemas.py before changing either."
    )


def test_the_two_files_point_at_each_other():
    """A cross-reference each way, so the next reader of either number meets the
    reason before the temptation. Asserted because a comment nobody checks is a
    comment that gets deleted in a tidy-up."""
    assert "PERIOD_CEILING_HOURS" in _source(), (
        "web/js/period.js no longer names the API constant it deliberately "
        "disagrees with"
    )
    schemas = (ROOT / "api" / "schemas.py").read_text(encoding="utf-8")
    assert "lives in the form" in schemas, (
        "api/schemas.py no longer says that the real 24-hour rule lives in the "
        "form; without that sentence 38 reads as a mistake"
    )
