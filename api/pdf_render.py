"""The export document's renderer. HTML and CSS in, PDF bytes out.

**Nothing in this module lays anything out, and that is the whole point.**

The export this replaces was four hundred lines of hand-rolled PDF written in
the browser, and its review found three blocking defects: a German title
printed 84pt off a 595pt page because nothing wrapped or clamped it; Arabic
rendered left-to-right with the full stop stranded at the start of the line,
because ``direction`` and ``textAlign`` were never set; and one non-WinAnsi
character in a staff-typed name -- ``Kūmara`` -- either turned the whole
document into a 71 KB image with no extractable text, or silently became
``K?mara``, which is a corrupted export of staff data.

All three are line-breaking, bidirectional-ordering and shaping problems, and
all three are solved by *not solving them here*. WeasyPrint is handed HTML and
CSS; Pango and HarfBuzz do the shaping, the bidi algorithm and the line
breaking. **A text-measuring routine in Python would be the same mistake in a
different language.** There is therefore no width, no font-metric table and no
character budget anywhere in this file, and if a layout problem appears the fix
belongs in ``api/templates/results.css``.

**No arithmetic either.** Section 7.6.1 leaves the front end no calculation but
unit conversion, and the server does not grow a second summation to compensate:
every figure below is read off the engine's own ``CalculationResult`` and
formatted for display. ``_figure`` quantises a ``Decimal`` to the metric's own
``display_precision`` and inserts thousands separators -- the same two things
``web/js/view.js::formatNumber`` does for the screen, so the paper and the page
agree -- and it never converts to ``float``. Section 1.2 puts decimals on the
wire as strings precisely because a double cannot hold them.

**WeasyPrint is imported lazily, inside the render call.** It needs Pango,
HarfBuzz and GObject as native libraries, and on a bare Windows checkout the
import raises ``OSError: cannot load library 'libgobject-2.0-0'``. A
module-level import would therefore take the whole of ``api/`` down on a
developer machine that has never rendered a PDF -- ``api/router.py`` imports
``api/export.py`` imports this -- and every unrelated API test with it. The
import cost is paid once per process by Python's module cache.
``weasyprint_unavailable_reason()`` is the single place anything asks whether
this host can render, so a test never has to guess.

**Nothing this renderer draws is fetched.** Contract 7.6 rule 7 forbids a
runtime asset from a third-party host, and that binds a server-rendered
document at least as hard as it binds a page -- the request would come from
inside the API container, where nobody would see it. The two brand faces are
embedded from a copy that ships inside this package, and ``_local_url_fetcher``
below refuses every URL that is not one of them, so the rule is enforced rather
than remembered. The twelve script fallback faces added for the twenty
languages are embedded the same way, from ``api/assets/fonts/noto/``.
"""

from __future__ import annotations

import mimetypes
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from api import i18n
from engine.types import DATA_COMPLETE, DATA_INCOMPLETE, DATA_UNDEFINED

# --------------------------------------------------------------------------
# Where the document's own files live.
# --------------------------------------------------------------------------
#
# THE FONTS ARE A THIRD COPY IN THIS TREE, AND THEY HAVE TO BE. The brand
# faces already sit under `web/assets/fonts/` (served by nginx) and
# `admin/static/fonts/` (shipped as the admin package's own package-data). This
# renderer can use neither: `api/` may not import `admin/`, and more decisively
# setuptools' package-data cannot reach outside its own package directory -
# pyproject.toml records that reasoning under [tool.setuptools.data-files]
# already - so a wheel built without a copy under `api/` has no fonts at all.
# `docker/api.Dockerfile` copies only `admin/ api/ db/ engine/` into the build
# context, which closes the same door a second time. A byte-identity test
# (`tests/api/test_pdf_render.py`) fails if this copy drifts from `web/`'s.
_PACKAGE_DIR = Path(__file__).resolve().parent
_TEMPLATE_DIR = _PACKAGE_DIR / "templates"
_ASSET_DIR = _PACKAGE_DIR / "assets"
_FONT_DIR = _ASSET_DIR / "fonts"

_TEMPLATE_NAME = "results.html.j2"
_STYLESHEET = _TEMPLATE_DIR / "results.css"

#: The substitution points in `results.css`. The stylesheet is handed to
#: WeasyPrint as a string rather than as a path, so a relative `url()` in it
#: has no base to resolve against; these become absolute `file://` URLs.
#:
#: THE SCRIPT FALLBACK FACES ARE NOT OPTIONAL EXTRAS. Both brand faces are
#: 228-glyph Latin subsets; the interface ships in twenty languages across
#: eleven scripts, and neither brand face carries one glyph of Arabic,
#: Devanagari, Gurmukhi, Gujarati, Tamil, Malayalam, Thai, kana, Hangul or Han.
#: These are what `results.css` names, so the document is set in the same type
#: on a checkout, in CI and in the container - `docker/api.Dockerfile`'s
#: `fonts-noto-core` is a safety net for whatever Debian happens to ship, not
#: the thing the stylesheet asks for. SIL OFL 1.1; licences and provenance sit
#: beside the files in `api/assets/fonts/noto/`.
_FONT_PLACEHOLDERS = {
    "KAICALC_FONT_SRC_GEOLOGICA": "geologica-bold.woff2",
    "KAICALC_FONT_SRC_KUMBH": "kumbh-sans-regular.woff2",
    "KAICALC_FONT_SRC_NOTO_SANS": "noto/NotoSans-Regular.woff2",
    "KAICALC_FONT_SRC_NOTO_ARABIC": "noto/NotoSansArabic-Regular.woff2",
    "KAICALC_FONT_SRC_NOTO_DEVANAGARI": "noto/NotoSansDevanagari-Regular.woff2",
    "KAICALC_FONT_SRC_NOTO_GURMUKHI": "noto/NotoSansGurmukhi-Regular.woff2",
    "KAICALC_FONT_SRC_NOTO_GUJARATI": "noto/NotoSansGujarati-Regular.woff2",
    "KAICALC_FONT_SRC_NOTO_TAMIL": "noto/NotoSansTamil-Regular.woff2",
    "KAICALC_FONT_SRC_NOTO_MALAYALAM": "noto/NotoSansMalayalam-Regular.woff2",
    "KAICALC_FONT_SRC_NOTO_THAI": "noto/NotoSansThai-Regular.woff2",
    "KAICALC_FONT_SRC_NOTO_CJK_JP": "noto/NotoSansCJKjp-Regular.woff2",
    "KAICALC_FONT_SRC_NOTO_CJK_KR": "noto/NotoSansCJKkr-Regular.woff2",
    "KAICALC_FONT_SRC_NOTO_CJK_SC": "noto/NotoSansCJKsc-Regular.woff2",
    "KAICALC_FONT_SRC_NOTO_CJK_TC": "noto/NotoSansCJKtc-Regular.woff2",
}


