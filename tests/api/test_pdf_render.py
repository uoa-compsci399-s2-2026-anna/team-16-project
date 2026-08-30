"""`api/pdf_render.py` — the server-rendered export document.

**What these tests refuse to accept as evidence.** `len(pdf) > 0` and
`pdf[:5] == b"%PDF-"` prove nothing about this file. The export it replaces
produced a structurally valid PDF that opened in every reader and said
`K?mara`; another shape of the same defect produced a 71 KB image with no
extractable text at all. Both are "a PDF". So every test below either extracts
the text and asserts on what a reader will actually see, or walks the laid-out
box tree and asserts on where the layout engine actually put it.

The three defects the task exists to close each have a test here that fails if
it comes back:

* a title 84pt off a 595pt page  ->  `test_no_text_runs_off_the_page`
* Arabic ordered left-to-right   ->  `test_an_rtl_locale_sets_the_base_direction`
* `Kūmara` becoming `K?mara`     ->  `test_a_macron_survives_the_document`

and the constraint that outranks all three - §2.2's mandatory,
non-dismissible placeholder warning while O-1 stands - has three:
`test_the_mock_warning_is_in_the_pdf_text`,
`test_the_mock_warning_repeats_on_every_page` and
`test_deleting_the_warning_from_the_template_refuses_to_render`.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from decimal import Decimal
from pathlib import Path

import pytest

from api import pdf_render
from api.pdf_render import (
    MOCK_WARNING_FLAG,
    MockWarningMissingError,
    build_context,
    render_html,
    render_results_pdf,
    text_direction,
)
from db.types import (
    DestinationGroupSpec,
    DestinationSpec,
    FoodCategorySpec,
    MetricSpec,
    SectorSpec,
    TaxonomySnapshot,
)
from engine.types import (
    BreakdownRow,
    CalculationResult,
    CalculationTotals,
    EntryResult,
    EquivalenceResult,
    MetricResult,
    MoneyResult,
    ScenarioResult,
)
from api import i18n
from api.pdf_render import DOCUMENT_STRINGS, MOCK_WARNING_BODY, UndrawableCharacterError
from tests.support.pdf import (
    border_widths,
    document_language,
    extract_text,
    laid_out_lines,
    overflowing_boxes,
    requires_weasyprint,
)

ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------
# Fixtures. Engine-shaped objects, built from `engine/types.py`'s own
# dataclasses rather than from a hand-written stand-in, so a field renamed in
# the contract breaks these tests instead of letting them drift.
# --------------------------------------------------------------------------

#: A staff-typed destination name carrying a macron. THE POINT OF THE WHOLE
#: TASK IS IN THIS STRING: `ū` (U+016B) is outside WinAnsi, and the browser
#: export either rasterised the page or wrote `K?mara`. It is also exactly the
#: kind of name a New Zealand food-waste taxonomy will really contain.
MACRON_NAME = "Kūmara peelings to compost"

#: A German compound of the length that put a title 84pt off the page. Nothing
#: here clamps or truncates it; CSS breaks it across lines.
LONG_NAME = (
    "Lebensmittelabfallvermeidungsmaßnahmenbewertungsverordnungsdurchführung "
    "Rückverfolgbarkeitsdokumentationspflichtenverzeichnis"
)


def _metric(
    code: str, total: str, *, unit: str = "kg CO2e", precision: int = 1, rows=()
) -> MetricResult:
    return MetricResult(
        metric_code=code,
        unit=unit,
        display_precision=precision,
        total=Decimal(total),
        by_destination=tuple(rows),
    )


def _row(destination: str, qty: str, value: str) -> BreakdownRow:
    return BreakdownRow(
        destination_code=destination,
        qty_kg=Decimal(qty),
        upstream=Decimal("2.5"),
        downstream=Decimal("-0.4"),
        value=Decimal(value),
    )


def _scenario(total_kg: str, metrics: dict[str, MetricResult], labels=()) -> ScenarioResult:
    return ScenarioResult(
        total_kg=Decimal(total_kg),
        metrics=metrics,
        equivalences=tuple(
            EquivalenceResult(
                code=f"eq{index}",
                label=label,
                value=Decimal("1"),
                source_metric_code="co2e",
            )
            for index, label in enumerate(labels)
        ),
    )


def _money() -> MoneyResult:
    """A filled-in §4.5 money block.

    The default fixture used to leave `money=None`, which meant the document's
    whole money section - and its four translated labels - were never rendered
    by any test. A locale that had lost one of those four keys would have gone
    to production green.
    """
    return MoneyResult(
        total_value_nzd=Decimal("18000.00"),
        wasted_value_nzd=Decimal("3600.00"),
        wasted_share_percent=Decimal("20.00"),
        saving_nzd=Decimal("2400.00"),
    )


def _result(
    *,
    co2e: str = "4449.0",
    is_mock: bool = True,
    entry_count: int = 1,
    destination_code: str = "landfill",
) -> CalculationResult:
    rows = (_row(destination_code, "1200.500", "3600.0"), _row("compost", "300.000", "849.0"))
    current = _scenario(
        "1500.500",
        {"co2e": _metric("co2e", co2e, rows=rows)},
        labels=["Equivalent to 18,024 km driven in an average car"],
    )
    alternative = _scenario(
        "1500.500", {"co2e": _metric("co2e", "1200.0", rows=(_row("prevention", "1500.500", "0.0"),))}
    )
    entry = EntryResult(
        sector_code="wholesale_retail",
        food_category_code="fruit",
        current=current,
        alternative=alternative,
        net_benefit={"co2e": Decimal("3249.0")},
    )
    return CalculationResult(
        factor_set_version="MOCK-v0",
        is_mock=is_mock,
        gwp_horizon=100,
        totals=CalculationTotals(
            current=current,
            alternative=alternative,
            net_benefit={"co2e": Decimal("3249.0")},
            money=_money(),
        ),
        entries=tuple(entry for _ in range(entry_count)),
    )


def _taxonomy(
    *, destination_name: str = "Landfill", sector_name: str = "Wholesale and retail"
) -> TaxonomySnapshot:
    return TaxonomySnapshot(
        sectors=(SectorSpec("wholesale_retail", sector_name, None, 30),),
        food_categories=(FoodCategorySpec("fruit", "Fruit", False, 10),),
        destination_groups=(DestinationGroupSpec("disposal", "Disposal", True, 30),),
        destinations=(
            DestinationSpec("landfill", destination_name, "disposal", None, 30, False),
            DestinationSpec("compost", "Composting", "disposal", None, 20, False),
            DestinationSpec("prevention", "Prevented", "disposal", None, 5, True),
        ),
        metrics=(MetricSpec("co2e", "Greenhouse gas", "kg CO2e", None, 1, 10),),
        unit_presets=(),
        factor_set_version="MOCK-v0",
        factor_set_is_mock=True,
    )


# --------------------------------------------------------------------------
# §2.2: the placeholder warning.
# --------------------------------------------------------------------------


@requires_weasyprint
def test_the_mock_warning_is_in_the_pdf_text():
    """The whole reason this file is dangerous: it is read later, elsewhere, by
    someone who was not there. O-1 means every figure in it is placeholder data
    until the client supplies real factors, and §2.2 makes saying so mandatory
    and non-dismissible on every export.

    Asserted on the EXTRACTED TEXT, not on the template source. A banner styled
    into invisibility is not drawn, so it is not in here - which is the failure
    a source-level assertion would miss.
    """
    text = extract_text(render_results_pdf(_result(), _taxonomy(), "en"))
    assert "placeholder" in text.lower()
    # The wording moved to the catalogue's own, which is what makes the warning
    # translatable at all - `web/js/results.js` renders these same two strings
    # into the on-screen banner, so the paper and the page now say the same
    # thing in the same words. The assertion is not weakened by the move: it
    # was "mock emissions factors", it is now the whole catalogue sentence, and
    # `test_the_mock_warning_survives_in_every_locale` checks all twenty-one.
    assert "verified calculation factors have not yet been supplied" in text.lower()


@requires_weasyprint
def test_the_mock_warning_repeats_on_every_page():
    """Page one carries the banner; every page carries the flag.

    `results.css` sets a named string on the banner and prints it in the
    `@top-center` margin box, so a reader who is handed page three of a printout
    still learns the figures are placeholders. Rendered with enough entries to
    paginate, and asserted page by page - a document that happens to fit on one
    page would make this test vacuous, so the assertion checks there is more
    than one page first.
    """
    from pypdf import PdfReader
    from io import BytesIO

    pdf = render_results_pdf(_result(entry_count=14), _taxonomy(), "en")
    reader = PdfReader(BytesIO(pdf))
    assert len(reader.pages) > 1, "not paginated - this test would prove nothing"
    for number, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").lower()
        assert "placeholder" in text, f"page {number} does not carry the warning"


@requires_weasyprint
def test_a_real_factor_set_carries_no_warning():
    """The other half of the same rule: the banner is a statement of fact about
    the factor set, not decoration. If it appeared under a published real set it
    would be false, and a warning that is always on is a warning nobody reads."""
    text = extract_text(render_results_pdf(_result(is_mock=False), _taxonomy(), "en"))
    assert "placeholder" not in text.lower()


def test_deleting_the_warning_from_the_template_refuses_to_render(tmp_path, monkeypatch):
    """**The mutation, automated.**

    PR #46's warning survived review only because it happened to reuse
    `buildResultsReport()`; the inheritance was the mechanism, not a decision,
    and a warning that survives by accident will one day not. So this test
    performs the edit that would drop it - the whole `mock-warning` block,
    removed from a copy of the template - and asserts the renderer refuses.

    Not "renders without a banner". Refuses. A placeholder document that does
    not say it is a placeholder is worse than no document, because it is the one
    that gets forwarded.
    """
    source = (ROOT / "api" / "templates" / "results.html.j2").read_text(encoding="utf-8")
    mutated = re.sub(
        r"\{% if doc\.is_mock %\}.*?\{% endif %\}", "", source, count=1, flags=re.S
    )
    assert mutated != source, "the mutation did not apply - the block was not found"

    (tmp_path / "results.html.j2").write_text(mutated, encoding="utf-8")
    monkeypatch.setattr(pdf_render, "_TEMPLATE_DIR", tmp_path)

    with pytest.raises(MockWarningMissingError):
        render_html(_result(), _taxonomy(), "en")


def test_the_warning_cannot_be_switched_off_by_a_caller():
    """There is no parameter, keyword or flag that suppresses it: `is_mock` is
    read off the engine's own result and nothing else decides. Asserted against
    the renderer's public signature so that adding such a parameter later fails
    here rather than passing review."""
    import inspect

    names = set(inspect.signature(render_results_pdf).parameters)
    assert names == {"result", "taxonomy", "locale"}
    assert build_context(_result(), _taxonomy(), "en")["is_mock"] is True


# --------------------------------------------------------------------------
# Defect one: text that ran off the page.
# --------------------------------------------------------------------------


@requires_weasyprint
def test_every_figure_comes_from_the_result():
    """The figures are the engine's, formatted for reading and not recomputed.
    `4449.0` at the metric's own `display_precision` of 1 is `4,449.0` - the
    same two operations `web/js/view.js::formatNumber` performs for the screen,
    so the paper and the page cannot disagree about one calculation."""
    text = extract_text(render_results_pdf(_result(co2e="4449.0"), _taxonomy(), "en"))
    assert "4,449.0" in text


@requires_weasyprint
def test_no_text_runs_off_the_page():
    """**Defect one.** A German compound of 70-odd characters, typed by staff
    into a destination name, laid out at A4.

    Text extraction cannot catch this - a glyph drawn two centimetres past the
    edge of the paper is still in the content stream and still comes back. So
    this walks the laid-out box tree and compares each text box's inline extent
    against the page. Nothing in `api/` measured anything to make it pass: the
    fix is two CSS declarations (`overflow-wrap: anywhere`, `word-break:
    break-word`) and the layout engine did the rest.
    """
    document = pdf_render.render_document(
        _result(),
        _taxonomy(destination_name=LONG_NAME, sector_name=LONG_NAME),
        "de",
    )
    assert overflowing_boxes(document) == []


@requires_weasyprint
def test_a_long_name_is_wrapped_and_not_truncated():
    """The other half of the same defect. "Nothing runs off the page" is also
    satisfiable by cutting the text off, which would be a corrupted export in a
    different way - so the long name has to still be readable in full."""
    pdf = render_results_pdf(
        _result(), _taxonomy(destination_name=LONG_NAME), "de"
    )
    text = extract_text(pdf).replace(" ", "")
    assert LONG_NAME.replace(" ", "") in text


# --------------------------------------------------------------------------
# Defect two: right-to-left text.
# --------------------------------------------------------------------------


def test_an_rtl_locale_sets_the_base_direction():
    """**Defect two**, at the point where it is decided.

    The browser export never set `direction` or `textAlign`, so Arabic came out
    left-to-right with the full stop at the start of the line. Setting `dir` on
    `<html>` is what makes Pango run the Unicode bidirectional algorithm in the
    right base context; task 4 does the catalogues, but the base direction is
    the thing the layout engine needs first and it is decided here.
    """
    assert 'dir="rtl"' in render_html(_result(), _taxonomy(), "ar")
    assert 'dir="ltr"' in render_html(_result(), _taxonomy(), "en")
    assert 'lang="ar"' in render_html(_result(), _taxonomy(), "ar")


@pytest.mark.parametrize(
    "locale, expected",
    [
        ("ar", "rtl"),
        ("ar-EG", "rtl"),
        ("ur-PK", "rtl"),
        ("he", "rtl"),
        ("az-Arab", "rtl"),
        ("en", "ltr"),
        ("en-NZ", "ltr"),
        ("mi", "ltr"),
        ("de-DE", "ltr"),
        ("", "ltr"),
    ],
)
def test_text_direction_reads_the_tag(locale, expected):
    assert text_direction(locale) == expected


def test_the_stylesheet_names_no_physical_side():
    """**The constraint task 4 inherits.** Logical properties only: `left` and
    `right` in a stylesheet are a layout that has to be unpicked before Arabic
    can work, and unpicking one is how a mirrored document ends up half
    mirrored. Comments are stripped first, since the reasoning above the rules
    discusses both words at length.
    """
    css = re.sub(r"/\*.*?\*/", "", pdf_render._STYLESHEET.read_text(encoding="utf-8"), flags=re.S)
    offenders = re.findall(
        r"(?:^|[;{\s])((?:margin|padding|border)-(?:left|right)"
        r"|text-align\s*:\s*(?:left|right)"
        r"|(?:^|\s)(?:left|right)\s*:)",
        css,
        flags=re.M,
    )
    assert offenders == [], f"physical properties in results.css: {offenders}"


# --------------------------------------------------------------------------
# Defect three: a character outside WinAnsi.
# --------------------------------------------------------------------------


@requires_weasyprint
def test_a_macron_survives_the_document():
    """**Defect three, and the one that is a data-integrity bug rather than a
    layout one.** `ū` (U+016B) is outside WinAnsi. The hand-rolled export either
    turned the whole document into a 71 KB image with no extractable text, or
    wrote `K?mara` - a corrupted export of a staff-typed name, in a New Zealand
    tool, for a New Zealand client whose vocabulary is full of macrons.

    Three things are asserted, and each one kills one of the old failure modes:

    * the macron character itself is in the extracted text - so it was not
      substituted, and `?` appears nowhere in the document;
    * the rest of the name is in the extracted text, unbroken;
    * text came back at all - a rasterised page would yield none, which is what
      the 71 KB image did.

    **Why the whole name is not compared as one string.** The shipped brand
    subsets carry no precomposed `ū`; `test_the_brand_faces_lack_precomposed_
    macrons` below states that as the fact it is. Kumbh Sans does carry `u` and
    the combining macron U+0304, so Pango composes the letter out of the two -
    correct on paper, in the brand's own face, which is the right outcome for a
    document where macrons are common. What it costs is that WeasyPrint maps the
    mark glyph back to the whole cluster in the PDF's ToUnicode table, so the
    text a reader COPIES out reads `Kuūmara`: the base letter appears twice. The
    glyphs are right and the character is there; the copy-and-paste form has a
    stray `u`. That is a font-coverage finding, recorded rather than papered
    over - and papering over it here, by normalising the extracted text until it
    matched, is precisely the "test that passes for the wrong reason" this
    project keeps producing.

    **Task 4 did not fix it, and adding twelve script faces did not either.**
    Noto Sans, now embedded for Vietnamese and Cyrillic, *does* carry a
    precomposed U+016B - but HarfBuzz decomposes the character and finds both
    pieces in Kumbh Sans first, so the brand face still wins and still composes.
    The only real fix is re-cutting the brand subsets to carry Latin Extended-A,
    which replaces a client-supplied asset that `web/assets/fonts/` also serves
    to every visitor; that is a brand decision, not a renderer's. The exact
    extracted form shifted from `Kuūmara` to `Ku ū mara` - the mark's own
    advance now reads as a space - which is the same defect with different
    whitespace, and is why the assertions below are on the pieces rather than
    on one literal.
    """
    pdf = render_results_pdf(_result(), _taxonomy(destination_name=MACRON_NAME), "en")
    text = unicodedata.normalize("NFC", extract_text(pdf))

    assert "ū" in text, "the macron did not reach the document as text"
    assert "?" not in text, "a character was substituted somewhere in the document"
    assert "K?mara" not in text
    assert "mara peelings to compost" in text
    # And it is real text, not a picture of text: the paragraph beside it came
    # back too, which a rasterised page could not have produced.
    assert "placeholder" in text.lower()


def test_the_brand_faces_lack_precomposed_macrons():
    """**A finding, pinned as a test so it cannot quietly change either way.**

    Both shipped brand subsets are about 228 glyphs and neither carries the
    precomposed Latin macron letters - no `ā`, no `ū` - which in a New Zealand
    product is a real gap: te reo Māori words are not decoration here, they are
    the client's own vocabulary. What saves the rendering is that Kumbh Sans
    does carry the combining macron U+0304, so the letters compose correctly on
    the page (see the test above); what it costs is the copy-and-paste form.

    If somebody re-cuts the subsets to include U+0100..U+017F this test fails,
    and the right response is to delete it and tighten the macron test above
    into a straight `MACRON_NAME in text`. If somebody re-cuts them WITHOUT the
    combining macron, the macron test fails instead - which is the outcome that
    actually matters, because that is `K?mara` coming back.
    """
    from fontTools.ttLib import TTFont

    for name in ("kumbh-sans-regular.woff2", "geologica-bold.woff2"):
        cmap = TTFont(ROOT / "api" / "assets" / "fonts" / name).getBestCmap()
        assert 0x016B not in cmap, f"{name} now has a precomposed u-macron"
    kumbh = TTFont(ROOT / "api" / "assets" / "fonts" / "kumbh-sans-regular.woff2")
    assert 0x0304 in kumbh.getBestCmap(), (
        "Kumbh Sans has lost the combining macron - te reo Maori names can no "
        "longer be composed in the brand's own face"
    )


# --------------------------------------------------------------------------
# §7.6 rule 7: nothing is fetched.
# --------------------------------------------------------------------------


def test_the_document_refuses_to_fetch_from_a_host():
    """Contract §7.6 rule 7 forbids a runtime asset from a third-party host,
    and a server-rendered document is the case where breaking it would be
    invisible - the request would leave the API container, where no reviewer and
    no user would ever see it. Enforced, not remembered."""
    with pytest.raises(ValueError):
        pdf_render._local_url_fetcher("https://fonts.googleapis.com/css2?family=Geologica")
    with pytest.raises(ValueError):
        pdf_render._local_url_fetcher("http://example.test/logo.png")


def test_the_document_refuses_to_read_outside_its_own_assets():
    """The same fetcher, as a path guard: a `file://` URL that climbs out of
    `api/assets/` is refused, so the renderer cannot be turned into a file-read
    primitive by a template that interpolates a caller-supplied string."""
    escape = (ROOT / "pyproject.toml").resolve().as_uri()
    with pytest.raises(ValueError):
        pdf_render._local_url_fetcher(escape)


def test_no_stylesheet_or_template_url_points_at_a_host():
    source = pdf_render.stylesheet_source() + render_html(_result(), _taxonomy(), "en")
    assert "http://" not in source
    assert "https://" not in source
    assert "//fonts.googleapis" not in source


def test_the_embedded_faces_match_the_public_ones():
    """`api/assets/fonts/` is a third copy of the brand faces and has to be -
    package-data cannot cross a package boundary and `api/` may not import
    `admin/`. What it must not be is a DIFFERENT copy: the exported document and
    the page it was exported from have to be in the same type."""
    for name in ("geologica-bold.woff2", "kumbh-sans-regular.woff2"):
        served = (ROOT / "web" / "assets" / "fonts" / name).read_bytes()
        embedded = (ROOT / "api" / "assets" / "fonts" / name).read_bytes()
        assert hashlib.sha256(embedded).hexdigest() == hashlib.sha256(served).hexdigest(), name


@requires_weasyprint
def test_the_brand_faces_are_embedded_in_the_pdf():
    """**That the fonts actually loaded, read back out of the file.**

    This is the test the rest of this file would have been missing, and it was
    written after a real near-miss: WeasyPrint catches whatever a URL fetcher
    raises and merely LOGS "Failed to load", so a fetcher that fails produces a
    perfectly good-looking document set in a fallback face, and every other
    assertion here - the warning, the figures, the wrapping - still passes. Off
    brand, silently, in the client's own deliverable.

    So the font names are read out of the PDF's own resource dictionary. A
    subset is named `ABCDEF+Geologica-Bold`, hence the substring match.
    """
    from io import BytesIO

    from pypdf import PdfReader

    pdf = render_results_pdf(_result(), _taxonomy(), "en")
    names: set[str] = set()
    for page in PdfReader(BytesIO(pdf)).pages:
        fonts = page.get("/Resources", {}).get("/Font", {})
        for key in fonts:
            names.add(str(fonts[key].get_object().get("/BaseFont", "")))

    joined = " ".join(names)
    assert "Geologica" in joined, f"headings are not in the brand face: {names}"
    assert "Kumbh" in joined, f"body text is not in the brand face: {names}"


def test_a_missing_face_is_an_error_and_not_a_silent_fallback(monkeypatch, tmp_path):
    """A document that quietly rendered in a system font would be off-brand with
    nothing in the output to say so. The brand's type or nothing."""
    monkeypatch.setattr(pdf_render, "_FONT_DIR", tmp_path)
    with pytest.raises(FileNotFoundError):
        pdf_render.stylesheet_source()


