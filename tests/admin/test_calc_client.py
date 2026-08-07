"""The panel's client for B's calculate endpoint. Contract §6.2, §6.2.1."""

import httpx
import pytest

from admin.calc_client import (
    CalculateRefused, CalculateUnavailable, HttpCalculateClient,
)


def _client(handler):
    """An HttpCalculateClient whose transport is a function under our control."""
    transport = httpx.MockTransport(handler)
    return HttpCalculateClient(base_url="http://api.test",
                               transport=transport)


def test_the_dry_run_header_is_always_sent():
    """Contract §8.2: "The dry-run view **must** send the dry-run header."
    Staff run dozens of these while tuning a formula, and persisting them
    would pollute the public statistics directly."""
    seen = {}

    def handler(request):
        seen["header"] = request.headers.get("x-dry-run")
        return httpx.Response(200, json={"totals": {}, "entries": []})

    _client(handler).dry_run({"entries": []}, cookies={}, factor_set_version=None)

    assert seen["header"] == "true"


def test_the_staff_session_cookie_is_forwarded():
    """§6.2: the header requires an authenticated staff session, and the API
    authenticates it with require_staff(). The panel holds that session; the
    API only sees what we forward."""
    seen = {}

    def handler(request):
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(200, json={"totals": {}, "entries": []})

    _client(handler).dry_run({"entries": []}, cookies={"session": "abc"},
                             factor_set_version=None)

    assert "session=abc" in seen["cookie"]


def test_a_factor_set_version_becomes_the_dry_run_object():
    seen = {}

    def handler(request):
        import json
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"totals": {}, "entries": []})

    _client(handler).dry_run({"entries": []}, cookies={},
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

    _client(handler).dry_run({"entries": []}, cookies={}, factor_set_version=None)

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
        _client(handler).dry_run({"entries": []}, cookies={},
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
        _client(handler).dry_run({"entries": []}, cookies={},
                                 factor_set_version=None)


def test_a_non_json_response_is_unavailable_not_refused():
    """A proxy error page or an HTML 502 is the service being broken, not
    the calculation being wrong."""
    def handler(request):
        return httpx.Response(502, text="<html>Bad Gateway</html>")

    with pytest.raises(CalculateUnavailable):
        _client(handler).dry_run({"entries": []}, cookies={},
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
        _client(handler).dry_run({"entries": []}, cookies={},
                                 factor_set_version=None)

    def handler_missing_code(request):
        return httpx.Response(500, json={"error": {"message": "nope"}})

    with pytest.raises(CalculateUnavailable):
        _client(handler_missing_code).dry_run({"entries": []}, cookies={},
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

    result = _client(handler).dry_run({"entries": []}, cookies={},
                                      factor_set_version=None)

    total = result["totals"]["current"]["metrics"]["co2e"]["total"]
    assert total == "3468.0000000000"
    assert isinstance(total, str)