# --------------------------------------------------------------------------
# The placeholder warning.
# --------------------------------------------------------------------------
#
# Section 2.2: the placeholder-data warning is MANDATORY AND NON-DISMISSIBLE on
# every results view and export while the active factor set has `is_mock`. Open
# item O-1 - the client has supplied no real emissions factors - means that is
# true of every calculation this system can currently perform, so every figure
# this document can carry today is placeholder data.
#
# A PDF is the artefact most likely to be read months later, elsewhere, by
# someone who was not in the room and cannot ask. PR #46's warning survived
# review only because it happened to reuse `buildResultsReport()` - the
# inheritance was the mechanism, not a decision - and the reviewer's finding was
# that a warning which survives by accident will one day not.
#
# So it is held in place three ways here, none of which is a comment:
#
#   1. `render_results_pdf` reads `result.is_mock` unconditionally. There is no
#      parameter, keyword or environment variable that suppresses it.
#   2. `_assert_mock_warning_present` re-reads the RENDERED HTML and raises
#      before WeasyPrint is ever called. Deleting the block from the template
#      turns the export into a 500, not into a quiet document.
#   3. `results.css` feeds the flag into the running header of every page via
#      `string-set`, so page three carries it as well as page one.
#
# **BOTH ARE CATALOGUE KEYS, NOT COPY WRITTEN HERE**, and that is what makes
# §2.2 hold in twenty languages rather than one. `web/js/results.js` renders
# the same two strings into the on-screen banner, so the paper and the page
# carry the same warning in the same words - and a Tamil reader gets the
# warning in Tamil, which is the only version of "non-dismissible" that means
# anything to them. `api/i18n.py::Catalogue.gettext` raises rather than falling
# back to English, so a catalogue that lost either string fails the render
# instead of quietly downgrading the warning to a language its reader may not
# have.
#
# The literals below are the ENGLISH SOURCE (the key); what (2) compares
# against is the translation for the document's own language, HTML-escaped the
# way Jinja escaped it - several translations contain an apostrophe, and a
# comparison against the unescaped form would fail on French and Italian for a
# reason that has nothing to do with the warning being present.
MOCK_WARNING_FLAG = "Placeholder data"
MOCK_WARNING_BODY = (
    "Demonstration only — verified calculation factors have not yet been "
    "supplied. Final results will depend on factors supplied and approved by "
    "Kai Commitment."
)


class MockWarningMissingError(RuntimeError):
    """Raised when a mock-factor document rendered without its warning.

    This is a programming error in the template, not a bad request, and it is
    deliberately loud: a placeholder document that does not say so is worse
    than no document, because it is the one that gets forwarded.
    """


# --------------------------------------------------------------------------
# Locale.
# --------------------------------------------------------------------------
#
# Task 4 makes Arabic and Urdu work properly - catalogues, digit shaping, the
# lot. What lives here is the one thing the layout engine needs before any of
# that: the base direction of the document. Setting `dir` on <html> is what
# makes Pango run the Unicode bidirectional algorithm in the right base
# context, which is the difference between a sentence that ends with a full
# stop and one that starts with it - defect two, in a single attribute.
#
# By the language subtag, and by the script subtag when one is written. This is
# a base direction, not a translation: an untranslated English string in an
# `ar` document still renders left-to-right inside a right-to-left page,
# because the bidi algorithm handles the run, not this table.
_RTL_LANGUAGES = frozenset(
    {"ar", "arc", "ckb", "dv", "fa", "he", "ku", "ps", "sd", "ug", "ur", "yi"}
)
_RTL_SCRIPTS = frozenset({"arab", "hebr", "thaa", "syrc", "nkoo", "adlm"})


def text_direction(locale: str) -> str:
    """`"rtl"` or `"ltr"` for a BCP-47-ish tag. Never raises: an unparseable
    tag is left-to-right, which is the safe default for a document whose
    figures are digits either way."""
    parts = [part.lower() for part in str(locale or "").replace("_", "-").split("-")]
    if not parts:
        return "ltr"
    if parts[0] in _RTL_LANGUAGES:
        return "rtl"
    if any(part in _RTL_SCRIPTS for part in parts[1:]):
        return "rtl"
    return "ltr"


# --------------------------------------------------------------------------
# Formatting. No arithmetic on a figure; no `float`, ever.
# --------------------------------------------------------------------------

#: What a figure the engine did not produce prints as. An en dash, not "0" and
#: not "": a metric with no alternative scenario has no value, and zero is a
#: claim about the world (`MoneyResult`'s docstring makes the same point).
ABSENT = "–"


def _figure(value: Any, precision: int = 2) -> str:
    """A `Decimal` at a metric's own `display_precision`, comma-grouped.

    The two operations `web/js/view.js::formatNumber` performs for the screen,
    performed here for the paper so that the same calculation does not read as
    two different numbers in two places.

    `ROUND_HALF_UP` on the `Decimal` itself. The value arrives as a `Decimal`
    (or, from a fixture, as its string form); `float(value)` would be the one
    prohibited conversion in this codebase and it is not performed anywhere in
    this module. Grouping is done over the digit string with `int()` on the
    integer part only, which is exact at any size.

    `precision` is clamped to 0..20 because it comes from a database row
    (section 2.1 `metric.display_precision`) rather than from this file, and an
    out-of-range value would take out the whole render rather than one figure -
    the same clamp, and the same reason, as the front end's.
    """
    if value is None:
        return ABSENT
    number = value if isinstance(value, Decimal) else Decimal(str(value))
    try:
        digits = max(0, min(int(precision), 20))
    except (TypeError, ValueError):
        digits = 2
    quantised = number.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    sign = "-" if quantised < 0 else ""
    integer, _, fraction = format(abs(quantised), "f").partition(".")
    grouped = f"{int(integer):,}"
    return f"{sign}{grouped}.{fraction}" if fraction else f"{sign}{grouped}"


# --------------------------------------------------------------------------
# Taxonomy lookup.
# --------------------------------------------------------------------------
#
# `code` is the cross-layer identifier and the only thing the engine's result
# carries; the human name lives in the taxonomy. NAMES ARE NEVER TRANSLATED -
# sector, destination, food-category and metric names are staff-typed rows, and
# a document that translated them would be putting words in the client's mouth.
# They are printed exactly as typed, in every locale.


def _names(rows: Any, attribute: str = "name") -> dict[str, str]:
    return {row.code: getattr(row, attribute) for row in (rows or ())}


class _Taxonomy:
    """Code-to-name maps, tolerant of a code the snapshot does not carry.

    An unknown code prints as itself rather than raising. A taxonomy row can be
    deactivated after a factor set was published, and a document that 500s
    because one destination was retired is worse than one that names it by its
    code - the figures beside it are still the engine's.
    """

    def __init__(self, taxonomy: Any) -> None:
        self.sectors = _names(getattr(taxonomy, "sectors", ()))
        self.food_categories = _names(getattr(taxonomy, "food_categories", ()))
        self.destinations = _names(getattr(taxonomy, "destinations", ()))
        #: v1.59, for the fallback disclosure. `getattr` with a default
        #: for the reason every line here uses one: this class is handed
        #: whatever the caller has, and a snapshot that predates the
        #: vocabulary is a snapshot with no foods rather than an error.
        self.food_items = _names(getattr(taxonomy, "food_items", ()))
        #: A food's parent, so the disclosure can name the category the
        #: figure actually came from. Read off the vocabulary rather than
        #: off the entry: the entry carries the category the visitor
        #: chose, and those are the same today only because §6.2 refuses
        #: a food whose parent is not the category it arrived with.
        self.item_parents = {
            row.code: getattr(row, "food_category", None)
            for row in (getattr(taxonomy, "food_items", ()) or ())
        }

        self.metrics = _names(getattr(taxonomy, "metrics", ()))
        self.metric_units = {
            row.code: (getattr(row, "display_unit", None) or row.unit)
            for row in (getattr(taxonomy, "metrics", ()) or ())
        }
        # **`food_category is None` is no longer the same answer as the standard
        # mix, and this document may no longer print them as the same words.**
        #
        # It was: the engine resolves a NULL category to the standard mix, so
        # printing the field verbatim would have told a reader the food type
        # was unknown when in fact it was the standard mix, and this class
        # substituted the standard-mix row's own name for `None`.
        #
        # The multi-select (contract v1.55) makes them two separate answers a
        # visitor gives with two separate checkboxes - §5.4 requires it - and
        # they are distinguishable on the wire: the standard mix sends its own
        # `code`, and "I do not know, or my waste is not broken down by type"
        # sends `null`. Substituting made the two print byte-identically, so a
        # submission that ticked both produced two rows a reader could not tell
        # apart carrying different figures. `ABSENT` is this document's existing
        # convention for a field the submission did not state, and no new
        # catalogue string is involved - which matters here, because
        # `Catalogue.gettext` raises rather than falling back to English, so a
        # new string on this path would refuse to render the document in every
        # language that had not translated it yet.

    @staticmethod
    def _look_up(table: dict[str, str], code: str | None) -> str:
        if code is None:
            return ABSENT
        return table.get(code, code)

    def sector(self, code: str | None) -> str:
        return self._look_up(self.sectors, code)

    def food_category(self, code: str | None) -> str:
        return self._look_up(self.food_categories, code)

    def destination(self, code: str | None) -> str:
        return self._look_up(self.destinations, code)

    def food_item(self, code: str | None) -> str:
        return self._look_up(self.food_items, code)

    def metric(self, code: str | None) -> str:
        return self._look_up(self.metrics, code)

    def unit(self, code: str, fallback: str) -> str:
        return self.metric_units.get(code) or fallback


