"""admin.throttle - login failure counters.

Contract: docs/interfaces.md 8.3. Password failures and TOTP failures share
one counter, and counters are keyed by username, never by IP address
(Decision 3: no IP address is ever stored).
"""

from admin.throttle import LoginThrottle

NOW = 1_000_000.0


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
