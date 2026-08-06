"""An unknown username must cost the same as a known one.

Contract §8.3 makes the throttle refuse to act as an oracle for which
usernames exist. Returning early on an unknown username reinstates that
oracle through the clock instead.
"""

import time

import pyotp
import pytest

from admin import auth
from admin.accounts import (
    begin_mfa_enrolment,
    complete_mfa_enrolment,
    create_staff,
    get_staff,
    set_password,
)
from admin.auth import authenticate_password
from admin.config import Settings
from admin.models import Staff
from admin.throttle import build_throttle
from admin.totp import TOTP_INTERVAL

pytestmark = pytest.mark.db

SECRET_KEY = "test-secret-key-not-used-anywhere-real"
NOW = 1800


@pytest.fixture
def enrolled_staff(session) -> Staff:
    """A fully onboarded account: password changed and MFA enrolled.

    Built through the service functions rather than by hand-constructing a
    Staff row, so the account is in a state require_staff_username actually
    admits - the same reasoning as test_session_generation.py's and
    test_issue_password.py's fixtures of the same name.
    """
    username = "erin"
    _, password = create_staff(session, username=username, display_name="Erin")
    session.flush()
    set_password(session, username, "a-strong-initial-password")
    secret, _ = begin_mfa_enrolment(session, username, secret_key=SECRET_KEY)
    code = pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(NOW)
    complete_mfa_enrolment(session, username, code, secret_key=SECRET_KEY, now=NOW)
    session.flush()
    return get_staff(session, username)


@pytest.fixture
def settings() -> Settings:
    """Enough Settings to build a throttle. Values follow test_throttle.py's
    make_settings defaults; this module has no need to vary them."""
    return Settings(
        secret_key=SECRET_KEY,
        database_url="mysql+pymysql://unused/",
        session_max_age_minutes=480,
        login_max_failures=5,
        login_lockout_minutes=15,
    )


def test_an_unknown_username_still_costs_a_password_verification(
    session, enrolled_staff, settings, monkeypatch
):
    """The behavioural gate: the expensive work happens on both paths.

    Asserted on the call rather than on elapsed time because a wall-clock
    assertion is flaky on a loaded CI machine — but the call is what the
    elapsed time is made of.
    """
    calls = []
    real = auth.verify_password
    monkeypatch.setattr(
        auth, "verify_password",
        lambda pw, h: calls.append(h) or real(pw, h),
    )

    authenticate_password(
        session, "no-such-account", "any-password",
        throttle=build_throttle(settings), now=time.monotonic(),
    )

    assert len(calls) == 1, "the unknown-username path skipped the verification"


def test_the_two_paths_take_comparable_time(session, enrolled_staff, settings):
    """The statistical backstop, with a deliberately loose bound.

    bcrypt at the project's cost factor dominates both paths once the dummy
    verification is in place, so the ratio should sit near 1.0. The bound is
    4x because CI machines stall; the defect this catches was 56x.
    """
    throttle = build_throttle(settings)

    def elapsed(username: str) -> float:
        start = time.perf_counter()
        authenticate_password(
            session, username, "wrong-password",
            throttle=throttle, now=time.monotonic(),
        )
        return time.perf_counter() - start

    known = min(elapsed(enrolled_staff.username) for _ in range(3))
    unknown = min(elapsed(f"no-such-account-{i}") for i in range(3))

    ratio = max(known, unknown) / min(known, unknown)
    assert ratio < 4.0, f"timing differs by {ratio:.1f}x — usernames are enumerable"