# --------------------------------------------------------------------------
# §7.6.1: the document prints, it does not calculate.
# --------------------------------------------------------------------------


def test_the_template_contains_no_arithmetic():
    """§7.6.1 leaves the front end no calculation but unit conversion, and the
    server does not grow a second summation to compensate. A template that
    totalled a column would be a second calculation site with no golden case
    behind it - structurally the same defect as summing in the browser, one
    layer down."""
    source = (ROOT / "api" / "templates" / "results.html.j2").read_text(encoding="utf-8")
    # Jinja comments stripped first: the block at the top of the template
    # states this rule in prose and names the filters it forbids, and a test
    # that tripped over the rule's own statement would be a test that reads a
    # comment rather than the code.
    template = re.sub(r"\{#.*?#\}", "", source, flags=re.S)
    for forbidden in ("|sum", "sum(", "|round", "round(", "|float", "float(", "|int"):
        assert forbidden not in template, f"{forbidden!r} in results.html.j2"


def test_no_figure_is_routed_through_a_float():
    """§1.2: decimals travel as strings because JavaScript's Number is a double,
    and the same reasoning binds the server that produced them. A `float()` on a
    figure would throw away digits at exactly the point they are being written
    down for keeps."""
    import ast

    tree = ast.parse(Path(pdf_render.__file__).read_text(encoding="utf-8"))
    calls = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "float" not in calls
    # Parsed rather than grepped on purpose: the module's own docstring says the
    # words `float(value)` while explaining why it does not do that, and a
    # substring search would be a test that reads a comment.


