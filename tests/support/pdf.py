"""Test helpers for the server-rendered PDF export.

**Why a skip exists here at all, and why it cannot happen quietly.**

WeasyPrint's Python package installs from PyPI on any platform, but it binds
Pango, HarfBuzz and GObject through cffi at import time and those are system
libraries. `docker/api.Dockerfile` and `docker/admin.Dockerfile` apt-get them;
CI installs them too (`.github/workflows/_test.yaml`); a bare Windows checkout
has none of them and the import raises `OSError: cannot load library
'libgobject-2.0-0'`. The team is split across Windows and macOS, so a suite
that went red on every desk that had never rendered a PDF would be a suite
people learn to ignore.

**But this project's signature failure is a test that passes for the wrong
reason, and a test that silently does not run is the purest form of it.** So
the skip is not unconditional: `KAICALC_PDF_REQUIRED=1` turns it into an
import-time failure that names the missing libraries, and CI sets that variable.
On a machine that is supposed to be able to render, "skipped" is not an
available outcome.
"""

from __future__ import annotations

import os
import re
from io import BytesIO

import pytest

from api.pdf_render import weasyprint_unavailable_reason

#: `None` on a host that can render, otherwise the import error, verbatim.
WEASYPRINT_REASON = weasyprint_unavailable_reason()

#: Set to `1` wherever rendering is a requirement rather than a nicety - the
#: CI job, and the container. Anywhere it is set, a host that cannot render
#: fails collection instead of skipping.
PDF_REQUIRED = os.environ.get("KAICALC_PDF_REQUIRED") == "1"

if WEASYPRINT_REASON is not None and PDF_REQUIRED:
    raise RuntimeError(
        "KAICALC_PDF_REQUIRED=1 but WeasyPrint cannot render on this host: "
        f"{WEASYPRINT_REASON}\n"
        "Install its system libraries (libpango-1.0-0, libpangoft2-1.0-0, "
        "libharfbuzz-subset0) or unset KAICALC_PDF_REQUIRED. The PDF tests are "
        "not allowed to skip where they are required - a skipped test is the "
        "one that proves nothing while looking green."
    )

requires_weasyprint = pytest.mark.skipif(
    WEASYPRINT_REASON is not None,
    reason=(
        "WeasyPrint's system libraries are not installed on this host "
        f"({WEASYPRINT_REASON}). Run these in the API container, or set "
        "KAICALC_PDF_REQUIRED=1 to make this an error."
    ),
)


def extract_text(pdf: bytes) -> str:
    """The text a reader can select out of the document, whitespace-normalised.

    **This, and not `len(pdf) > 0`, is what a PDF test asserts on.** The export
    this replaces produced a file that was a perfectly valid PDF, opened in
    every reader, and said `K?mara`; another variant of the same defect produced
    a 71 KB image with no extractable text at all. Both pass "is it a PDF" and
    both are the bug. Reading the text back out is the only assertion that can
    tell the difference, and it is also what catches a CSS change that hides an
    element - a hidden banner is not drawn, so it is not in here.

    Line breaks are collapsed to single spaces because where a line ends is the
    layout engine's decision and changes with the measure; a test that asserted
    on it would be asserting on the font metrics.
    """
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(pdf))
    raw = "\n".join(page.extract_text() or "" for page in reader.pages)
    return re.sub(r"\s+", " ", raw).strip()


def overflowing_boxes(document: object) -> list[str]:
    """Every laid-out text box that extends past its page, described.

    **The assertion the old exporter would have failed.** A German title
    printed 84pt off a 595pt page, and no amount of reading text back out of
    the file would have caught it: a glyph drawn past the edge of the paper is
    still in the content stream and still extracted. The page box tree is where
    the truth is, so this walks it and compares each text box's inline extent
    against the page's own width.

    Half a point of tolerance: WeasyPrint lays out in floating-point CSS pixels
    and a box that ends exactly on the margin can land a fraction over.
    """
    from weasyprint.formatting_structure import boxes as weasy_boxes

    problems: list[str] = []
    for number, page in enumerate(document.pages, start=1):
        limit = page.width
        for box in page._page_box.descendants():
            if not isinstance(box, weasy_boxes.TextBox):
                continue
            start = getattr(box, "position_x", None)
            width = getattr(box, "width", None)
            if start is None or width is None or width == "auto":
                continue
            if start < -0.5 or start + width > limit + 0.5:
                problems.append(
                    f"page {number}: {box.text[:60]!r} spans "
                    f"{start:.1f}..{start + width:.1f} on a {limit:.1f} page"
                )
    return problems
