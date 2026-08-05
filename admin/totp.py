"""TOTP primitives for mandatory staff two-factor authentication.

Contract: docs/interfaces.md 8.3.

Pure: no database access and no clock reads. The caller passes ``now``, which
is what makes the replay-protection tests deterministic instead of timing
dependent.
"""

import binascii
import io
import secrets as _secrets

import pyotp
import qrcode
import qrcode.image.svg

#: Seconds per TOTP time step. 30 is the RFC 6238 default and what every
#: authenticator app assumes.
TOTP_INTERVAL = 30

#: How many steps either side of the current one are accepted, to absorb
#: clock drift between the server and the user's phone.
DRIFT_STEPS = 1


def generate_totp_secret() -> str:
    """Generate a base32 TOTP secret for a new enrolment."""
    return pyotp.random_base32()


def provisioning_uri(
    secret: str, *, username: str, issuer: str = "Kai Commitment"
) -> str:
    """Build the otpauth:// URI that an authenticator app scans."""
    return pyotp.TOTP(secret, interval=TOTP_INTERVAL).provisioning_uri(
        name=username, issuer_name=issuer
    )


def qr_svg(uri: str) -> str:
    """Render a provisioning URI as inline SVG.

    SVG rather than PNG so that the QR code needs no Pillow dependency and can
    be embedded directly in the enrolment page.
    """
    image = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage)
    buffer = io.BytesIO()
    image.save(buffer)
    return buffer.getvalue().decode("utf-8")


def _normalise_code(raw: str) -> str:
    return raw.replace(" ", "").replace("-", "").strip()


def verify_totp(
    secret: str,
    code: str,
    *,
    now: int,
    last_counter: int | None = None,
) -> int | None:
    """Verify a TOTP code and return the time-step counter it matched.

    Returns None when the code is wrong, malformed, outside the drift window,
    or at or below ``last_counter``.

    ``last_counter`` is ``staff.mfa_last_counter``. A code stays valid for the
    whole 30-second step, so without this check an intercepted code could be
    replayed within that window. The caller persists the returned counter.

    Never raises: both the code and the stored secret can be malformed, and
    each case has to end as a failed verification rather than a 500.
    """
    if not isinstance(code, str):
        return None

    candidate = _normalise_code(code)
    if len(candidate) != 6 or not candidate.isdigit():
        return None

    try:
        totp = pyotp.TOTP(secret, interval=TOTP_INTERVAL)
        current = now // TOTP_INTERVAL
        # Current step first: it is the overwhelmingly common case.
        for offset in (0, *range(-DRIFT_STEPS, 0), *range(1, DRIFT_STEPS + 1)):
            counter = current + offset
            if last_counter is not None and counter <= last_counter:
                continue
            expected = totp.at(counter * TOTP_INTERVAL)
            if _secrets.compare_digest(expected, candidate):
                return counter
    except (binascii.Error, ValueError, TypeError):
        # An unusable secret: empty, not base32, or wrong padding.
        return None

    return None
