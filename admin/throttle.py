"""In-memory login throttling.

Contract: docs/interfaces.md 8.3.

Two properties matter here and both are deliberate:

* **Password failures and TOTP failures share one counter.** Throttling only
  the password step would leave a six-digit second factor — a 10^6 search
  space — open to anyone who already holds the password.
* **Counters are keyed by username, never by IP address.** Decision 3 forbids
  storing any identifier for a submitter, and username keying is the more
  precise signal anyway: one NAT address can front an entire company.

State is process-local. That is sufficient for a single-instance deployment,
which is what this project delivers. Multiple instances would need a shared
store; this is noted rather than built (YAGNI).
"""

from dataclasses import dataclass, field


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
        """Forget a username's failures. Call on successful login."""
        self._attempts.pop(self._key(username), None)
