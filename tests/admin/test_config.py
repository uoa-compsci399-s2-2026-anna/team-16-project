"""admin.config - environment-backed settings.

Contract: docs/interfaces.md 8.3. SESSION_HTTPS_ONLY (Finding 4 of the E-2
panel-pages review, round 1) controls whether the session cookie carries the
Secure flag - a security-relevant boolean, which is why round 2 tightened
how it is parsed: a misconfigured deployment should refuse to start rather
than silently run insecure.
"""

import dataclasses

import pytest

from admin.config import Settings, load_settings

_REQUIRED_ENV = {
    "SECRET_KEY": "test-secret-key-not-used-anywhere-real",
    "DATABASE_URL": "mysql+pymysql://unused/",
}


def _set_required(monkeypatch):
    for name, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize("raw", ["true", "TRUE", "True", "1", "yes", "on"])
def test_session_https_only_accepts_recognised_true_values(monkeypatch, raw):
    _set_required(monkeypatch)
    monkeypatch.setenv("SESSION_HTTPS_ONLY", raw)

    assert load_settings().session_https_only is True


@pytest.mark.parametrize("raw", ["false", "FALSE", "False", "0", "no", "off"])
def test_session_https_only_accepts_recognised_false_values(monkeypatch, raw):
    _set_required(monkeypatch)
    monkeypatch.setenv("SESSION_HTTPS_ONLY", raw)

    assert load_settings().session_https_only is False


def test_session_https_only_defaults_true_when_unset(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.delenv("SESSION_HTTPS_ONLY", raising=False)

    assert load_settings().session_https_only is True


def test_session_https_only_refuses_to_start_on_an_unrecognised_value(monkeypatch):
    """Round 2 minor: an unrecognised value used to fail open - silently
    False, dropping the Secure flag - rather than refusing to start. A
    plausible typo ("ture") is used deliberately rather than nonsense, since
    that is the realistic failure this guards against."""
    _set_required(monkeypatch)
    monkeypatch.setenv("SESSION_HTTPS_ONLY", "ture")

    with pytest.raises(ValueError):
        load_settings()


def test_protection_ships_enabled_by_default():
    """The guard against a test-suite convenience becoming a production
    accident.

    tests/conftest.py's ``admin_app`` fixture builds every app in this
    suite - except tests/admin/test_protection.py's own - with
    ``PROTECTION_ENABLED=false``, deliberately: admin/protection.py's
    ProtectionMiddleware is a deployment concern the rest of the suite was
    never written to expect, and turning it on for every fixture would mean
    header/rate/blocklist checks failing in files that have nothing to do
    with them. That override is only safe as long as nobody ever flips the
    *shipped* default the same way by mistake - so this test reads
    ``Settings.protection_enabled``'s own dataclass field default directly,
    not through ``load_settings()`` (which reads the environment) or any
    fixture (which the test suite itself now overrides). Nothing here can
    accidentally inherit the test-only "false" - that is the point.
    """
    field = next(
        f for f in dataclasses.fields(Settings) if f.name == "protection_enabled"
    )

    assert field.default is True
