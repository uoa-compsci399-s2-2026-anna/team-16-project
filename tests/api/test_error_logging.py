"""What an operator has to work with when the API answers 500.

Before this file the answer was nothing at all: the envelope going out was
correct and the exception behind it reached no log, no file and no stream. A
probe that raised `RuntimeError("probe-marker-9f3a")` inside a route produced a
clean 500 and zero log records naming it.

Every test here asserts a pair — that the record exists AND that its content is
what an operator needs — because "it logs something" is satisfied by logging the
wrong thing, and this project has shipped that shape eleven times.
"""

import logging

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio


def rendered(record: logging.LogRecord) -> str:
    """The record as a handler would emit it, traceback included."""
    return logging.Formatter("%(name)s %(levelname)s %(message)s").format(record)


def errors_from(caplog) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.levelno >= logging.ERROR]


async def test_a_deliberate_five_hundred_logs_the_exception_behind_it(
    app, caplog, monkeypatch
):
    """`_repository_problem` maps an unrecognised repository failure onto a bare
    `INTERNAL_ERROR`. The caller is owed exactly that and no more; the operator
    is owed the class and the traceback, which `raise … from exc` has always
    carried and nothing has ever read."""

    class TaxonomyExploded(RuntimeError):
        pass

    def boom(_db):
        raise TaxonomyExploded("marker-7c1e")

    monkeypatch.setattr("api.router.get_taxonomy", boom)

    with caplog.at_level(logging.DEBUG):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.get("/api/v1/taxonomy")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert response.json()["error"]["message"] == "An internal error occurred"

    records = errors_from(caplog)
    assert len(records) == 1, f"expected one error record, got {len(records)}"
    text = rendered(records[0])
    assert "TaxonomyExploded" in text, text
    assert "marker-7c1e" in text, text
    assert "GET" in text and "/api/v1/taxonomy" in text, text


async def test_a_four_hundred_is_not_an_operator_incident(app, caplog):
    """A visitor sending something bad is the system working. Logging a
    traceback for it makes the log unreadable on the day it is needed — and this
    is the affirmative half: it is not enough that failures log, non-failures
    must not.

    **Both 400 paths, because they are different code.** A route that raises
    `ApiProblem(400, …)` reaches `api_problem_handler`, the function this task
    edits. A malformed body never gets that far: FastAPI raises
    `RequestValidationError` and `validation_handler` builds the envelope by
    calling `problem_response` directly. Only the first can exercise the
    `status >= 500` condition, so a test that used the second alone would pass
    against `if exc.status >= 400` — a fix that logged every validation error —
    and prove nothing."""
    with caplog.at_level(logging.DEBUG):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            # Raised in the route: api/router.py:261. Goes through the handler.
            raised = await client.get("/api/v1/factors?format=xml")
            # Rejected before the route: entries has min_length=1. Goes through
            # validation_handler instead.
            malformed = await client.post("/api/v1/calculate", json={"entries": []})

    assert raised.status_code == 400 and malformed.status_code == 400
    assert errors_from(caplog) == []


async def test_an_unhandled_exception_is_written_down_before_it_becomes_a_500(
    app, caplog
):
    """The case nothing in the codebase anticipated — a bug, not a condition.
    The response is deliberately identical to every other `INTERNAL_ERROR`: the
    caller learns nothing, and that is right. The operator learns everything."""

    @app.get("/api/v1/__unhandled_probe__")
    def _probe():
        raise RuntimeError("marker-4b82")

    with caplog.at_level(logging.DEBUG):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.get("/api/v1/__unhandled_probe__")

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "INTERNAL_ERROR",
        "message": "An internal error occurred",
        "details": [],
    }

    records = errors_from(caplog)
    assert len(records) == 1, f"expected one error record, got {len(records)}"
    text = rendered(records[0])
    assert "RuntimeError" in text, text
    assert "marker-4b82" in text, text
    assert "Traceback" in text, text
    assert "/api/v1/__unhandled_probe__" in text, text


