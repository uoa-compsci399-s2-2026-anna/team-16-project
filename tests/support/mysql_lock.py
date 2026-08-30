"""An exclusive, cross-platform lock guarding the shared `kaicalc_test` MySQL
database that `tests/api`, `tests/db` and `tests/admin` all run against.

**The problem this closes.** `tests/conftest.py`'s `engine` fixture runs
`drop_all()`/`create_all()` against `kaicalc_test`, and `tests/admin`'s
fixtures commit rows against the same database and delete them by prefix in
teardown. There is no `pytest-xdist` in this project, so a single pytest
process is already serial - the risk is *two processes*: a developer starts
`pytest tests/admin` in one terminal, forgets it is running, and starts
`pytest tests/api tests/db` in another. Both connect to the same schema at
once, and nothing stops `drop_all()` in one from firing while the other is
mid-test. That has already produced eighteen phantom failures on one
occasion and a corrupted run on another - both silent, both diagnosed only
after the fact. A refusal at session start is strictly better than either.

**Why a lock file rather than a MySQL advisory lock (`GET_LOCK`).** A MySQL
lock would need a live connection to hold it, which means teaching this
module how to open one before any fixture has - and would leave nothing to
show a human *why* the session refused, beyond a MySQL error four frames
from here. A local file lock fails at the one moment that matters (before
the first test in the session runs), with a message written for the person
staring at the terminal, and doesn't care whether MySQL itself is reachable
yet.

**Why not the `filelock` package.** It happens to be present in this
environment (a transitive dependency of unrelated packages), but it is not
one of this project's own dependencies, and this project deliberately keeps
that list small with no build step. The stdlib already has what an
exclusive, non-blocking, per-platform lock needs: `msvcrt.locking` on
Windows, `fcntl.flock` on POSIX. Reaching for a third-party package here
would be adding a dependency to save about fifteen lines.

**Why the lock file lives in the OS temp directory and not the repository.**
This checkout is inside an iCloud-synced folder that has silently reverted
uncommitted edits more than once. A lock file's entire correctness depends
on the *same inode* being visible to both processes for the lifetime of the
lock; a sync client that evicts, re-fetches or rewrites the file mid-hold is
exactly the kind of interference that would make the lock lie. The resource
being protected is "the MySQL server on this host and port", not "this
particular checkout", so the lock file is named after `host:port` and kept
in `tempfile.gettempdir()`, outside any synced tree.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import IO, Optional

#: Matches `tests/conftest.py`'s `ROOT_URL` / `TEST_URL` - same server, same
#: port, so a lock file name derived from it identifies the actual shared
#: resource rather than the accident of which checkout is running.
MYSQL_HOST_PORT = "127.0.0.1-3307"

LOCK_PATH = Path(tempfile.gettempdir()) / f"kaicalc-pytest-mysql-{MYSQL_HOST_PORT}.lock"

REFUSAL_MESSAGE = (
    "\n"
    "Refusing to start: another pytest session already holds the lock on the\n"
    f"shared kaicalc_test MySQL database ({MYSQL_HOST_PORT}).\n"
    "\n"
    "tests/api, tests/db and tests/admin all run against the same database,\n"
    "and there is no pytest-xdist in this project - a single session is\n"
    "already serial, but two sessions overlapping is not, and has produced\n"
    "phantom failures and at least one corrupted run before.\n"
    "\n"
    f"Lock file: {LOCK_PATH}\n"
    "Wait for the other session to finish, then run this one again. If no\n"
    "other pytest process is actually running, the lock file is stale -\n"
    "delete it and retry."
)


class DatabaseLockHeld(RuntimeError):
    """Raised when another process already holds the MySQL test-database lock."""


def _lock_nonblocking(handle: IO) -> None:
    """Take an exclusive, non-blocking lock on `handle`, or raise.

    Raises `OSError` (Windows) or `BlockingIOError` (POSIX, a subclass of
    `OSError`) when another process already holds it - both are left to
    propagate; the caller turns that into `DatabaseLockHeld`.

    The handle is opened `"r+b"` by `acquire()` below and never written to
    here: on Windows, `msvcrt.locking()` locks a byte range that Windows
    enforces for *any* write to it from *any* handle, including one opened
    fresh by a process that already holds the lock elsewhere. A write in
    this function, on the losing side of that race, would raise
    `PermissionError` before `locking()` is even reached - a real signal
    that the lock is held, but from the wrong line, and awkward to
    distinguish from a genuine I/O fault. Locking a byte that is never
    written avoids the ambiguity entirely.
    """
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle: IO) -> None:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def acquire() -> IO:
    """Acquire the exclusive lock and return the open handle to release later.

    Raises `DatabaseLockHeld` immediately - never blocks - if another process
    already holds it. The handle must be passed to `release()` at session
    end; losing it leaks an open file descriptor for the rest of the process,
    which is harmless but untidy.
    """
    # Created once, outside the section that has to fail fast, and never
    # truncated on an ordinary acquire: `msvcrt.locking()` needs at least one
    # byte to lock, and re-opening in a truncating mode on every call would
    # itself be a write to the very byte range a concurrent holder has
    # locked - see `_lock_nonblocking`'s docstring for what that does.
    if not LOCK_PATH.exists() or LOCK_PATH.stat().st_size == 0:
        LOCK_PATH.write_bytes(b"0")

    handle = open(LOCK_PATH, "r+b")
    try:
        _lock_nonblocking(handle)
    except OSError as exc:
        try:
            handle.close()
        except OSError:  # pragma: no cover - Windows can refuse this too
            pass
        raise DatabaseLockHeld(REFUSAL_MESSAGE) from exc
    return handle


def release(handle: Optional[IO]) -> None:
    if handle is None:
        return
    try:
        _unlock(handle)
    finally:
        handle.close()