# --------------------------------------------------------------------------
# The view model.
# --------------------------------------------------------------------------
#
# One dict, `doc`, built here and printed by the template. Building it in
# Python rather than reaching into `result` from Jinja keeps the "no arithmetic
# in the template" rule checkable by reading one file: there is no expression
# in `results.html.j2` that could become a sum.

# --------------------------------------------------------------------------
# The document's own words.
# --------------------------------------------------------------------------
#
# **EVERY ONE OF THESE IS ALREADY A KEY IN `web/locales/*.json`, AND THAT IS A
# CONSTRAINT, NOT A COINCIDENCE.** The document is written in whichever of the
# twenty languages was asked for, and the only way to do that honestly is to
# say what the calculator already says: `tests/web/test_i18n_web.py::
# test_no_catalogue_carries_a_key_the_front_end_never_asks_for` fails on a key
# the front end does not use, so a heading invented here would mean authoring
# twenty unreviewed machine translations *and* breaking another stream's test.
# Nineteen of the twenty catalogues are machine-translated as it is; adding to
# them is a translation-workflow decision, not a renderer's.
#
# The wording therefore reads slightly differently from the English-only draft
# this replaces - "Improved" rather than "Alternative", "Impact summary" rather
# than "Impact totals" - and it reads the same as the screen the reader
# exported it from, which is worth more than either phrasing on its own.
#
# ONE THING THE CATALOGUES CANNOT SAY: the methane horizon. No key names it,
# so rather than leave one English label in a Tamil report, the horizon is
# printed as part of the factor-set value in the ISO notation - `GWP100` - that
# is the same token in every language. §4.4 formulas reference `const_GWP_CH4`
# and never a horizon literal; this is the reader's copy of the same fact.
_TITLE = "Food Waste Impact Calculator — Results"
_SUBTITLE = (
    "Results are estimates, produced from the calculation factors supplied "
    "and approved by Kai Commitment."
)
_COLOPHON = (
    "Data sources and calculation factors are maintained and approved by Kai "
    "Commitment."
)

_LABELS = {
    "total_mass": "Total food waste",
    "factor_set": "Factor set",
    "totals": "Impact summary",
    "metric": "Metric",
    "current": "Current",
    "alternative": "Improved",
    "net_benefit": "Potential Improvement",
    "destinations": "Waste destinations",
    "destination": "Destination",
    "money": "The money",
    "equivalences": "Tangible equivalents",
    "entries": "Added entries",
    #: §4.6. Reuses the exact key `web/js/results.js`'s "Percentage waste"
    #: card renders (see the module docstring for why nothing here is
    #: invented rather than reused: a heading coined only for this file would
    #: mean twenty unreviewed machine translations and would fail
    #: `test_no_catalogue_carries_a_key_the_front_end_never_asks_for`, which
    #: checks the catalogues against what the front end actually calls `t()`
    #: with).
    "production_share": "Percentage waste",
    #: The two figures behind each equivalence (Task 7) - the same words
    #: `web/js/results.js::equivalenceBasis` renders behind the page's
    #: disclosure and `buildResultsReport` prints in the text export, so the
    #: reader sees the identical labels regardless of which of the three
    #: surfaces they are holding.
    #:
    #: **`equivalence_basis` ("Basis:") is gone at v1.80**, with the
    #: `source_note` paragraph it labelled and with the mock caveat that
    #: followed it (#127). The client, using the tool as a tester, read one to
    #: four sentences of audit provenance behind a card's `?` and said it was
    #: meaningless there; `description` - one staff sentence saying what the
    #: comparison means - takes its place on all three surfaces. The key was
    #: deleted from all twenty catalogues in the same change, because
    #: `Catalogue.gettext` RAISES on a key it does not carry and a label left
    #: here with no catalogue entry would answer 500 to every non-English
    #: download.
    "equivalence_total": "Total",
    "equivalence_per_unit": "Per unit",
}

#: v1.59's fallback disclosure, word for word the two keys `web/js/results.js`
#: renders on screen and in the text export. Copied rather than shared because
#: the two surfaces have no code in common -- and pinned by
#: `tests/api/test_pdf_render.py`, so a reword on one side fails rather than
#: quietly producing a PDF that says something the screen did not.
_CATEGORY_AVERAGE_FLAG = "Food category average"
_CATEGORY_AVERAGE_BODY = (
    "%(food)s is priced at the %(category)s average. The published factor set "
    "carries no factors for this food, so the figures here are its category's "
    "rather than its own."
)

#: v1.68. The reporting period the figures cover, printed on the document
#: because a figure somebody keeps for months has to carry the period it
#: describes (§2.3) - and because `time_frame` reached `ExportPayload` from
#: v1.48 and this document printed it nowhere.
#:
#: **Every string here is already a key the front end asks for**, and that is a
#: constraint rather than a coincidence - see `_LABELS["production_share"]`. The
#: sentence is `web/js/results.js`'s own `resultsPeriod` line, word for word, so
#: the screen, the text download and this document say the same thing about the
#: same submission; the four phrases are `TIME_FRAME_LABELS`', which are in turn
#: step 5's own `<select>` options, so the word a visitor chose is the word they
#: are given back.
_PERIOD_SENTENCE = "These figures cover: %(period)s"
_TIME_FRAME_LABELS = {
    "one_week": "One week",
    "one_month": "One month",
    "one_quarter": "One quarter",
    "one_year": "One year",
}

#: **The standing per-equivalence caveat is gone at v1.80** - "The conversion
#: factor comes from the client. The total it is applied to comes from
#: placeholder factors." The client asked for it to go (#127) and what makes
#: that safe is that it was never the obligation: §7.6 rule 2's obligation is
#: the **page-level** placeholder banner, which is mandatory and
#: non-dismissible while `is_mock` and is drawn by the `{% if doc.is_mock %}`
#: block `test_deleting_the_warning_from_the_template_refuses_to_render`
#: guards. Nothing about that block moved, and
#: `test_a_real_factor_set_carries_no_warning` - which exists because an
#: UNCONDITIONAL version of the removed sentence once made a real published
#: set describe itself as placeholder data - is now proved by a document that
#: has one fewer place to say the word at all.

