"""``POST /api/v1/export/pdf`` — the request shape, and the call into the
document renderer.

**Why this route exists at all.** A teammate's hand-rolled, four-hundred-line
PDF export ran entirely in the browser: German titles ran off the page,
Arabic rendered left-to-right with the full stop stranded at the line's
start, and a staff-typed name outside WinAnsi turned the whole document into
an unreadable image. The fix moves rendering to the server, where a real
layout engine can shape and order the text. This module is the request/
response contract for that move; ``api/pdf_render.py`` is the layout engine's
invocation, and it does not lay anything out by hand either — WeasyPrint is
given HTML and CSS and Pango and HarfBuzz do the work.

**Why the endpoint recalculates instead of trusting the client.** This
document exists to be attached to an email and believed months later, so its
figures must be the engine's, computed on the request that downloads it —
never the numbers a browser happened to be holding. ``ExportPayload`` is
therefore a *request* shape (sector, food category, scenario lines), not a
*result* shape: it has no field a client's own totals could occupy, and
``extra="forbid"`` — inherited from ``PricingOptions`` — refuses a payload
that tries to add one. There is nowhere in this model for a precomputed
figure to hide.

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
here.

**But it no longer repeats that model's validators.** ``gwp_horizon`` and
``time_frame`` — and the two closed-vocabulary checks over them — now live on
``api.schemas.PricingOptions``, which both payloads inherit. Task 2's
implementer flagged the duplication and was right to: duplicated validators
drift, and a horizon this route accepted while ``/calculate`` refused it would
mean two documents of one request disagreeing about methane, with nothing to
say which was right. The shared base carries exactly the two fields the two
models have in common and none of the four they do not, so the reason for not
inheriting ``CalculatePayload`` is untouched.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from api.pdf_render import render_results_pdf
from api.schemas import MAX_ENTRIES, EntryPayload, PricingOptions

#: A locale tag, still not validated against a closed vocabulary the way
#: ``gwp_horizon`` and ``time_frame`` are - and the reason has changed shape
#: rather than gone away. The renderer now negotiates the tag against the
#: calculator's own catalogues (``api/i18n.resolve``), exactly as the page
#: does: ``zh-TW`` reaches Traditional Chinese, ``en-GB`` reaches English, and
#: a tag nobody claims reaches English rather than being refused. That is
#: deliberate - a download is not the place to tell somebody their browser's
#: language is unsupported - so there is nothing here for an allow-list to
#: reject. Bounded only so an absurd value cannot reach the ``lang``
#: attribute. §O-8 still has not settled which interface languages are
#: *promised*; twenty are shipped.
_LOCALE_MIN = 2
_LOCALE_MAX = 35


class ExportPayload(PricingOptions):
    """The request ``/calculate`` takes, plus the locale the document renders
    in — see the module docstring for why this does not subclass
    ``CalculatePayload`` itself, and why it no longer restates its checks."""

    entries: list[EntryPayload] = Field(min_length=1, max_length=MAX_ENTRIES)
    locale: str = Field(min_length=_LOCALE_MIN, max_length=_LOCALE_MAX)


#: The filename every export answers with. One name rather than one derived
#: from the request, because nothing in the payload is safe to put in a
#: `Content-Disposition` header unescaped, and a fixed name is what every
#: other download-shaped route in this API already does (`_csv_zip` in
#: `api/router.py`).
EXPORT_FILENAME = "kai-commitment-impact-calculator.pdf"


def render_export_pdf(result: Any, payload: ExportPayload, taxonomy: Any) -> bytes:
    """Build the response body for `POST /api/v1/export/pdf`.

    `result` is whatever `EngineAdapter.calculate` returned for this exact
    request — the same object `api/engine_adapter.py`'s `serialize_result`
    reads for `/calculate`'s JSON body. `taxonomy` is the §5.1 snapshot the
    route loaded through `db/repository.get_taxonomy`, and it is here for one
    reason: `result` speaks in `code`s, and a document a person reads has to
    say "Landfill", not `landfill`. Those names are staff-typed rows and are
    printed exactly as typed, in every locale.

    **The mock-data warning is wired through unconditionally, and this
    function is not where it could be lost.** §2.2 makes the placeholder
    banner mandatory and non-dismissible on every results view and export
    while the active factor set is mock, and `result.is_mock` is read straight
    off the engine's own result inside the renderer — there is no parameter
    here that could suppress it and no branch that treats "mock" as optional.
    `api/pdf_render._assert_mock_warning_present` then re-reads the *rendered*
    document and raises if the banner is not in it, so a template edit that
    dropped it produces a 500 rather than a quiet placeholder document
    somebody forwards.

    This function performs no arithmetic and no formatting of its own; it is
    three arguments and a call, so that there is exactly one place a figure is
    turned into text.
    """
    return render_results_pdf(result, taxonomy, payload.locale)
