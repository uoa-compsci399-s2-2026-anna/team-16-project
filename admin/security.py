"""Security primitives for the admin panel.

Contract: docs/interfaces.md 2.4 and 8.3.
"""

import base64
import hashlib
import secrets

import bcrypt
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

#: bcrypt hashes only the first 72 bytes of its input and discards the rest
#: without complaint.
BCRYPT_MAX_BYTES = 72

#: HKDF ``info`` for the TOTP-secret encryption key. Any future use of
#: SECRET_KEY must pick a different value here so the two never share a key.
TOTP_ENCRYPTION_INFO = b"totp-secret-encryption"


class PasswordTooLongError(ValueError):
    """Password exceeds bcrypt's 72-byte input limit."""


class TotpSecretUndecryptableError(RuntimeError):
    """A stored TOTP secret cannot be decrypted with the current SECRET_KEY.

    Almost always means SECRET_KEY was changed without running
    ``python -m admin.rotate_key``.
    """


def hash_password(plain: str) -> str:
    """Hash a password for storage in ``staff.password_hash``.

    Raises ``PasswordTooLongError`` above 72 UTF-8 bytes rather than letting
    bcrypt truncate in silence, which would leave someone believing a long
    passphrase protects their account when only its first 72 bytes do.
    """
    encoded = plain.encode("utf-8")
    if len(encoded) > BCRYPT_MAX_BYTES:
        raise PasswordTooLongError(
            f"Password is {len(encoded)} bytes; the maximum is {BCRYPT_MAX_BYTES}. "
            "Note that this is a byte limit, so non-ASCII characters count for "
            "more than one."
        )
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode("ascii")


def verify_password(plain: str, stored: str) -> bool:
    """Check a password against a stored hash.

    Returns False, rather than raising, for malformed input of either kind:
    an over-long candidate, or a stored hash that has been truncated or
    hand-edited. This sits directly behind a public login form, so every
    failure mode has to end as a failed login rather than a 500.
    """
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), stored.encode("ascii"))
    except ValueError:
        # bcrypt raises ValueError for both "password cannot be longer than
        # 72 bytes" and "Invalid salt"; UnicodeEncodeError, from a non-ASCII
        # stored hash, is itself a ValueError subclass.
        return False


# --- TOTP secret encryption at rest -----------------------------------------


def _derive_key(secret_key: str, info: bytes) -> bytes:
    """Derive a 32-byte, purpose-specific key from SECRET_KEY via HKDF-SHA256.

    ``info`` names the purpose. SECRET_KEY already signs session cookies, so
    it is never used as an encryption key directly.

    No salt: SECRET_KEY is high-entropy random material, which is the case
    HKDF's salt is optional for.
    """
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=info,
    ).derive(secret_key.encode("utf-8"))


def _fernet(secret_key: str) -> Fernet:
    derived = _derive_key(secret_key, TOTP_ENCRYPTION_INFO)
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_totp_secret(secret: str, *, secret_key: str) -> bytes:
    """Encrypt a TOTP secret for ``staff.mfa_secret_enc`` (VARBINARY(255)).

    Protects the case where a database dump leaks on its own — a committed
    backup, a misconfigured export. It does not protect against losing the
    database and SECRET_KEY together.
    """
    return _fernet(secret_key).encrypt(secret.encode("utf-8"))


def decrypt_totp_secret(blob: bytes, *, secret_key: str) -> str:
    """Decrypt a stored TOTP secret.

    Raises ``TotpSecretUndecryptableError`` rather than returning something
    unusable: a silently wrong secret would present as "your authenticator
    codes stopped working", which is a much harder thing to diagnose than a
    named error pointing at key rotation.
    """
    try:
        return _fernet(secret_key).decrypt(blob).decode("utf-8")
    except InvalidToken as exc:
        raise TotpSecretUndecryptableError(
            "Stored TOTP secret cannot be decrypted with the current "
            "SECRET_KEY. If SECRET_KEY was changed, run "
            "python -m admin.rotate_key --old <old> --new <new>."
        ) from exc


# --- Recovery codes ---------------------------------------------------------

#: Omits 0, 1, I, L and O. These codes are printed, filed away and typed back
#: in by hand, so a character that can be misread is a character that turns a
#: correct code into a failed recovery.
RECOVERY_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
RECOVERY_CODE_GROUPS = 3
RECOVERY_CODE_GROUP_LENGTH = 4


def _one_recovery_code() -> str:
    groups = [
        "".join(
            secrets.choice(RECOVERY_CODE_ALPHABET)
            for _ in range(RECOVERY_CODE_GROUP_LENGTH)
        )
        for _ in range(RECOVERY_CODE_GROUPS)
    ]
    return "-".join(groups)


def generate_recovery_codes(count: int) -> list[str]:
    """Generate ``count`` distinct single-use recovery codes.

    Twelve characters from a 31-character alphabet is roughly 59 bits.
    Returned in plaintext because this is the only moment they exist in
    readable form — only their hashes are stored.
    """
    codes: list[str] = []
    seen: set[str] = set()
    while len(codes) < count:
        code = _one_recovery_code()
        if code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


def _normalise_recovery_code(raw: str) -> str:
    return raw.strip().upper().replace("-", "").replace(" ", "")


def hash_recovery_code(code: str) -> str:
    """Hash a recovery code for ``staff_recovery_code.code_hash`` (CHAR(64)).

    SHA-256 rather than bcrypt, deliberately. bcrypt is slow in order to
    resist brute force against low-entropy human-chosen passwords; a
    recovery code is high-entropy material we generated ourselves, so brute
    force is already infeasible and a slow hash would buy nothing but
    latency on a path someone reaches while locked out.
    """
    normalised = _normalise_recovery_code(code)
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def verify_recovery_code(candidate: str, stored_hash: str) -> bool:
    """Check a recovery code against a stored hash, in constant time."""
    return secrets.compare_digest(hash_recovery_code(candidate), stored_hash)
