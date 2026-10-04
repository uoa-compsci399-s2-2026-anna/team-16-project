"""Every class the front end puts in the DOM either has a rule or a declared reason.

**This file exists because the project has already paid for one class with no
rule.** `leafPanel`'s own JSDoc records what `.amount-grid--leaf` cost: it was
emitted, it was matched by nothing in `web/css/styles.css`, and the next person
read it as load-bearing and built a layout override on top of it. #160 found
twenty more in the same condition -- some test hooks, some dead markup, some
rules somebody meant to write -- and the only part of that clean-up with any
value a year from now is the part that stops the list growing back.

The rule: a class name emitted by `web/*.html` or `web/js/*.js` must either
appear in a selector in `web/css/`, or be declared below with a reason. Nothing
else. Declared with a reason rather than matched against a convention, on the
same terms as `tests/web/test_i18n_web.py`'s `IDENTICAL_BY_DESIGN` and
`tests/api/test_fixture_consistency.py`'s
`METRICS_A_RESPONSE_FIXTURE_NEED_NOT_CARRY`: an exemption has to say why, and a
test below fails on an exemption that is no longer needed -- so the list can
only grow deliberately and it cannot outlive the reason it was written for.

WHAT THIS SCAN CAN SEE
----------------------
A class name that is a **static string literal** in one of these positions:

* a `class` attribute in `web/*.html`, read with `html.parser` rather than a
  regex (same reason as `i18n_keys._MarkedElements`: a regex over `<(\\w+)[^>]*>`
  loses nested elements, and it lost four navigation links once already);
* `class="..."`, `class='...'` or ``class=`...` `` inside a string or template
  literal in `web/js/*.js`;
* either branch of a **ternary of literals** inside a `${...}` in such a value,
  so ``class="period-day${isSelected ? ' is-selected' : ''}"`` yields both
  `period-day` and `is-selected`;
* `classList.add` / `.remove` / `.toggle` / `.replace` with a literal argument;
* `className = <literal>` and `className: <literal>`, including a ternary of
  literals, so `{ className: positive ? 'positive' : 'negative' }` yields both;
* `setAttribute('class', <literal>)`;
* the **indirect property names in `_INDIRECT_PROPERTIES`** -- object properties
  that carry a class name to a renderer by reference. Enumerated by name, on
  `i18n_keys._INDIRECT`'s precedent and for its reason: `collapsibleCard`'s
  `extraClass` is how step 3 and step 4 attach `.leaf-panel`, and a scan that
  guessed at indirections rather than naming them would drop them silently the
  day one was renamed. `test_every_indirect_property_is_still_used` fails if one
  stops appearing.

**Comments are stripped before anything is scanned, on both sides**, and that is
not tidiness either. `calculator.js` discusses `.step-card__toggle--static`,
`.allocation-matrix` and `leaf-group` at length in JSDoc, and `styles.css`
discusses `.amount-grid` and `.contribute-action` in prose while declaring
neither. #160's own issue text was written from `grep -c "\\.<class>"` over
`styles.css`, which counts a comment as a rule, and it under-reported the list by
six as a result. See `test_the_css_side_never_reads_a_class_out_of_a_comment`.

WHAT THIS SCAN CANNOT SEE, AND HOW THE GAPS ARE MADE EXPLICIT
-------------------------------------------------------------
A class name that is **computed** is invisible to it: built by concatenation,
returned from a function, held in a variable, or reached through a property this
file does not name. `signClass()` returns `'change-down'`, `'change-up'` or
`'change-none'` and `.change-down` is matched only because the three rules exist
for other reasons; `row(..., 'current-bar')` passes a class as a positional
argument. Those literals are real class names and this scan does not know it.

So the gaps are **enumerated rather than trusted**. Every expression the scan
meets in one of the positions above and cannot reduce to literals is collected,
and `test_every_unresolvable_class_expression_is_declared` requires each one to
appear in `CLASS_EXPRESSIONS_THE_SCAN_CANNOT_RESOLVE` with a reason. A new
dynamic class name therefore fails this file naming the expression, instead of
being absorbed. **That is the difference between this and a guard that would be
trusted while missing the next `.amount-grid--leaf`.**

Two further limits, stated because they are not enforced anywhere:

* "Matched" means the class name appears in **some** selector, not that the
  selector can fire. `.foo .bar` counts as a rule for `bar` even if nothing
  `.bar` is ever inside a `.foo`.
* The reverse direction -- a **rule** for a class nobody emits, which is what
  #152's withdrawn matrix left behind -- is deliberately **not** asserted here.
  Measured on this tree, it reports ten classes and eight of them are the scan's
  own blind spots (`leaf-panel` via `extraClass`, `change-*` via `signClass`,
  `current-bar` / `improved-bar` via a positional argument). A guard whose
  failures are mostly its own gaps gets switched off, and then the direction that
  does work goes with it.

Pure static analysis: no browser, no stack, nothing to batch against the rate
limit. It runs in the default suite.
"""

from __future__ import annotations

import itertools
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web"

#: Object properties that carry a class name to a renderer by reference, by
#: name. See the module docstring: enumerated, never guessed.
_INDIRECT_PROPERTIES = ("extraClass", "valueClass")


