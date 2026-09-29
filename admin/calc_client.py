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

from db.staff_proof import STAFF_PROOF_HEADER, mint_staff_proof


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
        actor: str,
        factor_set_version: str | None,
    ) -> dict:
        ...


class HttpCalculateClient:
    """Speaks contract v1.1 to ``POST /api/v1/calculate`` over httpx.

    ``transport`` exists only so tests can substitute
    ``httpx.MockTransport``; production leaves it unset and httpx builds its
    normal transport.

    ``secret_key`` is what closes open item O-9. The API refuses a dry run
    without an authenticated staff session, and forwarding the browser's
    cookies - which is all this client used to do - proves nothing to it: the
    API cannot read the panel's session cookie and must not learn how (see
    ``db/staff_proof.py``). So the panel signs a short-lived proof instead, one
    per call, with the ``SECRET_KEY`` both services share.

    It is optional only so that a test may build a client with no secret and
    drive the unauthenticated path deliberately. **A deployment always passes
    one** - ``admin/app.py`` reads it off the same ``Settings`` the session
    cookie is signed from - and without it every dry run answers
    `UNAUTHORIZED`, which is precisely the state O-9 records.
    """

    def __init__(
        self,
        base_url: str,
        transport: httpx.BaseTransport | None = None,
        *,
        secret_key: str | None = None,
    ):
        self._base_url = base_url
        self._transport = transport
        self._secret_key = secret_key

    def dry_run(
        self,
        request_body: dict,
        *,
        actor: str,
        factor_set_version: str | None,
    ) -> dict:
        """``actor`` is the signed-in staff username, from the panel's session.

        It replaced a ``cookies`` parameter that forwarded the browser's whole
        cookie jar to the API. That forwarding never authenticated anything -
        the API cannot read the panel's session cookie and deliberately must
        not learn how - so its only effect was to hand a live staff session
        cookie to a second service on every dry run. The proof below is what
        the API actually checks, and it is minted for this one call.
        """
        body = dict(request_body)
        if factor_set_version is None:
            body["dry_run"] = None
        else:
            body["dry_run"] = {
                "factor_set_version": factor_set_version,
                "bundle": None,
            }

        headers = {"X-Dry-Run": "true"}
        if self._secret_key is not None:
            headers[STAFF_PROOF_HEADER] = mint_staff_proof(
                actor, secret_key=self._secret_key
            )

        try:
            with httpx.Client(
                base_url=self._base_url,
                transport=self._transport,
            ) as client:
                response = client.post(
                    "/api/v1/calculate",
                    json=body,
                    headers=headers,
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
            # A §9 envelope's "error" is an object with a string code and
            # message. A string (a proxy's `{"error": "upstream
            # unavailable"}`) or a dict missing either field is not that
            # envelope - it is the absent-service case wearing a JSON body,
            # and treating it as a refusal would hand staff an
            # AttributeError (non-dict) or a literal "None" rendered into
            # the page (missing field) during the exact outage this client
            # exists to report cleanly.
            if (
                not isinstance(error, dict)
                or not isinstance(error.get("code"), str)
                or not isinstance(error.get("message"), str)
            ):
                raise CalculateUnavailable(
                    f"malformed error envelope ({response.status_code})"
                )
            raise CalculateRefused(
                code=error["code"],
                message=error["message"],
                details=error.get("details"),
            )

        if response.status_code >= 400:
            raise CalculateUnavailable(
                f"HTTP {response.status_code} with no error envelope"
            )

        return payload
