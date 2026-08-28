"""`POST /api/v1/export/pdf` (task 2 of the server-rendered PDF export).

Follows the same fixtures and conventions as `tests/api/test_api.py`: the
`app` fixture (`tests/support/sqlite.py`) is a SQLite-backed app wired to
`FakeEngineAdapter`, and `app.state.session_factory` is how a test reads back
what a request did or did not persist.
"""

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from db.models import Submission
from tests.support.pdf import requires_weasyprint

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

pytestmark = pytest.mark.asyncio


def _valid_payload() -> dict:
    """§6.2's request fixture, reshaped into an export request.

    `token` and `dry_run` are `/calculate`-only fields - `ExportPayload`
    declares neither (see `api/export.py`'s module docstring for why) - so
    both are dropped rather than left at their fixture value of `null`;
    `extra="forbid"` refuses an unknown field regardless of what it is set
    to, and leaving them in would make every "this succeeds" test below fail
    for a reason that has nothing to do with what it is testing.
    """
    body = json.loads((FIXTURES / "calculate_request.json").read_text(encoding="utf-8"))
    body.pop("token", None)
    body.pop("dry_run", None)
    body["locale"] = "en"
    return body


async def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def test_the_export_calculates_rather_than_trusting_the_client(app):
    """The document exists to be believed, so its figures are the engine's.
    A payload carrying its own totals must be refused, not rendered."""
    body = _valid_payload() | {"totals": {"current": {"total_kg": "999"}}}
    async with await _client(app) as client:
        response = await client.post("/api/v1/export/pdf", json=body)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@requires_weasyprint
async def test_the_export_persists_nothing(app):
    """A download is not a calculation (§2.3): no `submission` row, whatever
    the response looks like. Read directly off the app's own database rather
    than trusted from the response, per the brief - a request-body assertion
    cannot see a row that was not supposed to exist."""
    with app.state.session_factory() as db:
        before = db.scalar(select(func.count()).select_from(Submission))

    async with await _client(app) as client:
        response = await client.post("/api/v1/export/pdf", json=_valid_payload())
    assert response.status_code == 200, response.text

    with app.state.session_factory() as db:
        after = db.scalar(select(func.count()).select_from(Submission))
    assert after == before, (
        "an export created a submission - a download is not a calculation"
    )


@requires_weasyprint
async def test_the_export_answers_a_pdf(app):
    async with await _client(app) as client:
        response = await client.post("/api/v1/export/pdf", json=_valid_payload())
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert response.content[:5] == b"%PDF-"
    # `%PDF-` is a byte check, not evidence about the document. What the
    # document says is asserted in tests/api/test_pdf_render.py, by extracting
    # the text - the export this route replaced produced a perfectly valid PDF
    # that said `K?mara`. This test is about the ROUTE: status, content type,
    # and that the renderer was reached at all.


async def test_a_prevention_destination_is_refused_in_a_current_scenario(app):
    """The export runs the same §6.2 entry rules `/calculate` does - not a
    lighter check, since it drives the same engine over the same taxonomy."""
    payload = _valid_payload()
    payload["entries"][0]["current"] = [
        {"destination": "prevention", "qty_kg": "10.000"}
    ]
    async with await _client(app) as client:
        response = await client.post("/api/v1/export/pdf", json=payload)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_an_unknown_locale_shape_is_refused(app):
    """`extra="forbid"` again, this time on a bare structural mistake rather
    than a smuggled figure - the same guard, exercised from a different
    angle so it is not credited to the "totals" key alone."""
    body = _valid_payload() | {"unexpected_field": "x"}
    async with await _client(app) as client:
        response = await client.post("/api/v1/export/pdf", json=body)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
