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
    ScenarioResult,
)
from tests.support.pdf import extract_text, overflowing_boxes, requires_weasyprint

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
            money=None,
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
    assert "mock emissions factors" in text.lower()


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
