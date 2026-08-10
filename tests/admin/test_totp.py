"""admin.totp - TOTP secrets, provisioning URIs, verification, QR rendering.

Contract: docs/interfaces.md 8.3 (mandatory MFA, replay protection via
staff.mfa_last_counter).
"""

import pyotp
from urllib.parse import unquote, urlparse

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


# --------------------------------------------------------------------------
# What the authenticator app shows
# --------------------------------------------------------------------------
#
# An authenticator lists entries by their issuer. Every account on this system
# produced an entry reading only "Kai Commitment", so the two administrators
# the design requires were indistinguishable in the app, and a second
# deployment - a staging instance, or the same person's own test stack - would
# add two more entries with the same name and no way to tell any of them apart.
#
# The account name was always in the URI; the issuer is what a phone shows in
# the list, and it named the organisation rather than the system.


def test_the_issuer_names_the_system_not_just_the_organisation():
    """`Kai Commitment` alone does not say which of their systems this is.

    Removing the word `Admin` from the default fails this: it is what
    distinguishes the staff panel from anything else the same organisation
    might ask a person to enrol an authenticator against.
    """
    uri = provisioning_uri(SECRET, username="alice")

    assert "issuer=Kai%20Commitment%20Admin" in uri


def test_the_label_carries_both_the_system_and_the_account():
    """Most authenticators render the label as `issuer (account)`.

    Both halves have to be there: the issuer so a person with several
    enrolments can find this one, and the account so the two administrators
    are distinguishable from each other.
    """
    uri = provisioning_uri(SECRET, username="alice")
    label = unquote(urlparse(uri).path.lstrip("/"))

    assert label == "Kai Commitment Admin:alice"


def test_the_issuer_can_name_the_deployment():
    """One person may enrol against production and a staging stack.

    Without an override both entries read identically, which is the same
    defect one level up. The deployment sets ADMIN_TOTP_ISSUER; nothing in
    the application chooses this.
    """
    uri = provisioning_uri(SECRET, username="alice", issuer="Kai Commitment Admin (staging)")
    label = unquote(urlparse(uri).path.lstrip("/"))

    assert label == "Kai Commitment Admin (staging):alice"
    assert "issuer=Kai%20Commitment%20Admin%20%28staging%29" in uri
