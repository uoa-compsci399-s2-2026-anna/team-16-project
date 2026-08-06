"""A credential change must end the sessions that predate it.

Contract v0.7 listed this as a known limitation; v0.8 removes it.
"""

import pyotp
import pytest

from admin.accounts import (
    begin_mfa_enrolment,
    bump_session_generation,
    complete_mfa_enrolment,
    create_staff,
    get_staff,
    reset_mfa,
    set_password,
)
from admin.auth import (
    SESSION_GENERATION_KEY,
    SESSION_KEY,
    StaffAuthRequired,
    require_staff_username,
    stamp_session,
)
from admin.models import Staff
from admin.totp import TOTP_INTERVAL

pytestmark = pytest.mark.db

SECRET_KEY = "test-secret-key-not-used-anywhere-real"
NOW = 1800


@pytest.fixture
def enrolled_staff(session) -> Staff:
    """A fully onboarded account: password changed and MFA enrolled.

    Built through the service functions rather than by hand-constructing a
    Staff row, so the account is in a state require_staff_username actually
    admits — the same reasoning as test_auth.py's own `enrolled()` helper.
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


def test_a_stamped_session_is_accepted(session, enrolled_staff):
    session_data = {}
    stamp_session(session_data, enrolled_staff)

    assert require_staff_username(session, session_data) == enrolled_staff.username


def test_a_password_change_invalidates_the_session_that_predates_it(
    session, enrolled_staff
):
    session_data = {}
    stamp_session(session_data, enrolled_staff)

    set_password(session, enrolled_staff.username, "a-brand-new-password")
    session.flush()

    with pytest.raises(StaffAuthRequired):
        require_staff_username(session, session_data)


def test_an_mfa_reset_invalidates_the_session_that_predates_it(
    session, enrolled_staff
):
    """Contract §8.3 L2: another administrator resets a compromised account.

    Leaving the compromised session live would make the reset cosmetic.
    """
    session_data = {}
    stamp_session(session_data, enrolled_staff)

    reset_mfa(session, enrolled_staff.username)
    session.flush()

    with pytest.raises(StaffAuthRequired):
        require_staff_username(session, session_data)


def test_a_session_with_no_generation_at_all_is_refused(session, enrolled_staff):
    """Cookies minted before this feature shipped carry no generation.

    Treating "absent" as "matches" would leave every pre-existing session
    valid forever — which is the defect this task exists to fix.
    """
    session_data = {SESSION_KEY: enrolled_staff.username}

    with pytest.raises(StaffAuthRequired):
        require_staff_username(session, session_data)


def test_a_forged_generation_is_refused(session, enrolled_staff):
    session_data = {SESSION_KEY: enrolled_staff.username, SESSION_GENERATION_KEY: 999}

    with pytest.raises(StaffAuthRequired):
        require_staff_username(session, session_data)


def test_bump_returns_the_new_value(session, enrolled_staff):
    before = enrolled_staff.session_generation
    after = bump_session_generation(session, enrolled_staff.username)

    assert after == before + 1
    assert get_staff(session, enrolled_staff.username).session_generation == after
