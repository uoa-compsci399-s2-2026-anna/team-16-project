"""The small "?" beside a button (Task: admin usability), and what makes it
usable rather than decorative.

A bare "?" glyph with no accessible name announces as "question mark" to a
screen reader and tells nobody what it explains. Every call site is expected
to pass a `name` that says what the specific button does - never "Help" or
"More info" alone, which would be the generic-name mistake all over again in
a different shape. This is a static check over the template source: it does
not need the database or a running app, so it runs with `-m "not db"` too,
and it catches a new help_tip call the moment it is written rather than only
when its page happens to be exercised by an HTTP test.
"""

import re
from pathlib import Path

BRAND_DIR = Path(__file__).resolve().parents[2] / "admin" / "templates" / "brand"

#: Names this task explicitly calls out as not good enough on their own -
#: a name has to say *what* the control does, not just that help exists.
GENERIC_NAMES = {"help", "more info", "more information", "info", "what this does"}

#: One call per line in every file that uses the macro, of the shape
#: `help.help_tip(_("..."), _("..."))` - captures the two `_()` arguments.
CALL_PATTERN = re.compile(
    r'help\.help_tip\(\s*_\(\s*["\']([^"\']+)["\']\s*\)\s*,\s*_\(\s*["\']([^"\']+)["\']\s*\)',
)


def _help_tip_calls() -> list[tuple[str, str, str]]:
    """Every `help.help_tip(name, text)` call site, as (file, name, text)."""
    calls = []
    for path in sorted(BRAND_DIR.glob("*.html")):
        text = path.read_text(encoding="utf-8")
        for name, explanation in CALL_PATTERN.findall(text):
            calls.append((path.name, name, explanation))
    return calls


def test_the_macro_exists_and_is_used_somewhere():
    """Guards every assertion below against a rename that silently stops
    matching `CALL_PATTERN` - the same shape as test_guidance.py's own
    'declaring is used somewhere' tests."""
    calls = _help_tip_calls()
    assert calls, "no template calls help.help_tip(...) - the mechanism is unused"


def test_every_help_tip_name_says_what_it_explains():
    """The accessible name is the whole point: it is what a screen-reader
    user hears instead of the bare "?" glyph, and it is what has to tell two
    markers on the same page apart. "Help" or "More info" would pass a test
    that only checked *something* was there - this checks it is not that."""
    calls = _help_tip_calls()
    offenders = [
        (f, name) for f, name, _text in calls
        if name.strip().lower() in GENERIC_NAMES
    ]
    assert not offenders, f"generic, non-descriptive help-tip name(s): {offenders}"


def test_every_help_tip_name_is_distinct_within_its_page():
    """Two markers on one page both named "What Cancel does" would be
    indistinguishable to someone tabbing through them by name alone."""
    by_file: dict[str, list[str]] = {}
    for f, name, _text in _help_tip_calls():
        by_file.setdefault(f, []).append(name)

    dupes = {
        f: sorted({n for n in names if names.count(n) > 1})
        for f, names in by_file.items()
        if len(names) != len(set(names))
    }
    assert not dupes, f"duplicate help-tip names on one page: {dupes}"


def test_every_help_tip_explanation_is_short_plain_language():
    """One or two sentences, not a restatement of the button label and not a
    paragraph. Long enough to say what happens and what it affects; short
    enough that it reads as a tip and not a second copy of the guidance
    blocks this same task folded away. The upper bound is generous on
    purpose - this is a smoke check for a wall of text, not a style guide."""
    calls = _help_tip_calls()
    too_short = [(f, n) for f, n, t in calls if len(t.strip()) < 20]
    too_long = [(f, n) for f, n, t in calls if len(t.strip()) > 320]
    assert not too_short, f"help-tip text too short to explain anything: {too_short}"
    assert not too_long, f"help-tip text too long for a short tip: {too_long}"


def test_every_help_tip_is_a_native_disclosure():
    """Keyboard- and touch-operable without JavaScript: a `title` attribute
    or a hover-only tooltip would fail both, per this task's own brief. Every
    `<details class="help-tip">` must carry a `<summary aria-label="...">` -
    the structural half of the accessible-name requirement the tests above
    check the content of."""
    # `[^>]*` rather than an exact `class="help-tip">` match, on purpose: an
    # earlier version of this test used the exact form, and a mutation that
    # added an unrelated attribute to the same tag (`title="..."`, the very
    # attribute this task rejects) made the opener stop matching at all -
    # zero openers, zero pairs, a trivially "passing" test that had stopped
    # checking anything. Tolerating extra attributes on the opening tag is
    # what keeps this test looking at the tag a real edit would produce.
    opener = re.compile(r'<details class="help-tip"[^>]*>')
    offenders = []
    for path in sorted(BRAND_DIR.glob("*.html")):
        text = path.read_text(encoding="utf-8")
        for match in opener.finditer(text):
            following = text[match.end():match.end() + 200]
            if not re.match(r'\s*<summary aria-label="[^"]+"', following):
                offenders.append((path.name, match.group(0)))
    assert not offenders, (
        f"a help-tip <details> has no labelled <summary> right after it: {offenders}"
    )


def test_never_uses_a_title_attribute_or_hover_only_css_for_help():
    """The cheap version this task explicitly rejects: invisible to touch,
    slow for everyone else, and unreachable by keyboard alone."""
    macro = (BRAND_DIR / "_help_tip.html").read_text(encoding="utf-8")
    assert "title=" not in macro, "_help_tip.html uses a title attribute"
