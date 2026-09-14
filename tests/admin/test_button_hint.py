"""The description beside a button, on hover and on keyboard focus.

`tests/admin/test_help_tip.py` already covers the glyph shape's own rules -
a labelled `<summary>`, a name that is not generic, text that is not a wall
of prose - and those checks run unchanged over every call this task added,
because the name and text stay the first two positional arguments to
`help.help_tip(...)` whichever shape a call renders. This file covers the
half that is new: the `id` a button's `aria-describedby` and the macro's
rendered element have to agree on, and the CSS rule that keeps the
description in the accessibility tree instead of hidden behind `<details>`'s
native open/closed toggle.

This is a static check over the template and stylesheet source, same as
test_help_tip.py, and for the same reason: it does not need the database or a
running app, so it runs with `-m "not db"` too, and it catches a drift the
moment it is written rather than only when the page happens to be exercised
by a browser test. What it cannot prove is that a person can actually SEE the
reveal happen - that is `tests/admin/test_button_hint_browser.py`'s job, and
the split matches the trap this project has already been caught by once
(`tests/admin/test_guidance.py`'s own docstring): a hidden element still
contains its text, so a markup-only suite is not evidence of a working
reveal, only of consistent wiring.
"""

import re
from pathlib import Path

BRAND_DIR = Path(__file__).resolve().parents[2] / "admin" / "templates" / "brand"
CSS_PATH = Path(__file__).resolve().parents[2] / "admin" / "static" / "brand.css"
MACRO_PATH = BRAND_DIR / "_help_tip.html"

#: `aria-describedby="some-id"` anywhere in a template. Restricted to ids
#: ending `-hint` - this task's own naming convention, applied consistently
#: at every call site below - so this file's checks stay about the help_tip
#: wiring specifically and do not also have to understand the *other*
#: `aria-describedby` uses already in these templates (a field's own "-help"
#: paragraph, unrelated to any button).
DESCRIBEDBY = re.compile(r'aria-describedby="([\w-]*-hint)"')

#: One `help.help_tip(...)` call, captured whole so its `id=` and `glyph=`
#: keyword arguments can be read back out of it. Matches across newlines -
#: several call sites in this task wrap their arguments onto more than one
#: line - and stops at the macro call's own closing paren followed by `}}`,
#: not at the first `)` it meets, which would be the one closing an inner
#: `_("...")` argument.
CALL = re.compile(r'help\.help_tip\((?P<args>.*?)\)\s*\}\}', re.S)
ID_KWARG = re.compile(r'id="([^"]+)"')
GLYPH_KWARG = re.compile(r'glyph\s*=\s*(true|false)')


def _template_files():
    return sorted(BRAND_DIR.glob("*.html"))


def _calls_by_file():
    """{filename: [(declared_id, glyph_bool), ...]} for every help_tip call."""
    out = {}
    for path in _template_files():
        text = path.read_text(encoding="utf-8")
        calls = []
        for match in CALL.finditer(text):
            args = match.group("args")
            id_match = ID_KWARG.search(args)
            glyph_match = GLYPH_KWARG.search(args)
            glyph = True if glyph_match is None else glyph_match.group(1) == "true"
            calls.append((id_match.group(1) if id_match else None, glyph))
        if calls:
            out[path.name] = calls
    return out


def test_the_macro_takes_an_id_and_a_glyph_flag():
    """Guards every assertion below against a signature that quietly reverts:
    without `id`, a button has nothing for `aria-describedby` to name; without
    `glyph`, there is no way to render the reveal without also adding a
    permanent "?" to a button this task judged did not need one."""
    source = MACRO_PATH.read_text(encoding="utf-8")
    assert re.search(r"help_tip\(name,\s*text,\s*id,\s*glyph\s*=\s*true\s*\)", source), (
        "_help_tip.html's macro signature no longer reads "
        "help_tip(name, text, id, glyph=true) - every call site in brand/ "
        "was written against that shape"
    )


