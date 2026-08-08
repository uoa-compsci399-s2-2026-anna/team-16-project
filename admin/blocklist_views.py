"""The blocklist screen. Contract §2.3's exception, §8.3's "Blocklist".

There is no address anywhere on this screen — that is the design, not an
oversight. ``IpBlock`` stores an HMAC (``ip_hmac``) that this module never
reads and never renders; see ``db/blocklist_models.py``'s own module
docstring and ``IpBlock.__str__`` for why. Everything below works from the
row's other columns (``reason``, ``created_by``, ``created_at``,
``expires_at``) or, for the manual-block form, from an address a staff
member has just typed and that is never stored in plain form past the
``block_ip`` call that hashes it.

Two things this file follows from ``admin/accounts_view.py``, verified
against the installed ``sqladmin`` package the same way that module's own
docstring does:

* A custom route on a ``ModelView`` — whether ``@action`` or ``@expose`` —
  is wrapped in ``login_required`` only. sqladmin never calls
  ``is_accessible`` for either kind (it calls that only for the routes it
  generates itself: list/details/create/edit/delete). Every method below
  starts with its own ``_require_admin`` check for exactly that reason —
  see ``admin/accounts_view.py``'s module docstring for the fuller account
  and the confirmation against ``sqladmin/application.py``.
* Route names need the ``admin:`` prefix for the same reason given there.

**Why ``unblock`` never calls ``db.blocklist.unblock_ip``.** That function
takes a plaintext address and re-derives its fingerprint to find the row —
exactly right for a caller who is holding an address (the API layer, or
this file's own ``block`` route, or the CLI's ``unblock`` command). This
screen's unblock action starts from a *row* a staff member already selected
on ``/admin/ip-block/list`` — the address behind it was never displayed and
is not recoverable from the fingerprint — so it deletes that row directly
by id instead. There is no address to pass ``unblock_ip`` even if this
wanted to call it.

**Auditing is explicit, not inherited.** ``AuditedModelView``'s own
before-commit listener only ever fires for a write that went through
``insert_model``/``update_model``/``delete_model`` (see
``admin/modelviews.py``'s class docstring, point 1) — neither the manual
block route nor the unblock action is any of those three, so each writes
its own ``write_audit`` entry, the same pattern
``admin/accounts_view.py``'s ``reset_mfa_action``/``deactivate_action``
use. The audited `before`/`after` payloads here are hand-built rather than
``admin.audit.row_to_dict`` — that helper serialises every mapped column,
which for this model includes ``ip_hmac``. Hand-building the payload from
the four non-primary-key, non-fingerprint columns (``reason``, ``created_by``,
``created_at``, ``expires_at``) is what keeps a 64-character HMAC — the exact
value ``IpBlock.__str__`` already refuses to render, on the reasoning that
displaying it invites someone to try to reverse it — out of `/admin/audit`
as well as out of the list page.

``ip_hmac`` is **also** in ``admin/audit.py``'s ``REDACTED_FIELDS``, so
``row_to_dict`` would redact it even if some future caller did serialise a
whole ``IpBlock`` row. That is defence in depth behind this file, not a
replacement for it: the hand-built payloads here are still what keeps the
fingerprint out of a payload in the first place. Both are worth having,
because everything standing between that fingerprint and a table every
staff member can read is otherwise three class flags on this view and two
dicts in this file.

**A re-block's audit entry, and why ``created_at``/``created_by`` can
mismatch on the list page.** ``db.blocklist.block_ip`` upserts: a second
block of the same address updates `reason`, `created_by` and `expires_at`
in place but leaves `created_at` untouched (see its own docstring). The
list page therefore can show a row whose `created_by` names the most
recent administrator to act and whose `created_at` is from a much earlier
block by someone else — a mismatched pair if the two columns are read as
"who did this, and when". Fixed here by relabelling rather than by
changing what `block_ip` writes (out of scope — `db/blocklist.py` belongs
to Task 1, and its upsert semantics are documented as deliberate):
`column_labels` below renames `created_at` to "First blocked" and
`created_by` to "Most recently blocked by", so the two columns read as
what they actually are — two independent facts, not a synchronised pair —
instead of silently implying one actor set both.
"""

from starlette.exceptions import HTTPException
from starlette.responses import RedirectResponse
from starlette.responses import Response as StarletteResponse

from sqladmin import action, expose
from sqlalchemy import select

from admin.accounts import UnknownStaffError, get_staff
from admin.audit import write_audit
from admin.auth import SESSION_KEY
from admin.models import StaffRole
from admin.modelviews import AuditedModelView
from admin.runtime import get_runtime
from db.blocklist import InvalidAddressError, block_ip, ip_fingerprint, normalise_ip
from db.blocklist_models import IpBlock


