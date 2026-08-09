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
    """Parse a positive integer setting, refusing zero and negatives.

    ``int()`` already raises on garbage, which is what ``_bool`` was
    hardened to match. It does not raise on ``0`` or ``-1``, and every
    setting read through this function is a count or a duration where
    neither is meaningful. The one that turns a typo into an outage is
    ``PROTECTION_MAX_REQUESTS_PER_MINUTE=0``: ``admin/protection.py``
    refuses a request once its count *exceeds* the limit, so a limit of 0
    means the first unauthenticated request from any address is refused with
    429 — including on ``/admin/login``, which is the page an operator
    would then need in order to fix it. The others fail in the same shape:
    ``SESSION_MAX_AGE_MINUTES=0`` expires every session as it is minted,
    ``LOGIN_MAX_FAILURES=0`` locks out every account on its first attempt.

    ``.env.example`` is the one file the client will edit by hand, and a
    misconfigured deployment should refuse to start rather than start
    refusing everyone — the same ruling ``_bool`` records.
    """
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    value = int(raw)
    if value < 1:
        raise ValueError(
            f"{name}={raw!r} must be 1 or greater. Zero or a negative value "
            "would refuse or expire everything this setting governs, "
            "including the pages needed to correct it."
        )
    return value


def _str(name: str, default: str) -> str:
    raw = os.getenv(name, "").strip()
    return raw if raw else default


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


#: Where B's POST /api/v1/calculate lives, absent an explicit API_BASE_URL.
#: A same-host loopback address because in every environment this panel has
#: run in so far, the API is deployed alongside it; a deployment that splits
#: them onto separate hosts sets API_BASE_URL explicitly (see .env.example).
_DEFAULT_API_BASE_URL = "http://127.0.0.1:8000"


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
    api_base_url: str = _DEFAULT_API_BASE_URL
    # Whether ProtectionMiddleware (admin/protection.py) runs at all. True by
    # default; an operator debugging a false-positive block can flip this off
    # without redeploying code, at the cost of the blocklist and the header/
    # rate checks all going dark together - there is no finer-grained switch.
    protection_enabled: bool = True
    protection_max_requests_per_minute: int = 30
    # Whether to trust X-Forwarded-For for the caller's address. Defaults
    # False, and that default is load-bearing, not a placeholder: behind a
    # reverse proxy every caller arrives as the proxy's own address, so with
    # this True but no proxy in front, the rate limit becomes one counter
    # shared by every visitor and a single blocked address blocks everyone.
    # With this False (the only safe default) admin/protection.py reads
    # request.client.host and ignores X-Forwarded-For entirely - a header a
    # caller can set to anything, so trusting it without a proxy that
    # actually strips/overwrites inbound copies of it would let any caller
    # forge whichever address they like. Set True only once a reverse proxy
    # that overwrites X-Forwarded-For itself sits in front of this panel.
    protection_trusted_proxy: bool = False


def load_settings() -> Settings:
    return Settings(
        secret_key=_required("SECRET_KEY"),
        database_url=_required("DATABASE_URL"),
        session_max_age_minutes=_int("SESSION_MAX_AGE_MINUTES", 480),
        login_max_failures=_int("LOGIN_MAX_FAILURES", 5),
        login_lockout_minutes=_int("LOGIN_LOCKOUT_MINUTES", 15),
        session_https_only=_bool("SESSION_HTTPS_ONLY", True),
        api_base_url=_str("API_BASE_URL", _DEFAULT_API_BASE_URL),
        protection_enabled=_bool("PROTECTION_ENABLED", True),
        protection_max_requests_per_minute=_int(
            "PROTECTION_MAX_REQUESTS_PER_MINUTE", 30
        ),
        protection_trusted_proxy=_bool("PROTECTION_TRUSTED_PROXY", False),
    )