# --------------------------------------------------------------------------
# The register. An entry says what the class IS and what would make it bind.
# --------------------------------------------------------------------------

#: Classes the front end emits that no rule in `web/css/` matches, each with the
#: reason it is allowed to.
#:
#: Three kinds, and they are not interchangeable. A **hook** is a second class on
#: an element whose first class already styles it, kept so that a test or a
#: script can address one of several otherwise identical boxes. A **state** is
#: the DOM's own record of something the renderer decided, kept because nothing
#: else in the markup says it. A **namespace** is a scoping class whose siblings
#: are used and which is the one case the scoping does not need. Nothing in here
#: is "probably fine": a class that is none of the three is dead markup and the
#: answer is to delete it.
#:
#: Each reason ends with what would make the class bind, because the next person
#: to read it will be deciding whether to write that rule.
CLASSES_WITH_NO_RULE_OF_THEIR_OWN: dict[str, str] = {
    "amount-grid": (
        "HOOK on `.form-panel`. The single-leaf step-3 panel's wrapper, and the "
        "hook the browser tests use to mean 'the one element wrapping every "
        "step-3 field' -- test_leaf_figure_migration_browser.py reads its "
        "innerText, test_step_navigation.py selects `.amount-grid .zone` and "
        "`.amount-grid .form-field`. Styling it would mean two rules for one box "
        "and the second would silently win; the note over `.zones` in styles.css "
        "says so. Binds if the single-leaf panel ever needs a box the leaf card "
        "does not."
    ),
    "api-error": (
        "HOOK on `.field-error`. The banner the amount and review steps raise "
        "from a failed POST /calculate; `.field-error` already styles it. "
        "test_amount_step_field_errors_browser.py waits on "
        "`.field-error.api-error` and test_duplicate_entry_routing_browser.py "
        "says in prose why it waits on the narrower one. Binds if a server error "
        "must ever look unlike a field error."
    ),
    "calculator-page": (
        "NAMESPACE on <body>. `.home-page`, `.stats-page` and "
        "`.methodology-page` each scope their own page's rules; index.html "
        "carries this one and everything unscoped in styles.css is already the "
        "calculator, so scoping it would be a prefix that excludes nothing. Kept "
        "so the convention reads the same on all four pages. Binds the day a rule "
        "must apply to the calculator and not to the other three."
    ),
    "contribute-toggle__text": (
        "HOOK, the BEM pair of `.contribute-toggle__mark`. The toggle is a "
        "two-child flex row; only the mark has a box to describe and the text "
        "takes the toggle's own font and colour. Named rather than left a bare "
        "<span> because every rule around it addresses `__mark` specifically and "
        "an anonymous sibling is whatever a later `.contribute-toggle span` "
        "sweeps up. Binds if the text needs its own wrapping or flex behaviour."
    ),
    "entry-problem": (
        "HOOK on `.field-error`. The per-entry message on the review step; "
        "test_duplicate_entry_routing_browser.py locates `.entry-problem` to "
        "count flagged entries. Binds if an entry's problem must look unlike a "
        "field's."
    ),
    "introduction-main": (
        "STATE on `.main-content`. `render()` sets it from `state.step === -1`, "
        "and the full-bleed Kale panel the introduction needs is served by "
        "`.main-content:has(.hero)` instead -- both from the one condition, and "
        "the note over that rule in styles.css says why the browser gets the "
        "`:has()`. This is the half a reader of the DOM can see. Binds the day "
        "`:has()` has to be given up, or a rule needs the step rather than the "
        "hero."
    ),
    "is-outside": (
        "STATE, unstyled on purpose, and read by `period.js` rather than by CSS. "
        "A cell from the neighbouring month is drawn empty -- no number, no "
        "`data-day`, no `tabindex` -- so there is nothing on it to style and a "
        "tint would advertise a cell that cannot be chosen. `dayGrid` tests for "
        "the class as a SUBSTRING of the row's own HTML when it decides whether a "
        "sixth week is worth drawing, so renaming it silently restores the empty "
        "row. Binds if those dates are ever shown."
    ),
    "notice-browser": (
        "HOOK inside `.transparency-notice`. The footer's privacy copy is two "
        "sentences about two different stores, and two spans rather than one "
        "because `applyToDocument` assigns textContent and an element can carry "
        "one key. test_i18n_browser.py compares this half against its own "
        "catalogue entry in all twenty languages. Deliberately unstyled: it is "
        "one paragraph to read. Binds if the two halves must look different."
    ),
    "notice-server": (
        "HOOK inside `.transparency-notice`, the other half of the pair above, "
        "on the same terms and for the same test."
    ),
    "period-choices": (
        "HOOK on `.period-grid`. The month and year tables share `.period-grid` "
        "deliberately -- it carries the `min-inline-size: 0` fix a new <table> "
        "would not inherit -- and their cells are `td.period-choice`, so the "
        "table-level class has nothing of its own. It exists so a test can say "
        "WHICH grid is on screen, and test_period_picker_browser.py leans on it a "
        "dozen times. Binds if the choices table must differ from the day table "
        "at the table level."
    ),
    "period-date-field": (
        "HOOK, the named counterpart of `.period-time-field`. "
        "`.period-bound-row .form-field` gives every field in the row "
        "`flex: 1 1 200px` and only the time field is taken off that basis, so a "
        "rule here would restate the default. Named anyway, because "
        "`.period-bound-row > .form-field:first-child` says 'the one on the left' "
        "about a row that wraps. Binds if the date box gets a basis of its own."
    ),
    "restore-notice": (
        "HOOK on `.disclaimer.compact`. The session-restore notice is already the "
        "compact disclaimer in full; the third class is how "
        "test_session_restore_browser.py and test_step_history_browser.py tell it "
        "apart from the other disclaimers on the page -- both hold it as "
        "`NOTICE = '.restore-notice'`. Binds if this one disclaimer must look "
        "unlike the rest."
    ),
    "stats-list-region": (
        "HOOK on the <div> that groups an equivalence breakdown's `sr-only` "
        "heading with its `.stats-breakdown-list`. The list carries the layout and "
        "the heading is visually hidden, so the wrapper has nothing to declare; it "
        "is named because an anonymous <div> between `.stats-breakdown` and the "
        "list is the hardest kind of element to address later. Binds if the region "
        "needs a box -- spacing between several breakdowns, a border, a label made "
        "visible."
    ),
    "step-card--open": (
        "STATE on `.step-card`, and deliberately not styled. Openness is carried "
        "by the body's `hidden` attribute, not by a class (decision 3 of "
        "`collapsibleCard`: a shut card's controls must be genuinely unreachable "
        "by Tab, not merely invisible), and the card's ground does not change when "
        "it opens. Kept because it is the ONLY open-state marker a lone card "
        "carries: a lone card is fixed open, so it has no toggle and therefore no "
        "`aria-expanded`. Binds on any treatment that differs between an open and "
        "a shut card -- a shadow, a border colour, glass only while open."
    ),
    "time-frame-field": (
        "HOOK on `.form-field`. The reporting-period <select> sits in an ordinary "
        "field and wants nothing extra; the class is how test_step_navigation.py "
        "reaches `.time-frame-field > .field-hint` rather than whichever hint is "
        "first on the step. Binds if this field needs a width or a rhythm of its "
        "own."
    ),
}

