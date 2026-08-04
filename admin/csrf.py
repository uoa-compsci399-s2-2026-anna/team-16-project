"""CSRF form tokens.

sqladmin provides none. Every state-changing form in this panel needs one:
without it, an authenticated staff member visiting a hostile page has their
browser submit the panel's own forms with their session cookie attached.

The token is per-session rather than per-form, so several forms open at once
stay valid — a staff member with the enrolment page open in one tab and the
password page in another should not have one invalidate the other.
"""

import secrets

CSRF_SESSION_KEY = "csrf_token"
_TOKEN_BYTES = 32


class CsrfError(Exception):
    """A form arrived without a valid CSRF token."""


def issue_token(session_data: dict) -> str:
    """Return this session's token, minting one on first use."""
    token = session_data.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        session_data[CSRF_SESSION_KEY] = token
    return token


def check_token(session_data: dict, submitted: str | None) -> bool:
    """Constant-time comparison against the session's token.

    False when the session holds no token: a session that never issued one
    has no form legitimately in flight.
    """
    expected = session_data.get(CSRF_SESSION_KEY)
    if not expected or not submitted:
        return False
    return secrets.compare_digest(expected, submitted)
