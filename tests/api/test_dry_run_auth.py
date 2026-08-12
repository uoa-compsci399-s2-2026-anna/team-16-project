"""The dry-run staff gate, on an app built the way a deployment builds one.

**This is open item O-9's regression file, and the shape of it is the point.**
`api.app:create_app` took a `staff_authenticator` and no deployment supplied
one, so `app.state.staff_authenticator` was `None`, so every dry run answered
`UNAUTHORIZED`. `/admin/try` rendered that refusal inside a 200 page and looked
like a working screen. Contract §8.2 had never once succeeded outside a test.

The existing coverage could not have caught it, and it is worth being precise
about why, because the same shape is what this project keeps hitting:

* `tests/api/test_api.py::test_dry_run_requires_staff_and_does_not_persist`
  asserts the refusal, then assigns `app.state.staff_authenticator = lambda
  request: "alice"` and asserts the success. Both halves pass against an app
  that can never authenticate anybody, because the test *supplies* the thing
  production was missing. A test double filling a gap production has.
* Nothing anywhere built an app the way `create_app()` is actually called and
  then tried to complete a dry run.

So every test below drives the fixture app **without ever assigning
`staff_authenticator`**, and the credential comes from `db/staff_proof.py` -
the same function `admin/calc_client.py` calls. If the default authenticator is
removed, or the panel and the API stop agreeing on how a proof is made, these
fail.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from db.staff_proof import STAFF_PROOF_HEADER, mint_staff_proof
from tests.support.sqlite import API_TEST_SECRET_KEY

pytestmark = pytest.mark.asyncio


def _body():
    return {
        "entries": [
            {
                "sector": "processing",
                "food_category": "dairy",
                "current": [{"destination": "landfill", "qty_kg": "1"}],
            }
        ]
    }


def _proof(username="alice", *, secret_key=API_TEST_SECRET_KEY):
    return mint_staff_proof(username, secret_key=secret_key)


async def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


# --- the permitted case, which is the whole of O-9 --------------------------


async def test_a_deployment_shaped_app_completes_a_dry_run(app):
    """**The test O-9 needed.** No `staff_authenticator` is assigned anywhere;
    the app is exactly what `create_app()` returns, and the request carries
    only what the panel sends."""
    async with await _client(app) as client:
        response = await client.post(
            "/api/v1/calculate",
            headers={"X-Dry-Run": "true", STAFF_PROOF_HEADER: _proof()},
            json=_body(),
        )

    assert response.status_code == 200, response.text


async def test_an_accepted_dry_run_still_persists_nothing(app):
    """The authentication must not have bought a persisted submission - §6.2's
    whole reason for `X-Dry-Run` is that staff tuning a formula do not pollute
    the public statistics."""
    from sqlalchemy import func, select

    from db.models import Submission

    async with await _client(app) as client:
        response = await client.post(
            "/api/v1/calculate",
            headers={"X-Dry-Run": "true", STAFF_PROOF_HEADER: _proof()},
            json=_body(),
        )

    assert response.status_code == 200, response.text
    assert response.json()["token"] is None
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Submission)) == 0


# --- the refusals -----------------------------------------------------------


@pytest.mark.parametrize(
    "headers,why",
    [
        ({}, "no proof at all - the state every deployment was in"),
        ({STAFF_PROOF_HEADER: ""}, "the header present and empty"),
        ({STAFF_PROOF_HEADER: "nonsense"}, "not a signed value"),
        ({STAFF_PROOF_HEADER: "alice"}, "the bare username"),
    ],
    ids=["absent", "empty", "garbage", "bare-username"],
)
async def test_a_dry_run_without_a_valid_proof_is_refused(app, headers, why):
    async with await _client(app) as client:
        response = await client.post(
            "/api/v1/calculate",
            headers={"X-Dry-Run": "true", **headers},
            json=_body(),
        )

    assert response.status_code == 401, why
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


async def test_a_proof_signed_with_the_wrong_secret_is_refused(app):
    """One SECRET_KEY per deployment is a fact `docker/compose.yaml`'s shared
    volume enforces. This is what happens if it is ever not true - a refusal,
    never a silent acceptance."""
    async with await _client(app) as client:
        response = await client.post(
            "/api/v1/calculate",
            headers={
                "X-Dry-Run": "true",
                STAFF_PROOF_HEADER: _proof(secret_key="a-different-secret"),
            },
            json=_body(),
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


# --- the boundary: a proof buys a dry run and nothing else ------------------


async def test_a_proof_does_not_reach_anything_a_normal_calculation_cannot(app):
    """A valid proof on a request that is **not** a dry run changes nothing.

    The credential authorises the `X-Dry-Run` path, not a privileged mode of
    the calculator: with no dry-run header the request is an ordinary public
    calculation, is persisted like one, and mints a token like one.
    """
    from sqlalchemy import func, select

    from db.models import Submission

    async with await _client(app) as client:
        response = await client.post(
            "/api/v1/calculate",
            headers={STAFF_PROOF_HEADER: _proof()},
            json=_body(),
        )

    assert response.status_code == 200, response.text
    assert response.json()["token"], "an ordinary calculation still mints a token"
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Submission)) == 1


async def test_a_proof_does_not_open_the_other_routes(app):
    """`/taxonomy` and `/factors` behave identically with and without one -
    there is no staff-only read this credential unlocks."""
    async with await _client(app) as client:
        without = await client.get("/api/v1/taxonomy")
        with_proof = await client.get(
            "/api/v1/taxonomy", headers={STAFF_PROOF_HEADER: _proof()}
        )

    assert without.status_code == with_proof.status_code == 200
    assert without.json() == with_proof.json()


async def test_the_authenticator_is_still_injectable(app):
    """`create_app` keeps its `staff_authenticator` parameter, and assigning
    `None` after construction still disables the gate - the documented escape
    hatch, matching `blocklist_check`. Asserted so that the default cannot
    quietly become mandatory and break a test that relies on it."""
    app.state.staff_authenticator = None

    async with await _client(app) as client:
        response = await client.post(
            "/api/v1/calculate",
            headers={"X-Dry-Run": "true", STAFF_PROOF_HEADER: _proof()},
            json=_body(),
        )

    assert response.status_code == 401
