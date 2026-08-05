"""admin.csrf - form token protection.

sqladmin ships no CSRF protection of any kind. Every state-changing form in
this panel — changing a password, enrolling an authenticator, later
publishing a factor set — needs one, or a single induced click from an
authenticated staff member's browser performs the action.
"""

from admin.csrf import CSRF_SESSION_KEY, check_token, issue_token


def test_issuing_a_token_stores_it_in_the_session():
    session = {}

    token = issue_token(session)

    assert session[CSRF_SESSION_KEY] == token


def test_issuing_twice_returns_the_same_token():
    """A second form rendered in the same session must not invalidate the
    first one still open in another tab."""
    session = {}

    assert issue_token(session) == issue_token(session)


def test_tokens_differ_between_sessions():
    assert issue_token({}) != issue_token({})


def test_a_matching_token_passes():
    session = {}
    token = issue_token(session)

    assert check_token(session, token) is True


def test_a_wrong_token_fails():
    session = {}
    issue_token(session)

    assert check_token(session, "not-the-token") is False


def test_a_missing_token_fails():
    session = {}
    issue_token(session)

    assert check_token(session, None) is False
    assert check_token(session, "") is False


def test_a_session_with_no_token_rejects_everything():
    """Including an empty submission — otherwise a session that never issued
    a token would accept a blank field."""
    assert check_token({}, "anything") is False
    assert check_token({}, None) is False


def test_the_token_is_long_enough_to_be_unguessable():
    assert len(issue_token({})) >= 32
