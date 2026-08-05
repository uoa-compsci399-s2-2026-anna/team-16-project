"""In-memory login throttling.

Contract: docs/interfaces.md 8.3.

Two properties matter here and both are deliberate:

* **Password failures and TOTP failures share one counter.** Throttling only
  the password step would leave a six-digit second factor — a 10^6 search
  space — open to anyone who already holds the password.
* **Counters are keyed by username, never by IP address.** Decision 3 forbids
  storing any identifier for a submitter, and username keying is the more
  precise signal anyway: one NAT address can front an entire company.

State is process-local, and lives in the instance rather than in the module.
That is sufficient for a single-instance deployment, which is what this
project delivers, but it means the application must hold **one** throttle for
its whole lifetime: build it with ``build_throttle`` at start-up and pass that
object around. Multiple instances would need a shared store; this is noted
rather than built (YAGNI).
"""

from dataclasses import dataclass, field

from admin.config import Settings

#: The settings are expressed in minutes because that is the unit an operator
#: thinks in; the throttle works in seconds because that is the unit the clock
#: values passed to it are in.
_SECONDS_PER_MINUTE = 60


@dataclass
class _Attempts:
    count: int = 0
    #: When the most recent failure was recorded.
    last_failure_at: float = 0.0


@dataclass
class LoginThrottle:
    """Counts consecutive login failures per username."""

    max_failures: int
    lockout_seconds: int
    _attempts: dict[str, _Attempts] = field(default_factory=dict, repr=False)

    @staticmethod
    def _key(username: str) -> str:
        # Case-insensitive, or the throttle can be stepped around by varying
        # the case of the username.
        return username.strip().casefold()

    def _expired(self, entry: _Attempts, now: float) -> bool:
        return now - entry.last_failure_at >= self.lockout_seconds

    def record_failure(self, username: str, *, now: float) -> None:
        """Record one failed authentication step."""
        key = self._key(username)
        entry = self._attempts.get(key)
        if entry is None or self._expired(entry, now):
            entry = _Attempts()
            self._attempts[key] = entry
        entry.count += 1
        entry.last_failure_at = now

    def is_locked(self, username: str, *, now: float) -> bool:
        entry = self._attempts.get(self._key(username))
        if entry is None or entry.count < self.max_failures:
            return False
        return not self._expired(entry, now)

    def seconds_remaining(self, username: str, *, now: float) -> int:
        if not self.is_locked(username, now=now):
            return 0
        entry = self._attempts[self._key(username)]
        return int(self.lockout_seconds - (now - entry.last_failure_at))

    def clear(self, username: str) -> None:
        """Forget a username's failures.

        Call only once a login is complete — that is, once a second factor has
        passed. Clearing on a correct password would let anyone holding the
        password reset the counter at will and guess codes indefinitely, which
        is the attack the shared counter exists to stop. ``admin.auth`` is the
        only caller.
        """
        self._attempts.pop(self._key(username), None)


def build_throttle(settings: Settings) -> LoginThrottle:
    """Build the application's throttle from configuration.

    **The result must be a process-wide singleton.** The counters live in the
    instance, so a throttle constructed per request starts empty every time
    and throttles nothing at all — a failure that looks like working code and
    shows up only as an account that never locks.

    Converts ``login_lockout_minutes`` into the seconds the constructor takes.
    That conversion is the whole reason this function exists: passing the
    minutes straight through gives a 15-second lockout where 15 minutes was
    configured, and nothing downstream would notice.

    Non-positive values are refused here rather than tolerated. A zero
    ``max_failures`` locks every username on sight and a zero lockout expires
    the instant it is taken, so both produce a panel that is broken in a way
    no test of the throttle itself would catch.
    """
    if settings.login_max_failures < 1:
        raise ValueError(
            "LOGIN_MAX_FAILURES must be at least 1; "
            f"got {settings.login_max_failures}."
        )
    if settings.login_lockout_minutes < 1:
        raise ValueError(
            "LOGIN_LOCKOUT_MINUTES must be at least 1; "
            f"got {settings.login_lockout_minutes}."
        )
    return LoginThrottle(
        max_failures=settings.login_max_failures,
        lockout_seconds=settings.login_lockout_minutes * _SECONDS_PER_MINUTE,
    )
