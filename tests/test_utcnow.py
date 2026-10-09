"""``utcnow()`` — three copies of one helper, and the microseconds it drops.

**Why this file exists.** Every ``DATETIME`` column in this schema carries zero
digits of fractional-seconds precision — ``tests/test_migrations.py`` asserts
that for ``submission``'s period columns and says why: "The wire drops
microseconds before the value is written (``api.schemas.PricingOptions``)
precisely because the column does not keep them."

``api/schemas.py`` does exactly that on the request path, and its comment is the
whole argument in three lines: a value carrying microseconds "would be a value
that changes when it is stored — and the download prints what was stored.
Dropped here, where the caller can be told it happened, rather than in MySQL,
where nobody is."

``utcnow()`` is the *other* source of timestamps — ``published_at``,
``created_at``, ``enrolled_at``, ``last_login_at``, the blocklist's
``created_at`` — and until 2026-10-07 it did not drop them. MySQL does not
truncate when it stores a ``DATETIME(0)``; it **rounds**. Measured against the
running MySQL 8::

    CAST('2026-10-06 22:26:59.700000' AS DATETIME)  ->  2026-10-06 22:27:00

So a Python value and its stored counterpart could differ by a whole displayed
minute, and did: ``tests/admin/test_factor_set_actions.py``'s
``test_the_list_page_shows_the_published_set`` writes ``utcnow()``, commits, and
asserts the panel prints ``published_at.strftime('%d %b %Y, %H:%M')``. On
2026-10-06 the write landed in the last half-second of a minute, MySQL rounded
it up, the page rendered ``22:27`` and the test expected ``22:26``. It is a
0.5-in-60 window — about one run in a hundred and twenty — which is exactly
often enough to be waved away as a flake and never fixed.

**And there are three copies of the helper, not two.** ``db/`` may not import
``admin/`` (the layering note in ``CLAUDE.md``), so ``admin/models.py``,
``db/models.py`` and ``db/blocklist_models.py`` each define their own. The note
in ``db/blocklist_models.py`` said "Change both together" and was itself
counting wrong. A duplicate nobody can import away needs a test that notices
when one of them is missed.
"""

from __future__ import annotations

import pathlib
import re
from datetime import datetime, timezone

from admin.models import utcnow as admin_utcnow
from db.blocklist_models import utcnow as blocklist_utcnow
from db.models import utcnow as db_utcnow

REPO = pathlib.Path(__file__).resolve().parents[1]

#: The three that exist on purpose. A fourth is not forbidden — the layering rule
#: could produce one — but it has to be added here, which is the point.
DECLARED_COPIES = (
    "admin/models.py",
    "db/models.py",
    "db/blocklist_models.py",
)

ALL_THREE = (
    ("admin.models", admin_utcnow),
    ("db.models", db_utcnow),
    ("db.blocklist_models", blocklist_utcnow),
)


def test_all_three_utcnows_agree():
    """Naive, UTC, and to the second — from every copy.

    Asserted on all three rather than on one, because the thing most likely to
    go wrong with a deliberate duplicate is that only one of them is edited.
    """
    for name, now in ALL_THREE:
        value = now()
        assert value.tzinfo is None, (
            f"{name}.utcnow returned an aware datetime ({value!r}). MySQL "
            f"DATETIME stores no zone and §1.3 fixes everything to UTC; mixing "
            f"aware and naive values in one column is a comparison bug waiting "
            f"to happen."
        )
        assert value.microsecond == 0, (
            f"{name}.utcnow returned {value!r}, which carries microseconds. "
            f"Every DATETIME column here has zero fractional-seconds "
            f"precision and MySQL ROUNDS rather than truncates - measured, "
            f"CAST('2026-10-06 22:26:59.700000' AS DATETIME) is 22:27:00 - so "
            f"this value can be a whole displayed minute away from the one the "
            f"database holds. That is how "
            f"test_the_list_page_shows_the_published_set failed in CI on "
            f"2026-10-06, roughly a one-in-a-hundred-and-twenty window."
        )

    reference = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    for name, now in ALL_THREE:
        drift = abs((now() - reference).total_seconds())
        assert drift <= 2, (
            f"{name}.utcnow is {drift}s from the clock, so it is no longer "
            f"reading the current time in UTC"
        )


def test_there_are_exactly_three_copies_and_they_are_the_declared_ones():
    """A fourth copy has to be declared here, or this fails naming it.

    `db/` may not import `admin/`, so the duplication cannot be removed and a
    test is the only thing that can keep the copies in step. The previous note
    in `db/blocklist_models.py` said "Change both together" while three
    existed, which is the failure this guards: not a wrong edit, a wrong count.

    Scans the four source packages rather than the whole tree, so a helper of
    the same name inside `tests/` or a one-off script is not swept in.
    """
    found = sorted(
        path.relative_to(REPO).as_posix()
        for package in ("admin", "api", "db", "engine")
        for path in (REPO / package).rglob("*.py")
        if re.search(r"(?m)^def utcnow\(", path.read_text(encoding="utf-8"))
    )
    assert found == sorted(DECLARED_COPIES), (
        f"the copies of `utcnow()` are {found}, not the {len(DECLARED_COPIES)} "
        f"declared in this file ({sorted(DECLARED_COPIES)}). A new one must be "
        f"added to DECLARED_COPIES and must drop microseconds like the others; "
        f"a deleted one means the duplication was finally removed and this "
        f"test should shrink with it."
    )
