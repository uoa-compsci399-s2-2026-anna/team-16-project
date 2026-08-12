"""The panel's vouching credential for a staff-only API call. Contract §6.2, §8.2.

**What this closes.** `api.app:create_app` takes a `staff_authenticator` and,
until this module existed, nothing in a deployment supplied one. Every dry run
therefore answered `UNAUTHORIZED`, `/admin/try` rendered that refusal inside a
200 page, and §8.2 was inert everywhere except in the two API test files that
passed a `lambda request: "alice"`. That is open item **O-9**: a feature whose
page renders, whose tests pass, and which had never worked outside a test.

**Why a purpose-built proof and not the panel's session cookie.** `api/` may not
import `admin/` (v1.3's ruling, the same reason `PREVENTION_CODE` lives in
`db/types.py`), so the panel's session machinery is out of reach by import —
but it could have been *moved* here and shared, exactly as `db/detection.py`
was. That was the other candidate and it was rejected, for a reason that is
about the security boundary rather than about layering:

    Teaching the API to read the panel's session cookie makes the API a second
    place where a staff session can be *established*. A mistake in it is then
    an authentication defect in the public-facing service. This module instead
    keeps authentication in exactly one place — the panel, which has already
    run `AdminAuth.authenticate()`, the role floor and the onboarding gates
    before it mints anything — and gives the API a much smaller thing to
    verify: not "who is this caller" but "did the panel vouch for this call,
    just now".

The second consequence is narrower reach. `/admin` and `/api/v1/` are one
origin behind nginx, so a shared session cookie would be sent by the browser
to the API as well, and any staff member's browser could drive the arbitrary
`dry_run.bundle` path directly. A proof cannot be minted without `SECRET_KEY`,
which no browser has, so the dry-run path stays reachable only through the two
screens §8.2 defines.

**What a proof asserts, exactly.** "At time T, the admin panel had an
authenticated staff session for <username>." Nothing more. It is a bearer
credential and it is treated as one: it goes only over the internal compose
network from the panel to the API, and it expires in `PROOF_TTL_SECONDS`.

**What it deliberately does not assert.** That the account is *still* valid.
The API performs no `staff` lookup — it holds no `staff` model, and adding one
would hand the public service a reason to read the credential table. So an
account deactivated in the second after a proof was minted can have that proof
accepted for up to `PROOF_TTL_SECONDS`. The window is bounded and small, and
what it buys is a dry run that persists nothing; it is not a way into anything
a normal calculation cannot reach. If that trade stops being acceptable, the
fix is a `staff` lookup here, not a wider credential.

**The salt is load-bearing.** `itsdangerous` derives the signing key from
`secret_key` *and* `salt`, so a value signed under one salt cannot be verified
under another. The panel's session cookie is signed by `SessionMiddleware`
with itsdangerous' default salt (`b"itsdangerous.Signer"`); this module pins a
different one. A stolen session cookie therefore cannot be replayed as a
proof, and a proof cannot be replayed as a session cookie, even though both are
signed under the one `SECRET_KEY` the deployment shares.
"""

import json
from base64 import b64decode, b64encode

import itsdangerous
from itsdangerous.exc import BadSignature

#: The header the panel sends and the API reads.
#:
#: A header rather than a cookie: this credential is not the browser's and must
#: never be set on a browser. `admin/calc_client.py` attaches it to a
#: server-to-server httpx call and nothing else ever does.
STAFF_PROOF_HEADER = "X-Staff-Proof"

#: How long a minted proof stays acceptable, in seconds.
#:
#: Sized against what it has to survive - one HTTP call from the panel to the
#: API over the compose network - and not against anything a human does. The
#: staff member's own session is hours long (`SESSION_MAX_AGE_MINUTES`); this
#: is the life of one request, and a fresh proof is minted for each. Sixty
#: seconds rather than five is slack for a slow engine call and for clock skew
#: between two containers, which do not share a clock.
PROOF_TTL_SECONDS = 60

#: Pinned, and never to be reused for anything else. See the module docstring:
#: this is what stops a session cookie and a proof being interchangeable under
#: the shared SECRET_KEY. Changing this string invalidates every proof in
#: flight, which is harmless (they live 60 seconds), but it must never be set
#: to itsdangerous' default, which is what SessionMiddleware signs with.
_PROOF_SALT = b"kaicalc-staff-dry-run-proof"


def _signer(secret_key: str) -> itsdangerous.TimestampSigner:
    return itsdangerous.TimestampSigner(secret_key, salt=_PROOF_SALT)


def mint_staff_proof(username: str, *, secret_key: str) -> str:
    """Vouch for ``username`` for the next ``PROOF_TTL_SECONDS``.

    Called by the panel, once per outgoing dry-run call, **after** its own
    authentication has passed. Nothing else may call it: minting a proof is
    asserting that a staff session exists, and a caller that has not checked
    is asserting something it does not know.

    The payload is base64-encoded JSON rather than the bare username, matching
    the shape ``admin/protection.py`` already decodes for the session cookie
    and leaving room for a field to be added without a format flag day. It is
    **not** encrypted and is not secret - the signature is what makes it
    unforgeable, and there is nothing here that is not already known to both
    ends.
    """
    payload = b64encode(json.dumps({"sub": username}).encode("utf-8"))
    return _signer(secret_key).sign(payload).decode("ascii")


def verify_staff_proof(token: str | None, *, secret_key: str) -> str | None:
    """Return the username a valid, unexpired proof vouches for, else ``None``.

    Called by the API. **Every failure returns ``None``** - absent, malformed,
    wrongly-signed, expired, or validly signed over a payload that is not the
    JSON object ``mint_staff_proof`` writes. One return value for all of them
    on purpose: the caller's only correct response to any of these is 401, and
    a function that distinguished them would invite a caller that did too.

    ``SignatureExpired`` is a ``BadSignature`` subclass, so the ``max_age``
    check and the signature check are caught by the same clause and cannot
    drift apart. ``ValueError`` covers a validly-signed payload that is not
    well-formed base64 or JSON - unreachable without the secret key, and
    caught anyway rather than raised into a request handler.
    """
    if not token:
        return None
    try:
        unsigned = _signer(secret_key).unsign(
            token.encode("utf-8"), max_age=PROOF_TTL_SECONDS
        )
        data = json.loads(b64decode(unsigned))
    except (BadSignature, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    username = data.get("sub")
    if not isinstance(username, str) or not username:
        return None
    return username
