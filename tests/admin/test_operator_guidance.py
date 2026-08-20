"""The paths an operator is *told* to visit are paths the panel actually serves.

**Why this file exists.** Four operator-facing messages named the screen an
unclaimed password can be read back from. Three of them said ``/admin/staff``,
which is a 404 — sqladmin serves the staff list at ``/admin/staff/list`` and
registers nothing at the bare prefix. The fourth, ``rotate-key``'s, said
``/admin/staff/unclaimed-password`` and was right, so the tree disagreed with
itself for four commits and nothing failed.

Nothing failed because the only assertion touching any of them was

    assert "the other administrator can read it back from /admin/staff" in out

which is true of the broken form *and* of the correct one — the broken form is
a prefix of the fix. That is this project's recurring "passes for the wrong
reason" shape, and an assertion on a URL is where it is easiest to write by
accident: the substring that names the thing is almost always a prefix of the
string that works.

**So these tests do not check spelling. They resolve the path.** Every
``/admin/...`` path the CLI and ``docker/init.sh`` name is driven as a real,
signed-in administrator and must not answer 404. A message that names a screen
nobody can open is the defect, and only a request can tell the two apart —
which is the same standard ``test_role_matrix.py`` applies to the role floor,
for the same reason.

**Who reads these lines, and when.** Somebody who has lost a bootstrap password
and is following the single sentence that says it can be re-issued. A 404 at
that moment reads as "it is gone", at exactly the point they are already
worried they have locked themselves out of their own deployment.
"""

import ast
import re
from pathlib import Path

import pytest

from admin.cli import (
    BOOTSTRAP_CREATED_MARKER,
    UNCLAIMED_PASSWORD_SCREEN,
    main,
    report_bootstrap_result,
)
from tests.admin.conftest import _cleanup_staff_named

#: `db` for the whole file; `asyncio` per test rather than file-wide. This file
#: mixes async HTTP tests with synchronous ones that only capture stdout, and
#: pytest-asyncio warns on every synchronous test carrying the mark. pytest.ini
#: sets `asyncio_mode = strict`, so each `async def` below needs it explicitly.
pytestmark = [pytest.mark.db]

#: Repository root: tests/admin/test_operator_guidance.py -> parents[2].
_ROOT = Path(__file__).resolve().parents[2]

_CLI_SOURCE = _ROOT / "admin" / "cli.py"
_INIT_SH = _ROOT / "docker" / "init.sh"

#: An ``/admin/...`` path as it appears in prose or in a printed message.
#:
#: Anchored at both ends on purpose. Three mutants have survived on this branch
#: inside a week because of an unanchored pattern, so: the leading ``/admin/``
#: is literal, each segment is a full segment rather than a partial match, and
#: the character class deliberately excludes ``,`` ``.`` ``` ` ``` and ``'`` so
#: that trailing punctuation in a sentence is not swallowed into the path.
_ADMIN_PATH = re.compile(r"/admin/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*")


def _paths_in(text: str) -> set[str]:
    """Every ``/admin/...`` path in ``text``, minus the family spellings.

    ``/admin/staff/*`` means "the staff screens" and is not a URL anybody
    types; counting it would make this test demand that a literal asterisk
    route exist. It is excluded by looking at what follows the match rather
    than by trying to express "not followed by an asterisk" inside the
    pattern — a lookahead after a greedy group attaches to the wrong segment
    and silently matches everything, which is precisely the unanchored
    failure this file is here to avoid.
    """
    found = set()
    for match in _ADMIN_PATH.finditer(text):
        if text[match.end():match.end() + 1] == "*":
            continue
        found.add(match.group(0))
    return found


