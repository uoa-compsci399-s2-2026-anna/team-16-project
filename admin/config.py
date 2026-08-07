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


_BOOL_TRUE_VALUES = ("1", "true", "yes", "on")
_BOOL_FALSE_VALUES = ("0", "false", "no", "off")


def _bool(name: str, default: bool) -> bool:
    """Parse a boolean setting, refusing to guess at an unrecognised value.

    ``_int`` already raises on garbage (``int("ture")`` fails on its own);
    the first version of this function did not match that for a boolean -
    any unrecognised value silently became False. For SESSION_HTTPS_ONLY
    that means a typo drops the Secure flag in production without a sound,
    which is the exact failure Finding 4 raised this setting to prevent in
    the first place. A misconfigured deployment should refuse to start
    rather than quietly run insecure.
    """
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in _BOOL_TRUE_VALUES:
        return True
    if raw in _BOOL_FALSE_VALUES:
        return False
    raise ValueError(
        f"{name}={raw!r} is not a recognised boolean. Use one of "
        f"{_BOOL_TRUE_VALUES + _BOOL_FALSE_VALUES}."
    )


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
    # Where B's POST /api/v1/calculate lives. Defaults to a same-host
    # loopback address because in every environment this panel has run in
    # so far, the API is deployed alongside it; a deployment that splits
    # them onto separate hosts sets API_BASE_URL explicitly.
    api_base_url: str = "http://127.0.0.1:8000"


def load_settings() -> Settings:
    return Settings(
        secret_key=_required("SECRET_KEY"),
        database_url=_required("DATABASE_URL"),
        session_max_age_minutes=_int("SESSION_MAX_AGE_MINUTES", 480),
        login_max_failures=_int("LOGIN_MAX_FAILURES", 5),
        login_lockout_minutes=_int("LOGIN_LOCKOUT_MINUTES", 15),
        session_https_only=_bool("SESSION_HTTPS_ONLY", True),
        api_base_url=os.getenv("API_BASE_URL", "http://127.0.0.1:8000").strip()
        or "http://127.0.0.1:8000",
    )