#: §4.6's four states for a totals-level figure, worded to match
#: `web/js/results.js::productionShareText` and `::moneyFieldText` exactly -
#: literally the same catalogue keys, not a rephrasing of them - so the card,
#: the text export and this document say the same thing about the same
#: submission. Every one of these seven strings is already something the
#: front end asks for; see `_LABELS["production_share"]`'s comment for why
#: that is a constraint here rather than a coincidence.
_DATA_INCOMPLETE_VALUE = "Data incomplete"
_DATA_NOT_SUPPLIED_VALUE = "Not supplied"
#: v1.51's fourth state's own value string - distinct from both of the above
#: because "every entry answered and the ratio is undefined" is neither "some
#: did and some did not" nor "nobody said".
_DATA_UNDEFINED_VALUE = "Undefined"
_PRODUCTION_SHARE_INCOMPLETE_NOTE = (
    "Some entries stated a production total and some did not, so a share of "
    "waste cannot be shown."
)
_PRODUCTION_SHARE_NOT_SUPPLIED_NOTE = (
    "You did not say how much food this covered, so a share of waste cannot "
    "be shown."
)
_PRODUCTION_SHARE_UNDEFINED_NOTE = (
    "You said this covered 0 kg in total, so a share of waste cannot be shown."
)
_MONEY_INCOMPLETE_NOTE = (
    "Not every entry supplied this figure, so it cannot be totalled."
)
_MONEY_UNDEFINED_NOTE = "The total value was zero, so this cannot be calculated."

#: The title block's byline (§4.2/Task 5). "Who produced it" - `home.js`
#: already renders this exact sentence as a news article's byline, so reusing
#: it here says the same thing about the same organisation in the same words,
#: rather than coining a second way to say "Kai Commitment made this".
_PRODUCED_BY = "From Kai Commitment"

#: `§4.5`'s money block. The unit annotations the English-only draft carried in
#: the label - "(NZD)", "(%)" - are gone rather than translated: a currency
#: code is not language and gluing it into a translatable string would have
#: made four more keys that no catalogue has. The third element is the same
#: distinction `web/js/results.js`'s `nzd` / `percentText` draw on the
#: *value* rather than the label - "NZ$" and "%" are currency notation, not
#: phrases, so `_money_figure` below glues them onto the formatted number the
#: same way, never through `translate()`.
_MONEY_LABELS = (
    ("total_value_nzd", "Total value of food handled", "currency"),
    ("wasted_value_nzd", "Value of food wasted", "currency"),
    ("wasted_share_percent", "Share of value wasted", "percent"),
    ("saving_nzd", "Value of food not wasted at all", "currency"),
)


def _money_figure(value: Any, kind: str) -> str:
    """A money-block value, with the unit `_figure` alone does not carry.

    `_figure` is shared with every other table in this document - metric
    totals, destination masses - none of which take a currency or a percent
    sign, so the sign belongs here rather than in `_figure` itself. Mirrors
    `web/js/results.js`'s `nzd` (`` `NZ$${formatNumber(...)}` ``) and
    `percentText` (`` `${formatNumber(...)}%` ``) exactly, so a figure reads
    the same amount with the same unit on the page, in the text export and in
    this document - the defect this function exists to close was the PDF
    printing `45,000.00` where the other two surfaces print `NZ$45,000.00`.
    """
    figure = _figure(value, 2)
    return f"NZ${figure}" if kind == "currency" else f"{figure}%"

#: Every translatable string the document can print, in one tuple, so that a
#: test can assert all twenty catalogues carry all of them **without rendering
#: anything**. A per-locale render proves the document came out; this proves
#: there is no locale in which one heading would quietly have to be English.
#:
#: **The conditional and the interpolated strings are deliberately not in
#: here**, because `test_no_locale_falls_back_to_english` asserts every entry
#: below appears *verbatim* in a rendered document, and neither kind can:
#: `_CATEGORY_AVERAGE_BODY` carries `%(food)s`, and v1.68's `_PERIOD_SENTENCE`
#: carries `%(period)s` and appears only when a period was stated. Their
#: coverage is asserted by name instead - see
#: `test_the_pdf_and_the_screen_word_the_fallback_disclosure_identically` and
#: `test_the_period_strings_are_the_screens_own_and_every_catalogue_has_them`,
#: which check the stronger property for them: that they are keys the front end
#: itself renders, so the three surfaces cannot come to word one fact two ways.
DOCUMENT_STRINGS: tuple[str, ...] = (
    _TITLE,
    _SUBTITLE,
    _COLOPHON,
    MOCK_WARNING_FLAG,
    MOCK_WARNING_BODY,
    _PRODUCED_BY,
    _DATA_INCOMPLETE_VALUE,
    _DATA_NOT_SUPPLIED_VALUE,
    _DATA_UNDEFINED_VALUE,
    _PRODUCTION_SHARE_INCOMPLETE_NOTE,
    _PRODUCTION_SHARE_NOT_SUPPLIED_NOTE,
    _PRODUCTION_SHARE_UNDEFINED_NOTE,
    _MONEY_INCOMPLETE_NOTE,
    _MONEY_UNDEFINED_NOTE,
    *_LABELS.values(),
    *(key for _attribute, key, _kind in _MONEY_LABELS),
)


def _instant_text(moment: Any) -> str:
    """One period bound, as a person reads it: `14/09/2026 08:10`.

    **`en-NZ`, in every language, and that is `web/js/period.js`'s decision
    rather than one taken here.** `stats.js`'s timestamp and `home.js`'s news
    date both pin `Intl.DateTimeFormat('en-NZ', …)` with the reason written
    beside them: a date *format* is O-4 (localisation beyond language), which
    is open and promises nothing. The sentence around the period is translated;
    the period inside it is not reformatted. A download is a particularly bad
    place to settle O-4, because the file outlives the argument.

    Formatted by hand rather than through `babel` or a locale table for the
    reason `_generated_at_text` gives about its own stamp: digits read the same
    in every language, and this module owns no calendar dictionary. `dd/mm/yyyy
    hh:mm` is character-for-character what `web/js/period.js`'s `DATE_DISPLAY`
    put in the box the visitor typed into, so the document prints back exactly
    what the form accepted - a document that reformatted it to `09/14/2026`
    would be showing a date that field would refuse.

    Seconds are not printed because the requirement is minutes and
    `submission.period_start` is a `DATETIME` with no fractional precision
    (§2.3).
    """
    return f"{moment:%d/%m/%Y %H:%M}"


def _period_text(period: Any, translate: Any) -> str:
    """The reporting period as one phrase, in the three shapes §6.2 accepts,
    or `""` when no period was stated.

    | What the request carried | What this returns |
    | --- | --- |
    | nothing | `""` |
    | a preset alone | `One week` |
    | a preset **and** an interval - the designed normal case from v1.67 | `One week · 14/09/2026 08:10 – 21/09/2026 08:10` |
    | `custom` and an interval | `14/09/2026 08:10 – 21/09/2026 08:10` |

    **A preset beside an interval prints both**, because from v1.67 a preset is
    a button that *fills* the picker: the interval is what the figures cover
    and `time_frame` is the record of which shortcut produced it. Printing only
    the phrase over dates the visitor may since have moved tells half the
    truth, and the half it drops is the one the client asks for first - *did
    they mean a standard week, or did they choose those dates?*

    **`custom` has no phrase of its own and is not in `_TIME_FRAME_LABELS`.**
    "Custom period" is the name of a control; read back to somebody holding
    their own report it says nothing they did not already know. What they chose
    was two instants, so two instants are what this prints.

    **The dash and the separator are notation and carry no catalogue key.**
    Same ruling as `GWP100` in the summary tile and the `·` already joining the
    cover line's three facts: `A – B` is the same notation in every language
    this calculator ships in, the sentence around it is translated by a key
    that has existed since v1.48, and a sentence coined here instead would mean
    every non-English download raising `MissingTranslationError` until twenty
    catalogues caught up.

    `period` is anything carrying `time_frame`, `period_start` and
    `period_end` - `api/export.py`'s `ExportPayload` is exactly that, and is
    what the route passes. Duck-typed like `result` and `taxonomy` above for
    the same reason: this module lays out values and does not own their types.
    This function computes nothing; §6.2 forbids deriving a duration from the
    pair and none is derived.
    """
    if period is None:
        return ""
    start = getattr(period, "period_start", None)
    end = getattr(period, "period_end", None)
    interval = f"{_instant_text(start)} – {_instant_text(end)}" if start and end else ""
    time_frame = getattr(period, "time_frame", None)
    if time_frame == "custom":
        return interval
    phrase = _TIME_FRAME_LABELS.get(time_frame)
    if phrase is None:
        return ""
    phrase = translate(phrase)
    return f"{phrase} · {interval}" if interval else phrase