def test_every_call_declares_an_id():
    """`id` has no default, so a call that omits it renders `id=\"None\"`
    twice over (once as the HTML id, once nowhere - nothing points at it) and
    is worse than not calling the macro at all: `aria-describedby` on the
    button would name an id that exists but was never meant to be found this
    way. Caught here rather than waiting for the browser test to notice a
    button whose description never appears."""
    offenders = []
    for name, calls in _calls_by_file().items():
        for declared_id, _glyph in calls:
            if not declared_id:
                offenders.append(name)
    assert not offenders, f"help_tip call(s) with no id= argument: {offenders}"


def test_every_aria_describedby_target_is_declared_in_the_same_file():
    """The id a button's `aria-describedby` names has to be the id the
    matching `help_tip(...)` call declares, in the same template - a screen
    reader resolves `aria-describedby` against the rendered page, and a page
    is one file's output. A mismatch here is exactly the defect that would
    leave `aria-describedby` pointing at nothing, silently - no error, no
    visible symptom, just an attribute a screen reader cannot resolve."""
    offenders = {}
    for path in _template_files():
        text = path.read_text(encoding="utf-8")
        used = set(DESCRIBEDBY.findall(text))
        declared = {
            declared_id
            for declared_id, _glyph in _calls_by_file().get(path.name, [])
            if declared_id
        }
        missing = used - declared
        if missing:
            offenders[path.name] = sorted(missing)
    assert not offenders, f"aria-describedby with no matching help_tip id=: {offenders}"


def test_no_two_buttons_in_one_file_share_a_help_tip_id():
    """Two *buttons* sharing one `aria-describedby` target would be invalid
    HTML the moment both rendered - `aria-describedby`, and the CSS `#id` it
    would otherwise need, can only ever resolve to the first of the two, so
    the second button's description would silently borrow the first
    button's text.

    Counted from `aria-describedby="..."` on the button side rather than
    from `help_tip(id=...)` call sites: `brand/submission_moderate.html`
    declares the same id from two branches of one `{% if %}/{% else %}` -
    exactly one of which ever renders for a given response, describing the
    one button that page has - and that is the legitimate case this test
    must not flag. Two buttons naming the same id, which is what an
    `aria-describedby` count catches, is the actual defect."""
    offenders = {}
    for path in _template_files():
        text = path.read_text(encoding="utf-8")
        used = DESCRIBEDBY.findall(text)
        dupes = sorted({i for i in used if used.count(i) > 1})
        if dupes:
            offenders[path.name] = dupes
    assert not offenders, f"two buttons sharing one aria-describedby id: {offenders}"


def test_every_bare_reveal_sits_immediately_after_the_button_it_describes():
    """`glyph=false` renders no `<details>`, so the only thing making the
    description reachable by mouse or keyboard is CSS's adjacent-sibling
    selector (`:is(.button, ...):hover + .help-tip--bare`, `_help_tip.html`'s
    own comment explains the rest) - which only ever matches an IMMEDIATE
    next sibling. A `glyph=false` call placed anywhere else in the markup
    would compile, pass every other test in this file, and reveal nothing to
    a mouse or a keyboard, ever - the exact class of defect a markup-only
    test can otherwise miss entirely.

    Checked structurally: the button or link carrying the matching
    `aria-describedby` must be followed, with at most whitespace between,
    directly by the `{{ help.help_tip(...) }}` call that declares that id.
    """
    offenders = []
    for path in _template_files():
        text = path.read_text(encoding="utf-8")
        for call_match in CALL.finditer(text):
            args = call_match.group("args")
            glyph_match = GLYPH_KWARG.search(args)
            if glyph_match is None or glyph_match.group(1) != "false":
                continue
            id_match = ID_KWARG.search(args)
            if not id_match:
                continue  # caught by test_every_call_declares_an_id
            declared_id = id_match.group(1)
            before = text[: call_match.start()]
            # The nearest `</button>` or `</a>` before this call, with only
            # whitespace between it and the call itself - not merely
            # somewhere earlier in the file.
            tail_match = re.search(r"(</button>|</a>)\s*\{\{\s*\Z", before, re.S)
            if not tail_match:
                offenders.append((path.name, declared_id, "not adjacent to a </button> or </a>"))
                continue
            # And that closing tag's own element carries this exact id.
            element_start = before.rfind("<", 0, tail_match.start())
            element = before[element_start: tail_match.end()]
            if f'aria-describedby="{declared_id}"' not in element:
                offenders.append((path.name, declared_id, "adjacent element has no matching aria-describedby"))
    assert not offenders, f"bare help_tip reveal(s) not wired to the button beside them: {offenders}"