#: The expressions the scan met in a class position and could not reduce to
#: literals, each with what it hides. **This is the blind-spot list, and it is
#: the reason this file can be trusted at all**: a new computed class name fails
#: `test_every_unresolvable_class_expression_is_declared` naming the expression,
#: rather than being quietly absent from everything above.
#:
#: Keyed `module.js:declaration: expression`, because the same expression in two
#: modules is two decisions -- **and so is the same expression twice in one
#: module.** The module-only key cost this file its first real miss: see
#: `_enclosing_declaration`.
CLASS_EXPRESSIONS_THE_SCAN_CANNOT_RESOLVE: dict[str, str] = {
    "calculator.js:foodStep: extra": (
        "The last parameter of the `choice` arrow declared inside `foodStep`, "
        "defaulting to `''` and appended to `.simple-choice`. Its call sites "
        "pass `'simple-choice--unspecified'` or nothing at all, as positional "
        "arguments, which is a position this scan does not read -- so that class "
        "is matched here only because its rule exists."
    ),
    "calculator.js:collapsibleCard: extraClass": (
        "`collapsibleCard`'s documented escape hatch -- 'classes the consumer's "
        "own selectors need'. The literals its call sites pass ARE seen, because "
        "`extraClass` is named in `_INDIRECT_PROPERTIES`; what remains invisible "
        "is a call site that passes a computed value."
    ),
    "calculator.js:stepFloatingNavigation: extraClass": (
        "**A different `extraClass` from `collapsibleCard`'s, and the reason "
        "this register is keyed by declaration rather than by module** (v1.95). "
        "It is a positional parameter, so the three literals its call sites pass "
        "-- `'item-floating-nav'`, `'amount-floating-nav'`, "
        "`'destination-floating-nav'` -- are invisible to this scan; all three "
        "have rules, matched here only because those rules exist. It also "
        "interpolates `${extraClass}__links` onto the `<ul>`, so it hides three "
        "further names -- `item-floating-nav__links`, `amount-floating-nav__links` "
        "and `destination-floating-nav__links` -- which have **no rules and are "
        "not meant to**: they are the hooks six assertions in "
        "`test_step_two_point_five_browser.py` use to address one step's "
        "navigation rather than whichever one is on screen. Those tests are what "
        "measures them. Binds if a step's navigation needs styling the other "
        "steps' does not."
    ),
    "home.js:element: options.className": (
        "`element()`'s own forwarding of its options object. Every call site's "
        "literal is seen at the call site as `className: '...'`; this is the "
        "helper reading it back."
    ),
    "methodology.js:element: options.className": (
        "`element()` again, same helper copied into this module, same reasoning."
    ),
    "stats.js:element: options.className": (
        "`element()` again, same helper copied into this module, same reasoning."
    ),
    "improvement.js:ComparisonBars: className": (
        "`bar(value, className)`'s positional parameter. Its two call sites pass "
        "`'current-bar'` and `'improved-bar'` as function arguments, which is a "
        "position this scan does not read -- so `.current-bar` and "
        "`.improved-bar` are matched here only because their rules exist, and "
        "deleting those rules would not fail this file. "
        "`test_results_export.py` and the improvement browser tests measure the "
        "bars themselves."
    ),
    "improvement.js:ImpactComparisonCard: change.className": (
        "`metricChange()`'s returned object. It is one of `'neutral'`, "
        "`'positive'` and `'negative'`; the first two are seen at the `className:` "
        "properties that build the object and `'negative'` with them, so all "
        "three are in fact covered -- but by the object literal, not by this "
        "attribute."
    ),
    "improvement.js:ImpactComparisonCard: change.valueClass": (
        "`metricChange()`'s other returned field, which is `signClass()`'s result: "
        "`'change-none'`, `'change-down'` or `'change-up'`, returned from a "
        "function and so invisible. Those three rules exist and this file would "
        "not notice if they went; the arrow glyphs are asserted by the "
        "improvement tests."
    ),
    "calculator.js:amountStep: state.errorCode ? `error-${slug(state.errorCode)}` : ''": (
        "**One class per API error code, and this one is open-ended on purpose: "
        "it cannot be enumerated here, because the codes come from §6's error "
        "vocabulary and the set grows with the contract.** `error-` plus a slug, "
        "beside `.api-error` on the banner. Measured on this tree: `web/css/` "
        "carries no `.error-<anything>` rule and no test in `tests/` selects one, "
        "so the whole family is a diagnostic marker -- what the DOM says about "
        "WHICH error -- and nothing depends on it. It is declared rather than "
        "deleted because `calculator.js` is not this change's to edit (#160); it "
        "is reported to that file's owner as the one item here that may simply be "
        "removable."
    ),
    "calculator.js:reviewStep: state.errorCode ? `error-${slug(state.errorCode)}` : ''": (
        "The same open-ended `error-<code>` family on the review step's banner. "
        "Same vocabulary, same measurement and same conclusion as `amountStep`'s "
        "entry above; listed separately because it is a separate site, which is "
        "what keying by declaration means."
    ),
    "improvement.js:ComparisonSummary: change.className": (
        "The same `metricChange()` object, read by the other renderer on the same "
        "screen. Two sites, two entries: the reason happens to be identical here, "
        "and that is worth being able to see rather than having to assume."
    ),
    "improvement.js:ComparisonSummary: change.valueClass": (
        "`signClass()`'s result in that same other renderer, as above."
    ),
    "results.js:summaryCards: negativeClass(total)": (
        "Returns `' value-negative'` or `''`, appended to `result-value`. Both "
        "`.result-value` and `.value-negative` have rules and are matched through "
        "other positions; the orientation of a negative result figure is measured "
        "by `test_results_export.py` rather than here."
    ),
}