def _metric_rows(
    current: Any, alternative: Any, net_benefit: Any, names: _Taxonomy
) -> list[dict[str, str]]:
    """One row per metric the engine reported, in the engine's own order.

    Iterating `current.metrics` is what makes metrics data rather than code
    (the invariant the engine is built on): adding a metric means inserting a
    row and writing a formula, and this table grows a line without this file
    being touched. There is no `if code == "co2e"` anywhere below.
    """
    rows = []
    alternative_metrics = getattr(alternative, "metrics", None) or {}
    net = net_benefit or {}
    for code, metric in current.metrics.items():
        precision = metric.display_precision
        other = alternative_metrics.get(code)
        rows.append(
            {
                "code": code,
                "name": names.metric(code),
                "unit": names.unit(code, metric.unit),
                "current": _figure(metric.total, precision),
                "alternative": (
                    _figure(other.total, precision) if other is not None else ABSENT
                ),
                "net_benefit": (
                    _figure(net.get(code), precision) if code in net else ABSENT
                ),
            }
        )
    return rows


def _destination_rows(totals: Any, names: _Taxonomy) -> list[dict[str, str]]:
    """The mass split, read off the first metric's `by_destination`.

    `qty_kg` is the same mass under every metric - the metrics differ in what
    they charge per kilogram, not in how many kilograms went where - so one
    metric's breakdown is the whole split, and reading it is a lookup rather
    than a calculation. When the engine reported no breakdown (an empty tuple
    is dropped on the wire and can be empty here too) the section is omitted
    entirely rather than printed empty.
    """

    def split(scenario: Any) -> dict[str, Decimal]:
        metrics = getattr(scenario, "metrics", None) or {}
        for metric in metrics.values():
            if metric.by_destination:
                return {row.destination_code: row.qty_kg for row in metric.by_destination}
        return {}

    current = split(totals.current)
    alternative = split(getattr(totals, "alternative", None))
    if not current and not alternative:
        return []

    codes = list(current) + [code for code in alternative if code not in current]
    return [
        {
            "name": names.destination(code),
            "current": _figure(current.get(code), 3) if code in current else ABSENT,
            "alternative": (
                _figure(alternative.get(code), 3) if code in alternative else ABSENT
            ),
        }
        for code in codes
    ]


def _money_rows(money: Any, data_state: Any, translate: Any) -> list[dict[str, Any]]:
    """Section 4.5's money block, when the visitor supplied one.

    Every field is optional and `None` means nobody supplied what it derives
    from - never zero, which would be a claim. §4.6 gave each field its own
    `data_state`, on the same terms as `production_share_percent`, and this
    reads it on the same rule `web/js/results.js::moneyFieldText` uses: a
    `complete` field prints its figure, an `incomplete` field prints the
    shared note instead of nothing (Task 1 turned a partial sum into `None`,
    and a block that only checked "is this `None`" would render nothing for
    that row, which is honest but not the most it can say), an `undefined`
    field (v1.51 - reachable only by `wasted_share_percent`, the one ratio
    among the four) prints its own note rather than either of the others, and
    a `not_supplied` field - or any state this function has not learned the
    name of - is left off the table entirely, exactly as before §4.6 existed.

    `is_note` marks a row whose value is a translated sentence rather than a
    formatted figure, so `results.html.j2` can leave `.figure`'s
    `white-space: nowrap` off it - the whole reason that flag exists rather
    than reusing `_figure`'s own absent marker for this case.

    `translate` is the document language's `gettext`; the labels are catalogue
    keys, and it raises rather than returning English for a language that has
    lost one.
    """
    if money is None:
        return []
    rows: list[dict[str, Any]] = []
    for attribute, label, kind in _MONEY_LABELS:
        value = getattr(money, attribute, None)
        if value is not None:
            rows.append(
                {"name": translate(label), "value": _money_figure(value, kind), "is_note": False}
            )
            continue
        state = getattr(data_state, attribute, None) if data_state is not None else None
        if state == DATA_INCOMPLETE:
            rows.append(
                {"name": translate(label), "value": translate(_MONEY_INCOMPLETE_NOTE), "is_note": True}
            )
        elif state == DATA_UNDEFINED:
            rows.append(
                {"name": translate(label), "value": translate(_MONEY_UNDEFINED_NOTE), "is_note": True}
            )
    return rows


def _equivalence_rows(scenario: Any, translate: Any) -> list[dict[str, Any]]:
    """§6.3: a calculator that cannot say which numbers are measured and which
    are borrowed cannot be defended in public, and the PDF is the copy that
    travels. **Which is why `description` has to reach this document and not
    only the screen** (v1.80, #127): paper has no "open" gesture, so whatever
    the page's disclosure holds is printed outright here.

    `description` is the client's own wording and is not translated (§7.7.7),
    exactly as `label` and `preset.label` are not; the two labels around it
    are. **It is printed only when it is there, and `source_note` is not a
    fallback for it** - that fallback is the long provenance prose this
    revision took off both surfaces.

    `is_mock` is no longer a parameter. It was here for the per-equivalence
    caveat alone, and that sentence is gone; the placeholder obligation is the
    page-level banner, which reads `is_mock` off the result in
    `build_context` and is unaffected by anything in this function. A
    parameter kept "just in case" is a parameter the next reader will find a
    use for.

    `item.source_metric_code` is the engine's own attribute name - this
    function reads the engine's result object directly (`api/export.py`
    re-runs the calculation and hands this module the object, not the wire
    response), so it is `source_metric_code` here and never the wire's
    `source_metric`.

    `figure` is the equivalence's own value, at whole-number precision -
    `_figure(item.value, 0)`, the same operation `web/js/results.js::
    equivalenceBasis` performs with `formatNumber(row.value, 0)` for the
    page's own last row. It exists so the template's `<dd>` has a number to
    print instead of reprinting `label` (the whole interpolated sentence,
    already the card's own heading two lines up) a second time.
    """
    rows = []
    for item in scenario.equivalences:
        source = scenario.metrics.get(item.source_metric_code)
        rows.append({
            "label": item.label,
            "name": item.name,
            "total": f"{_figure(source.total, source.display_precision)} {source.unit}" if source else "",
            "per_unit": item.value_per_unit_display,
            "figure": _figure(item.value, 0),
            #: `or ""` rather than `or item.source_note`. An absent sentence
            #: prints nothing: the template's `{% if row.description %}`
            #: draws no paragraph, and the card is the figures alone.
            "description": item.description or "",
        })
    return rows