def test_figures_keep_the_engines_digits():
    """The formatter groups and quantises; it does not re-derive. Half-up on the
    `Decimal` itself, at the metric's own `display_precision`, and exact at any
    size - `int()` on the integer part of the digit string is arbitrary
    precision, so a figure larger than a double can hold still prints whole."""
    assert pdf_render._figure(Decimal("4449.0"), 1) == "4,449.0"
    assert pdf_render._figure(Decimal("1200.500"), 3) == "1,200.500"
    assert pdf_render._figure(Decimal("-0.455"), 2) == "-0.46"
    assert pdf_render._figure(Decimal("825"), 2) == "825.00"
    assert pdf_render._figure(Decimal("12345678901234567890.5"), 0) == "12,345,678,901,234,567,891"
    assert pdf_render._figure(None, 2) == pdf_render.ABSENT


def test_names_come_from_the_taxonomy_and_are_never_translated():
    """`code` is the cross-layer identifier; the name is a staff-typed row. It
    is printed as typed, in every locale - a document that translated it would
    be putting words in the client's mouth."""
    for locale in ("en", "ar", "de"):
        context = build_context(_result(), _taxonomy(destination_name=MACRON_NAME), locale)
        assert context["entries"][0]["sector"] == "Wholesale and retail"
        assert any(row["name"] == MACRON_NAME for row in context["destinations"])


