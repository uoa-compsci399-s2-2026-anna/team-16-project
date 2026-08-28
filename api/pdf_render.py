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
than remembered.
"""

from __future__ import annotations

import mimetypes
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

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

#: The two substitution points in `results.css`. The stylesheet is handed to
#: WeasyPrint as a string rather than as a path, so a relative `url()` in it
#: has no base to resolve against; these become absolute `file://` URLs.
_FONT_PLACEHOLDERS = {
    "KAICALC_FONT_SRC_GEOLOGICA": "geologica-bold.woff2",
    "KAICALC_FONT_SRC_KUMBH": "kumbh-sans-regular.woff2",
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
# Kept free of `&`, `<`, `>`, `"` and `'` so that Jinja's autoescaping is the
# identity function over it and (2) can compare against the same literal.
MOCK_WARNING_FLAG = "PLACEHOLDER DATA"
MOCK_WARNING_BODY = (
    "The figures in this document were produced from mock emissions factors. "
    "The Kai Commitment has not yet supplied the real New Zealand factor set, "
    "so every number here is a placeholder that shows how the calculator "
    "works, not what the impact is. Do not quote, publish or act on them."
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
        self.metrics = _names(getattr(taxonomy, "metrics", ()))
        self.metric_units = {
            row.code: (getattr(row, "display_unit", None) or row.unit)
            for row in (getattr(taxonomy, "metrics", ()) or ())
        }
        # §6.2: `food_category is None` on an entry means "use the standard
        # mix", and the engine resolves it that way - but it reports the code
        # back as `None`, so a document that printed the field verbatim would
        # tell a reader the food type was unknown when in fact it was the
        # standard mix. The row is found by its FLAG rather than by the code
        # `standard_mix`, for the reason `is_prevention` replaced
        # `PREVENTION_CODE`: a second vocabulary's standard-mix row (§10.3's
        # ReFED fixture) is a standard mix by every property that matters and
        # is not called that.
        self.standard_mix = next(
            (
                row.name
                for row in (getattr(taxonomy, "food_categories", ()) or ())
                if getattr(row, "is_standard_mix", False)
            ),
            None,
        )

    @staticmethod
    def _look_up(table: dict[str, str], code: str | None) -> str:
        if code is None:
            return ABSENT
        return table.get(code, code)

    def sector(self, code: str | None) -> str:
        return self._look_up(self.sectors, code)

    def food_category(self, code: str | None) -> str:
        if code is None and self.standard_mix is not None:
            return self.standard_mix
        return self._look_up(self.food_categories, code)

    def destination(self, code: str | None) -> str:
        return self._look_up(self.destinations, code)

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

_LABELS = {
    "total_mass": "Total food waste",
    "gwp_horizon": "Methane horizon",
    "factor_set": "Factor set",
    "totals": "Impact totals",
    "metric": "Impact",
    "current": "Current",
    "alternative": "Alternative",
    "net_benefit": "Net benefit",
    "destinations": "Where the waste goes",
    "destination": "Destination",
    "money": "Value of the food",
    "equivalences": "What that is equivalent to",
    "entries": "Each stage in detail",
}

_MONEY_LABELS = (
    ("total_value_nzd", "Total value of food handled (NZD)"),
    ("wasted_value_nzd", "Value of food wasted (NZD)"),
    ("wasted_share_percent", "Share of value wasted (%)"),
    ("saving_nzd", "Value recovered in the alternative (NZD)"),
)


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


def _money_rows(money: Any) -> list[dict[str, str]]:
    """Section 4.5's money block, when the visitor supplied one.

    Every field is optional and `None` means nobody supplied what it derives
    from - never zero, which would be a claim. A field that is `None` is left
    out of the table rather than printed as a dash, and a block in which every
    field is `None` produces no table at all.
    """
    if money is None:
        return []
    rows = []
    for attribute, label in _MONEY_LABELS:
        value = getattr(money, attribute, None)
        if value is not None:
            rows.append({"name": label, "value": _figure(value, 2)})
    return rows


def build_context(result: Any, taxonomy: Any, locale: str) -> dict[str, Any]:
    """The template's whole input. Public so a test can assert on it directly
    rather than only through a rendered PDF."""
    names = _Taxonomy(taxonomy)
    totals = result.totals

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
        "lang": str(locale),
        "dir": text_direction(locale),
        "title": "Kai Commitment food waste impact calculator",
        "subtitle": (
            "Impact of the food waste described, and of the alternative "
            "scenario beside it. Calculated by the Kai Commitment calculator; "
            "figures are the calculator server's, not the browser's."
        ),
        "is_mock": bool(result.is_mock),
        "mock_flag": MOCK_WARNING_FLAG,
        "mock_body": MOCK_WARNING_BODY,
        "factor_set_version": result.factor_set_version,
        "gwp_horizon": f"GWP{result.gwp_horizon}",
        "total_kg": _figure(totals.current.total_kg, 3),
        "labels": _LABELS,
        "totals": _metric_rows(
            totals.current,
            getattr(totals, "alternative", None),
            getattr(totals, "net_benefit", None),
            names,
        ),
        "destinations": _destination_rows(totals, names),
        "money": _money_rows(getattr(totals, "money", None)),
        "equivalences": [item.label for item in totals.current.equivalences],
        "entries": entries,
        "colophon": (
            "Produced by the Kai Commitment food waste impact calculator, for "
            "the New Zealand Food Waste Champions 12.3 Trust. Impact is "
            "calculated on the server from the published factor set named "
            f"above ({result.factor_set_version})."
        ),
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
    return {
        "string": path.read_bytes(),
        "mime_type": _mime_type(path),
        "redirected_url": url,
    }


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


def _assert_mock_warning_present(html: str, *, is_mock: bool) -> None:
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
    """
    if not is_mock:
        return
    for literal in (MOCK_WARNING_FLAG, MOCK_WARNING_BODY):
        if literal not in html:
            raise MockWarningMissingError(
                "The factor set is mock but the rendered export does not carry "
                f"the placeholder warning ({literal[:40]!r} is absent). Section "
                "2.2 makes it mandatory and non-dismissible on every export. "
                "Restore the mock-warning block in "
                "api/templates/results.html.j2."
            )


def render_html(result: Any, taxonomy: Any, locale: str) -> str:
    """The document as HTML, warning already verified. Separated from the PDF
    call so that a test - and a developer debugging a layout - can look at what
    WeasyPrint was given without needing WeasyPrint installed."""
    context = build_context(result, taxonomy, locale)
    html = _environment().get_template(_TEMPLATE_NAME).render(doc=context)
    _assert_mock_warning_present(html, is_mock=context["is_mock"])
    return html


def render_document(result: Any, taxonomy: Any, locale: str) -> Any:
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

    html = render_html(result, taxonomy, locale)

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


def render_results_pdf(result: Any, taxonomy: Any, locale: str) -> bytes:
    """`CalculationResult` -> a PDF, in the brand's type, at A4.

    `result` is what the engine returned for this request; `taxonomy` is the
    `TaxonomySnapshot` that turns its codes into staff-typed names; `locale`
    decides the document's base direction and its `lang` (which is also what
    lets Pango pick a script-appropriate face and Pyphen a hyphenation
    dictionary).

    This function reads no clock, opens no socket and touches no database -
    `db/repository.py` is the only module that may do the last of those, and
    the caller has already loaded everything this needs.
    """
    return render_document(result, taxonomy, locale).write_pdf()