class IpBlockAdmin(AuditedModelView, model=IpBlock):
    name = "IP block"
    name_plural = "IP blocks"
    icon = "fa-solid fa-ban"
    category = "Administration"

    # A row is created by entering an address, which is a different form
    # from editing a hash nobody can read back into one - the manual-block
    # route below is its own form, not sqladmin's generic create scaffold.
    can_create = False
    # Extend or change a reason by blocking again - `block_ip` upserts on
    # the fingerprint (db/blocklist.py), so there is never a second row to
    # reconcile with a generic edit form.
    can_edit = False
    # Removal goes through the audited `unblock` action below, never
    # sqladmin's generic delete (which would bypass write_audit entirely -
    # see admin/modelviews.py's AuditedModelView docstring, point 1: a
    # delete_model call *would* be audited automatically, but can_delete is
    # kept False anyway so there is exactly one way to remove a row, not
    # two that have to be kept in sync).
    can_delete = False

    # Never ip_hmac: 64 hex characters no human can act on, and the one
    # thing every other part of this module exists to keep off the screen.
    column_list = [
        IpBlock.reason, IpBlock.created_by, IpBlock.created_at, IpBlock.expires_at,
    ]
    # column_details_list defaults to every mapped column independently of
    # column_list (admin/modelviews.py's AuditedModelView docstring, point
    # 2) - without this line, /ip-block/details/{pk} would render ip_hmac
    # in full, one click from every row this same view otherwise redacts.
    column_details_list = column_list
    # See the module docstring's last section for why these two columns are
    # relabelled rather than left reading as a matched "who and when" pair.
    column_labels = {
        IpBlock.created_by: "Most recently blocked by",
        IpBlock.created_at: "First blocked",
    }
    column_default_sort = ("created_at", True)

    # sqladmin's own list page has no built-in "New" button here -
    # can_create is False, deliberately (see above), so the generic
    # `check_can_create` block in sqladmin/list.html never renders one.
    # Without this override, "/ip-block/block" is reachable only by a staff
    # member recalling the URL from the contract document, which is a bad
    # position to be in mid-incident. This is a real sqladmin hook
    # (`list_template`, checked against sqladmin/models.py's own ModelView
    # ClassVar) rather than a workaround: the override template extends
    # sqladmin's own "sqladmin/list.html" and replaces only the
    # `model_menu_bar` block, so every other part of the page - search,
    # filters, pagination, the bulk-action dropdown - is untouched.
    list_template = "brand/ip_block_list.html"

    def is_visible(self, request) -> bool:
        return self._is_admin(request)

    def is_accessible(self, request) -> bool:
        """Only role=admin - blocking access to a public service is an
        administrator's decision, the same floor accounts_view.py's
        StaffAdmin sets for account management."""
        return self._is_admin(request)

    def _is_admin(self, request) -> bool:
        username = request.session.get(SESSION_KEY)
        if not username:
            return False
        with self.session_maker() as session:
            try:
                return get_staff(session, username).role is StaffRole.admin
            except UnknownStaffError:
                return False

    def _require_admin(self, request) -> None:
        if not self.is_accessible(request):
            raise HTTPException(status_code=403)

    def _list_url(self, request):
        return request.url_for("admin:list", identity=self.identity)

    def _selected_ids(self, request) -> list[int]:
        """Every id named in `pks`, silently dropping anything that does
        not parse as one. A hand-typed `?pks=abc` is the only way to reach
        a non-integer value here (the list page's own checkboxes only ever
        submit real row ids), and refusing outright over one bad entry in
        an otherwise-valid bulk selection would cost a staff member every
        row they meant to unblock along with the one they mistyped."""
        ids = []
        for pk in request.query_params.get("pks", "").split(","):
            pk = pk.strip()
            if not pk:
                continue
            try:
                ids.append(int(pk))
            except ValueError:
                continue
        return ids

    @action(
        name="unblock",
        label="Unblock",
        confirmation_message=(
            "This removes the block. The address becomes reachable again "
            "immediately."
        ),
    )
    async def unblock_action(self, request) -> RedirectResponse:
        self._require_admin(request)
        actor = request.session.get(SESSION_KEY, "unknown")
        with self.session_maker() as session:
            for pk in self._selected_ids(request):
                row = session.get(IpBlock, pk)
                if row is None:
                    continue
                # Snapshotted before delete: after delete there is nothing
                # left to read `reason`/`created_by`/`expires_at` off.
                # ip_hmac is deliberately absent - see the module docstring.
                before = {
                    "reason": row.reason,
                    "created_by": row.created_by,
                    "created_at": row.created_at,
                    "expires_at": row.expires_at,
                }
                row_id = row.id
                session.delete(row)
                write_audit(
                    session, actor=actor, action="delete", table_name="ip_block",
                    row_id=row_id, before=before, after=None,
                )
            session.commit()
        return RedirectResponse(self._list_url(request), status_code=302)

    @expose("/block", methods=["GET", "POST"])
    async def block_form(self, request) -> StarletteResponse:
        """Manual block: an attack in progress and no CDN or upstream
        firewall in front of this panel to stop it (see
        docs/architecture.md §9.1). GET renders the form; POST applies it.

        No CSRF token, matching every other state-changing route this panel
        already ships (admin/accounts_view.py's actions, and every
        `@action` in this project - see that module's own "Carried
        Forward" note in the task history: mutating on GET with no CSRF
        token is a pre-existing sqladmin pattern here, not something this
        route introduces).
        """
        self._require_admin(request)
        # Passed on every render of this form (GET and every rejected
        # POST) so brand/block_ip.html can offer a way back to the list -
        # action_refused.html already does the same with its own next_url.
        context = {"error": None, "list_url": self._list_url(request)}
        if request.method == "GET":
            return await self.templates.TemplateResponse(
                request, "brand/block_ip.html", context
            )

        form = await request.form()
        address = (form.get("address") or "").strip()
        reason = (form.get("reason") or "").strip()
        minutes_raw = (form.get("minutes") or "").strip()

        if not address:
            context["error"] = "Enter the address to block."
            return await self.templates.TemplateResponse(
                request, "brand/block_ip.html", context, status_code=400
            )
        # Rejected here rather than left for `block_ip` to raise. The form
        # used to accept any non-empty string, and `ip_fingerprint` used to
        # hash it verbatim - so "203.0.113.09", " 203.0.113.9", a value
        # carrying a port, or one of IPv6's several spellings of the same
        # address each produced a *different* fingerprint from the one
        # `ProtectionMiddleware` computes for the caller who actually
        # arrives. The row appeared on the list page, the audit entry was
        # written, and the block stopped nobody, with nothing failing
        # anywhere: the same silent-failure class as the two sides of the
        # HKDF derivation disagreeing. `db.blocklist.normalise_ip` now
        # canonicalises inside `ip_fingerprint`, so the only remaining case
        # is input that is not an address at all, which is this branch.
        #
        # The rejected value is deliberately *not* passed back into the
        # template. brand/block_ip.html renders `error` and nothing else from
        # the submitted form - re-populating the field would put a value a
        # staff member may have typed *thinking* it was an address (§2.3's
        # concern is exactly this) into the page's HTML, and the page is one
        # that renders inside the panel every staff member can reach.
        try:
            normalise_ip(address)
        except InvalidAddressError as exc:
            context["error"] = str(exc)
            return await self.templates.TemplateResponse(
                request, "brand/block_ip.html", context, status_code=400
            )
        if not reason:
            context["error"] = (
                "Enter a reason. This is what the screen and the audit log "
                "show in place of the address."
            )
            return await self.templates.TemplateResponse(
                request, "brand/block_ip.html", context, status_code=400
            )

        # Explicit, not left at block_ip's own default. minutes=None is
        # documented as meaning "no expiry" (db/blocklist.py) - correct
        # when a staff member deliberately leaves the field blank, but
        # only ever reached here on that deliberate choice: a blank field
        # parses to None below, a typed duration parses to that many
        # minutes, and nothing in between silently drops a duration the
        # operator actually entered.
        minutes: int | None = None
        if minutes_raw:
            try:
                minutes = int(minutes_raw)
            except ValueError:
                context["error"] = (
                    "Duration must be a whole number of minutes, or left "
                    "blank for no expiry."
                )
                return await self.templates.TemplateResponse(
                    request, "brand/block_ip.html", context, status_code=400
                )
            if minutes <= 0:
                context["error"] = (
                    "Duration must be a positive number of minutes, or "
                    "left blank for no expiry."
                )
                return await self.templates.TemplateResponse(
                    request, "brand/block_ip.html", context, status_code=400
                )

        runtime = get_runtime(request)
        secret_key = runtime.settings.secret_key
        actor = request.session.get(SESSION_KEY, "unknown")

        with self.session_maker() as session:
            fp = ip_fingerprint(address, secret_key=secret_key)
            existing = session.scalar(select(IpBlock).where(IpBlock.ip_hmac == fp))
            before = None
            if existing is not None:
                before = {
                    "reason": existing.reason,
                    "created_by": existing.created_by,
                    "created_at": existing.created_at,
                    "expires_at": existing.expires_at,
                }

            row = block_ip(
                session, address, reason=reason, actor=actor,
                secret_key=secret_key, minutes=minutes,
            )
            session.flush()

            write_audit(
                session, actor=actor,
                action="update" if before is not None else "create",
                table_name="ip_block", row_id=row.id, before=before,
                after={
                    "reason": row.reason,
                    "created_by": row.created_by,
                    "created_at": row.created_at,
                    "expires_at": row.expires_at,
                },
            )
            session.commit()

        return RedirectResponse(self._list_url(request), status_code=302)
