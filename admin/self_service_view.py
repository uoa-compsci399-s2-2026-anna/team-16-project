"""The signed-in account's own security screen. Contract §8.3.

**Why this page exists.** ``admin/accounts.py``'s self-recovery guard refuses
``issue_password`` and ``reset_mfa`` aimed at the account performing them:
each deliberately skips the proof the ordinary path demands — issuing a
password never asks for the current one, resetting an enrolment never asks
for the device — and applied to oneself they are the two halves of a takeover
from a stolen session. That guard removed the only way a staff member could
change their own credentials alone. This screen puts the capability back
through a path that actually proves who is asking.

**It needs no administrator role, and that is the point.** ``/admin/staff`` is
recovery layer L2, one administrator acting on another, and stays
administrator-only. Managing your own second factor is not an administrative
act and must not require finding a colleague; a `staff` member reaches this
page and still cannot reach that one.

**What the session already proves, and what it does not.** Every path here
runs behind ``AdminAuth.authenticate()``'s ordinary branch — this path is not
in ``_PRE_LOGIN_PAGES`` — which means an established ``SESSION_KEY``, a
matching ``session_generation``, ``must_change_password`` cleared and
``mfa_enrolled`` set. So the caller has, at some point, presented both
factors. That is exactly the thing a stolen session also has, which is why
every state-changing action here re-authenticates on top of it:

* changing the password requires **the current password** and nothing else.
  A code from an authenticator is not a substitute: the whole hazard is a
  session that already got past the second factor.
* adding an authenticator requires the current password **or** a code from an
  already-enrolled device — either one is a fresh proof the session cookie
  by itself does not carry.
* removing one requires the same, and the last confirmed device cannot be
  removed at all.

**One account's screen cannot act on another.** The acting account is read
from ``SESSION_KEY`` and from nowhere else; no field on any form here names a
username or an account id, and a ``device_id`` belonging to somebody else is
refused by ``admin/accounts.py::remove_totp_device``, which looks devices up
within the account rather than globally. The refusal is a property of the
service layer, not of this view — ``admin/accounts.py`` is the only module
that mutates ``staff`` rows and now the only one that mutates their devices,
which is what keeps a second caller added later from having to remember.

The change-password form does now *display* the signed-in username, in a
readonly field placed off screen so a password manager can tell that this
changes an existing credential rather than creates a new one. It carries no
``name`` attribute, so it is not submitted and cannot be aimed anywhere; the
claim above is unchanged, and it is worth keeping that way — the moment a
handler here reads a username out of a form, the paragraph stops being true.

**A note on ``is_accessible``.** ``@expose`` wraps this route in
``login_required``, which calls ``AdminAuth.authenticate()`` and *not* this
view's ``is_accessible`` — the same sqladmin 0.30 behaviour
``admin/accounts_view.py``'s docstring records for ``@action``. Nothing
security-relevant may therefore be placed in ``is_accessible`` here. It
returns True because the gate that matters is ``authenticate()``, which every
account that can see this menu entry has already passed.
"""

import time
from datetime import datetime, timezone
from pathlib import Path

from sqladmin import BaseView, expose
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.templating import Jinja2Templates

from admin.accounts import (
    DEFAULT_DEVICE_NAME,
    MAX_DEVICE_NAME_LENGTH,
    MAX_TOTP_DEVICES,
    DuplicateDeviceNameError,
    InvalidDeviceNameError,
    LastAuthenticatorError,
    MfaNotEnrolledError,
    TooManyDevicesError,
    UnknownDeviceError,
    begin_mfa_enrolment,
    complete_mfa_enrolment,
    discard_unconfirmed_devices,
    get_staff,
    last_password_change,
    remove_totp_device,
    rename_totp_device,
    resume_mfa_enrolment,
    set_password,
)
from admin.audit import write_audit
from admin.auth import SESSION_KEY, reauthenticate, stamp_session
from admin.csrf import check_token, issue_token
from admin.runtime import get_runtime
from admin.totp import TOTP_INTERVAL, qr_svg
from admin.views import MIN_PASSWORD_LENGTH, _grouped, _password_problem

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

