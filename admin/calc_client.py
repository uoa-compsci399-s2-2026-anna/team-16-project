"""The panel's client for B's calculate endpoint. Contract §6.2, §6.2.1.

``POST /api/v1/calculate`` belongs to a teammate whose branch is not merged
yet, so this module imports none of her code - it only speaks HTTP to a URL.
That is also why ``HttpCalculateClient`` accepts a ``transport``: production
never passes one (httpx builds its default transport), but the test suite
substitutes ``httpx.MockTransport`` so the whole client can be exercised
without a running service.

The one distinction that matters: a connection error, a timeout, a
non-JSON body, or a JSON body with no ``error`` key means the service is
broken or absent (``CalculateUnavailable``). A well-formed §9 error
envelope means the service worked and said no (``CalculateRefused``). The
dry-run view exists precisely so staff can tune formulas against a service
that, for the length of this task, mostly is not deployed - reporting an
outage as "your formula is wrong" sends them hunting a bug that is not
theirs.
"""

from typing import Any, Protocol

import httpx


class CalculateUnavailable(Exception):
    """The endpoint could not be reached, or did not answer as a service.

    Covers connection failures, timeouts, non-JSON bodies, and JSON bodies
    that are not a §9 error envelope (no ``error`` key) - none of these say
    anything about whether the formula under test is correct.
    """


class CalculateRefused(Exception):
    """The endpoint answered with a well-formed §9 error envelope.

    Carries the envelope's ``code``, ``message`` and ``details`` so the
    dry-run view can show staff exactly what the engine rejected and why -
    §9.1 gives FORMULA_ERROR a staff presentation with located details for
    exactly this purpose.
    """

    def __init__(self, code: str, message: str, details: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


class CalculateClient(Protocol):
    def dry_run(
        self,
        request_body: dict,
        *,
        cookies: dict,
        factor_set_version: str | None,
    ) -> dict:
        ...


class HttpCalculateClient:
    """Speaks contract v1.1 to ``POST /api/v1/calculate`` over httpx.

    ``transport`` exists only so tests can substitute
    ``httpx.MockTransport``; production leaves it unset and httpx builds its
    normal transport.
    """

    def __init__(self, base_url: str, transport: httpx.BaseTransport | None = None):
        self._base_url = base_url
        self._transport = transport

    def dry_run(
        self,
        request_body: dict,
        *,
        cookies: dict,
        factor_set_version: str | None,
    ) -> dict:
        body = dict(request_body)
        if factor_set_version is None:
            body["dry_run"] = None
        else:
            body["dry_run"] = {
                "factor_set_version": factor_set_version,
                "bundle": None,
            }

        try:
            with httpx.Client(
                base_url=self._base_url,
                transport=self._transport,
                cookies=cookies,
            ) as client:
                response = client.post(
                    "/api/v1/calculate",
                    json=body,
                    headers={"X-Dry-Run": "true"},
                )
        except httpx.HTTPError as exc:
            raise CalculateUnavailable(str(exc)) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise CalculateUnavailable(
                f"non-JSON response ({response.status_code})"
            ) from exc

        if not isinstance(payload, dict):
            raise CalculateUnavailable(
                f"unexpected response shape ({response.status_code})"
            )

        error = payload.get("error")
        if error is not None:
            raise CalculateRefused(
                code=error.get("code"),
                message=error.get("message"),
                details=error.get("details"),
            )

        if response.status_code >= 400:
            raise CalculateUnavailable(
                f"HTTP {response.status_code} with no error envelope"
            )

        return payload