# --------------------------------------------------------------------------
# The scan.
# --------------------------------------------------------------------------

_JS_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_JS_LINE_COMMENT = re.compile(r"^\s*//.*$", re.M)
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)
#: Strings inside CSS, removed before class names are read: `[href=".x"]` and
#: `content: '.'` are not selectors.
_CSS_STRING = re.compile(r"'[^'\n]*'|\"[^\"\n]*\"")
#: A class in a selector. The leading character cannot be a digit, so `1.5rem`
#: and `rgba(0, 50, 35, 0.09)` are not classes.
_CSS_CLASS = re.compile(r"\.(-?[A-Za-z_][A-Za-z0-9_-]*)")
#: A complete class name. Anything else in a class value is a fragment.
_CLASS_TOKEN = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")

_CLASS_ATTR = re.compile(r"class\s*=\s*([\"'`])")
_CLASS_LIST = re.compile(r"\.classList\.(?:add|remove|toggle|replace)\s*\(")
_CLASS_NAME = re.compile(r"\bclassName\s*[:=]\s*")
_SET_ATTRIBUTE = re.compile(r"\.setAttribute\s*\(\s*(['\"])class\1\s*,\s*")
_QUOTES = "\"'`"

#: Stands in for a value the scan could not work out. A control character, so no
#: real class name can collide with it.
UNKNOWN = "\x01"


def strip_js_comments(text: str) -> str:
    """Comments out, before anything is read for a class name.

    `i18n_keys._strip_comments`' reason, in the other dimension: a mutation that
    deleted the real `t()` call survived the whole suite because the same string
    appeared in a JSDoc block. `calculator.js` names `.step-card__toggle--static`
    twice in prose and `.allocation-matrix` four times; a scan of the raw text
    would report all of them as emitted and this file would then be asserting
    that the comments are consistent.
    """
    return _JS_LINE_COMMENT.sub("", _JS_BLOCK_COMMENT.sub("", text))


def _read_string(text: str, index: int) -> tuple[str, int]:
    """The literal whose opening quote is at `text[index]`: (body, end)."""
    quote = text[index]
    out: list[str] = []
    position = index + 1
    while position < len(text):
        character = text[position]
        if character == "\\":
            out.append(text[position:position + 2])
            position += 2
            continue
        if character == quote:
            return "".join(out), position + 1
        if quote == "`" and character == "$" and text[position + 1:position + 2] == "{":
            inner, position = _read_interpolation(text, position + 1)
            out.append("${" + inner + "}")
            continue
        out.append(character)
        position += 1
    raise ValueError(f"unterminated string literal at offset {index}")