#: ``MAX_DEVICE_NAME_LENGTH`` moved to ``admin/accounts.py`` and is imported
#: above. It is now enforced by ``rename_totp_device`` as well as rendered as
#: this page's ``maxlength``, and a limit defined in a view while being applied
#: in the service layer is two numbers waiting to disagree — MySQL in
#: non-strict mode truncates the difference silently.


def _last_used(last_counter: int | None) -> datetime | None:
    """When an authenticator last proved itself. **Derived, not stored.**

    There is no ``last_used_at`` column and this task deliberately did not add
    one. ``staff_totp_device.last_counter`` already holds the TOTP time step of
    the last code that device had accepted — it is the replay guard — and
    ``admin/totp.py::TOTP_INTERVAL`` is the width of a step, so the product is
    the instant of that acceptance **to within one step**, thirty seconds. Read
    it as "about then", never as a timestamp somebody recorded: a code accepted
    at 09:41:29 and one accepted at 09:41:01 land on the same value.

    NULL means no code from this device has ever been accepted, which is the
    ordinary state of an enrolment that was begun and never confirmed.

    Naive UTC, matching ``admin/models.py::utcnow`` and contract §1.3, so that
    every datetime this page renders is the same kind of object.
    """
    if last_counter is None:
        return None
    return datetime.fromtimestamp(
        last_counter * TOTP_INTERVAL, tz=timezone.utc
    ).replace(tzinfo=None)


