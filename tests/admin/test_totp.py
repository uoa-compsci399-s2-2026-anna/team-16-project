"""admin.totp - TOTP secrets, provisioning URIs, verification, QR rendering.

Contract: docs/interfaces.md 8.3 (mandatory MFA, replay protection via
staff.mfa_last_counter).
"""

import pyotp
import pytest

from admin.totp import (
    TOTP_INTERVAL,
    generate_totp_secret,
    provisioning_uri,
    qr_svg,
    verify_totp,
)

# A fixed secret and a fixed clock keep these tests deterministic. 1800 is
# time-step counter 60 exactly.
SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
NOW = 1800
COUNTER = NOW // TOTP_INTERVAL


def code_for(counter: int) -> str:
    return pyotp.TOTP(SECRET).at(counter * TOTP_INTERVAL)


def test_generated_secret_is_base32_of_the_expected_length():
    secret = generate_totp_secret()

    assert len(secret) == 32
    assert set(secret) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")


def test_generated_secrets_differ():
    assert generate_totp_secret() != generate_totp_secret()


def test_provisioning_uri_carries_the_secret_issuer_and_username():
    uri = provisioning_uri(SECRET, username="alice", issuer="Kai Commitment")

    assert uri.startswith("otpauth://totp/")
    assert f"secret={SECRET}" in uri
    assert "alice" in uri
    assert "Kai%20Commitment" in uri


def test_verify_accepts_the_current_code():
    assert verify_totp(SECRET, code_for(COUNTER), now=NOW) == COUNTER


def test_verify_accepts_the_previous_and_next_step_for_clock_drift():
    """Phones and servers disagree by seconds. One step either side is the
    standard tolerance."""
    assert verify_totp(SECRET, code_for(COUNTER - 1), now=NOW) == COUNTER - 1
    assert verify_totp(SECRET, code_for(COUNTER + 1), now=NOW) == COUNTER + 1


def test_verify_rejects_a_code_two_steps_away():
    assert verify_totp(SECRET, code_for(COUNTER - 2), now=NOW) is None


def test_verify_rejects_a_wrong_code():
    assert verify_totp(SECRET, "000000", now=NOW) is None


def test_verify_rejects_a_code_already_used():
    """Replay protection. The same six digits stay valid for 30 seconds, so
    without this an intercepted code can be used twice."""
    assert verify_totp(SECRET, code_for(COUNTER), now=NOW, last_counter=COUNTER) is None


def test_verify_rejects_a_code_older_than_the_last_one_used():
    assert (
        verify_totp(SECRET, code_for(COUNTER - 1), now=NOW, last_counter=COUNTER)
        is None
    )


def test_verify_still_accepts_a_newer_code_after_one_was_used():
    assert (
        verify_totp(SECRET, code_for(COUNTER + 1), now=NOW, last_counter=COUNTER)
        == COUNTER + 1
    )


def test_verify_tolerates_spaces_in_the_typed_code():
    """Authenticator apps display codes as '123 456'."""
    code = code_for(COUNTER)
    spaced = f"{code[:3]} {code[3:]}"

    assert verify_totp(SECRET, spaced, now=NOW) == COUNTER


def test_verify_rejects_malformed_input_without_raising():
    """The enrolment form is user input; a stray value is a failed
    verification, not a 500."""
    assert verify_totp(SECRET, "", now=NOW) is None
    assert verify_totp(SECRET, "abcdef", now=NOW) is None
    assert verify_totp(SECRET, "12345678901234567890", now=NOW) is None
    assert verify_totp(SECRET, None, now=NOW) is None


def test_qr_svg_returns_inline_svg_markup():
    svg = qr_svg(provisioning_uri(SECRET, username="alice"))

    assert "<svg" in svg
    assert "</svg>" in svg


@pytest.mark.parametrize("bad_secret", ["", "not base32!", "8888"])
def test_verify_rejects_an_unusable_secret_without_raising(bad_secret):
    assert verify_totp(bad_secret, "123456", now=NOW) is None