def test_an_absent_food_category_names_the_standard_mix():
    """§6.2: `food_category is None` means "use the standard mix", and the
    engine resolves it that way but reports the code back as `None`. Printing
    the field verbatim would tell a reader the food type was unknown when in
    fact it was the standard mix - which is the kind of quiet mis-statement a
    document read six months later cannot be corrected on."""
    taxonomy = _taxonomy()
    mixed = TaxonomySnapshot(
        sectors=taxonomy.sectors,
        food_categories=(
            FoodCategorySpec("standard_mix", "Mixed food waste", True, 5),
        ),
        destination_groups=taxonomy.destination_groups,
        destinations=taxonomy.destinations,
        metrics=taxonomy.metrics,
        unit_presets=(),
        factor_set_version="MOCK-v0",
        factor_set_is_mock=True,
    )
    result = _result()
    entry = result.entries[0]
    unspecified = CalculationResult(
        factor_set_version=result.factor_set_version,
        is_mock=result.is_mock,
        gwp_horizon=result.gwp_horizon,
        totals=result.totals,
        entries=(
            EntryResult(
                sector_code=entry.sector_code,
                food_category_code=None,
                current=entry.current,
                alternative=entry.alternative,
                net_benefit=entry.net_benefit,
            ),
        ),
    )
    context = build_context(unspecified, mixed, "en")
    assert context["entries"][0]["food_category"] == "Mixed food waste"