def _production_share_context(totals: Any, translate: Any) -> dict[str, str]:
    """§4.6's totals-level production share, and its own four states (v1.51) -
    mirrors `web/js/results.js::productionShareText` word for word (see the
    comment above `_DATA_INCOMPLETE_VALUE`), so the on-screen card, the text
    export and this document tell the same story about the same submission.

    `complete` prints the percentage; `incomplete` says the coverage was
    partial rather than showing nothing where a wrong number used to sit;
    `undefined` says every entry answered and the total came to zero, so the
    ratio itself has no value - distinct from `not_supplied`, which - along
    with any state this function has not learned the name of, the same
    forward-compatible fallback the rest of this module gives every other
    absent figure - says nobody stated it.
    """
    data_state = getattr(totals, "data_state", None)
    state = getattr(data_state, "production_share_percent", None) if data_state is not None else None
    value = getattr(totals, "production_share_percent", None)
    if state == DATA_COMPLETE and value is not None:
        return {"value": f"{_figure(value, 2)}%", "note": ""}
    if state == DATA_INCOMPLETE:
        return {
            "value": translate(_DATA_INCOMPLETE_VALUE),
            "note": translate(_PRODUCTION_SHARE_INCOMPLETE_NOTE),
        }
    if state == DATA_UNDEFINED:
        return {
            "value": translate(_DATA_UNDEFINED_VALUE),
            "note": translate(_PRODUCTION_SHARE_UNDEFINED_NOTE),
        }
    return {
        "value": translate(_DATA_NOT_SUPPLIED_VALUE),
        "note": translate(_PRODUCTION_SHARE_NOT_SUPPLIED_NOTE),
    }


def _generated_at_text(generated_at: Any) -> str:
    """When this document was produced, in a form that needs no translation.

    ISO-8601-shaped rather than run through a per-locale month-name table -
    the same reasoning `factor_set` above relies on for `GWP100`: digits are
    the same in every language, so a stamp in this shape reads correctly in
    all twenty-one without this file owning a calendar dictionary it has no
    business owning.

    **`generated_at` is a parameter, not `datetime.now()` called in here.**
    Every other function in this module is a pure read of `result` and
    `taxonomy`; this is the one fact about a rendered document that
    legitimately depends on the wall clock, and the dependency is kept at the
    caller (`api/export.py`'s route, which has a clock to read) rather than
    buried in a render function no test could pin to a fixed instant. The
    fallback below exists only for a caller that does not care - most of the
    tests in `tests/api/test_pdf_render.py` predate this field and pass none.
    """
    moment = generated_at if generated_at is not None else datetime.now(timezone.utc)
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%d %H:%M UTC")


def build_context(
    result: Any,
    taxonomy: Any,
    locale: str,
    generated_at: Any = None,
    *,
    period: Any = None,
) -> dict[str, Any]:
    """The template's whole input, in the language that was asked for.

    Public so a test can assert on it directly rather than only through a
    rendered PDF.

    `generated_at` is the one exception to "this module reads no clock" - see
    `_generated_at_text` for why it is threaded through as a value rather than
    read here.

    **`lang` is the language the document is actually written in, not the tag
    the caller sent.** `api/i18n.resolve` turns `zh-TW` into `zh-Hant` and
    `en-GB` into `en`, and a tag no catalogue claims into `en` - and then the
    document says `en`, because labelling an English document `lang="he"` would
    tell a screen reader to pronounce English as Hebrew and would set the page
    right-to-left around text that runs the other way.

    **`dir` comes from the catalogue's own declaration**, not from a table of
    language subtags - the people who wrote a translation are the better
    authority on which way it runs. `text_direction` is the fallback for a
    catalogue that did not say, and it reads the language subtag rather than
    assuming left-to-right, so a new right-to-left catalogue that forgets the
    key still mirrors. That one attribute is the whole of
    defect two: it is what makes Pango run the Unicode bidirectional algorithm
    in a right-to-left base context, which is the difference between a sentence
    that ends with a full stop and one that starts with it. **Nothing in this
    file reorders a character.**

    NAMES ARE STILL NOT TRANSLATED. Sector, destination, food-category and
    metric names are staff-typed rows (§2.1) and are printed exactly as typed,
    in every one of the twenty-one languages.
    """
    catalogue = i18n.catalogue(locale)
    translate = catalogue.gettext
    names = _Taxonomy(taxonomy)
    totals = result.totals
    #: v1.68. Read once: it decides both whether the line is printed and what
    #: it says, and two calls would be two chances for those to disagree.
    period_text = _period_text(period, translate)

    #: v1.59, and the same list the screen shows, built from the same
    #: field. `EntryResult.item_basis` is rolled up in `engine/calculate.py`
    #: over every breakdown row of both scenarios; this reads it and does not
    #: recompute it, because the results page, its text export and this
    #: document have to tell one story about one submission and three
    #: roll-ups in two languages is three chances not to.
    #:
    #: Only `category` is disclosed -- `mixed` is the ordinary state, since
    #: the prevention offset is a category-level row (""" + S + """2.2) and every entry
    #: that moves mass to prevention therefore has a category-priced line.
    #:
    #: One line per food, deduplicated: two entries naming the same food are
    #: one caveat, and the reader is being told a fact about the factor set
    #: rather than about a row of their own table.
    category_averages = []
    seen_items: set[str] = set()
    for entry in result.entries:
        code = getattr(entry, "food_item_code", None)
        basis = getattr(getattr(entry, "item_basis", None), "value", None)
        if basis != "category" or code is None or code in seen_items:
            continue
        seen_items.add(code)
        parent = names.item_parents.get(code) or entry.food_category_code
        category_averages.append(
            translate(_CATEGORY_AVERAGE_BODY) % {
                "food": names.food_item(code),
                "category": names.food_category(parent),
            }
        )

    entries = []
    for entry in result.entries:
        entries.append(
            {
                "sector": names.sector(entry.sector_code),
                "food_category": names.food_category(entry.food_category_code),
                "total_kg": _figure(entry.current.total_kg, 3),
                "metrics": _metric_rows(
                    entry.current, entry.alternative, entry.net_benefit, names
                ),
            }
        )

    return {
        "lang": catalogue.language,
        "dir": catalogue.direction or text_direction(catalogue.language),
        "title": translate(_TITLE),
        "subtitle": translate(_SUBTITLE),
        "is_mock": bool(result.is_mock),
        #: v1.59. Present and empty when nothing fell back, so the
        #: template's `{% if %}` is the only place the decision is made.
        "category_average_flag": translate(_CATEGORY_AVERAGE_FLAG),
        "category_averages": category_averages,
        "mock_flag": translate(MOCK_WARNING_FLAG),
        "mock_body": translate(MOCK_WARNING_BODY),
        "factor_set_version": result.factor_set_version,
        "gwp_horizon": f"GWP{result.gwp_horizon}",
        # The two facts that identify the calculation basis, in one tile. See
        # the note above `_TITLE`: no catalogue key names the methane horizon,
        # and `GWP100` is the same token in every language.
        "factor_set": f"{result.factor_set_version} · GWP{result.gwp_horizon}",
        "total_kg": _figure(totals.current.total_kg, 3),
        "labels": {slot: translate(key) for slot, key in _LABELS.items()},
        # The title block (Task 5): what it is (`title`, above), who produced
        # it, when, and the factor-set version it used (`factor_set`, above -
        # printed a second time here so it sits in the block itself and not
        # only in the summary grid below it).
        "produced_by": translate(_PRODUCED_BY),
        "generated_at": _generated_at_text(generated_at),
        #: v1.68. The whole sentence, already translated and already
        #: interpolated, or `""` when no period was stated - so the template's
        #: `{% if %}` is the only place the decision is made, exactly as
        #: `category_averages` above. A label and never a computation (§2.3):
        #: no figure below it is scaled by it and no duration is derived.
        "period": (
            translate(_PERIOD_SENTENCE) % {"period": period_text}
            if period_text
            else ""
        ),
        "production_share": _production_share_context(totals, translate),
        "totals": _metric_rows(
            totals.current,
            getattr(totals, "alternative", None),
            getattr(totals, "net_benefit", None),
            names,
        ),
        "destinations": _destination_rows(totals, names),
        "money": _money_rows(getattr(totals, "money", None), getattr(totals, "data_state", None), translate),
        "equivalences": _equivalence_rows(totals.current, translate),
        "entries": entries,
        "colophon": translate(_COLOPHON),
    }