def _paths_in_cli_strings() -> set[str]:
    """Paths named by ``admin/cli.py``'s string literals, docstrings included.

    Parsed rather than grepped so that **comments are excluded**. The comment
    on ``UNCLAIMED_PASSWORD_SCREEN`` quotes the broken ``/admin/staff`` in
    order to explain it, and a plain text scan would read that explanation as
    a fresh occurrence of the bug and fail. Comments are not in the AST, so
    parsing draws the line in exactly the right place: what the module *says*
    is checked, what it says *about itself* is not.
    """
    tree = ast.parse(_CLI_SOURCE.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found |= _paths_in(node.value)
    return found


def _cli_paths_are_not_empty(paths: set[str]) -> None:
    """A guard on the collector itself.

    Without it, a rename that stopped the extraction working - a moved file, a
    pattern that matches nothing - would leave every parametrised case with an
    empty set and the suite would go green having checked nothing. One of the
    three mutants that survived on this branch was a regex matching nothing at
    all, so the collector gets an assertion of its own.
    """
    assert paths, "no /admin/... paths were extracted; the collector is broken"


# --- the paths resolve ------------------------------------------------------


@pytest.mark.asyncio
async def test_the_screen_the_cli_names_is_one_the_panel_serves(admin_client):
    """The single load-bearing case, spelled out rather than parametrised.

    ``UNCLAIMED_PASSWORD_SCREEN`` is what every recovery message interpolates,
    so if this answers 404 then every one of those messages is broken at once.
    Verified over HTTP against the deployed stack as well - `/admin/staff`
    answered 404 and `/admin/staff/list` answered 200 through nginx, signed in
    as an administrator.
    """
    response = await admin_client.get(
        UNCLAIMED_PASSWORD_SCREEN, follow_redirects=False
    )
    assert response.status_code != 404, (
        f"the recovery messages send operators to {UNCLAIMED_PASSWORD_SCREEN}, "
        "and the panel serves nothing there"
    )
    assert response.status_code == 200, (
        f"{UNCLAIMED_PASSWORD_SCREEN} did not render for an administrator"
    )


@pytest.mark.asyncio
async def test_the_screen_the_cli_names_carries_the_action_it_promises(
    admin_client,
):
    """Existing is not enough — the page has to carry the button.

    The messages do not merely name a URL, they name what to press once it
    loads: `Show the password waiting to be collected`. A route that resolves
    to a page without that action would pass the test above and still strand
    the operator, which is the same "it exists, nobody can use it" gap that
    O-9 turned out to be.
    """
    response = await admin_client.get(UNCLAIMED_PASSWORD_SCREEN)
    flat = " ".join(re.sub(r"<[^>]+>", " ", response.text).split())
    assert "Show the password waiting to be collected" in flat


@pytest.mark.asyncio
async def test_every_admin_path_the_cli_names_is_served(admin_client):
    """The whole class, not just the one that was wrong.

    ``rotate-key`` names two more screens (`/admin/staff/unclaimed-password`
    and `/admin/ip-block/block`) and nothing checked either. Walking the
    module's own strings means a path added to a message in future is covered
    the moment it is written, with no list here to remember to update.
    """
    paths = _paths_in_cli_strings()
    _cli_paths_are_not_empty(paths)

    unserved = []
    for path in sorted(paths):
        response = await admin_client.get(path, follow_redirects=False)
        if response.status_code == 404:
            unserved.append(path)
    assert not unserved, (
        f"admin/cli.py tells operators to visit {unserved}, "
        "and the panel serves nothing there"
    )


@pytest.mark.asyncio
async def test_every_admin_path_init_sh_names_is_served(admin_client):
    """``docker/init.sh``'s closing block, which is what an operator reads in
    `docker compose logs migrate` — for most deployments the *first* thing
    they read, and the only place the bootstrap passwords ever appear.

    Scanned as text: it is shell, there is no AST, and every ``/admin/`` in it
    is inside an `echo` that an operator sees.
    """
    paths = _paths_in(_INIT_SH.read_text(encoding="utf-8"))
    _cli_paths_are_not_empty(paths)

    unserved = []
    for path in sorted(paths):
        response = await admin_client.get(path, follow_redirects=False)
        if response.status_code == 404:
            unserved.append(path)
    assert not unserved, (
        f"docker/init.sh tells operators to visit {unserved}, "
        "and the panel serves nothing there"
    )


# --- the messages actually carry it -----------------------------------------
#
# The tests above prove the constant resolves. These prove the constant is what
# reaches standard output - a message that interpolated the right value into
# the wrong sentence, or stopped interpolating it at all, would pass every
# test above and still print a 404 at the operator.


def _bare_prefix_alone(text: str) -> list[str]:
    """Occurrences of ``/admin/staff`` that are *not* part of a longer path.

    ``"/admin/staff" not in out`` cannot express this - it is true of the fix
    as well, since the fix contains it. The negative lookahead is what makes
    the assertion one-sided, and it is applied to the whole output rather than
    to a sentence so that a second copy of the old wording anywhere in a
    message is caught too.
    """
    return re.findall(r"/admin/staff(?![/A-Za-z0-9_-])", text)


def test_the_bootstrap_report_names_the_working_screen(capsys):
    """No database needed: ``report_bootstrap_result`` is a pure function, and
    it is shared by the CLI subcommand and by ``admin.app``'s start-up hook, so
    one assertion covers the output of both paths."""
    report_bootstrap_result([("admin", "x" * 20), ("admin2", "y" * 20)])

    out = capsys.readouterr().out
    assert UNCLAIMED_PASSWORD_SCREEN in out
    assert not _bare_prefix_alone(out), (
        "the bootstrap report is back to naming the bare prefix, which 404s"
    )


def test_create_staff_output_names_the_working_screen(admin_app, capsys):
    """Driven through ``main()`` rather than ``cmd_create_staff``.

    The recovery sentence is printed by ``main``, not by the service function,
    so the existing ``test_cli.py`` coverage - which calls the ``cmd_*``
    functions directly - never saw a byte of it. That is how three of these
    messages drifted to a 404 with a green suite.
    """
    username = "guidance-create"
    _cleanup_staff_named(admin_app, username)
    try:
        assert main(["create-staff", username, "Guidance Create"]) in (0, None)
        out = capsys.readouterr().out
    finally:
        _cleanup_staff_named(admin_app, username)

    assert "Show the password waiting to be collected" in out
    assert UNCLAIMED_PASSWORD_SCREEN in out
    assert not _bare_prefix_alone(out)


def test_issue_password_output_names_the_working_screen(admin_app, capsys):
    """The message on the path the framing calls out by name: somebody who has
    lost a password, running the one command that mints another."""
    username = "guidance-issue"
    _cleanup_staff_named(admin_app, username)
    try:
        assert main(["create-staff", username, "Guidance Issue"]) in (0, None)
        capsys.readouterr()
        assert main(["issue-password", username]) in (0, None)
        out = capsys.readouterr().out
    finally:
        _cleanup_staff_named(admin_app, username)

    assert "Show the password waiting to be collected" in out
    assert UNCLAIMED_PASSWORD_SCREEN in out
    assert not _bare_prefix_alone(out)


def test_init_sh_closing_block_names_the_working_screen():
    """The shell copy of the same sentence. It cannot import the constant, so
    this is the join that keeps it from drifting away from one again."""
    text = _INIT_SH.read_text(encoding="utf-8")

    assert UNCLAIMED_PASSWORD_SCREEN in text
    assert not _bare_prefix_alone(text), (
        "docker/init.sh is back to naming the bare prefix, which 404s"
    )


# --- the signal init.sh reads to choose its closing message ------------------
#
# `docker/init.sh` used to close by telling every operator the bootstrap
# passwords were "printed above" - on every restart of an already-bootstrapped
# deployment, where nothing had printed. It now branches on whether
# `report_bootstrap_result` said anything, and these three tests are what hold
# that together. Any one of them alone is satisfied by a broken implementation:
# the first passes against a constant nothing emits, the second against a
# constant no script reads, and the first two together pass against a function
# that prints the marker unconditionally - which is the original defect exactly.


def test_init_sh_reads_the_marker_the_cli_prints():
    """Shell cannot import a Python constant, so this is the join."""
    assert BOOTSTRAP_CREATED_MARKER in _INIT_SH.read_text(encoding="utf-8"), (
        "docker/init.sh no longer matches the line admin/cli.py prints, so its "
        "closing message will take the wrong branch on every run"
    )


def test_the_bootstrap_report_prints_that_marker_when_it_creates(capsys):
    """The other half: a constant no code emits is a string, not a signal."""
    report_bootstrap_result([("admin", "x" * 20), ("admin2", "y" * 20)])

    assert BOOTSTRAP_CREATED_MARKER in capsys.readouterr().out


def test_the_bootstrap_report_is_silent_when_it_creates_nothing(capsys):
    """**The one that makes the other two mean anything.**

    The marker is a discriminator only if it is absent on the ordinary run.
    `kaicalc-admin bootstrap` exits 0 either way — it has to, because
    `docker/init.sh` runs under `set -e` — so its output is the only thing that
    separates "created two accounts" from "found two accounts". A
    `report_bootstrap_result` that printed this line unconditionally would
    satisfy both tests above and put init.sh back to announcing passwords that
    were never printed.
    """
    report_bootstrap_result([])

    assert capsys.readouterr().out == ""
