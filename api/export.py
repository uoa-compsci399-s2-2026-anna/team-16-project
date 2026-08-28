"""``POST /api/v1/export/pdf`` — the request shape and a stand-in renderer.

**Why this route exists at all.** A teammate's hand-rolled, four-hundred-line
PDF export ran entirely in the browser: German titles ran off the page,
Arabic rendered left-to-right with the full stop stranded at the line's
start, and a staff-typed name outside WinAnsi turned the whole document into
an unreadable image. The fix moves rendering to the server, where a real
layout engine can shape and order the text. This module is the request/
response contract for that move; the layout engine itself is a later task
(see ``_placeholder_pdf`` below).

**Why the endpoint recalculates instead of trusting the client.** This
document exists to be attached to an email and believed months later, so its
figures must be the engine's, computed on the request that downloads it —
never the numbers a browser happened to be holding. ``ExportPayload`` is
therefore a *request* shape (sector, food category, scenario lines), not a
*result* shape: it has no field a client's own totals could occupy, and
``model_config = ConfigDict(extra="forbid")`` refuses a payload that tries to
add one. There is nowhere in this model for a precomputed figure to hide.

**Why the route in ``api/router.py`` persists nothing.** One calculation
equals one submission (§2.3); a download is not a calculation. The route
never calls ``upsert_submission`` and never touches ``X-Dry-Run`` or the
staff proof header — both are ``/calculate``'s concerns for a different kind
of call (persisted, or an authenticated staff rehearsal) and neither applies
to a stateless export. It always prices against the published factor set,
exactly as ``/calculate``'s own non-dry-run path does.

**Deliberately not a subclass of ``CalculatePayload``.** That model also
carries ``token`` (what a persisted calculation is looked up or resumed by)
and ``dry_run`` (a staff-only alternate bundle). Neither means anything to a
route that persists nothing and always prices the published set, and
inheriting them would invite a caller to believe one of them does something
here. ``ExportPayload`` instead reuses ``EntryPayload`` — the same scenario
and mass-conservation rules — and repeats ``gwp_horizon``/``time_frame``'s two
one-line closed-vocabulary checks rather than pull in the fields that carry
no export meaning.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from api.schemas import MAX_ENTRIES, TIME_FRAMES, EntryPayload

#: A locale tag, not yet validated against a closed vocabulary the way
#: ``gwp_horizon`` and ``time_frame`` are: nothing downstream of this field
#: exists yet to say which locales are supported (that is the renderer's
#: job, landing in a later task). Bounded so an absurd value cannot reach it,
#: not because a real allow-list exists to check against.
_LOCALE_MIN = 2
_LOCALE_MAX = 35


class ExportPayload(BaseModel):
    """The request ``/calculate`` takes, plus the locale the document renders
    in — see the module docstring for why this does not subclass
    ``CalculatePayload`` itself."""

    model_config = ConfigDict(extra="forbid")

    gwp_horizon: int = 100
    time_frame: str | None = None
    entries: list[EntryPayload] = Field(min_length=1, max_length=MAX_ENTRIES)
    locale: str = Field(min_length=_LOCALE_MIN, max_length=_LOCALE_MAX)

    @field_validator("gwp_horizon")
    @classmethod
    def validate_horizon(cls, value: int) -> int:
        if value not in (20, 100):
            raise ValueError("must be 20 or 100")
        return value

    @field_validator("time_frame")
    @classmethod
    def validate_time_frame(cls, value: str | None) -> str | None:
        if value is not None and value not in TIME_FRAMES:
            raise ValueError(f"must be one of {sorted(TIME_FRAMES)}")
        return value


#: The filename every export answers with. One name rather than one derived
#: from the request, because nothing in the payload is safe to put in a
#: `Content-Disposition` header unescaped, and a fixed name is what every
#: other download-shaped route in this API already does (`_csv_zip` in
#: `api/router.py`).
EXPORT_FILENAME = "kai-commitment-impact-calculator.pdf"


def _escape_pdf_text(text: str) -> str:
    """Backslash, and the two parenthesis characters `Tj`'s literal-string
    syntax treats specially. Nothing else: the placeholder text below is
    written in this module, not typed by a caller, so this exists for the
    `/` in a factor-set version label, not for arbitrary input."""
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _placeholder_pdf(lines: list[str]) -> bytes:
    """**The stand-in renderer this task promises and the next task removes.**

    This is not the document — it is the smallest thing that is unambiguously
    a valid, single-page PDF, built by hand over PDF's own object syntax with
    no dependency at all. It exists solely so this route's third test
    (`response.content[:5] == b"%PDF-"`) can be green before a real layout
    engine exists to answer it.

    **What it does:** one page (US Letter, 612x792pt), one Type1 Helvetica
    font, each string in `lines` placed as its own left-aligned `Tj` text
    run at 12pt, 18pt apart, top to bottom. Byte offsets for the `xref` table
    are computed from what has actually been written rather than hard-coded,
    so the file is a structurally valid PDF that any reader can open — not
    only one that happens to skip the xref and re-scan for objects.

    **What it does not do, and why those are exactly the three defects this
    task exists to close:** it writes `Tj` directly over Python string
    slicing, so a character outside WinAnsi is not shaped, substituted, or
    even detected — `.encode("latin-1", "replace")` silently turns it into
    `?`. It has no line-wrapping or page-measurement, so a long title is not
    wrapped or shrunk, only carried off the right edge of the page. And it
    lays out strictly left-to-right with no bidi algorithm, so RTL text and
    the punctuation beside it would land in the wrong order. **A real layout
    engine (the next task) is what replaces this function** — everything
    above it in this module (the request shape, the recalculation, the
    persist-nothing rule) stays exactly as it is; only the four or so lines
    that currently build `objects` need to become a call into that engine.
    """
    content_lines = []
    y = 760
    for line in lines:
        content_lines.append(f"BT /F1 12 Tf 72 {y} Td ({_escape_pdf_text(line)}) Tj ET")
        y -= 18
    content = "\n".join(content_lines).encode("latin-1", "replace")

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 5 0 R >> >> "
        b"/MediaBox [0 0 612 792] /Contents 4 0 R >>",
        b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n"
        + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    buf = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(buf))
        buf += f"{index} 0 obj\n".encode("ascii")
        buf += body
        buf += b"\nendobj\n"

    xref_offset = len(buf)
    buf += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    buf += b"0000000000 65535 f \n"
    for offset in offsets:
        buf += f"{offset:010d} 00000 n \n".encode("ascii")
    buf += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF"
    ).encode("ascii")
    return bytes(buf)


def render_export_pdf(result: Any, payload: ExportPayload) -> bytes:
    """Build the response body for `POST /api/v1/export/pdf`.

    `result` is whatever `EngineAdapter.calculate` returned for this exact
    request — the same object `api/engine_adapter.py`'s `serialize_result`
    reads for `/calculate`'s JSON body — so a real figure is always one
    attribute access away from this function; today it does not read one,
    because there is no layout engine yet to place it (see
    `_placeholder_pdf`).

    **The mock-data warning is wired through unconditionally, not left for
    the next task to remember.** §2.2 makes the placeholder banner mandatory
    and non-dismissible on every results view and export while the active
    factor set is mock, and `result.is_mock` is read straight off the
    engine's own result here — there is no parameter that could suppress it
    and no branch that treats "mock" as optional. The line this function
    emits is plain text rather than the client's styled banner because that
    banner does not exist in a PDF yet; that it appears at all, unconditionally,
    is the part later work must preserve.
    """
    lines = ["Kai Commitment Impact Calculator"]
    if result.is_mock:
        lines.append(
            "PLACEHOLDER DATA: this factor set is not final. Do not treat these "
            "figures as real."
        )
    lines.append(
        f"Factor set: {result.factor_set_version} | locale: {payload.locale}"
    )
    lines.append(
        "This export is a placeholder pending the server-side PDF renderer; "
        "it carries no calculated figures yet."
    )
    return _placeholder_pdf(lines)
