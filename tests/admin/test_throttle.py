"""admin.throttle - login failure counters.

Contract: docs/interfaces.md 8.3. Password failures and TOTP failures share
one counter, and counters are keyed by username, never by IP address
(Decision 3: no IP address is ever stored).
"""

import pytest

from admin.config import Settings
from admin.throttle import LoginThrottle, build_throttle

NOW = 1_000_000.0


def make_settings(*, max_failures: int = 5, lockout_minutes: int = 15) -> Settings:
    return Settings(
        secret_key="test-secret-key-not-used-anywhere-real",
        database_url="mysql+pymysql://unused/",
        session_max_age_minutes=480,
        login_max_failures=max_failures,
        login_lockout_minutes=lockout_minutes,
    )


def make_throttle() -> LoginThrottle:
    return LoginThrottle(max_failures=3, lockout_seconds=900)


def test_a_fresh_username_is_not_locked():
    throttle = make_throttle()

    assert throttle.is_locked("alice", now=NOW) is False


def test_failures_below_the_threshold_do_not_lock():
    throttle = make_throttle()

    throttle.record_failure("alice", now=NOW)
    throttle.record_failure("alice", now=NOW)

    assert throttle.is_locked("alice", now=NOW) is False


def test_reaching_the_threshold_locks_the_username():
    throttle = make_throttle()

    for _ in range(3):
        throttle.record_failure("alice", now=NOW)

    assert throttle.is_locked("alice", now=NOW) is True


def test_the_lock_expires_after_the_lockout_period():
    throttle = make_throttle()
    for _ in range(3):
        throttle.record_failure("alice", now=NOW)

    assert throttle.is_locked("alice", now=NOW + 901) is False


def test_the_lock_is_still_in_force_one_second_before_expiry():
    throttle = make_throttle()
    for _ in range(3):
        throttle.record_failure("alice", now=NOW)

    assert throttle.is_locked("alice", now=NOW + 899) is True


def test_locking_one_username_does_not_lock_another():
    throttle = make_throttle()
    for _ in range(3):
        throttle.record_failure("alice", now=NOW)

    assert throttle.is_locked("bob", now=NOW) is False


def test_a_successful_login_clears_the_counter():
    throttle = make_throttle()
    throttle.record_failure("alice", now=NOW)
    throttle.record_failure("alice", now=NOW)

    throttle.clear("alice")
    throttle.record_failure("alice", now=NOW)

    assert throttle.is_locked("alice", now=NOW) is False


def test_the_counter_resets_once_the_lock_has_expired():
    """Otherwise a single further failure after the lock lifts would lock the
    account again immediately."""
    throttle = make_throttle()
    for _ in range(3):
        throttle.record_failure("alice", now=NOW)

    later = NOW + 901
    throttle.record_failure("alice", now=later)

    assert throttle.is_locked("alice", now=later) is False


def test_seconds_remaining_counts_down():
    throttle = make_throttle()
    for _ in range(3):
        throttle.record_failure("alice", now=NOW)

    assert throttle.seconds_remaining("alice", now=NOW) == 900
    assert throttle.seconds_remaining("alice", now=NOW + 300) == 600


def test_seconds_remaining_is_zero_when_not_locked():
    throttle = make_throttle()

    assert throttle.seconds_remaining("alice", now=NOW) == 0


def test_usernames_are_matched_case_insensitively():
    """Otherwise 'Alice' and 'alice' get a fresh allowance each, and the
    throttle can be walked straight past by varying the case."""
    throttle = make_throttle()
    for _ in range(3):
        throttle.record_failure("alice", now=NOW)

    assert throttle.is_locked("ALICE", now=NOW) is True


# --- wiring the configured values onto the throttle --------------------------


def test_build_throttle_converts_lockout_minutes_to_seconds():
    """The setting is in minutes and the constructor wants seconds. Handing
    the minutes straight over gives a 15-second lockout in place of a
    15-minute one, which is a throttle in name only."""
    throttle = build_throttle(make_settings(max_failures=5, lockout_minutes=15))

    assert throttle.max_failures == 5
    assert throttle.lockout_seconds == 900


def test_a_built_throttle_holds_a_lock_for_the_configured_minutes():
    """The conversion checked through behaviour rather than through the
    attribute, so a units mix-up cannot pass by agreeing with itself."""
    throttle = build_throttle(make_settings(max_failures=3, lockout_minutes=15))
    for _ in range(3):
        throttle.record_failure("alice", now=NOW)

    assert throttle.is_locked("alice", now=NOW + 14 * 60) is True
    assert throttle.is_locked("alice", now=NOW + 15 * 60 + 1) is False


def test_a_non_positive_max_failures_is_refused():
    """Zero would lock every username on sight; a negative value is the same
    thing. Either way the panel becomes unusable rather than merely insecure,
    so it is worth refusing at start-up instead of at the first login."""
    with pytest.raises(ValueError):
        build_throttle(make_settings(max_failures=0))


def test_a_non_positive_lockout_is_refused():
    """A zero-length lockout is a throttle that never throttles: the lock
    expires the instant it is taken, so failures are unlimited."""
    with pytest.raises(ValueError):
        build_throttle(make_settings(lockout_minutes=0))
