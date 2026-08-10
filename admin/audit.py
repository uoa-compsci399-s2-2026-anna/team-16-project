"""The single insertion point into audit_log. Contract §5.5.

``write_audit`` itself now lives in ``db/repository.py`` — where §5.5 has
always said it belongs, and where B's branch already had it. This module
re-exports it so that its existing callers (``admin/modelviews.py``,
``admin/taxonomy_views.py``, ``admin/factor_lifecycle.py``,
``admin/blocklist_views.py``) need no change, and so that the API layer and
the admin panel cannot drift into two audit formats.

The copy that gave way was this module's own ``write_audit``/``_scrub``.
``_scrub`` only walked top-level keys, so a ``mfa_secret_enc`` nested one
level down inside a payload was written verbatim into a table every staff
member can read. ``db.repository._json_safe`` recurses into dicts and lists
and redacts at every level.

**Three differences in what gets written, not just where it is written from:**

* ``_encode``'s ``date`` branch was the one thing this module did better — a
  plain ``date`` fell through ``_json_safe`` to ``deepcopy`` and left a
  non-serialisable object in the payload. Folded in, after the ``datetime``
  branch, because ``datetime`` subclasses ``date``.
* ``ip_hmac`` is added to ``REDACTED_FIELDS``, which ``db/repository.py``'s
  copy of that set did not have.
* **Timestamps change shape.** ``_scrub`` rendered a naive datetime bare
  (``2026-08-09T03:04:00``); ``_json_safe`` appends ``Z`` when ``tzinfo`` is
  None (``2026-08-09T03:04:00Z``). Every admin call site now writes the second
  form, so ``audit_log`` holds both: rows written before this change in the
  first, rows after it in the second. The new form is the more correct one —
  contract §1.3 fixes everything to UTC and ``utcnow()`` deliberately strips
  the zone, so a bare timestamp was UTC that did not say so — but anything
  that ever parses this column has to accept both.
* ``bytes`` renders as ``"[binary]"`` rather than ``value.hex()``. Every bytes
  column in the project is in ``REDACTED_FIELDS`` and never reaches that
  branch, so this is belt-and-braces either way.

``row_to_dict`` stays here: it has no counterpart in the repository, and its
callers are all in ``admin/``.

**Do not import anything from ``admin.audit`` into ``admin/models.py``,
``admin/taxonomy_models.py`` or ``admin/factor_models.py``.** The chain
``admin.audit → db.repository → db.models → admin.{models, taxonomy_models,
factor_models}`` is acyclic only because those three import nothing from here.
One such import closes the loop, and a circular import is an ImportError at
collection time — the same failure class, and the same "no test in the
repository runs" symptom, as the duplicate-table crash this integration just
cleared. The inversion is recorded in ``db/models.py``; this is the edge it
puts one line away.
"""

from typing import Any

from db.repository import (  # noqa: F401  - re-exported for admin/'s callers
    REDACTED_FIELDS,
    write_audit,
)

__all__ = ["REDACTED_FIELDS", "row_to_dict", "write_audit"]


def row_to_dict(row: Any) -> dict:
    """Every mapped column of one row, by name.

    Relationships are excluded — a relationship is other rows, not a column
    of this one. Shared by admin/modelviews.py (building an `after` snapshot
    for ordinary CRUD) and admin/factor_lifecycle.py (building one for a
    clone's own audit entry): both need the same "every column, nothing
    derived" shape, so this lives in admin/audit.py, which both already
    depend on for `write_audit`, rather than being defined twice.
    """
    return {
        column.key: getattr(row, column.key)
        for column in row.__mapper__.column_attrs
    }