# --------------------------------------------------------------------------
# Rendering.
# --------------------------------------------------------------------------


def weasyprint_unavailable_reason() -> str | None:
    """`None` when this host can render, otherwise why it cannot.

    WeasyPrint's Python package installs from PyPI on any platform, but it
    binds Pango, HarfBuzz and GObject through cffi at import time and those are
    system libraries. On a bare Windows checkout the import raises `OSError:
    cannot load library 'libgobject-2.0-0'`; `docker/api.Dockerfile` installs
    them, so the container renders.

    One place asks the question so that no test has to guess at the answer, and
    so that a skip in a suite always names the same cause.
    """
    try:
        import weasyprint  # noqa: F401
    except Exception as exc:  # pragma: no cover - depends on the host
        return f"{type(exc).__name__}: {exc}"
    return None


def _font_url(filename: str) -> str:
    return _FONT_DIR.joinpath(filename).resolve().as_uri()


def stylesheet_source() -> str:
    """`results.css` with the two font placeholders resolved to `file://` URLs.

    Raises rather than rendering with a missing face: a document that silently
    fell back to a system font would not be in the brand's type, and nothing
    about the output would say so.
    """
    css = _STYLESHEET.read_text(encoding="utf-8")
    for placeholder, filename in _FONT_PLACEHOLDERS.items():
        path = _FONT_DIR / filename
        if not path.is_file():
            raise FileNotFoundError(
                f"The brand face {filename} is missing from {_FONT_DIR}. The "
                "export renders in the brand's type or not at all; check that "
                "api/assets/fonts is present in this install (it is listed in "
                "pyproject.toml under [tool.setuptools.package-data])."
            )
        css = css.replace(placeholder, _font_url(filename))
    return css


def _local_url_fetcher(url: str, timeout: int = 10, ssl_context: Any = None) -> Any:
    """Contract 7.6 rule 7, enforced rather than remembered.

    No runtime asset may come from a third-party host. This refuses every URL
    that is not a `file://` URL inside this package's own `assets/` directory,
    so an `http(s)` `url()` added to the stylesheet or an `<img src>` added to
    the template fails the render instead of quietly reaching the network from
    inside the API container - where no reviewer and no user would ever see the
    request. It also refuses a `file://` URL that escapes `assets/`, which
    keeps the renderer from being turned into a file-read primitive if a
    template ever interpolates a caller-supplied string into a URL.

    Both refusals are decided BEFORE WeasyPrint is imported, so the rule can be
    tested on a host that has no Pango - which is every Windows desk on this
    team, and the reason a rule that could only be tested in a container would
    be a rule nobody ran.
    """
    parsed = urlparse(url)
    if parsed.scheme != "file":
        raise ValueError(
            f"The export document may not fetch {url!r}: contract 7.6 rule 7 "
            "forbids a runtime asset from any host. Ship the asset inside "
            "api/assets/ and reference it from there."
        )
    path = Path(url2pathname(unquote(parsed.path))).resolve()
    if not path.is_relative_to(_ASSET_DIR):
        raise ValueError(
            f"The export document may only read files under {_ASSET_DIR}; "
            f"{path} is outside it."
        )

    # Read here rather than delegating to `weasyprint.default_url_fetcher`.
    # Two reasons, and the second is the one that matters: that function is
    # deprecated as of WeasyPrint 69 and warns on every render, and it is a
    # fetcher that CAN reach the network - delegating to it leaves a code path
    # that would make an HTTP request if this function's guards were ever
    # loosened. Reading the file directly means there is no network-capable
    # call in the renderer at all, which is what §7.6 rule 7 actually asks for.
    #
    # THE RETURN TYPE IS VERSION-DEPENDENT AND THE WRONG ONE IS SILENT.
    # WeasyPrint accepted a dict through 68 and warns for it in 69, and it
    # catches whatever a fetcher raises and merely LOGS "Failed to load" -- so
    # a fetcher that raises (a DeprecationWarning promoted to an error under
    # `-W error`, for instance) does not fail the render, it produces a
    # document quietly set in a fallback face. `URLFetcherResponse` is used
    # where it exists and the dict is the fallback for the >=62 floor, and
    # `test_the_brand_faces_are_embedded_in_the_pdf` reads the font names back
    # out of the PDF so that a silent fallback fails a test rather than
    # shipping.
    body = path.read_bytes()
    mime_type = _mime_type(path)
    try:
        from weasyprint.urls import URLFetcherResponse
    except ImportError:  # WeasyPrint < 69
        return {"string": body, "mime_type": mime_type, "redirected_url": url}
    return URLFetcherResponse(
        url, body=body, headers={"Content-Type": mime_type}
    )


#: `mimetypes` does not know the web font types on every host - it answers
#: `None` for `.woff2` on Windows and on a slim Debian image - and a font handed
#: over as `application/octet-stream` is a font WeasyPrint may decline to parse.
#: Stated rather than guessed for the two types this renderer actually serves.
_MIME_TYPES = {".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf"}


def _mime_type(path: Path) -> str:
    guess = _MIME_TYPES.get(path.suffix.lower())
    return guess or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _environment() -> Any:
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    return Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        # HTML autoescaping is on. Every string in the context is either
        # written in this module or a staff-typed taxonomy name out of the
        # database, and a name containing `<` must print as `<` rather than
        # open a tag - the document is built out of exactly the same kind of
        # data the browser export was, and that export is the reason this task
        # exists.
        autoescape=select_autoescape(default_for_string=True, default=True),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _assert_mock_warning_present(
    html: str, *, is_mock: bool, flag: str, body: str
) -> None:
    """The warning, re-read off the rendered HTML before anything is drawn.

    `render_results_pdf` already passes `result.is_mock` through
    unconditionally; this is the half that a template edit cannot get past.
    Deleting the `mock-warning` block from `results.html.j2` does not produce a
    document without a banner - it produces no document at all, and a 500 that
    names the file. Section 2.2 makes the banner mandatory and
    non-dismissible, and "non-dismissible" has to include "not by a later
    edit".

    Both literals are compared, not just the flag, so replacing the body with
    softer wording while leaving the heading in place is caught too.

    **The literals are the ones for THIS document's language**, not the English
    source. Checking for the English wording would have made the guard a check
    that fires only on English exports - and §2.2 is not a rule about English
    documents. They are HTML-escaped before comparing, because Jinja escaped
    them on the way in and a number of the translations contain an apostrophe.
    """
    if not is_mock:
        return
    from markupsafe import escape

    for literal in (flag, body):
        if str(escape(literal)) not in html:
            raise MockWarningMissingError(
                "The factor set is mock but the rendered export does not carry "
                f"the placeholder warning ({literal[:40]!r} is absent). Section "
                "2.2 makes it mandatory and non-dismissible on every export. "
                "Restore the mock-warning block in "
                "api/templates/results.html.j2."
            )


def render_html(
    result: Any,
    taxonomy: Any,
    locale: str,
    generated_at: Any = None,
    *,
    period: Any = None,
) -> str:
    """The document as HTML, warning already verified. Separated from the PDF
    call so that a test - and a developer debugging a layout - can look at what
    WeasyPrint was given without needing WeasyPrint installed."""
    return _html(build_context(result, taxonomy, locale, generated_at, period=period))


