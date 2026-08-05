"""admin.config - environment-backed settings.

Contract: docs/interfaces.md 8.3. SESSION_HTTPS_ONLY (Finding 4 of the E-2
panel-pages review, round 1) controls whether the session cookie carries the
Secure flag - a security-relevant boolean, which is why round 2 tightened
how it is parsed: a misconfigured deployment should refuse to start rather
than silently run insecure.
"""

import pytest

from admin.config import load_settings

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