def test_css_never_hides_the_description_with_display_none():
    """`.help-tip__body` must not fall back to `<details>`'s native
    open/closed hiding, which is `display: none` under the hood - a screen
    reader does not announce a `display: none` node, `aria-describedby`
    target or not, so relying on it would mean the description is only ever
    announced after the glyph has been opened, and never on a `glyph=false`
    button, which has no glyph to open. A regression back to `display: none`
    anywhere in this rule would reintroduce exactly that, silently - the
    page would still look right with a mouse and read wrong with a screen
    reader, which is the one combination none of the browser tests below are
    positioned to catch on their own (they drive a browser, not a screen
    reader)."""
    css = re.sub(r"/\*.*?\*/", "", CSS_PATH.read_text(encoding="utf-8"), flags=re.S)
    # Every rule BLOCK whose selector list mentions `.help-tip__body`
    # specifically - not the whole `.help-tip` section, which legitimately
    # sets `display: none` on an unrelated element
    # (`.help-tip summary::-webkit-details-marker`, the native disclosure
    # triangle). Comments are stripped first: the prose above these rules
    # mentions `.help-tip__body` by name too, and would otherwise be read as
    # a selector.
    body_rules = re.findall(r"([^{}]*\.help-tip__body[^{}]*)\{([^}]*)\}", css)
    assert body_rules, "no CSS rule targets .help-tip__body at all"
    offenders = [selector for selector, body in body_rules if "display: none" in body]
    assert not offenders, (
        f".help-tip__body is set to display: none by: {offenders} - that "
        f"hides the description from aria-describedby too, not only from view"
    )


def test_css_reveal_is_anchored_and_width_clamped_rather_than_free_floating():
    """**This test used to require the opposite** - `position: static`, on
    the reasoning that an in-flow block can never be wider than its
    containing block and so can never overflow the page at 320px. The width
    argument was right and is still the thing being protected here. What it
    missed is that an in-flow reveal *resizes the row it appears in*, and
    two of the four rows cannot absorb that: a `flex-wrap: nowrap` dialog
    footer crushed the hovered submit button from 192x50 to 114x142 and
    moved it out from under the pointer, and the sticky dry-run bar grew
    upwards into a live hover oscillation Playwright gave up on after 60
    retries. Both measured in a real browser; neither is visible to a
    stylesheet reading. See `brand.css`'s own comment on the revealed rule.

    So the reveal is `position: absolute` now, and the overflow guarantee is
    carried by an explicit width clamp instead of by normal flow. That is
    what this test pins: absolute is only safe while the clamp is there, and
    a future edit that drops the clamp gets the old 320px defect back with
    nothing to notice it but this assertion and the browser test at 320px
    (`test_button_hint_browser.py`'s own overflow case).
    """
    css = CSS_PATH.read_text(encoding="utf-8")
    section = css[css.index(".help-tip {"):]
    section = section[: section.index("/* One-time secrets")]
    # `.help-tip__body` is a SIBLING of `.help-tip`/`<details>`, never its
    # child - see _help_tip.html and brand.css's own section comment for why
    # a descendant combinator (`>`) does not work here at all. `+` is the
    # combinator every reveal rule uses.
    reveal_rule = re.search(
        r"\.help-tip\[open\] \+ \.help-tip__body,.*?\{([^}]*)\}", section, re.S
    )
    assert reveal_rule, "no reveal rule found for .help-tip__body"
    declarations = reveal_rule.group(1)
    assert "position: absolute" in declarations, (
        "the revealed .help-tip__body is not positioned - in flow it resizes "
        "the button row it sits in; see this test's own docstring"
    )
    assert "max-inline-size" in declarations, (
        "the revealed .help-tip__body has no width clamp. Absolutely "
        "positioned, nothing else stops it being wider than the viewport, "
        "which is the 320px horizontal-overflow defect this mechanism was "
        "first written to avoid"
    )
    # Absolute against WHAT: every container that directly holds a body has to
    # establish a containing block, or the card is positioned against the page
    # and lands nowhere near its button. `:where()` so this can never win
    # against a container's own `position` - `.dialog__actions` is `sticky`
    # and must stay that way.
    assert ":where(:has(> .help-tip__body)) { position: relative; }" in section, (
        "nothing gives the row holding a .help-tip__body a positioning "
        "context, so an absolutely positioned reveal is anchored to the page"
    )
    # `position: fixed` would escape the row entirely and follow the viewport.
    assert "position: fixed" not in section
    # And the descendant form must never reappear - it is what silently
    # broke revealing the glyph shape by hover or focus the first time.
    assert ".help-tip[open] > .help-tip__body" not in section, (
        "a descendant combinator (>) for .help-tip__body is back - it does "
        "not work, because .help-tip__body is not <details>'s child; see "
        "_help_tip.html's header comment for the measured reason"
    )


