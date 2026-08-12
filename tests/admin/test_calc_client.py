"""The panel's client for B's calculate endpoint. Contract §6.2, §6.2.1."""

import httpx
import pytest

from admin.calc_client import (
    CalculateRefused, CalculateUnavailable, HttpCalculateClient,
)
from db.staff_proof import STAFF_PROOF_HEADER, verify_staff_proof

#: Any value; the point is that the client signs with it and the assertions
#: verify with the same one.
SECRET = "test-secret-key-not-used-anywhere-real"


def _client(handler, *, secret_key: str | None = None):
    """An HttpCalculateClient whose transport is a function under our control.

    ``secret_key`` defaults to None so the tests that are about the response
    handling - refusals, outages, malformed envelopes - stay unchanged and
    send no proof. The two tests that are about authentication pass one.
    """
    transport = httpx.MockTransport(handler)
    return HttpCalculateClient(base_url="http://api.test",
                               transport=transport, secret_key=secret_key)


def test_the_dry_run_header_is_always_sent():
    """Contract §8.2: "The dry-run view **must** send the dry-run header."
    Staff run dozens of these while tuning a formula, and persisting them
    would pollute the public statistics directly."""
    seen = {}

    def handler(request):
        seen["header"] = request.headers.get("x-dry-run")
        return httpx.Response(200, json={"totals": {}, "entries": []})

    _client(handler).dry_run({"entries": []}, actor="alice", factor_set_version=None)

    assert seen["header"] == "true"


def test_a_staff_proof_is_sent_and_names_the_signed_in_staff_member():
    """§6.2's "requires an authenticated staff session", as the API can check it.

    **This replaces a test that asserted the browser's cookie jar was
    forwarded**, on the stated ground that "the API authenticates it with
    require_staff()". There is no `require_staff()` and there never was; the
    API cannot read the panel's session cookie and deliberately must not learn
    how (db/staff_proof.py). So the forwarding proved nothing, every dry run in
    every real deployment answered UNAUTHORIZED, and this test passed anyway -
    open item O-9, and as exact an instance of "passes for the wrong reason" as
    this project has produced.

    Asserted by *verifying* the proof rather than by checking the header is
    non-empty: a header carrying any other string would satisfy the weaker
    assertion and be refused by the API.
    """
    seen = {}

    def handler(request):
        seen["proof"] = request.headers.get(STAFF_PROOF_HEADER)
        return httpx.Response(200, json={"totals": {}, "entries": []})

    _client(handler, secret_key=SECRET).dry_run(
        {"entries": []}, actor="alice", factor_set_version=None
    )

    assert seen["proof"], "no staff proof was sent"
    assert verify_staff_proof(seen["proof"], secret_key=SECRET) == "alice"


def test_no_cookie_is_forwarded_to_the_api():
    """The panel must not hand a live staff session cookie to another service.

    It did, on every dry run, for no benefit - the API ignored it. Removing the
    forwarding is part of O-9's fix rather than a tidy-up: the proof is scoped
    to one call and one purpose, and a session cookie is scoped to neither.
    """
    seen = {}

    def handler(request):
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(200, json={"totals": {}, "entries": []})

    _client(handler, secret_key=SECRET).dry_run(
        {"entries": []}, actor="alice", factor_set_version=None
    )

    assert seen["cookie"] is None


def test_a_factor_set_version_becomes_the_dry_run_object():
    seen = {}

    def handler(request):
        import json
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"totals": {}, "entries": []})

    _client(handler).dry_run({"entries": []}, actor="alice",
                             factor_set_version="2026-Q3-draft")

    assert seen["body"]["dry_run"] == {"factor_set_version": "2026-Q3-draft",
                                       "bundle": None}


def test_no_version_means_the_published_set():
    """§6.2.1's table: header true, dry_run null - "staff verifying live
    behaviour". Still not persisted."""
    seen = {}

    def handler(request):
        import json
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"totals": {}, "entries": []})

    _client(handler).dry_run({"entries": []}, actor="alice", factor_set_version=None)

    assert seen["body"]["dry_run"] is None


def test_an_error_envelope_becomes_a_refusal_carrying_its_code():
    """§9. FORMULA_ERROR is the one this view exists to surface, and §9.1
    gives it a staff presentation with located details precisely so a dry
    run can show them."""
    def handler(request):
        return httpx.Response(500, json={"error": {
            "code": "FORMULA_ERROR",
            "message": "co2e: name 'upstrem' is not defined",
            "details": [{"field": "formula.co2e", "issue": "undefined_name"}],
        }})

    with pytest.raises(CalculateRefused) as excinfo:
        _client(handler).dry_run({"entries": []}, actor="alice",
                                 factor_set_version=None)

    assert excinfo.value.code == "FORMULA_ERROR"
    assert "upstrem" in excinfo.value.message
    assert excinfo.value.details[0]["field"] == "formula.co2e"


def test_an_unreachable_endpoint_is_distinguishable_from_a_refusal():
    """The endpoint does not exist yet. A staff member clicking Dry run
    before it is deployed must read that the service is unreachable, not a
    stack trace and not "your formula is wrong"."""
    def handler(request):
        raise httpx.ConnectError("nope")

    with pytest.raises(CalculateUnavailable):
        _client(handler).dry_run({"entries": []}, actor="alice",
                                 factor_set_version=None)


def test_a_non_json_response_is_unavailable_not_refused():
    """A proxy error page or an HTML 502 is the service being broken, not
    the calculation being wrong."""
    def handler(request):
        return httpx.Response(502, text="<html>Bad Gateway</html>")

    with pytest.raises(CalculateUnavailable):
        _client(handler).dry_run({"entries": []}, actor="alice",
                                 factor_set_version=None)


def test_a_malformed_error_envelope_is_unavailable_not_refused():
    """A gateway or proxy emitting {"error": "upstream unavailable"} is the
    absent-service case wearing a JSON body: a non-dict `error`, or a dict
    missing a string `code` or `message`, is not a §9 envelope and must not
    surface as a refusal - let alone leak an AttributeError from treating a
    string like a dict."""
    def handler(request):
        return httpx.Response(200, json={"error": "boom"})

    with pytest.raises(CalculateUnavailable):
        _client(handler).dry_run({"entries": []}, actor="alice",
                                 factor_set_version=None)

    def handler_missing_code(request):
        return httpx.Response(500, json={"error": {"message": "nope"}})

    with pytest.raises(CalculateUnavailable):
        _client(handler_missing_code).dry_run({"entries": []}, actor="alice",
                                              factor_set_version=None)


def test_decimals_are_left_as_strings():
    """§1.2. The response's numbers are strings and must stay strings all
    the way to the template - parsing one into a float here would lose the
    precision the whole contract is arranged to preserve."""
    def handler(request):
        return httpx.Response(200, json={
            "totals": {"current": {"metrics": {
                "co2e": {"total": "3468.0000000000", "unit": "kg CO2e"}}}},
            "entries": [],
        })

    result = _client(handler).dry_run({"entries": []}, actor="alice",
                                      factor_set_version=None)

    total = result["totals"]["current"]["metrics"]["co2e"]["total"]
    assert total == "3468.0000000000"
    assert isinstance(total, str)