class SecurityView(BaseView):
    name = "My security"
    icon = "fa-solid fa-shield-halved"

    def is_visible(self, request: Request) -> bool:
        return True

    def is_accessible(self, request: Request) -> bool:
        # See the module docstring: sqladmin never consults this for an
        # @expose route, so it carries no weight and must not be given any.
        return True

    # --- page state ---------------------------------------------------------

    def _context(self, db, username: str, **extra) -> dict:
        staff = get_staff(db, username)
        confirmed = staff.enrolled_totp_devices
        context = {
            "username": staff.username,
            "display_name": staff.display_name,
            "devices": [
                {
                    "id": d.id,
                    # The 1, 2, 3 the screen shows, assigned here rather than
                    # from a template loop index. The table and the Remove
                    # dialog's chooser are two different loops over two
                    # different lists - the second is filtered to what may be
                    # removed - so a per-loop index would number them
                    # differently the moment one device is unremovable, and
                    # the whole point of the numbering is that the two read
                    # together.
                    "number": number,
                    "name": d.name,
                    "enrolled": d.enrolled_at is not None,
                    "enrolled_at": d.enrolled_at,
                    "created_at": d.created_at,
                    # Derived from the replay counter, not stored. See
                    # _last_used: accurate to one thirty-second step.
                    "last_used": _last_used(d.last_counter),
                    # Rendered as "the only one" rather than hiding the
                    # button. A disabled control with no explanation reads
                    # as a bug; the refusal is a rule and should say so.
                    "removable": d.enrolled_at is None or len(confirmed) > 1,
                }
                for number, d in enumerate(staff.totp_devices, start=1)
            ],
            "device_count": len(staff.totp_devices),
            "max_devices": MAX_TOTP_DEVICES,
            "at_device_limit": len(staff.totp_devices) >= MAX_TOTP_DEVICES,
            "min_password_length": MIN_PASSWORD_LENGTH,
            # Passed rather than written into the template, for the same
            # reason min_password_length is: the number the field enforces
            # cannot then drift from the number the handler enforces.
            "max_device_name_length": MAX_DEVICE_NAME_LENGTH,
            # Read out of the audit trail rather than off a column - see
            # admin/accounts.py::last_password_change for why there is no
            # `password_changed_at` and what None honestly means.
            "password_changed_at": last_password_change(db, staff),
            "error": None,
            "notice": None,
            "enrolling": None,
            # Which dialog the page should open with, and nothing more than
            # that: purely presentational. A refused submission has to come
            # back inside the dialog it was made in, because a modal dialog
            # covers a message printed on the page behind it.
            "open_dialog": None,
        }
        context.update(extra)
        return context

    def _page(self, request, context, status_code=200):
        context = dict(context)
        context["csrf_token"] = issue_token(request.session)
        return templates.TemplateResponse(
            request, "brand/security.html", context, status_code=status_code
        )

    # --- re-authentication --------------------------------------------------

    def _reauthenticate(self, db, staff, form, *, runtime, now, password_only=False):
        """Delegate to ``admin/auth.py::reauthenticate``. See it for the whole
        argument — what the second proof is for, why a TOTP code is refused
        for a password change, and why failures are charged to the login
        throttle.

        **The body moved out of this class rather than being copied into the
        second caller.** ``admin/accounts_view.py``'s account-creation route
        needs the identical proof for the identical reason, and this project
        has already had the other outcome five times over in its test
        teardowns: a second copy written slightly weaker, and the weaker one
        being the one that runs. This wrapper stays so that the call sites
        below read as they always did.
        """
        return reauthenticate(
            db, staff, form, runtime=runtime, now=now, password_only=password_only
        )

    # --- the route ----------------------------------------------------------

    @expose("/security", identity="security", methods=["GET", "POST"])
    async def security(self, request: Request) -> Response:
        runtime = get_runtime(request)

        # SESSION_KEY and nothing else. current_username() would also accept
        # a pending login, which is the credential state the onboarding pages
        # run in - half a login, and not enough to be handed the account's
        # own security controls. authenticate() has already refused a bare
        # pending login on this path, so this is belt-and-braces; it is here
        # because the day someone adds "/admin/security" to _PRE_LOGIN_PAGES
        # for a plausible-sounding reason, this line is what stops the page
        # from opening on one factor.
        username = request.session.get(SESSION_KEY)
        if not username:
            return RedirectResponse(request.url_for("admin:login"), status_code=302)

        now = time.time()

        if request.method == "GET":
            with runtime.session_factory() as db:
                # **This is where an abandoned enrolment dies, and a GET is the
                # right signal for it.** The QR and the secret are rendered
                # only in the response to the re-authenticated POST that minted
                # them, so a GET of this page means the person is no longer
                # looking at either and the row can never be completed — the
                # enrol dialog's own Cancel is already a link here, so
                # cancelling destroys it with no further wiring. Leaving it
                # would leave a stored TOTP secret that has never
                # authenticated anything, occupying a name and a slot against
                # the next attempt, in a state ("Set-up not finished") that no
                # control can move it out of.
                #
                # A GET that writes is not idempotent, which is a real cost and
                # the reason to name it here rather than let it be discovered.
                # It is bounded — the second GET has nothing left to discard —
                # and the alternatives are worse: a beacon on unload is
                # unreliable, and a deadline leaves the secret sitting there
                # for the whole of it in exchange for nothing, since the row's
                # only possible future is to be destroyed.
                #
                # It cannot touch a confirmed device: discard_unconfirmed_devices
                # filters on `enrolled_at is None` and this page is unreachable
                # until the account has a confirmed one.
                discarded = discard_unconfirmed_devices(db, username)
                notice = None
                for device in discarded:
                    write_audit(
                        db, actor=username, action="delete",
                        table_name="staff_totp_device", row_id=device.id,
                        before={"username": username, "name": device.name,
                                "confirmed": False},
                        after=None,
                    )
                if discarded:
                    db.commit()
                    # Said out loud. A row vanishing between two page loads
                    # with no explanation reads as data loss, and this screen
                    # is the one place somebody is already anxious about
                    # losing a second factor.
                    names = ", ".join(f"“{d.name}”" for d in discarded)
                    notice = (
                        f"Unfinished set-up for {names} was discarded. An "
                        "authenticator that was never confirmed cannot be "
                        "picked up again — the code and the QR are shown once "
                        "— so it is cleared rather than left in the list. Your "
                        "working authenticators are untouched."
                    )
                context = self._context(db, username, notice=notice)
            return self._page(request, context)

        form = await request.form()
        with runtime.session_factory() as db:
            if not check_token(request.session, form.get("csrf_token")):
                context = self._context(
                    db, username, error="That form expired. Please try again."
                )
                return self._page(request, context, status_code=400)

            action = form.get("action") or ""
            handler = {
                "change-password": self._change_password,
                "begin-device": self._begin_device,
                "confirm-device": self._confirm_device,
                "remove-device": self._remove_device,
                "rename-device": self._rename_device,
            }.get(action)
            if handler is None:
                context = self._context(
                    db, username, error="That action is not available here."
                )
                return self._page(request, context, status_code=400)

            return handler(request, db, username, form, runtime=runtime, now=now)

    # --- actions ------------------------------------------------------------

    def _change_password(self, request, db, username, form, *, runtime, now):
        staff = get_staff(db, username)
        problem = self._reauthenticate(
            db, staff, form, runtime=runtime, now=now, password_only=True
        )
        if problem is None:
            new = form.get("password") or ""
            confirm = form.get("confirm") or ""
            # The same rules the forced-change page applies, reused rather
            # than restated: length, the bcrypt byte ceiling, the two
            # entries matching, and refusing the password already in force.
            # Two copies would drift, and the copy a given user meets
            # depends only on which page they happened to be sent to.
            problem = _password_problem(new, confirm, staff.password_hash)

        if problem is not None:
            db.rollback()
            context = self._context(
                db, username, error=problem, open_dialog="password"
            )
            return self._page(request, context, status_code=400)

        set_password(db, username, new)
        # ChangePasswordView writes no audit entry - it runs mid-onboarding,
        # before an actor exists in any meaningful sense. Here one does, and
        # a credential change that leaves no trace is the one change an
        # administrator reading the trail could not see. The plaintext and
        # the hash both stay out of it: `password_hash` is in
        # write_audit's REDACTED_FIELDS, and naming the field at all is
        # enough to say what happened.
        write_audit(
            db, actor=staff.username, action="update", table_name="staff",
            row_id=staff.id, before=None,
            after={"username": staff.username, "changed": "password",
                   "self_service": True},
        )
        db.commit()

        # set_password bumped session_generation, which just invalidated the
        # cookie this request arrived on. Re-stamp it, for the reason
        # ChangePasswordView gives: this person changed their own password
        # deliberately, so they are not the session being evicted. Every
        # *other* session naming this account is, which is the point.
        staff = get_staff(db, username)
        stamp_session(request.session, staff)

        context = self._context(
            db, username,
            notice="Password changed. Any other session signed in as this "
                   "account has been signed out.",
        )
        return self._page(request, context)

    def _begin_device(self, request, db, username, form, *, runtime, now):
        staff = get_staff(db, username)
        problem = self._reauthenticate(db, staff, form, runtime=runtime, now=now)

        device_name = (form.get("device_name") or "").strip()
        if problem is None and not device_name:
            problem = "Give the new authenticator a name, so you can tell it apart later."
        elif problem is None and len(device_name) > MAX_DEVICE_NAME_LENGTH:
            problem = (
                f"That name is too long. Use at most {MAX_DEVICE_NAME_LENGTH} "
                "characters."
            )

        if problem is None:
            # The second moment an abandoned enrolment dies, and the answer to
            # "does one interfere with starting a fresh one": it did, twice.
            # An unconfirmed row holds its name against
            # DuplicateDeviceNameError and counts toward MAX_TOTP_DEVICES, so
            # four abandoned scans left an account unable to add a real
            # authenticator at all. Reaped before the mint, so the name and
            # the slot are free for the request that is asking for them.
            #
            # Deliberately here and not inside begin_mfa_enrolment: that
            # function is shared with admin/views.py::EnrolView, where an
            # unfinished enrolment is *resumed* across GETs during onboarding
            # and re-minting invalidates the code already on somebody's phone.
            # The service layer decides what may be destroyed; this page
            # decides when. See discard_unconfirmed_devices.
            discarded = discard_unconfirmed_devices(db, username)
            for device in discarded:
                write_audit(
                    db, actor=username, action="delete",
                    table_name="staff_totp_device", row_id=device.id,
                    before={"username": username, "name": device.name,
                            "confirmed": False},
                    after=None,
                )
            try:
                secret, uri = begin_mfa_enrolment(
                    db, username,
                    secret_key=runtime.settings.secret_key,
                    issuer=runtime.settings.totp_issuer,
                    device_name=device_name,
                    # The account is already enrolled - authenticate() would
                    # not have let this request through otherwise - so
                    # without this every call here raises
                    # MfaAlreadyEnrolledError. See begin_mfa_enrolment's own
                    # docstring for why the default is the other way and why
                    # EnrolView must never pass it.
                    allow_additional=True,
                )
            except (DuplicateDeviceNameError, TooManyDevicesError) as exc:
                # NOT LastAuthenticatorError: begin_mfa_enrolment cannot
                # raise it, and catching it here would render "this is your
                # only authenticator" for an account that has too many.
                problem = str(exc)

        if problem is not None:
            db.rollback()
            context = self._context(db, username, error=problem, open_dialog="add")
            return self._page(request, context, status_code=400)

        # Committed before the QR is rendered. begin_mfa_enrolment persists
        # the encrypted secret precisely so the plaintext never has to be
        # carried between the two requests - not on the form, not in the
        # history, not in a request log - and leaving it uncommitted would
        # show a QR for a secret the confirming request never sees.
        db.commit()
        context = self._context(
            db, username,
            enrolling={
                "name": device_name,
                "qr": qr_svg(uri),
                "secret_grouped": _grouped(secret),
            },
            # `enrolling` is what renders that dialog; this only suppresses the
            # page-level banner behind it, so an error belonging to the dialog
            # is printed once, inside it, rather than also on the page the
            # modal covers. Not dead code - deleting it prints the message
            # somewhere nobody can read it.
            open_dialog="enrol",
        )
        return self._page(request, context)

    def _confirm_device(self, request, db, username, form, *, runtime, now):
        device_name = (form.get("device_name") or "").strip()
        code = (form.get("code") or "").strip()

        # No re-authentication here, deliberately, and the reason is that
        # there is nothing left for it to prove. Starting the enrolment took
        # the password or an existing code; finishing it takes a code from
        # the new device, which nobody can produce without the secret, and
        # the secret was shown exactly once - in the response to that
        # re-authenticated request. A caller who can complete this step has
        # already demonstrated more than a second password prompt would ask.
        try:
            # Always [] in practice: this page is unreachable until the
            # account is enrolled, so it is never the first device, and
            # complete_mfa_enrolment mints recovery codes for the first only.
            # Asserted rather than assumed - see the test of the same name.
            complete_mfa_enrolment(
                db, username, code,
                secret_key=runtime.settings.secret_key,
                now=int(now),
                device_name=device_name,
            )
        except MfaNotEnrolledError as exc:
            db.rollback()
            # Re-render the *same* QR rather than minting a second secret,
            # the property admin/views.py::_enrolment_view_context exists to
            # hold: a fresh secret invalidates the code already on the
            # person's phone and then blames their device clock for it.
            resumed = resume_mfa_enrolment(
                db, username,
                secret_key=runtime.settings.secret_key,
                issuer=runtime.settings.totp_issuer,
                device_name=device_name,
            )
            if resumed is None:
                context = self._context(
                    db, username,
                    error="That enrolment is no longer in progress. Start again.",
                )
                return self._page(request, context, status_code=400)
            secret, uri = resumed
            context = self._context(
                db, username,
                error=str(exc),
                enrolling={
                    "name": device_name,
                    "qr": qr_svg(uri),
                    "secret_grouped": _grouped(secret),
                },
                open_dialog="enrol",
            )
            return self._page(request, context, status_code=400)

        staff = get_staff(db, username)
        device = next(d for d in staff.totp_devices if d.name == device_name)
        write_audit(
            db, actor=staff.username, action="create",
            table_name="staff_totp_device", row_id=device.id, before=None,
            # secret_enc is never named here, and `secret_enc` is in
            # REDACTED_FIELDS besides - two independent reasons the trail
            # cannot carry it. audit_log is readable by every staff member.
            after={"username": staff.username, "name": device.name,
                   "self_service": True},
        )
        db.commit()

        context = self._context(
            db, username,
            notice=f"{device_name} is now set up. Both authenticators work; "
                   "keep the other one until you are sure.",
        )
        return self._page(request, context)

    def _rename_device(self, request, db, username, form, *, runtime, now):
        """Rename one authenticator.

        **Why the name is editable rather than replaced by the ordinal.** Every
        name on this screen ends up saying nothing — the onboarding device is
        called "Authenticator" because something had to call it something, and
        the second gets whatever its owner typed at a form that gave them no
        reason to think about it. That is not evidence the column is useless;
        it is evidence that a name nobody can revise is a name nobody invests
        in. The ordinal already exists in the ``#`` column and identifies the
        row without saying anything about the device, which is exactly what is
        missing when somebody is deciding which phone to remove.

        **Re-authenticated, like every other action here, and not only for
        consistency.** Renaming is how a stolen session gets the *owner* to
        remove the wrong device: relabel the session's own enrolment as the
        owner's old phone and the owner does the damage themselves, through the
        Remove step, which is fully proved and would therefore succeed. The
        proof is cheap and it closes that.

        No ``session_generation`` bump, and that asymmetry with
        ``_remove_device`` is the point: this changes a note, not a credential.
        """
        staff = get_staff(db, username)
        problem = self._reauthenticate(db, staff, form, runtime=runtime, now=now)

        raw = form.get("device_id") or ""
        try:
            device_id = int(raw)
        except (TypeError, ValueError):
            problem = problem or "That authenticator is not on this account."
            device_id = None

        renamed = None
        if problem is None:
            try:
                # Scoped to this account inside the service layer, which is
                # what makes another account's device id a not-found rather
                # than a rename. Nothing on this form names an account.
                device = rename_totp_device(
                    db, username, device_id, form.get("device_name") or ""
                )
                renamed = device.name
            except (
                UnknownDeviceError,
                InvalidDeviceNameError,
                DuplicateDeviceNameError,
            ) as exc:
                problem = str(exc)

        if problem is not None:
            db.rollback()
            context = self._context(db, username, error=problem, open_dialog="rename")
            return self._page(request, context, status_code=400)

        write_audit(
            db, actor=staff.username, action="update",
            table_name="staff_totp_device", row_id=device_id,
            before=None,
            after={"username": staff.username, "name": renamed,
                   "changed": "name", "self_service": True},
        )
        db.commit()

        context = self._context(
            db, username,
            notice=f"Renamed to “{renamed}”. This changes the name here only — "
                   "your authenticator app still shows the name it was given "
                   "when you scanned the code, and cannot be relabelled from "
                   "this screen.",
        )
        return self._page(request, context)

    def _remove_device(self, request, db, username, form, *, runtime, now):
        staff = get_staff(db, username)
        problem = self._reauthenticate(db, staff, form, runtime=runtime, now=now)

        raw = form.get("device_id") or ""
        try:
            device_id = int(raw)
        except (TypeError, ValueError):
            problem = problem or "That authenticator is not on this account."
            device_id = None

        removed_name = None
        if problem is None:
            try:
                # Scoped to this account inside the service layer, which is
                # what makes another account's device id a not-found rather
                # than a deletion. Nothing on this form names an account.
                device = remove_totp_device(db, username, device_id)
                removed_name = device.name
                removed_id = device.id
            except (UnknownDeviceError, LastAuthenticatorError) as exc:
                problem = str(exc)

        if problem is not None:
            db.rollback()
            context = self._context(db, username, error=problem, open_dialog="remove")
            return self._page(request, context, status_code=400)

        # The eviction (session_generation) is bumped by remove_totp_device
        # itself, not here - see its docstring. This view is one caller of
        # two and a half: admin/cli.py already reaches every other account
        # mutation, and eviction living in a view would mean a device removed
        # from the server did not end the sessions, on precisely the path
        # where the reason for removing it is that the phone is in somebody
        # else's hands.
        write_audit(
            db, actor=staff.username, action="delete",
            table_name="staff_totp_device", row_id=removed_id,
            before={"username": staff.username, "name": removed_name},
            after=None,
        )
        db.commit()

        staff = get_staff(db, username)
        # Same re-stamp, same reason, as the password change above: the
        # person doing this is not the session being evicted.
        stamp_session(request.session, staff)

        context = self._context(
            db, username,
            notice=f"{removed_name} has been removed. Any other session "
                   "signed in as this account has been signed out.",
        )
        return self._page(request, context)
