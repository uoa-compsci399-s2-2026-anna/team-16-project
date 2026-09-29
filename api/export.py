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

**But it no longer repeats that model's validators.** ``gwp_horizon``,
``time_frame`` and — from v1.67 — ``period_start`` and ``period_end``, with
every check over them, live on ``api.schemas.PricingOptions``, which both
payloads inherit. Task 2's implementer flagged the duplication and was right
to: duplicated validators drift, and a horizon this route accepted while
``/calculate`` refused it would mean two documents of one request disagreeing
about methane, with nothing to say which was right. v1.67's interval joined
them there for the same reason — a download and the calculation it documents
must not disagree about the period the figures cover either. The shared base
carries exactly the fields the two models have in common and none of the four
they do not, so the reason for not inheriting ``CalculatePayload`` is
untouched.

**This route persists nothing, so the interval is a label here and not even a
stored one.** ``period_start`` and ``period_end`` reach ``ExportPayload`` so
that the document can print the period its figures cover; no row is written,
and ``upsert_submission`` is not called (see above).

**And from v1.68 the document does print it.** ``render_export_pdf`` hands the
payload itself to the renderer as its ``period``, because the payload is
already exactly the three fields a period is -- ``time_frame``,
``period_start`` and ``period_end`` -- and passing it whole is what stops a
fourth shape of "a period" existing. ``api/pdf_render.py::_period_text`` is
where it becomes a sentence, and it is the same sentence
``web/js/results.js`` puts on the screen and into the text download.
"""

from __future__ import annotations

from datetime import datetime
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


#: The filename this route's `Content-Disposition` header names — and, for the
#: calculator itself, inert. `web/js/results.js`'s `downloadPdf` takes a `Blob`
#: from a completed `fetch` rather than a navigation the browser could read a
#: header from, and stamps its own name via `exportFilename()` on the `<a
#: download>` it creates, so nothing under `web/` ever reads this constant. The
#: route is POST-only — a `GET` answers 405 — so no browser ever navigates here
#: directly either; the header only names the file for a caller that issues the
#: POST itself and saves the response as-is (curl, say, or a future API
#: consumer). Still one fixed name rather than one derived from the request,
#: because nothing in the payload is safe to put in the header unescaped — the
#: same reason `_csv_zip` in `api/router.py` uses a fixed name too.
EXPORT_FILENAME = "kai-commitment-impact-calculator.pdf"


def render_export_pdf(
    result: Any, payload: ExportPayload, taxonomy: Any, generated_at: datetime
) -> bytes:
    """Build the response body for `POST /api/v1/export/pdf`.

    `generated_at` is read once, by the route in `api/router.py`, and passed
    in rather than read here - the title block's "generated" fact has to name
    the moment this request was actually served, and a value threaded through
    as a parameter is what lets a test pin that moment to something fixed
    instead of racing the wall clock.

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

    `period` is the payload itself (v1.68), which already carries exactly the
    three fields a reporting period is — `time_frame`, `period_start` and
    `period_end`, all three inherited from `PricingOptions`. Passing the whole
    payload rather than unpacking the three is deliberate: a fourth object
    meaning "a period" is a fourth thing that can disagree with the other
    three, and the renderer reads them by name the same way it reads `result`
    and `taxonomy`.

    This function performs no arithmetic and no formatting of its own; it is
    four arguments and a call, so that there is exactly one place a figure is
    turned into text.
    """
    return render_results_pdf(
        result, taxonomy, payload.locale, generated_at, period=payload
    )
