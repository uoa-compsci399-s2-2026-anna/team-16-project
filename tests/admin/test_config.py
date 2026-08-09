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


def test_protection_ships_enabled_by_default(monkeypatch):
    """The guard against a test-suite convenience becoming a production
    accident.

    tests/conftest.py's ``_protection_off_by_default`` fixture sets
    ``PROTECTION_ENABLED=false`` in the environment for this whole test
    session, deliberately: admin/protection.py's ProtectionMiddleware is a
    deployment concern the rest of the suite was never written to expect,
    and leaving it on for every fixture would mean header/rate/blocklist
    checks failing in files that have nothing to do with them.

    An earlier version of this test read ``Settings.protection_enabled``'s
    dataclass field default directly via ``dataclasses.fields(Settings)``,
    reasoning that this couldn't be fooled by the environment. That reasoning
    had a hole: ``load_settings()`` - the function every real deployment
    actually calls - never reads the dataclass field default at all. It
    passes ``protection_enabled=_bool("PROTECTION_ENABLED", True)``
    explicitly (admin/config.py), a second, independent ``True`` that has to
    be kept in sync with the field's own by hand. The dataclass-field version
    of this test could not see that second value drift - flipping
    ``load_settings()``'s own default to ``False`` would ship a panel with
    protection off on every deployment that never sets the env var, and this
    test would still pass.

    Fixed by calling ``load_settings()`` itself, the actual production path,
    with the required settings present and ``PROTECTION_ENABLED`` explicitly
    absent - ``monkeypatch.delenv`` is required here specifically because
    ``_protection_off_by_default`` has already put "false" in the
    environment for this session, and this test exists to check what happens
    with *no* override present, i.e. what a fresh deployment gets.
    """
    _set_required(monkeypatch)
    monkeypatch.delenv("PROTECTION_ENABLED", raising=False)

    assert load_settings().protection_enabled is True


@pytest.mark.parametrize("raw", ["0", "-1", "-30"])
def test_protection_rate_limit_refuses_zero_and_negatives(monkeypatch, raw):
    """`PROTECTION_MAX_REQUESTS_PER_MINUTE=0` used to be accepted, and
    admin/protection.py refuses a request once the count *exceeds* the limit -
    so 0 refused the first unauthenticated request from every address with
    429, `/admin/login` included, i.e. the page an operator would then need
    to fix it. `_bool` was deliberately hardened to refuse garbage rather
    than guess; `_int` accepted 0 and negatives in the one file the client
    edits by hand.
    """
    _set_required(monkeypatch)
    monkeypatch.setenv("PROTECTION_MAX_REQUESTS_PER_MINUTE", raw)

    with pytest.raises(ValueError):
        load_settings()


@pytest.mark.parametrize("name", [
    "SESSION_MAX_AGE_MINUTES", "LOGIN_MAX_FAILURES", "LOGIN_LOCKOUT_MINUTES",
])
def test_every_integer_setting_refuses_zero(monkeypatch, name):
    """The guard is in `_int` itself rather than at the one call site that
    prompted it: every setting read through it is a count or a duration where
    zero fails in the same shape (a session that expires as it is minted, an
    account locked out on its first attempt). A guard at one call site is one
    the next `_int` setting silently does not get."""
    _set_required(monkeypatch)
    monkeypatch.setenv(name, "0")

    with pytest.raises(ValueError):
        load_settings()


def test_the_rate_limit_still_accepts_a_real_value(monkeypatch):
    """The counter-case: the guard must not have made the setting unusable."""
    _set_required(monkeypatch)
    monkeypatch.setenv("PROTECTION_MAX_REQUESTS_PER_MINUTE", "1")

    assert load_settings().protection_max_requests_per_minute == 1
