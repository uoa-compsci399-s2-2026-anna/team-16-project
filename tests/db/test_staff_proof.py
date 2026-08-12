"""``db/staff_proof.py`` — the panel's vouching credential. Contract §6.2, O-9.

**The standard this file is written to.** A test that asserts refusal passes
against a function that refuses everything, so every refusal below is paired
with the permitted case: `verify_staff_proof` must accept a genuine proof, and
that assertion is what gives the six refusals their meaning. `mint_staff_proof`
returning `""` would pass a file that only checked forgeries.
"""

import time

import itsdangerous
import pytest

from db.staff_proof import (
    PROOF_TTL_SECONDS,
    STAFF_PROOF_HEADER,
    mint_staff_proof,
    verify_staff_proof,
)

SECRET = "a-test-secret-key"
OTHER_SECRET = "a-different-test-secret-key"


# --- the permitted case -----------------------------------------------------


def test_a_freshly_minted_proof_verifies_and_names_its_subject():
    """The case every refusal below is measured against."""
    token = mint_staff_proof("alice", secret_key=SECRET)

    assert verify_staff_proof(token, secret_key=SECRET) == "alice"


def test_the_username_survives_verbatim():
    """Not merely "some string": the API returns this to the router as the
    actor, so a proof that verified to the wrong person would be worse than
    one that failed."""
    for username in ("alice", "a", "user.with.dots", "u-1_2", "Ā-macron"):
        token = mint_staff_proof(username, secret_key=SECRET)
        assert verify_staff_proof(token, secret_key=SECRET) == username


def test_two_proofs_for_one_user_are_both_valid():
    """Minting is per call, so nothing may be single-use or stateful - a
    comparison view makes two calls per scenario."""
    first = mint_staff_proof("alice", secret_key=SECRET)
    second = mint_staff_proof("alice", secret_key=SECRET)

    assert verify_staff_proof(first, secret_key=SECRET) == "alice"
    assert verify_staff_proof(second, secret_key=SECRET) == "alice"


# --- the refusals -----------------------------------------------------------


@pytest.mark.parametrize(
    "token,why",
    [
        (None, "no header on the request at all"),
        ("", "the header present and empty"),
        ("nonsense", "not a signed value in any format"),
        ("alice", "the bare username, which is what a forger would try first"),
        ("YWxpY2U.abc.def", "signed shape, invented signature"),
    ],
    ids=["absent", "empty", "garbage", "bare-username", "invented-signature"],
)
def test_a_proof_that_is_not_one_is_refused(token, why):
    assert verify_staff_proof(token, secret_key=SECRET) is None, why


def test_a_proof_signed_with_a_different_secret_is_refused():
    """The deployment fact this depends on: the panel and the API read one
    SECRET_KEY from one mounted volume. If they ever did not, this is the
    behaviour - refusal, not silent acceptance."""
    token = mint_staff_proof("alice", secret_key=OTHER_SECRET)

    assert verify_staff_proof(token, secret_key=SECRET) is None


def test_an_expired_proof_is_refused():
    """`TimestampSigner` embeds the mint time; `max_age` is what makes the TTL
    real. Driven by signing with a backdated timestamp rather than by sleeping
    for a minute."""
    signer = itsdangerous.TimestampSigner(SECRET, salt=b"kaicalc-staff-dry-run-proof")
    fresh = mint_staff_proof("alice", secret_key=SECRET)
    payload = fresh.encode("utf-8").rsplit(b".", 2)[0]

    stale = signer.sign(payload)
    # Re-sign at a timestamp older than the window. itsdangerous reads the
    # clock through `time.time`, so moving it is what ages the token.
    real_time = time.time
    try:
        time.time = lambda: real_time() - PROOF_TTL_SECONDS - 5
        stale = signer.sign(payload).decode("ascii")
    finally:
        time.time = real_time

    assert verify_staff_proof(stale, secret_key=SECRET) is None
    # And the fresh one still verifies, so this test is about age and not
    # about the re-signing having produced a broken token.
    assert verify_staff_proof(fresh, secret_key=SECRET) == "alice"


def test_a_proof_is_still_valid_just_inside_the_window():
    """The other side of the expiry boundary. Without this, a TTL of zero
    would pass every test above."""
    signer = itsdangerous.TimestampSigner(SECRET, salt=b"kaicalc-staff-dry-run-proof")
    fresh = mint_staff_proof("alice", secret_key=SECRET)
    payload = fresh.encode("utf-8").rsplit(b".", 2)[0]

    real_time = time.time
    try:
        time.time = lambda: real_time() - PROOF_TTL_SECONDS + 5
        recent = signer.sign(payload).decode("ascii")
    finally:
        time.time = real_time

    assert verify_staff_proof(recent, secret_key=SECRET) == "alice"


# --- the salt, which is what keeps the two credentials apart ----------------


def test_a_session_cookie_cannot_be_replayed_as_a_proof():
    """**The load-bearing security property.** The panel's session cookie and a
    dry-run proof are both signed under the one SECRET_KEY the deployment
    shares. Only the pinned salt stops a stolen session cookie being presented
    to the API as a staff proof.

    Signed here exactly as `starlette.middleware.sessions.SessionMiddleware`
    signs it - `TimestampSigner(secret_key)` with itsdangerous' default salt -
    over a payload of the shape the panel's session carries.
    """
    from base64 import b64encode
    import json

    session_like = b64encode(
        json.dumps({"staff_username": "alice", "staff_generation": 1}).encode()
    )
    cookie = itsdangerous.TimestampSigner(SECRET).sign(session_like).decode("ascii")

    assert verify_staff_proof(cookie, secret_key=SECRET) is None


def test_a_proof_cannot_be_replayed_as_a_session_cookie():
    """The same property from the other side, so that neither direction is
    left to an assumption."""
    token = mint_staff_proof("alice", secret_key=SECRET)

    with pytest.raises(itsdangerous.exc.BadSignature):
        itsdangerous.TimestampSigner(SECRET).unsign(
            token.encode("utf-8"), max_age=PROOF_TTL_SECONDS
        )


def test_the_header_name_is_not_a_cookie_header():
    """It must never be settable by, or readable from, a browser cookie jar."""
    assert STAFF_PROOF_HEADER.lower() not in ("cookie", "set-cookie")
