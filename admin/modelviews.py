"""sqladmin views that write an audit entry for every change. Contract §8.1.

The eleven taxonomy and factor views of §8.1 inherit AuditedModelView in E-4
and E-5, so the auditing lives here once rather than in each of them.

Transaction-boundary note (see the report for the full account): sqladmin's
own ``Query.insert``/``update``/``delete`` (sqladmin/_queries.py) open their
own session via ``self.session_maker(...)`` and commit it *before* control
returns to ``insert_model``/``update_model``/``delete_model`` here. That
commit has already happened by the time this class's overrides run, so the
audit entry written below is unavoidably a second, later transaction against
a fresh session — the row change and its audit entry cannot be made to share
one atomic commit while going through these three hooks. A process that dies
between the two leaves the row change persisted with no audit entry for it.
Closing that gap fully would mean moving the audit write into sqladmin's
``on_model_change``/``after_model_change`` hooks and reusing sqladmin's own
session object, but ``on_model_change`` runs before attributes are copied
onto the model (no "after" state yet) and ``after_model_change`` runs after
sqladmin's own commit (same problem, one step later) — so even that path
only narrows the window, it does not close it in the general case.
"""

from typing import Any

from sqladmin import ModelView
from sqladmin.filters import OperationColumnFilter

from admin.audit import write_audit
from admin.auth import SESSION_KEY
from admin.models import AuditLog


def _row_to_dict(row: Any) -> dict:
    """Every mapped column of one row, by name.

    Relationships are excluded: they are other rows, and each of those has
    its own audit entry when it changes.
    """
    return {
        column.key: getattr(row, column.key)
        for column in row.__mapper__.column_attrs
    }


class AuditedModelView(ModelView):
    """Base for every CRUD view. Contract §8.1.

    The pre-change row is read in the before-write hook. sqladmin's
    after-write hook receives the already-mutated object, so reading there
    would record the new values as both "before" and "after" — an audit trail
    in which no update ever changed anything.
    """

    #: Overridable for a view whose model name differs from the audited table.
    audit_table_name: str | None = None

    def _table_name(self) -> str:
        return self.audit_table_name or self.model.__tablename__

    def _actor(self, request) -> str:
        """The acting username, from the session only.

        Never from the submitted form: an actor a client can set is not an
        audit trail. AuthenticationBackend has already refused the request if
        this is absent, so the fallback is a defensive marker, not a path
        that runs in production.
        """
        return request.session.get(SESSION_KEY) or "unknown"

    async def insert_model(self, request, data: dict):
        model = await super().insert_model(request, data)
        with self.session_maker() as session:
            write_audit(
                session, actor=self._actor(request), action="create",
                table_name=self._table_name(), row_id=getattr(model, "id", None),
                before=None, after=_row_to_dict(model),
            )
            session.commit()
        return model

    async def update_model(self, request, pk: str, data: dict):
        with self.session_maker() as session:
            existing = session.get(self.model, pk)
            before = _row_to_dict(existing) if existing else None

        model = await super().update_model(request, pk, data)

        with self.session_maker() as session:
            write_audit(
                session, actor=self._actor(request), action="update",
                table_name=self._table_name(), row_id=getattr(model, "id", None),
                before=before, after=_row_to_dict(model),
            )
            session.commit()
        return model

    async def delete_model(self, request, pk: str):
        with self.session_maker() as session:
            existing = session.get(self.model, pk)
            before = _row_to_dict(existing) if existing else None
            row_id = getattr(existing, "id", None) if existing else None

        result = await super().delete_model(request, pk)

        with self.session_maker() as session:
            write_audit(
                session, actor=self._actor(request), action="delete",
                table_name=self._table_name(), row_id=row_id,
                before=before, after=None,
            )
            session.commit()
        return result


class AuditLogAdmin(ModelView, model=AuditLog):
    """Contract §8.2: read-only, filterable by actor, time and table."""

    name = "Audit entry"
    name_plural = "Audit log"
    icon = "fa-solid fa-clipboard-list"
    category = "Administration"

    # Contract §8.1 hard-codes all three. The audit trail is the record of
    # who changed what; a trail that can be edited records nothing.
    can_create = False
    can_edit = False
    can_delete = False
    can_export = True

    column_list = [
        AuditLog.at, AuditLog.actor, AuditLog.action,
        AuditLog.table_name, AuditLog.row_id,
    ]
    column_default_sort = ("at", True)
    column_searchable_list = [AuditLog.actor, AuditLog.table_name]
    # sqladmin 0.30 requires Filter instances here, not raw mapped columns -
    # the brief's literal `[AuditLog.actor, ...]` raises AttributeError
    # ("... has no attribute 'parameter_name'") the first time /list is
    # rendered, since get_filters() returns column_filters unchanged and the
    # template reads .parameter_name straight off each entry.
    # OperationColumnFilter supplies contains/equals/starts-with for the three
    # string columns and equals/greater-than/less-than for the datetime one.
    column_filters = [
        OperationColumnFilter(AuditLog.actor),
        OperationColumnFilter(AuditLog.action),
        OperationColumnFilter(AuditLog.table_name),
        OperationColumnFilter(AuditLog.at),
    ]
    page_size = 50