async def test_the_middleware_is_what_catches_it_not_the_registered_handler(
    app, caplog
):
    """`create_app` registers `internal_error_handler` for `Exception`, which
    goes to Starlette's OUTERMOST `ServerErrorMiddleware`. `database_session` is
    inside it and catches first, so that handler never runs for anything raised
    in a route.

    This is pinned because the arrangement is invisible from either file alone.
    Someone tidying up will one day delete the middleware's `except Exception`
    on the reasonable-sounding grounds that a handler is registered for exactly
    that — and the logging added here would go with it, silently."""

    @app.get("/api/v1/__origin_probe__")
    def _probe():
        raise RuntimeError("marker-1d55")

    with caplog.at_level(logging.DEBUG):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            await client.get("/api/v1/__origin_probe__")

    records = errors_from(caplog)
    names = [record.name for record in records]
    assert names == ["api.app"], (
        f"the unhandled path logged from {names}, not the middleware in api.app"
    )
    # The name alone is coupled to a module path; pinning the function too
    # means this still fails if the call migrates to a different function in
    # the same module, and still passes if the module is renamed or moved.
    func_names = [record.funcName for record in records]
    assert func_names == ["database_session"], (
        f"the unhandled path logged from {func_names}, not database_session"
    )


async def test_the_record_carries_nothing_section_2_3_forbids(app, caplog, monkeypatch):
    """§2.3: no IP address, no user agent, no browser fingerprint is ever
    stored — and a log line is storage.

    The nginx access log was found writing four forbidden fields three weeks
    ago, so this is checked rather than reasoned about. Both log sites are
    driven here, each carrying the same three forbidden headers: the
    unhandled-exception path through `database_session` in `api/app.py`
    (`marker-9e07`), and the deliberate-5xx path through `api_problem_handler`
    in `api/errors.py` (`marker-5a2c`, via the same monkeypatched
    `get_taxonomy` the first test in this file uses). Both messages are built
    from the method and the path on purpose: `request.headers` and the body
    are each one convenient f-string away, and neither may be in here. Two
    distinct markers, one per site, because a pooled marker would still pass
    if one site silently stopped logging."""

    @app.get("/api/v1/__privacy_probe__")
    def _probe():
        raise RuntimeError("marker-9e07")

    class TaxonomyExploded(RuntimeError):
        pass

    def boom(_db):
        raise TaxonomyExploded("marker-5a2c")

    monkeypatch.setattr("api.router.get_taxonomy", boom)

    headers = {
        "User-Agent": "ua-marker-3f9d",
        "X-Forwarded-For": "203.0.113.9",
        "X-Real-IP": "203.0.113.9",
    }

    with caplog.at_level(logging.DEBUG):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            await client.get("/api/v1/__privacy_probe__", headers=headers)
            await client.get("/api/v1/taxonomy", headers=headers)

    records = errors_from(caplog)
    # Deliberately scans ALL of caplog.records, not the filtered `records`
    # above: §2.3 is level-agnostic, so the forbidden-string scan has to be the
    # broad net that would also catch a future `logger.info("request from
    # %s", ...)` that never rises to ERROR.
    text = "\n".join(rendered(record) for record in caplog.records)
    assert "ua-marker-3f9d" not in text, "a user agent reached the log"
    assert "203.0.113.9" not in text, "a client address reached the log"

    # The affirmative half, and it must stay ERROR-only: each marker proves its
    # own log site actually fired at ERROR, and checking against the
    # unfiltered text above would let a record at any level satisfy it.
    text_errors = "\n".join(rendered(record) for record in records)
    assert "marker-9e07" in text_errors
    assert "marker-5a2c" in text_errors

    # And both sites, not just one of them carrying both markers by accident -
    # this is what makes the pair above non-vacuous.
    names = {record.name for record in records}
    assert names == {"api.app", "api.errors"}, f"expected both log sites, got {names}"