def _html(context: dict[str, Any]) -> str:
    """One context to one HTML string, warning verified. Split out so that
    `render_document` does not have to build the context twice to run the
    font-coverage guard over the same strings the template was given."""
    html = _environment().get_template(_TEMPLATE_NAME).render(doc=context)
    _assert_mock_warning_present(
        html,
        is_mock=context["is_mock"],
        flag=context["mock_flag"],
        body=context["mock_body"],
    )
    return html


# --------------------------------------------------------------------------
# Font coverage: a box is a defect, not a cosmetic problem.
# --------------------------------------------------------------------------
#
# THIS IS THE SAME DEFECT CLASS AS THE ONE THE WHOLE TASK EXISTS TO CLOSE. The
# browser export turned `Kūmara` into `K?mara` or into a 71 KB picture; a
# document whose font has no glyph for a character draws an empty box, or
# nothing at all, and looks perfectly fine to whoever generated it. Both are a
# corrupted export of somebody else's data, and neither says so anywhere.
#
# Twelve faces are embedded (see `_FONT_PLACEHOLDERS`) and between them they
# cover every character in all twenty catalogues. The four CJK faces are
# SUBSETS, because a whole Noto Sans CJK face is 10.9 MiB in WOFF2 and there
# would be four of them - 43.6 MiB in the repository, the wheel and two images,
# against 696 KiB for the four cut to what the catalogues actually contain.
# `api/assets/fonts/noto/PROVENANCE.md` records that measurement.
#
# What a subset gives up is a staff-typed taxonomy name in CJK, and this is
# what stops that being silent: the document's own text is checked against the
# embedded faces before WeasyPrint is called, and an uncovered character raises
# with the character named. A loud failure, not tofu.


class UndrawableCharacterError(RuntimeError):
    """The document contains a character no embedded face can draw."""


def _face_charsets() -> tuple[tuple[str, frozenset[int]], ...]:
    """Every embedded face, and the code points it has a glyph for.

    Read from the font files themselves rather than from a table beside them: a
    table is a second thing to keep in step with the fonts, and the day it
    drifts is the day this guard starts passing for the wrong reason.

    `fontTools` is not a new dependency - WeasyPrint declares `fonttools[woff]`
    and `docker/constraints.txt` pins it at 4.63.0 alongside `brotli`, which is
    what reads a WOFF2. Cached, because this is a dozen font parses and the
    answer cannot change while the process is alive.
    """
    global _FACE_CHARSETS
    if _FACE_CHARSETS is None:
        from fontTools.ttLib import TTFont

        faces = []
        for filename in _FONT_PLACEHOLDERS.values():
            path = _FONT_DIR / filename
            faces.append((path.name, frozenset(TTFont(path).getBestCmap())))
        _FACE_CHARSETS = tuple(faces)
    return _FACE_CHARSETS


_FACE_CHARSETS: tuple[tuple[str, frozenset[int]], ...] | None = None


def _is_drawable(character: str) -> bool:
    """Whether some embedded face can draw this character.

    Two ways, and the second is the one that keeps te reo Māori working. A face
    may carry the character outright, or it may carry the pieces it decomposes
    into - `ū` is U+016B, which neither brand subset has, but Kumbh Sans has
    `u` and the combining macron U+0304 and HarfBuzz composes the letter out of
    them. That is why `test_a_macron_survives_the_document` passes today, and a
    coverage check that did not know about it would refuse to render the very
    document this export exists for.

    The decomposition has to be satisfied by ONE face, not by several: glyphs
    from two different faces do not compose into a letter.
    """
    if character.isspace():
        return True
    point = ord(character)
    decomposed = unicodedata.normalize("NFD", character)
    for _name, charset in _face_charsets():
        if point in charset:
            return True
        if decomposed != character and all(ord(c) in charset for c in decomposed):
            return True
    return False


def _document_characters(context: Any) -> set[str]:
    """Every character the context will put on the page.

    Walked over the context rather than scraped out of the rendered HTML,
    because the HTML also contains tag names, class names and entity
    references, none of which is drawn - a guard that checked those would be
    checking the template's source code for glyph coverage.
    """
    characters: set[str] = set()
    stack = [context]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            characters.update(item)
        elif isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, (list, tuple, set)):
            stack.extend(item)
    return characters


def assert_every_character_is_drawable(context: Any) -> None:
    """Raise unless every character in the document has a glyph."""
    undrawable = sorted(c for c in _document_characters(context) if not _is_drawable(c))
    if undrawable:
        listed = ", ".join(f"U+{ord(c):04X} {c!r}" for c in undrawable[:12])
        raise UndrawableCharacterError(
            f"The document contains {len(undrawable)} character(s) that no "
            f"embedded face can draw: {listed}. WeasyPrint would draw an empty "
            "box or nothing at all, and the document would not say so - which "
            "is the same defect as the export this one replaced. Re-cut the "
            "face concerned, or add one, following "
            "api/assets/fonts/noto/PROVENANCE.md."
        )


def render_document(
    result: Any,
    taxonomy: Any,
    locale: str,
    generated_at: Any = None,
    *,
    period: Any = None,
) -> Any:
    """The laid-out document, one step before it becomes bytes.

    `render_results_pdf` is this plus `write_pdf()`, and the split exists for
    one reason: **a test needs the page geometry.** The defect this task closes
    is a title that printed 84pt off a 595pt page, and the only assertion that
    actually catches that is "no box extends past the page" - text extracted
    from a PDF comes back whether it was drawn on the paper or two centimetres
    past the edge of it. `document.pages[i]` carries the laid-out box tree, so
    `tests/api/test_pdf_render.py` can measure what WeasyPrint decided instead
    of taking a non-empty file as proof.

    It is the same call path the export uses - not a parallel one - so a test
    written against it is a test of what ships.
    """
    from weasyprint import CSS, HTML
    from weasyprint.text.fonts import FontConfiguration

    context = build_context(result, taxonomy, locale, generated_at, period=period)
    html = _html(context)
    # Checked here rather than in `render_html` because it is a question about
    # glyphs, and because `fontTools` is WeasyPrint's own dependency: a host
    # that can answer it is exactly a host that can render.
    assert_every_character_is_drawable(context)

    # One `FontConfiguration` per render, shared by the stylesheet and the
    # render: WeasyPrint registers `@font-face` faces into it when the CSS is
    # parsed and looks them up from it when the document is laid out, so the
    # two calls have to be given the same object or the faces are simply not
    # found and the document silently draws in a fallback.
    fonts = FontConfiguration()
    stylesheet = CSS(
        string=stylesheet_source(),
        font_config=fonts,
        url_fetcher=_local_url_fetcher,
    )
    return HTML(
        string=html,
        base_url=str(_ASSET_DIR),
        url_fetcher=_local_url_fetcher,
    ).render(stylesheets=[stylesheet], font_config=fonts)


def render_results_pdf(
    result: Any,
    taxonomy: Any,
    locale: str,
    generated_at: Any = None,
    *,
    period: Any = None,
) -> bytes:
    """`CalculationResult` -> a PDF, in the brand's type, at A4.

    `result` is what the engine returned for this request; `taxonomy` is the
    `TaxonomySnapshot` that turns its codes into staff-typed names; `locale`
    decides the document's base direction and its `lang` (which is also what
    lets Pango pick a script-appropriate face and Pyphen a hyphenation
    dictionary). `generated_at` is the moment the title block reports as when
    the document was produced (a `datetime`, ideally timezone-aware) - see
    `_generated_at_text` for why this function still does not read the clock
    itself.

    This function opens no socket and touches no database - `db/repository.py`
    is the only module that may do the last of those, and the caller has
    already loaded everything else this needs.
    """
    return render_document(
        result, taxonomy, locale, generated_at, period=period
    ).write_pdf()