def _read_interpolation(text: str, index: int) -> tuple[str, int]:
    """The `${...}` whose `{` is at `text[index]`: (inner, end).

    Brace-matched and string-aware, because a `${}` can hold a nested template
    with its own `${}` -- ``${code ? `error-${slug(code)}` : ''}`` is in
    `calculator.js` and a non-nesting pattern reads it as ending at the inner
    brace.
    """
    depth = 0
    position = index
    out: list[str] = []
    while position < len(text):
        character = text[position]
        if character in _QUOTES:
            body, position = _read_string(text, position)
            out.append(character + body + character)
            continue
        if character == "{":
            depth += 1
            if depth > 1:
                out.append(character)
            position += 1
            continue
        if character == "}":
            depth -= 1
            if depth == 0:
                return "".join(out), position + 1
            out.append(character)
            position += 1
            continue
        out.append(character)
        position += 1
    raise ValueError(f"unterminated interpolation at offset {index}")


def _read_expression(text: str, index: int) -> str:
    """One expression starting at `text[index]`, to the next top-level break.

    For `className:` and `className =` where the value is not a literal. Stops
    at a depth-zero `,`, `)`, `}`, `;` or newline, so
    `{ className: positive ? 'positive' : 'negative', valueClass, text: ... }`
    yields the ternary and not the rest of the object.
    """
    depth = 0
    position = index
    while position < len(text):
        character = text[position]
        if character in _QUOTES:
            _, position = _read_string(text, position)
            continue
        if character in "([{":
            depth += 1
        elif character in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and character in ",;\n":
            break
        position += 1
    return text[index:position].strip()


def _split_ternary(expression: str) -> tuple[str, str] | None:
    """`cond ? a : b` at depth zero, as (a, b), or None if it is not one."""
    depth = 0
    question = colon = -1
    position = 0
    while position < len(expression):
        character = expression[position]
        if character in _QUOTES:
            _, position = _read_string(expression, position)
            continue
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
        elif depth == 0 and character == "?":
            #: `??` and `?.` are not a conditional.
            if expression[position + 1:position + 2] in ("?", "."):
                position += 2
                continue
            if question < 0:
                question = position
        elif depth == 0 and character == ":" and question >= 0 and colon < 0:
            colon = position
        position += 1
    if question < 0 or colon < 0:
        return None
    return expression[question + 1:colon].strip(), expression[colon + 1:].strip()


def _resolve(expression: str) -> set[str]:
    """Every literal string `expression` can produce, or `{UNKNOWN}`."""
    expression = expression.strip()
    if not expression:
        return {""}
    if expression[0] in _QUOTES:
        try:
            body, end = _read_string(expression, 0)
        except ValueError:
            return {UNKNOWN}
        if expression[end:].strip():
            return {UNKNOWN}
        return _expand(body)
    branches = _split_ternary(expression)
    if branches is None:
        return {UNKNOWN}
    return _resolve(branches[0]) | _resolve(branches[1])


def _pieces(value: str) -> list[tuple[str, str]]:
    """A template body as alternating ('text', s) and ('expr', s) pieces."""
    out: list[tuple[str, str]] = []
    position = 0
    while position < len(value):
        if value[position] == "$" and value[position + 1:position + 2] == "{":
            inner, position = _read_interpolation(value, position + 1)
            out.append(("expr", inner))
            continue
        start = position
        while position < len(value) and not (
            value[position] == "$" and value[position + 1:position + 2] == "{"
        ):
            position += 1
        out.append(("text", value[start:position]))
    return out


def _expand(value: str) -> set[str]:
    """Every string a template body can produce, UNKNOWN standing in for the rest."""
    options = [
        {body} if kind == "text" else _resolve(body)
        for kind, body in _pieces(value)
    ]
    if not options:
        return {""}
    return {"".join(parts) for parts in itertools.product(*options)}


def classes_in_value(value: str) -> tuple[set[str], list[str]]:
    """(the class names in one class value, the expressions that defeated it).

    A token that TOUCHES an unknown is a fragment, not a class: `error-` in
    ``error-${slug(code)}`` is half a name and reporting it as a class would be
    worse than reporting nothing. A token that does not touch one is real, which
    is what makes ``period-day${isSelected ? ' is-selected' : ''}`` yield
    `period-day` even when the interpolation is opaque.
    """
    classes: set[str] = set()
    candidates = _expand(value)
    for candidate in candidates:
        for token in candidate.split():
            if UNKNOWN in token:
                continue
            if _CLASS_TOKEN.match(token):
                classes.add(token)
    unresolved: list[str] = []
    if any(UNKNOWN in candidate for candidate in candidates):
        for kind, body in _pieces(value):
            #: Substring, not membership. `_resolve` returns the exact set
            #: `{UNKNOWN}` for something it cannot read at all, but a nested
            #: template gives `{'error-\x01', ''}` -- partly known -- and a
            #: membership test calls that resolved. It did, while this file was
            #: being written, and it hid the `error-${slug(code)}` case from the
            #: blind-spot register entirely.
            if kind == "expr" and any(UNKNOWN in option for option in _resolve(body)):
                unresolved.append(body.strip())
    return classes, unresolved


