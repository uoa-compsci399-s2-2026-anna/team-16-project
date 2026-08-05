"""admin.security - password hashing, TOTP secret encryption, recovery codes.

Contract: docs/interfaces.md 2.4 and 8.3.
"""

import pytest

from admin.security import (
    PasswordTooLongError,
    TotpSecretUndecryptableError,
    _derive_key,
    decrypt_totp_secret,
    encrypt_totp_secret,
    generate_recovery_codes,
    hash_password,
    hash_recovery_code,
    verify_password,
    verify_recovery_code,
)

SECRET = "test-secret-key-not-used-anywhere-real"
TOTP_SECRET = "JBSWY3DPEHPK3PXP"


def test_verify_password_accepts_the_correct_password():
    stored = hash_password("correct horse battery staple")

    assert verify_password("correct horse battery staple", stored) is True


def test_verify_password_rejects_a_wrong_password():
    stored = hash_password("correct horse battery staple")

    assert verify_password("Correct horse battery staple", stored) is False


def test_hashing_the_same_password_twice_gives_different_hashes():
    """A per-hash salt is what stops one rainbow table covering every account."""
    first = hash_password("same password")
    second = hash_password("same password")

    assert first != second
    assert verify_password("same password", first) is True
    assert verify_password("same password", second) is True


def test_hash_does_not_contain_the_plaintext():
    stored = hash_password("SuperSecret123")

    assert "SuperSecret123" not in stored


def test_a_password_at_exactly_the_bcrypt_limit_is_accepted():
    stored = hash_password("a" * 72)

    assert verify_password("a" * 72, stored) is True


def test_password_over_the_bcrypt_limit_is_rejected_rather_than_truncated():
    """bcrypt uses only the first 72 bytes and discards the rest in silence.

    Truncating quietly would let someone set a 100-character passphrase and
    believe all of it protects the account when only the first 72 bytes do.
    """
    with pytest.raises(PasswordTooLongError):
        hash_password("a" * 73)


def test_verify_password_rejects_an_over_long_candidate_without_raising():
    """The login form is public input. An over-long password is a failed
    login, not a 500."""
    stored = hash_password("short password")

    assert verify_password("a" * 500, stored) is False


def test_verify_password_returns_false_for_a_corrupt_stored_hash():
    """A truncated or hand-edited hash in the database must fail the login
    rather than raise out of the login handler."""
    assert verify_password("anything", "not-a-bcrypt-hash") is False


def test_the_length_limit_counts_bytes_not_characters():
    """A CJK character is three UTF-8 bytes, so 25 of them already exceed the
    limit while being only 25 characters long."""
    with pytest.raises(PasswordTooLongError):
        hash_password("密" * 25)


# --- TOTP secret encryption at rest (staff.mfa_secret_enc) ------------------


def test_totp_secret_survives_a_round_trip():
    blob = encrypt_totp_secret(TOTP_SECRET, secret_key=SECRET)

    assert decrypt_totp_secret(blob, secret_key=SECRET) == TOTP_SECRET


def test_ciphertext_does_not_contain_the_secret():
    blob = encrypt_totp_secret(TOTP_SECRET, secret_key=SECRET)

    assert TOTP_SECRET.encode() not in blob


def test_encrypting_the_same_secret_twice_gives_different_ciphertext():
    first = encrypt_totp_secret(TOTP_SECRET, secret_key=SECRET)
    second = encrypt_totp_secret(TOTP_SECRET, secret_key=SECRET)

    assert first != second


def test_decrypting_with_a_different_secret_key_fails_loudly():
    """This is exactly why rotate_key has to exist: after SECRET_KEY changes,
    every stored secret is unreadable until it is re-encrypted."""
    blob = encrypt_totp_secret(TOTP_SECRET, secret_key=SECRET)

    with pytest.raises(TotpSecretUndecryptableError):
        decrypt_totp_secret(blob, secret_key="a-completely-different-key")


def test_ciphertext_fits_the_column():
    """staff.mfa_secret_enc is VARBINARY(255)."""
    blob = encrypt_totp_secret(TOTP_SECRET, secret_key=SECRET)

    assert len(blob) <= 255


def test_the_encryption_key_is_derived_rather_than_being_the_secret_key():
    """SECRET_KEY already signs session cookies. Using one key for two
    purposes is the defect this derivation exists to avoid."""
    derived = _derive_key(SECRET, info=b"totp-secret-encryption")

    assert derived != SECRET.encode()


def test_different_purposes_derive_different_keys():
    """So that a future second use of SECRET_KEY cannot silently share a key
    with TOTP encryption."""
    for_totp = _derive_key(SECRET, info=b"totp-secret-encryption")
    for_something_else = _derive_key(SECRET, info=b"some-other-purpose")

    assert for_totp != for_something_else


# --- Recovery codes (staff_recovery_code) -----------------------------------


def test_generate_recovery_codes_returns_the_requested_number():
    assert len(generate_recovery_codes(5)) == 5


def test_recovery_codes_are_all_different():
    codes = generate_recovery_codes(5)

    assert len(set(codes)) == 5


def test_recovery_code_verification_accepts_the_code():
    code = generate_recovery_codes(1)[0]
    stored = hash_recovery_code(code)

    assert verify_recovery_code(code, stored) is True


def test_recovery_code_verification_rejects_a_different_code():
    first, second = generate_recovery_codes(2)

    assert verify_recovery_code(second, hash_recovery_code(first)) is False


def test_recovery_code_verification_tolerates_transcription_differences():
    """These are printed and typed back in by hand, often months later. Case
    and separators must not be the difference between recovering an account
    and losing it."""
    code = generate_recovery_codes(1)[0]
    stored = hash_recovery_code(code)

    assert verify_recovery_code(code.lower(), stored) is True
    assert verify_recovery_code(code.replace("-", ""), stored) is True
    assert verify_recovery_code(f"  {code}  ", stored) is True


def test_recovery_code_hash_fits_the_column():
    """staff_recovery_code.code_hash is CHAR(64): SHA-256 as hex."""
    stored = hash_recovery_code(generate_recovery_codes(1)[0])

    assert len(stored) == 64


def test_recovery_codes_avoid_visually_ambiguous_characters():
    """Read off paper and typed back in. 0/O and 1/I/L must not both be
    possible, or a correct code will be rejected as a typo."""
    alphabet = set("".join(generate_recovery_codes(30))) - {"-"}

    assert not (alphabet & set("01OIL"))
