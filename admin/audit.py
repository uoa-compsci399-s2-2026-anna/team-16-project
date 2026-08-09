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
and redacts at every level. ``_encode``'s ``date`` branch was the one thing
this module did better, and it has been folded into ``_json_safe``.

``row_to_dict`` stays here: it has no counterpart in the repository, and its
callers are all in ``admin/``.
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