class _HtmlClasses(HTMLParser):
    """The `class` attributes in one HTML file, parsed rather than matched."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.classes: set[str] = set()

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name == "class" and value:
                self.classes.update(value.split())

    handle_startendtag = handle_starttag


def html_files() -> list[Path]:
    return sorted(WEB.glob("*.html"))


def script_files() -> list[Path]:
    """`web/js/` only. `web/vendor/` is Chart.js, vendored and not ours to audit."""
    return sorted((WEB / "js").glob("*.js"))


def stylesheets() -> list[Path]:
    return sorted((WEB / "css").glob("*.css"))


#: A top-level declaration in a front-end module: `function name(`,
#: `async function name(` or `const name =`, anchored at column 0.
_TOP_LEVEL_DECLARATION = re.compile(r"^(?:async\s+)?function\s+(\w+)|^const\s+(\w+)\s*=", re.M)


def _enclosing_declaration(source: str, position: int) -> str:
    """The nearest top-level declaration above `position`, or `<module>`.

    **A locator, not a scope analysis**, and the register below is keyed on it.
    Its whole job is to tell two class positions in one module apart, so that a
    second site cannot inherit a reason written for the first -- which is what
    happened: v1.94's `stepFloatingNavigation` interpolates an `extraClass` of
    its own, the key `calculator.js: extraClass` was already held by
    `collapsibleCard`, and three class names reached the DOM behind a reason
    written about a different function. Nothing failed.

    It reports the nearest *top-level* name, so a parameter of a helper declared
    inside a function is attributed to that function: `extra` is a parameter of
    a `choice` arrow inside `foodStep`, and `className` one of a `bar` arrow
    inside `ComparisonBars`. That is the honest answer for a locator -- neither
    helper is addressable from outside the function it lives in.

    A class value sitting in a module-level constant *after* a function would be
    attributed to that function, which is wrong in principle and absent from
    this tree. If one ever appears, the two tests on the register fail naming
    the key rather than absorbing it, because the key is what changes.
    """
    found = "<module>"
    for match in _TOP_LEVEL_DECLARATION.finditer(source, 0, position):
        found = match.group(1) or match.group(2)
    return found


def _value_positions(source: str):
    """Every (offset, how) at which a class value begins, in one script."""
    for match in _CLASS_ATTR.finditer(source):
        yield match.end() - 1, "attribute"
    for pattern, how in (
        (_CLASS_LIST, "classList"),
        (_CLASS_NAME, "className"),
        (_SET_ATTRIBUTE, "setAttribute"),
    ):
        for match in pattern.finditer(source):
            position = match.end()
            while position < len(source) and source[position].isspace():
                position += 1
            yield position, how
    for name in _INDIRECT_PROPERTIES:
        for match in re.finditer(rf"\b{re.escape(name)}\s*:\s*", source):
            position = match.end()
            while position < len(source) and source[position].isspace():
                position += 1
            yield position, f"indirect:{name}"


def scan_front_end() -> tuple[set[str], dict[str, None]]:
    """(every class name the front end emits, the expressions that defeated it)."""
    classes: set[str] = set()
    unresolved: dict[str, None] = {}
    for path in html_files():
        parser = _HtmlClasses()
        parser.feed(path.read_text(encoding="utf-8"))
        classes |= parser.classes
    for path in script_files():
        source = strip_js_comments(path.read_text(encoding="utf-8"))
        for position, _how in _value_positions(source):
            if position < len(source) and source[position] in _QUOTES:
                value, _ = _read_string(source, position)
            else:
                value = "${" + _read_expression(source, position) + "}"
            found, rest = classes_in_value(value)
            classes |= found
            for expression in rest:
                where = _enclosing_declaration(source, position)
                unresolved[f"{path.name}:{where}: {expression}"] = None
    return classes, unresolved


def css_class_names() -> set[str]:
    """Every class name named in a selector in `web/css/`, comments excluded."""
    found: set[str] = set()
    for path in stylesheets():
        text = _CSS_COMMENT.sub(" ", path.read_text(encoding="utf-8"))
        text = _CSS_STRING.sub(" ", text)
        found |= set(_CSS_CLASS.findall(text))
    return found


EMITTED, UNRESOLVED = scan_front_end()
IN_CSS = css_class_names()


# --------------------------------------------------------------------------
# The gate.
# --------------------------------------------------------------------------

def test_every_class_the_front_end_emits_has_a_rule_or_a_declared_reason():
    """**The gate.** #160 is twenty classes that would have failed this.

    Mutation, and it is required rather than optional: add a class to any
    element in `web/*.html` or to any `class="..."` in `web/js/`, do not write a
    rule for it, and this fails naming it. Measured on this tree.
    """
    offenders = sorted(EMITTED - IN_CSS - set(CLASSES_WITH_NO_RULE_OF_THEIR_OWN))
    assert not offenders, (
        f"{len(offenders)} class name(s) are emitted by web/ and matched by no "
        f"rule in web/css/: {offenders}.\n"
        "Each one is a rule somebody meant to write, dead markup, or a hook. If "
        "it is a rule, write it. If it is dead, delete it from the markup. If it "
        "is a hook or a state, declare it in CLASSES_WITH_NO_RULE_OF_THEIR_OWN "
        "with a reason saying what it is and what would make it bind -- and put a "
        "one-line comment beside the rule it leans on, so the next person reading "
        "the stylesheet finds it there too. `.amount-grid--leaf` is what happens "
        "when none of those is done."
    )


def test_every_declared_exemption_carries_a_reason():
    """An exemption without a reason is a convention, and a convention rots.

    Mutation: add a class to `CLASSES_WITH_NO_RULE_OF_THEIR_OWN` with `''`, or
    with a line of placeholder text, and this fails naming it -- which is what
    stops the register becoming a list of names somebody added to get a green
    run. The length floor is 60 characters: the shortest real reason in here is
    `notice-server`'s, which is two clauses and 110, and nothing shorter than 60
    can say both what the class is and what would make it bind.
    """
    thin = {
        name: reason
        for name, reason in CLASSES_WITH_NO_RULE_OF_THEIR_OWN.items()
        if len(" ".join(reason.split())) < 60
    }
    assert not thin, (
        f"these exemptions do not carry a reason: {sorted(thin)}. An entry has to "
        "say what the class IS -- a hook, a state, a namespace -- and what would "
        "make it bind."
    )
    placeholders = {
        name
        for name, reason in CLASSES_WITH_NO_RULE_OF_THEIR_OWN.items()
        if re.search(r"\b(TODO|FIXME|XXX|tbd|unknown|not sure|for now)\b", reason, re.I)
    }
    assert not placeholders, (
        f"these exemptions say they are unfinished: {sorted(placeholders)}. An "
        "exemption that nobody has decided about is not an exemption."
    )


def test_every_declared_exemption_still_needs_one():
    """The register cannot outlive the reason it was written for.

    `IDENTICAL_BY_DESIGN`'s property, in this dimension. Two ways an entry stops
    earning itself and both are failures here: the class is given a rule (so the
    exemption now hides a rule nobody is checking against the comment beside it),
    or the class stops being emitted at all (so the entry documents markup that
    no longer exists -- which is exactly the state #152 left the matrix's CSS in).

    Mutation: write `.api-error { color: red }` and this fails naming
    `api-error`.
    """
    now_styled = sorted(set(CLASSES_WITH_NO_RULE_OF_THEIR_OWN) & IN_CSS)
    assert not now_styled, (
        f"{now_styled} now have rules in web/css/, so they are not exempt from "
        "anything and must leave CLASSES_WITH_NO_RULE_OF_THEIR_OWN. Check the "
        "comment beside the new rule says what the class is for."
    )
    not_emitted = sorted(set(CLASSES_WITH_NO_RULE_OF_THEIR_OWN) - EMITTED)
    assert not not_emitted, (
        f"{not_emitted} are declared here and the front end no longer emits them. "
        "Delete the entry -- and if the class was a hook, check the test that "
        "used it still measures what it measured."
    )


def test_every_unresolvable_class_expression_is_declared():
    """**The blind spots are enumerated, not trusted.**

    A guard that silently misses the next `.amount-grid--leaf` is worse than no
    guard, because it will be trusted. This scan reads static literals; a class
    name that is computed is invisible to it. So every expression it met in a
    class position and could not reduce is listed with what it hides, and a new
    one fails here rather than disappearing.

    Mutation: write `class="${someFunction(x)}"` anywhere in `web/js/` and this
    fails naming `someFunction(x)`.
    """
    undeclared = sorted(set(UNRESOLVED) - set(CLASS_EXPRESSIONS_THE_SCAN_CANNOT_RESOLVE))
    assert not undeclared, (
        f"{len(undeclared)} class expression(s) this scan cannot resolve are not "
        f"declared: {undeclared}.\n"
        "A computed class name is a class name the gate above cannot see. Either "
        "write the class as a literal so it becomes visible, or declare the "
        "expression in CLASS_EXPRESSIONS_THE_SCAN_CANNOT_RESOLVE saying which "
        "class names it hides and what measures them instead."
    )


def test_every_declared_blind_spot_is_still_one():
    """And the blind-spot list cannot outlive its code either.

    An expression listed here that the scan no longer meets is a note about code
    that has been rewritten -- and worse, it is a reader being told a gap exists
    where one does not.
    """
    stale = sorted(set(CLASS_EXPRESSIONS_THE_SCAN_CANNOT_RESOLVE) - set(UNRESOLVED))
    assert not stale, (
        f"{stale} are declared as expressions this scan cannot resolve and it no "
        "longer meets them. Delete the entries."
    )


@pytest.mark.parametrize("name", _INDIRECT_PROPERTIES)
def test_every_indirect_property_is_still_used(name):
    """A renamed indirection must fail loudly here, not drop its classes.

    `i18n_keys._INDIRECT`'s assertion, for the same failure: these property
    names are the only reason `extraClass: 'leaf-panel'` is seen at all, and a
    rename would make `.leaf-panel` invisible to the gate with nothing saying so.
    """
    sources = "".join(
        strip_js_comments(path.read_text(encoding="utf-8")) for path in script_files()
    )
    assert re.search(rf"\b{re.escape(name)}\s*:", sources), (
        f"`{name}` is named in _INDIRECT_PROPERTIES and no longer appears as an "
        "object property in web/js/. If it was renamed, rename it here; if the "
        "indirection is gone, delete it here."
    )


# --------------------------------------------------------------------------
# The scan, measured rather than believed.
# --------------------------------------------------------------------------

def test_the_css_side_never_reads_a_class_out_of_a_comment():
    """**#160's own issue text got this wrong, and it cost six classes.**

    The issue was written from `grep -c "\\.<class>"` over `styles.css`, which
    counts a class named in a COMMENT as a rule. `styles.css` discusses
    `.amount-grid` at length over `.zones` and `.contribute-action` over the
    contribute slot, and declares neither -- so both read as styled and neither
    reached the list. This is the fix asserted rather than assumed.
    """
    raw = "".join(path.read_text(encoding="utf-8") for path in stylesheets())
    for name in ("amount-grid", "contribute-action"):
        assert f".{name}" in raw, (
            f"this test is pinned on `.{name}` being discussed in a comment in "
            "web/css/ and declared nowhere. It is not mentioned at all any more, "
            "so pick another example or delete this test -- do not delete the "
            "assertion below."
        )
        assert name not in css_class_names(), (
            f"`{name}` is being read out of a comment as though it were a rule. "
            "That is the defect that under-reported #160 by six classes."
        )


def test_the_scan_sees_every_mechanism_it_claims_to():
    """Each way the front end names a class, with a case that exercises it.

    A scan whose regexes stopped matching would return a small set, every
    difference above would be empty, and the whole file would pass while
    measuring nothing. These are real emissions on this tree, one per mechanism,
    and they fail naming the mechanism rather than the class.
    """
    cases = {
        "a class attribute in web/*.html": "transparency-notice",
        "a class attribute in a template literal": "step-card__toggle",
        "a ternary of literals inside ${...}": "step-card--open",
        "a prefix beside an opaque interpolation": "period-day",
        "the second branch of such a ternary": "is-selected",
        "classList.add with a literal": "is-dragging",
        "className = with a literal": "language-bar",
        "className: in an options object": "stats-list-region",
        "setAttribute('class', ...)": "language-bar__globe",
        "an indirect property named in _INDIRECT_PROPERTIES": "leaf-panel",
    }
    missed = {how: name for how, name in cases.items() if name not in EMITTED}
    assert not missed, (
        f"the scan no longer sees: {missed}. Each entry is one of the mechanisms "
        "the module docstring claims to read; a missing one means the gate is "
        "blind to a whole class of emissions, not that this class moved."
    )


def test_the_scan_is_not_quietly_empty():
    """A floor under both sides, because an empty set passes every test above.

    PR #151's round found a mutation that appeared to survive because a
    PowerShell write had corrupted the stylesheet and the served file parsed to
    70 rules instead of 647 -- nothing was mutated because nothing was there.
    The same failure here is a changed path or a renamed directory, and it would
    be silent. Round numbers well under the real counts (321 emitted, 319 in CSS
    when this was written): this is a floor, not a pin, so ordinary work does not
    move it.
    """
    assert len(html_files()) >= 4, f"only {len(html_files())} HTML files found in {WEB}"
    assert len(script_files()) >= 15, f"only {len(script_files())} scripts found in {WEB / 'js'}"
    assert len(stylesheets()) >= 1, f"no stylesheet found in {WEB / 'css'}"
    assert len(EMITTED) >= 250, f"the scan found only {len(EMITTED)} emitted classes"
    assert len(IN_CSS) >= 250, f"only {len(IN_CSS)} class names found in web/css/"


def test_a_fragment_beside_an_interpolation_is_not_reported_as_a_class():
    """The one judgement in the scanner, with the cases on each side of it.

    `error-${slug(code)}` is half a name. Reporting `error-` as an emitted class
    would put a fragment in the gate's failure message and send the reader
    looking for a rule that can never exist; reporting nothing for
    `period-day${isSelected ? ' is-selected' : ''}` would hide two real classes.
    The rule is adjacency, and these are the shapes that test it.
    """
    opaque, unresolved = classes_in_value("field-error api-error ${code ? `error-${slug(code)}` : ''}")
    assert opaque == {"field-error", "api-error"}, opaque
    assert unresolved == ["code ? `error-${slug(code)}` : ''"], unresolved

    both, nothing = classes_in_value("period-day${isSelected ? ' is-selected' : ''}")
    assert both == {"period-day", "is-selected"}, both
    assert nothing == [], nothing

    joined, opaque_tail = classes_in_value("result-value${negativeClass(total)}")
    assert joined == set(), (
        f"`result-value` touches an opaque interpolation, so it is a prefix as "
        f"far as this scan can tell: {joined}"
    )
    assert opaque_tail == ["negativeClass(total)"], opaque_tail

    nested, _ = classes_in_value("a ${flag ? 'b' : `c-${n}`} d")
    assert nested == {"a", "b", "d"}, nested
