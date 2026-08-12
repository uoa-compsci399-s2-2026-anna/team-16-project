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

#: HKDF ``info`` for the unclaimed-password key. **Distinct from
#: TOTP_ENCRYPTION_INFO on purpose, and the rule that says so is the comment
#: directly above.** The two protect different things with different lifetimes
#: — a TOTP secret is valid for the life of a device, an unclaimed password
#: until it is claimed — and sharing a key would mean a compromise of one
#: analysis is a compromise of both, and that ``kaicalc-admin rotate-key``
#: could not be taught to re-wrap one without the other.
#:
#: **The byte string is frozen and must never be "tidied up" to match the
#: name.** v1.16 renamed the column, this constant and both functions below
#: from *initial* to *unclaimed*; the ``info`` is key-derivation material, so
#: changing these bytes derives a different key and turns every value already
#: stored under the old one into a blob nothing can open — silently, since
#: Fernet's failure is an ``InvalidToken`` at read time, on a credential
#: screen, for exactly the accounts that were mid-onboarding. A rename is free;
#: this line is not.
UNCLAIMED_PASSWORD_ENCRYPTION_INFO = b"initial-password-encryption"


class PasswordTooLongError(ValueError):
    """Password exceeds bcrypt's 72-byte input limit."""


class TotpSecretUndecryptableError(RuntimeError):
    """A stored TOTP secret cannot be decrypted with the current SECRET_KEY.

    Almost always means SECRET_KEY was changed without running
    ``python -m admin.cli rotate-key``.
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


def _fernet_for(secret_key: str, info: bytes) -> Fernet:
    """A Fernet bound to one purpose, named by ``info``.

    Purpose-separated at this level rather than by each caller deriving its
    own: a second copy of these two lines is a second place for the ``info``
    argument to be omitted, and omitting it is silent - two purposes sharing
    one key encrypt and decrypt each other's values perfectly well.
    """
    derived = _derive_key(secret_key, info)
    return Fernet(base64.urlsafe_b64encode(derived))


def _fernet(secret_key: str) -> Fernet:
    return _fernet_for(secret_key, TOTP_ENCRYPTION_INFO)


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
            "python -m admin.cli rotate-key --old <old> --new <new>."
        ) from exc


# --- The unclaimed password -------------------------------------------------
#
# **This is reversible storage of a live credential, and it is the one place in
# this system that has any.** Everything else a person could type is hashed:
# `password_hash` is bcrypt, `code_hash` is SHA-256, and neither can be read
# back at all. Contract v1.15 item 3 records the decision and its cost, and
# v1.16 widened it from the password an account is created with to any password
# the system has minted for it and nobody has claimed; what follows is the part
# that belongs beside the code.
#
# WHAT IT BUYS. A system-minted password is shown once, on the page that
# creates the account or the page that issues a replacement, and handed over in
# person - there is no email system, deliberately. A closed tab loses it. The
# account survives (nobody can log in as it without also completing the forced
# change), but the administrator has to issue another, and if the lost one had
# already been read out to the colleague, that is now a password they will try
# and be refused by. Keeping the value until it is claimed removes that whole
# exchange - and v1.16 removed it from the *issued* case too, which until then
# was the one the original feature left unprotected.
#
# WHAT IT COSTS, precisely. Anyone holding a database dump *and* SECRET_KEY can
# log in as every account holding an unclaimed password. A newly created
# account is pre-MFA in a way that does not help: the attacker reaches the
# forced enrolment page and enrols their own authenticator, so the password is
# the whole of the protection. An account that has already enrolled and has
# only had a password issued still holds its second factor, so there the
# password is one of two. What bounds the exposure in both cases is the
# column's lifetime - NULL before creation, NULL from each password change
# onward, and for a colleague sitting next to you that is minutes.
#
# WHAT IT DOES NOT PROTECT AGAINST, said plainly for the same reason
# `encrypt_totp_secret` says it: losing the database and SECRET_KEY together.
# In the shipped container arrangement SECRET_KEY lives in its own named
# volume (docker/entrypoint.sh writes /var/lib/kaicalc/secret_key) that is
# mounted into the application services and *not* into the database container,
# so an ordinary dump - mysqldump, a leaked backup, a misconfigured export -
# does not carry it. A compromise of the Docker host carries both.


def encrypt_unclaimed_password(plain: str, *, secret_key: str) -> bytes:
    """Encrypt an unclaimed password for ``staff.unclaimed_password_enc``.

    Fernet, so this is reversible - which is the entire point and the entire
    cost. See the note above. Two callers, both in ``admin/accounts.py``:
    ``create_staff`` and, since v1.16, ``issue_password``.
    """
    return _fernet_for(secret_key, UNCLAIMED_PASSWORD_ENCRYPTION_INFO).encrypt(
        plain.encode("utf-8")
    )


def decrypt_unclaimed_password(blob: bytes, *, secret_key: str) -> str:
    """Read back a stored unclaimed password.

    Raises ``TotpSecretUndecryptableError`` - the same error the TOTP path
    raises, and for the same cause: SECRET_KEY was changed without rotating.
    Reusing it rather than adding a second, near-identical exception keeps the
    one recovery instruction ("run rotate-key") attached to every symptom of
    the one mistake that produces it.
    """
    try:
        return (
            _fernet_for(secret_key, UNCLAIMED_PASSWORD_ENCRYPTION_INFO)
            .decrypt(blob)
            .decode("utf-8")
        )
    except InvalidToken as exc:
        raise TotpSecretUndecryptableError(
            "A stored unclaimed password cannot be decrypted with the current "
            "SECRET_KEY. If SECRET_KEY was changed, run "
            "python -m admin.cli rotate-key --old <old> --new <new>."
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
