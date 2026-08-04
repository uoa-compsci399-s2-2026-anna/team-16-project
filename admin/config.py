"""Environment-backed settings for the admin panel.

Reading only - anything that transforms a value (key derivation, hashing)
lives in ``admin.security`` where it is tested.
"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


class MissingSettingError(RuntimeError):
    """A required setting is absent or empty."""


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise MissingSettingError(
            f"{name} is not set. Copy .env.example to .env and fill it in."
        )
    return value


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    secret_key: str
    database_url: str
    session_max_age_minutes: int
    login_max_failures: int
    login_lockout_minutes: int
    # Defaults True: the session cookie carries admin access, and the
    # deployment terminates TLS upstream, which is exactly the arrangement
    # where a missing Secure flag lets one http:// navigation on the admin
    # subdomain hand over the session. Set SESSION_HTTPS_ONLY=false only for
    # local development served over plain http.
    session_https_only: bool = True


def load_settings() -> Settings:
    return Settings(
        secret_key=_required("SECRET_KEY"),
        database_url=_required("DATABASE_URL"),
        session_max_age_minutes=_int("SESSION_MAX_AGE_MINUTES", 480),
        login_max_failures=_int("LOGIN_MAX_FAILURES", 5),
        login_lockout_minutes=_int("LOGIN_LOCKOUT_MINUTES", 15),
        session_https_only=_bool("SESSION_HTTPS_ONLY", True),
    )