def test_css_uses_only_logical_properties():
    """`padding-inline`, `margin-inline`, `inset-inline-start` - never `left`
    or `right`, because Arabic and Urdu render the other way and a physical
    property does not follow the text direction it should."""
    css = CSS_PATH.read_text(encoding="utf-8")
    section = css[css.index(".help-tip {"):]
    section = section[: section.index("/* One-time secrets")]
    physical = re.findall(r"\b(?:margin|padding|inset)-(?:left|right)\s*:", section)
    assert not physical, f"physical (left/right) properties in the help-tip CSS: {physical}"


def test_no_new_button_carries_a_title_attribute():
    """The cheap version this task explicitly rejects
    (test_help_tip.py's own `test_never_uses_a_title_attribute...` covers the
    macro file; this covers the nine templates this task edited, where a
    `title=` could have been added directly on a button instead)."""
    offenders = [
        path.name
        for path in _template_files()
        if path.name in _calls_by_file() and "title=" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"a template with a help_tip call also carries title=: {offenders}"


def test_the_macro_never_nests_the_body_inside_details_again():
    """Pins the actual defect found while writing this task, which no other
    static check in this file would catch: a closed `<details>`'s
    non-summary content is not rendered by Chromium through ANY author CSS -
    `display` included - so the first version of this macro, which put
    `.help-tip__body` INSIDE `<details>` and relied on overriding `display`
    to reveal it on hover or focus without opening it, compiled, passed
    every markup-only test, and revealed nothing to a mouse or a keyboard on
    every `glyph=true` button. `tests/admin/test_button_hint_browser.py`
    caught it by reading `is_visible()` in a real browser; this is the cheap
    version of the same guard, over the source `_help_tip.html` renders,
    so a regression fails in under a second rather than after a
    thirteen-minute run reaches the browser suite."""
    source = MACRO_PATH.read_text(encoding="utf-8")
    glyph_branch = re.search(r"\{%-\s*if glyph\s*-%\}(.*?)\{%-\s*else\s*-%\}", source, re.S)
    assert glyph_branch, "could not find the glyph=true branch in _help_tip.html"
    rendered = glyph_branch.group(1)
    details_open = rendered.index("<details")
    details_close = rendered.index("</details>")
    assert "help-tip__body" not in rendered[details_open:details_close], (
        "help-tip__body is back inside <details> - a closed <details>' "
        "content is not renderable through any author CSS, so nothing "
        "inside it can ever be revealed by hover or focus without opening "
        "it; keep .help-tip__body as <details>'s sibling, not its child"
    )