def test_an_unknown_code_prints_as_itself():
    """A taxonomy row deactivated after a factor set was published must not 500
    the document. Naming a destination by its code is worse than naming it
    properly and much better than losing the figures beside it."""
    context = build_context(
        _result(destination_code="retired_destination"), _taxonomy(), "en"
    )
    assert any(row["name"] == "retired_destination" for row in context["destinations"])


def test_a_metric_is_a_row_and_not_a_call_site():
    """Metrics are data, not code: the totals table is built by iterating what
    the engine reported, so adding a metric adds a line to this document without
    this file or the template being touched. There is no `if code == "co2e"`
    anywhere in the renderer."""
    result = _result()
    extra = dict(result.totals.current.metrics)
    extra["cost_nzd"] = _metric("cost_nzd", "825", unit="NZD", precision=2)
    scenario = ScenarioResult(
        total_kg=result.totals.current.total_kg,
        metrics=extra,
        equivalences=result.totals.current.equivalences,
    )
    grown = CalculationResult(
        factor_set_version=result.factor_set_version,
        is_mock=result.is_mock,
        gwp_horizon=result.gwp_horizon,
        totals=CalculationTotals(
            current=scenario, alternative=None, net_benefit=None, money=None
        ),
        entries=result.entries,
    )
    codes = [row["code"] for row in build_context(grown, _taxonomy(), "en")["totals"]]
    assert codes == ["co2e", "cost_nzd"]

    # And structurally: no comparison anywhere in the renderer tests a value
    # against a metric code. Parsed rather than grepped, because the module's
    # own comment states the rule by quoting the thing it forbids.
    import ast

    tree = ast.parse(Path(pdf_render.__file__).read_text(encoding="utf-8"))
    compared = {
        node.value
        for compare in ast.walk(tree)
        if isinstance(compare, ast.Compare)
        for node in compare.comparators
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert compared.isdisjoint({"co2e", "cost_nzd", "water_l", "prevention"})


# --------------------------------------------------------------------------
# Task 4: twenty languages, and the two that mirror.
# --------------------------------------------------------------------------
#
# WHAT THE HAND-ROLLED EXPORT DID, so that what is asserted below is measured
# against it rather than against "looks fine":
#
#   * Right-to-left was not handled at all. `context.direction` and
#     `textAlign` were never set, so every accent bar, indent and footer
#     stayed physically left under Arabic and the bidi algorithm put the colon
#     on the wrong side of each label. The worst instance printed the
#     mock-data sentence's FULL STOP AT THE START OF THE LINE.
#   * Twelve of the twenty locales were unselectable, unsearchable and opaque
#     to a screen reader, because one character outside WinAnsi flipped the
#     whole document to a rasterised image.
#
# Neither is caught by "the PDF is not empty", and neither is caught by "there
# is text in it". The tests below therefore assert on three different kinds of
# evidence, and each says what it would miss on its own:
#
#   * the EXTRACTED TEXT - proves glyphs were drawn as text rather than as a
#     picture, and proves *which* words. Blind to where they are on the page.
#   * the DOCUMENT CATALOGUE's `/Lang` - what a screen reader and a search
#     index read. Blind to what was actually drawn.
#   * the LAID-OUT BOX TREE - the only thing that can tell a mirrored document
#     from one that merely contains Arabic, because a PDF stores glyphs in
#     drawing order and extraction reorders them either way.

#: Twenty catalogues plus English, whose catalogue is the source strings. Read
#: from the loader rather than listed, so a twenty-first language is covered by
#: every test here on the day its file lands.
ALL_LOCALES = i18n.languages()


def _normalise(text: str) -> str:
    """Extracted text, reduced to what can honestly be compared.

    Three things are removed, and each one is a real property of PDF text
    extraction rather than a convenience:

    * **Whitespace**, because where a line ends is the layout engine's
      decision, and because a mark composed onto a base letter can come back
      with a space beside it (Vietnamese `so` with its two marks extracts with
      the marks spaced away from the letter).
    * **Hyphens**, because `hyphens: auto` plus `<html lang>` means Pyphen now
      breaks German and French headings, and the soft hyphen is in the text.
    * **Case**, because `text-transform: uppercase` on the placeholder flag is
      applied by the renderer, so the drawn text is `PLATZHALTERDATEN` and the
      catalogue says `Platzhalterdaten`.

    NFC first, so a letter composed from base plus combining mark compares
    equal to its precomposed form. What is deliberately NOT done is any
    reordering: an assertion that reversed right-to-left text until it matched
    would be a test that passes for the wrong reason, and that is exactly the
    failure this project keeps producing.
    """
    text = unicodedata.normalize("NFC", text)
    for stripped in ("­", "‐", "-"):
        text = text.replace(stripped, "")
    return re.sub(r"\s+", "", text).casefold()


@requires_weasyprint
@pytest.mark.parametrize("locale", ALL_LOCALES)
def test_every_locale_renders_as_extractable_text(locale):
    """**Defect two's second half, per language.**

    Twelve of twenty locales came back as a picture. A picture yields no text,
    so this asserts text comes back - and then asserts *which* text, because
    "some text" is also what a document rendered entirely in English would
    give.

    Four things, and each kills a different failure:

    * text comes back at all -> not rasterised;
    * `/Lang` is this document's language -> a screen reader and a search
      index are told what they are reading;
    * the placeholder flag is present IN THIS LANGUAGE -> the catalogue was
      actually used, and section 2.2's warning reached the page rather than
      only the HTML;
    * the staff-typed destination name is present verbatim -> section 2.1's
      database rows are not translated, in any locale.

    **What it misses:** where any of it sits on the page. A document rendered
    left-to-right would pass every line of this, which is why
    `test_a_right_to_left_document_is_actually_mirrored` exists and reads the
    box tree instead.
    """
    pdf = render_results_pdf(_result(), _taxonomy(), locale)
    text = extract_text(pdf)
    assert text.strip(), f"{locale}: no extractable text - the page was rasterised"

    assert document_language(pdf) == locale, (
        f"{locale}: the document declares {document_language(pdf)!r}"
    )

    flag = i18n.catalogue(locale).gettext(MOCK_WARNING_FLAG)
    assert _normalise(flag) in _normalise(text), (
        f"{locale}: the placeholder warning is not on the page in this language"
    )

    assert "Landfill" in text, (
        f"{locale}: a staff-typed destination name was lost or translated"
    )


@pytest.mark.parametrize("locale", ALL_LOCALES)
def test_no_locale_falls_back_to_english(locale):
    """**Every string the document can print, in this language, in the HTML.**

    Asserted on the rendered HTML rather than on extracted text, and
    deliberately: extraction returns a PDF's glyphs in drawing order, so a
    right-to-left or a reordering script comes back with its clauses shuffled
    and a whole-sentence comparison against it would fail for Arabic, Tamil and
    Thai on grounds that have nothing to do with translation. The HTML is what
    the catalogue produced, exactly.

    **What it misses:** whether any of it was drawn. A stylesheet that hid an
    element would leave this green, which is what
    `test_every_locale_renders_as_extractable_text` and
    `test_the_mock_warning_is_in_the_pdf_text` cover from the other side.
    """
    from markupsafe import escape

    html = render_html(_result(), _taxonomy(), locale)
    catalogue = i18n.catalogue(locale)

    for source in DOCUMENT_STRINGS:
        translated = catalogue.gettext(source)
        assert str(escape(translated)) in html, (
            f"{locale}: {source!r} did not reach the document"
        )
        if locale != "en" and translated != source:
            assert str(escape(source)) not in html, (
                f"{locale}: the English source of {source!r} is in the document"
            )


@requires_weasyprint
@pytest.mark.parametrize("locale", [code for code in ALL_LOCALES if code != "en"])
def test_no_locale_prints_the_english_title(locale):
    """The same rule as above, read back off the PAPER rather than the HTML.

    One string, because one string is all that survives this comparison for
    every script - but it is the string a reader sees first, and a document
    that silently rendered in English would be caught by it in any language.
    """
    title = "Food Waste Impact Calculator — Results"
    text = _normalise(extract_text(render_results_pdf(_result(), _taxonomy(), locale)))
    english = _normalise(title)
    if _normalise(i18n.catalogue(locale).gettext(title)) != english:
        assert english not in text, f"{locale} rendered the English title"


@pytest.mark.parametrize("locale", [code for code in ALL_LOCALES if code != "en"])
def test_every_document_string_is_in_every_catalogue(locale):
    """**The check that keeps the strict lookup from ever firing in earnest.**

    `Catalogue.gettext` raises rather than falling back to English, which is
    right for a document read months later by somebody who cannot ask - but a
    renderer that raises in production is only an improvement on a renderer
    that lies if something catches the mismatch first. This is that something,
    and it runs without rendering anything, so it is green or red on every
    desk including the ones with no Pango.

    **What it misses:** a translation that is present but wrong, or present but
    still in English. `tests/web/test_i18n_web.py` owns both of those for the
    whole catalogue; this is about the twenty-one strings the export uses.
    """
    catalogue = i18n.catalogue(locale)
    missing = [key for key in DOCUMENT_STRINGS if key not in catalogue.strings]
    assert not missing, f"{locale} has no translation for: {missing}"


def test_a_missing_key_is_refused_rather_than_rendered_in_english(monkeypatch):
    """**The mutation, automated.**

    A catalogue with one key removed. The front end's rule - render the English
    source - is right for a page and wrong for this: a Tamil report with an
    English heading in the middle looks like a corrupted file, and one rendered
    *entirely* in English would be indistinguishable from one that had been
    asked for in English. So the render fails.
    """
    catalogue = i18n.catalogue("ta")
    without = dict(catalogue.strings)
    del without[MOCK_WARNING_BODY]
    monkeypatch.setitem(
        i18n._CATALOGUES,
        "ta",
        i18n.Catalogue("ta", without, catalogue.direction, catalogue.tags),
    )
    with pytest.raises(i18n.MissingTranslationError):
        render_html(_result(), _taxonomy(), "ta")


@requires_weasyprint
@pytest.mark.parametrize("locale", ALL_LOCALES)
def test_the_mock_warning_survives_in_every_locale(locale):
    """Section 2.2 in twenty-one languages, which is the only version of
    "non-dismissible" that means anything to a reader of one of the other
    twenty. O-1 means every figure this document can carry is placeholder data.

    The flag is checked on the paper; the body is guaranteed by the render
    having succeeded at all, because `_assert_mock_warning_present` compares
    BOTH translated literals against the rendered HTML and raises before
    WeasyPrint is called.
    """
    pdf = render_results_pdf(_result(), _taxonomy(), locale)
    flag = i18n.catalogue(locale).gettext(MOCK_WARNING_FLAG)
    assert _normalise(flag) in _normalise(extract_text(pdf))


# --------------------------------------------------------------------------
# The two that mirror.
# --------------------------------------------------------------------------


@requires_weasyprint
@pytest.mark.parametrize("locale", ["ar", "ur"])
def test_a_right_to_left_document_is_actually_mirrored(locale):
    """**Defect two, at the only place it can honestly be measured.**

    PR #46 rendered Arabic left-to-right and printed the mock-data sentence's
    full stop at the START of the line. Extracted text cannot catch that: a PDF
    stores glyphs in drawing order, so Arabic comes back reordered whichever
    base direction was used, and every text-level assertion passes on the
    broken rendering too.

    So this reads the laid-out box tree. A paragraph's LAST line is short, and
    which end of the measure that short line sits at is precisely the base
    direction - in a right-to-left document it hugs the right edge, and the
    sentence therefore *ends*, with its terminal punctuation, at the left. That
    is the visible failure, stated as a measurement.

    The accent bar is checked with it, because "every accent bar, indent and
    the footer stayed physically left" was the rest of the same finding:
    `results.css` writes `border-inline-start` and never `border-left`, so
    under `dir="rtl"` the bar has to resolve to the right edge.

    **What it misses:** nothing about the words. A document with the right
    geometry and the wrong language passes this, which is what the extraction
    tests above are for.

    **Mutation:** hardcode `dir="ltr"` in `results.html.j2` and both assertions
    fail - the last line moves to the left edge and the border moves with it.
    """
    document = pdf_render.render_document(_result(), _taxonomy(), locale)
    body = laid_out_lines(document, "mock-warning__body")

    assert len(body["lines"]) > 1, "single-line paragraph - this proves nothing"
    start, width = body["lines"][-1]
    assert width < body["content_width"] - 20, (
        "the last line fills the measure - a full line sits at both edges and "
        "this test would prove nothing"
    )

    measure_end = body["content_x"] + body["content_width"]
    assert abs((start + width) - measure_end) < 1.0, (
        f"{locale}: the last line ends at {start + width:.1f} on a measure "
        f"ending at {measure_end:.1f} - it is not right-aligned, so the "
        "sentence's terminal punctuation is at the wrong end of the line"
    )
    assert start > body["content_x"] + 20, (
        f"{locale}: the last line still starts at the left margin"
    )

    left, right = border_widths(document, "mock-warning")
    assert right > 0 and left == 0, (
        f"{locale}: the accent bar did not move to the right edge "
        f"(left={left}, right={right}) - border-inline-start did not mirror"
    )


@requires_weasyprint
def test_a_left_to_right_document_is_not_mirrored():
    """The other half, and the reason the test above is not vacuous: the same
    two measurements on English have to come out the other way round. Without
    this, a renderer that right-aligned everything in every language would pass
    the right-to-left test."""
    document = pdf_render.render_document(_result(), _taxonomy(), "en")
    body = laid_out_lines(document, "mock-warning__body")

    assert len(body["lines"]) > 1
    start, width = body["lines"][-1]
    assert width < body["content_width"] - 20
    assert abs(start - body["content_x"]) < 1.0, "the last line is not left-aligned"

    left, right = border_widths(document, "mock-warning")
    assert left > 0 and right == 0


@pytest.mark.parametrize("locale", ALL_LOCALES)
def test_the_document_direction_follows_the_catalogue(locale):
    """`dir` is read off the catalogue's own declaration, not guessed from the
    language subtag. Arabic and Urdu are the two right-to-left catalogues the
    calculator ships; everything else runs the other way, and a document that
    mirrored a language nobody had marked would be a layout defect introduced
    by a table rather than by a translator."""
    expected = "rtl" if locale in ("ar", "ur") else "ltr"
    assert build_context(_result(), _taxonomy(), locale)["dir"] == expected


def test_a_catalogue_that_does_not_declare_a_direction_reads_the_tag(monkeypatch):
    """**The fallback that keeps `text_direction` load-bearing.**

    `dir` is optional in the catalogue format, and every catalogue on disk
    happens to carry it - so a renderer that defaulted a silent file to
    left-to-right would look correct today and mirror a future Hebrew or
    Persian catalogue the wrong way the day somebody forgot the key. The
    language subtag answers instead, which is the one thing that cannot be
    forgotten: it is the file's name.
    """
    catalogue = i18n.catalogue("ar")
    silent = i18n.Catalogue("ar", catalogue.strings, None, catalogue.tags)
    monkeypatch.setitem(i18n._CATALOGUES, "ar", silent)
    assert build_context(_result(), _taxonomy(), "ar")["dir"] == "rtl"


@pytest.mark.parametrize(
    "requested, language",
    [
        ("ar", "ar"),
        ("ar-EG", "ar"),
        ("zh-TW", "zh-Hant"),
        ("zh-HK", "zh-Hant"),
        ("zh-CN", "zh"),
        ("zh-Hans", "zh"),
        ("en-NZ", "en"),
        ("fil", "tl"),
        ("he", "en"),
        ("qq", "en"),
        ("", "en"),
    ],
)
def test_the_document_declares_the_language_it_is_written_in(requested, language):
    """`zh-TW` must not truncate into Simplified Chinese, and a tag nobody
    claims must not leave the document claiming to be in a language it is not
    written in.

    `he` is the case worth reading twice: there is no Hebrew catalogue, so the
    document is English - and it says `lang="en"`, `dir="ltr"`. Declaring
    `lang="he"` would tell a screen reader to pronounce English as Hebrew and
    would mirror the page around text that runs the other way.
    """
    context = build_context(_result(), _taxonomy(), requested)
    assert context["lang"] == language
    assert 'lang="%s"' % language in render_html(_result(), _taxonomy(), requested)


# --------------------------------------------------------------------------
# Font coverage: a box is a defect.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("locale", ALL_LOCALES)
def test_every_character_the_document_can_print_has_a_glyph(locale):
    """**The fonts actually cover the scripts in use, checked per language.**

    The brand faces are 228-glyph Latin subsets and the twenty languages span
    eleven scripts. A character with no glyph in any embedded face is drawn as
    an empty box or dropped, which is the same class of defect as the WinAnsi
    rasterisation this export replaces - it looks fine to whoever generated it.

    Read out of the font files' own character maps, not out of a table beside
    them: a table is a second thing to keep in step, and the day it drifts is
    the day this passes for the wrong reason.

    **What it misses:** which face a character is drawn in - Traditional
    Chinese set in Simplified glyphs would pass. That is what the per-language
    ordering in `results.css` is for, and what
    `test_the_script_faces_are_embedded_in_the_pdf` checks from the file.
    """
    catalogue = i18n.catalogue(locale)
    text = "".join(catalogue.gettext(key) for key in DOCUMENT_STRINGS)
    undrawable = sorted({c for c in text if not pdf_render._is_drawable(c)})
    assert not undrawable, (
        f"{locale} would print these as empty boxes: "
        f"{[hex(ord(c)) for c in undrawable]}"
    )


def test_no_character_in_any_catalogue_would_print_as_a_box():
    """**The widest form of the coverage question, and it comes out clean.**

    Not the twenty-one strings the document prints today - every string in
    every catalogue, 340 of them times twenty languages, 1,861 distinct
    characters across eleven scripts. All of them have a glyph in some embedded
    face.

    That is a stronger claim than the document needs, and it is asserted at the
    wider scope on purpose: the export's copy is assembled from catalogue keys,
    so the set it draws from is the set below. A key swapped for another one
    tomorrow cannot introduce a box.

    **What it misses:** a staff-typed taxonomy name, which is not in any
    catalogue and can contain anything. That is what
    `test_a_character_no_face_can_draw_is_refused_and_not_drawn_as_a_box`
    covers, and why the renderer raises rather than drawing one.
    """
    everything = set()
    for locale in ALL_LOCALES:
        for value in i18n.catalogue(locale).strings.values():
            everything.update(value)
    assert len(everything) > 1500, f"only {len(everything)} characters - fixture broken"
    undrawable = sorted({c for c in everything if not pdf_render._is_drawable(c)})
    assert not undrawable, [hex(ord(c)) for c in undrawable]


@requires_weasyprint
def test_a_character_no_face_can_draw_is_refused_and_not_drawn_as_a_box():
    """**What stops the CJK subsets being a silent gap.**

    The four CJK faces are cut to the characters the catalogues contain,
    because whole ones are 10.9 MiB each and there are four
    (`api/assets/fonts/noto/PROVENANCE.md` has the measurement). A staff-typed
    taxonomy name in Chinese could therefore contain a character no embedded
    face has - and WeasyPrint would draw an empty box and say nothing, which is
    the defect this whole task exists to stop.

    It raises instead, naming the character. A loud failure, not tofu.
    """
    rare = "鱻"  # a Han character outside every embedded subset
    assert not pdf_render._is_drawable(rare), (
        "this character is now covered - pick another, or delete this test"
    )
    with pytest.raises(UndrawableCharacterError) as raised:
        render_results_pdf(_result(), _taxonomy(destination_name=rare), "zh")
    assert "U+9C7B" in str(raised.value)


@requires_weasyprint
@pytest.mark.parametrize(
    "locale, face",
    [
        ("ar", "Noto-Sans-Arabic"),
        ("ur", "Noto-Sans-Arabic"),
        ("ja", "Noto-Sans-CJK-JP"),
        ("ko", "Noto-Sans-CJK-KR"),
        ("zh", "Noto-Sans-CJK-SC"),
        ("zh-Hant", "Noto-Sans-CJK-TC"),
        ("ta", "Noto-Sans-Tamil"),
        ("th", "Noto-Sans-Thai"),
        ("hi", "Noto-Sans-Devanagari"),
        ("pa", "Noto-Sans-Gurmukhi"),
        ("gu", "Noto-Sans-Gujarati"),
        ("ml", "Noto-Sans-Malayalam"),
    ],
)
def test_the_script_faces_are_embedded_in_the_pdf(locale, face):
    """**Read back out of the file, because a failed fetch is silent.**

    WeasyPrint catches whatever a URL fetcher raises and merely logs "Failed to
    load", so a face that did not load produces a good-looking document set in
    whatever the host had lying around - and on a machine with
    `fonts-noto-core` installed that is a *different copy of the same family*,
    which no other assertion here could tell apart. The font names in the PDF's
    own resource dictionary can.

    Traditional Chinese is in the list beside Simplified for the reason the
    per-language ordering in `results.css` exists: the four CJK faces overlap,
    and a single stack would set one of the two in the other's glyphs.
    """
    from io import BytesIO

    from pypdf import PdfReader

    pdf = render_results_pdf(_result(), _taxonomy(), locale)
    names = set()
    for page in PdfReader(BytesIO(pdf)).pages:
        fonts = page.get("/Resources", {}).get("/Font", {})
        for key in fonts:
            names.add(str(fonts[key].get_object().get("/BaseFont", "")))
    joined = " ".join(sorted(names))
    assert face in joined, f"{locale} is not set in {face}: {sorted(names)}"

    # AND NOT IN A SIBLING'S GLYPHS. This half was added after the mutation
    # test found the first half green with the per-language ordering deleted:
    # the four CJK subsets are each cut to their own catalogue, so a document
    # set in the wrong one still reaches the right one for the characters that
    # one happens to lack - and "Noto-Sans-CJK-TC is in the file" was true of a
    # Traditional document half-set in Simplified. The claim that matters is
    # that no sibling is there at all.
    siblings = {
        "Noto-Sans-CJK-JP",
        "Noto-Sans-CJK-KR",
        "Noto-Sans-CJK-SC",
        "Noto-Sans-CJK-TC",
    } - {face}
    for sibling in sorted(siblings):
        assert sibling not in joined, (
            f"{locale} is partly set in {sibling} - the per-language font "
            "ordering in results.css is not doing its job"
        )


def test_the_embedded_catalogues_match_the_public_ones():
    """`api/assets/locales/` is a copy of `web/locales/` and has to be -
    package-data cannot cross a package boundary and `web/` is not in the API
    image's build context. What it must not be is a DIFFERENT copy: a visitor
    who reads a label on the screen and then downloads the PDF must not meet
    two translations of it."""
    served = sorted((ROOT / "web" / "locales").glob("*.json"))
    embedded = sorted((ROOT / "api" / "assets" / "locales").glob("*.json"))
    assert [p.name for p in served] == [p.name for p in embedded]
    for one, other in zip(served, embedded):
        assert (
            hashlib.sha256(one.read_bytes()).hexdigest()
            == hashlib.sha256(other.read_bytes()).hexdigest()
        ), one.name


def test_the_fallback_faces_ship_with_their_licence():
    """The SIL OFL requires the licence to travel with the font. A wheel that
    carried the faces and not the licence would be a redistribution breaking
    its own terms, and `pyproject.toml` lists the `.txt` and `.md` patterns for
    exactly this reason."""
    directory = ROOT / "api" / "assets" / "fonts" / "noto"
    faces = sorted(directory.glob("*.woff2"))
    assert len(faces) == 12, [p.name for p in faces]
    licences = sorted(directory.glob("LICENCE-*.txt"))
    assert licences, "no licence beside the fonts"
    for licence in licences:
        # Case-insensitive: Debian's two copyright files state the same grant
        # in two different casings ("SIL OPEN FONT LICENSE Version 1.1" as a
        # heading, "the SIL Open Font License, Version 1.1" as a sentence), and
        # which one a package uses is not a property worth asserting.
        text = licence.read_text(encoding="utf-8", errors="replace").lower()
        assert "sil open font license" in text, licence.name
        assert "1.1" in text, licence.name
    assert (directory / "PROVENANCE.md").is_file()


def test_the_stylesheet_names_every_embedded_face():
    """A face on disk that no rule names is dead weight in the wheel; a face
    named by a rule and missing from disk is a render that silently falls back.
    Both are caught by comparing the two lists."""
    source = pdf_render._STYLESHEET.read_text(encoding="utf-8")
    for placeholder, filename in pdf_render._FONT_PLACEHOLDERS.items():
        assert placeholder in source, placeholder
        assert (pdf_render._FONT_DIR / filename).is_file(), filename
    on_disk = {p.name for p in (pdf_render._FONT_DIR / "noto").glob("*.woff2")}
    declared = {
        Path(name).name
        for name in pdf_render._FONT_PLACEHOLDERS.values()
        if name.startswith("noto/")
    }
    assert on_disk == declared
