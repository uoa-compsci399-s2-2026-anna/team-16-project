"""The onboarding page views.

Each is a sqladmin BaseView. Note that @expose wraps every route in
login_required, so these pages sit behind AdminAuth.authenticate() - which is
what admits a pending login to /admin/verify and redirects an account with
onboarding outstanding to the other two.

Route names are admin:view-{identity}, not admin:{identity}; sqladmin builds
them as f"view-{view.identity}".
"""

import time
from pathlib import Path

from sqladmin import BaseView, expose
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.templating import Jinja2Templates

from admin.accounts import (
    MfaAlreadyEnrolledError,
    MfaNotEnrolledError,
    RECOVERY_CODE_COUNT,
    begin_mfa_enrolment,
    complete_mfa_enrolment,
    get_staff,
    set_password,
    unused_recovery_code_count,
)
from admin.auth import (
    SESSION_KEY,
    authenticate_recovery_code,
    authenticate_totp,
)
from admin.backend import (
    PENDING_SESSION_KEY,
    _pending_login_from_session,
    current_username,
)
from admin.csrf import check_token, issue_token
from admin.runtime import get_runtime
from admin.security import BCRYPT_MAX_BYTES, decrypt_totp_secret, verify_password
from admin.totp import provisioning_uri, qr_svg

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

#: Contract 8.3 sets no policy. Twelve is comfortably above the eight NIST
#: treats as a floor, and well under bcrypt's 72-byte ceiling.
MIN_PASSWORD_LENGTH = 12

#: Contract 8.3: "The panel prompts for regeneration once 2 codes remain."
LOW_RECOVERY_CODE_THRESHOLD = 2


def _redirect(request: Request, name: str) -> RedirectResponse:
    return RedirectResponse(request.url_for(name), status_code=302)


def _looks_like_a_totp_code(raw: str) -> bool:
    """Shape test used to route a submission to exactly one of
    authenticate_totp / authenticate_recovery_code.

    A TOTP code is exactly six digits; a recovery code is twelve characters
    (three groups of four, dashes stripped) from a 31-character alphabet.
    The two shapes cannot collide - a recovery code's normalised length is
    always twelve, never six, regardless of which characters happen to land
    in it - so trying one and falling through to the other on failure is
    unnecessary, and each spurious fall-through cost the shared login
    throttle a second recorded failure for a single wrong submission.
    """
    candidate = raw.replace(" ", "").replace("-", "")
    return len(candidate) == 6 and candidate.isdigit()


class VerifyView(BaseView):
    name = "Verification"

    def is_visible(self, request: Request) -> bool:
        return False  # a step in the login flow, not a destination

    def is_accessible(self, request: Request) -> bool:
        return True

    @expose("/verify", identity="verify", methods=["GET", "POST"])
    async def verify(self, request: Request) -> Response:
        runtime = get_runtime(request)

        # Already logged in. authenticate() admits such a request because its
        # job is reachability, not whether the page still has anything to do
        # for this caller - that judgement belongs here. There is no second
        # factor left to supply, so send them on rather than presenting a form
        # that cannot be completed: authenticate_totp needs a PendingLogin and
        # cannot be handed a bare username.
        if request.session.get(SESSION_KEY):
            return _redirect(request, "admin:index")

        # Reuse Task 4's parser rather than rebuilding the value here. It
        # validates the shape explicitly, because PendingLogin is a plain
        # frozen dataclass with no field validation and the session, though
        # signed, is client-held - a malformed expires_at would otherwise
        # reach is_expired()'s comparison and raise a 500 out of the page.
        pending = _pending_login_from_session(request.session)
        if pending is None:
            return _redirect(request, "admin:login")

        now = time.time()
        if pending.is_expired(now=now):
            request.session.pop(PENDING_SESSION_KEY, None)
            return _redirect(request, "admin:login")

        context = {"csrf_token": issue_token(request.session), "error": None}
        if request.method == "GET":
            return templates.TemplateResponse(request, "brand/verify.html", context)

        form = await request.form()
        if not check_token(request.session, form.get("csrf_token")):
            context["error"] = "That form expired. Please try again."
            return templates.TemplateResponse(
                request, "brand/verify.html", context, status_code=400
            )

        code = (form.get("code") or "").strip()
        with runtime.session_factory() as db:
            # The same field takes either a TOTP code or a recovery code -
            # someone who has lost their authenticator arrives here with no
            # other way in, and sending them to a separate page would mean
            # finding it first. Route on shape rather than trying one and
            # falling through to the other on failure: both calls share the
            # login throttle counter, so a fall-through on a genuinely wrong
            # submission recorded two failures instead of one, locking an
            # account out at roughly half the configured allowance.
            if _looks_like_a_totp_code(code):
                username = authenticate_totp(
                    db,
                    pending,
                    code,
                    throttle=runtime.throttle,
                    secret_key=runtime.settings.secret_key,
                    now=now,
                )
            else:
                username = authenticate_recovery_code(
                    db, pending, code, throttle=runtime.throttle, now=now
                )
            # sqladmin.Admin.__init__ calls
            # self.session_maker.configure(autoflush=False, autocommit=False)
            # on construction (sqladmin/application.py) - and runtime.session_factory
            # is that exact sessionmaker, shared app-wide, so autoflush is off for
            # every session this view opens. Without an explicit flush here,
            # unused_recovery_code_count's own SELECT does not see the used_at
            # written by consume_recovery_code moments earlier in this same
            # transaction, and reports one too many codes remaining - which
            # would silently defeat the low-recovery-code interstitial on
            # exactly the boundary case it exists to catch.
            if username is not None:
                db.flush()
            remaining = unused_recovery_code_count(db, username) if username else 0
            db.commit()

        if username is None:
            context["error"] = "That code was not accepted."
            return templates.TemplateResponse(
                request, "brand/verify.html", context, status_code=400
            )

        request.session.pop(PENDING_SESSION_KEY, None)
        request.session[SESSION_KEY] = username

        if remaining <= LOW_RECOVERY_CODE_THRESHOLD:
            return templates.TemplateResponse(
                request,
                "brand/low_codes.html",
                {"remaining": remaining},
            )
        return _redirect(request, "admin:index")


def _password_problem(new: str, confirm: str, current_hash: str) -> str | None:
    """Return a message to display, or None when the password is acceptable."""
    if len(new) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters."
    if len(new.encode("utf-8")) > BCRYPT_MAX_BYTES:
        return (
            f"That password is too long. The limit is {BCRYPT_MAX_BYTES} bytes, "
            "and accented or non-Latin characters count for more than one."
        )
    if new != confirm:
        return "The two entries did not match."
    if verify_password(new, current_hash):
        # The issued password travelled out of band - spoken, written down,
        # possibly still in a chat log. A change that keeps it retires nothing.
        return "Choose a password you have not used here before."
    return None


class ChangePasswordView(BaseView):
    name = "Change password"

    def is_visible(self, request: Request) -> bool:
        return False

    def is_accessible(self, request: Request) -> bool:
        return True

    @expose("/change-password", identity="change-password", methods=["GET", "POST"])
    async def change_password(self, request: Request) -> Response:
        runtime = get_runtime(request)
        # current_username, not SESSION_KEY, and its own docstring states the
        # rule correctly: this is "the account a request is acting on, before
        # or after the second factor". Usually there is no second factor yet
        # and the pending login is what identifies the user - but an
        # administrator can force an already-logged-in account back through
        # this page, and current_username prefers the established SESSION_KEY
        # precisely for that case. Saying these pages "run before a second
        # factor exists" would describe only the common half and contradict
        # the branch below.
        username = current_username(request.session)
        if not username:
            return _redirect(request, "admin:login")

        context = {"csrf_token": issue_token(request.session), "error": None}
        if request.method == "GET":
            return templates.TemplateResponse(
                request, "brand/change_password.html", context
            )

        form = await request.form()
        if not check_token(request.session, form.get("csrf_token")):
            context["error"] = "That form expired. Please try again."
            return templates.TemplateResponse(
                request, "brand/change_password.html", context, status_code=400
            )

        new = form.get("password") or ""
        confirm = form.get("confirm") or ""

        with runtime.session_factory() as db:
            staff = get_staff(db, username)
            problem = _password_problem(new, confirm, staff.password_hash)
            if problem is None:
                set_password(db, username, new)
                enrolled = staff.mfa_enrolled
                db.commit()
                if not enrolled:
                    target = "admin:view-enrol"
                elif request.session.get(SESSION_KEY):
                    # Already logged in and just changing a password.
                    #
                    # Kept deliberately, unlike EnrolView's mirror image of
                    # it, which was removed. Nothing can reach this today:
                    # the gate opens this page only while
                    # must_change_password is set, and once an account holds
                    # a SESSION_KEY the only writer of that flag back to True
                    # is create_staff, which by definition has already run.
                    # But this is unreachable for want of a *caller*, not
                    # because the security model forbids the state - contract
                    # 8.3's Known limitation names the missing caller
                    # outright ("admin/accounts.py needs an issue_password()
                    # that sets a random password **and**
                    # must_change_password = True"), so this branch is what
                    # that function will land on. EnrolView's branch was the
                    # opposite case: there the state itself is forbidden, and
                    # supporting it was the risk.
                    target = "admin:index"
                else:
                    # Mid-onboarding: a second factor is still owed.
                    target = "admin:view-verify"
                return _redirect(request, target)

        context["error"] = problem
        return templates.TemplateResponse(
            request, "brand/change_password.html", context, status_code=400
        )


def _enrolment_view_context(db, username: str, secret_key: str) -> dict | None:
    """The QR and secret this page should show, for every one of its paths.

    Every branch of EnrolView goes through here - the initial GET, a
    rejected code, a CSRF refusal - because the property they all need is
    the same one: **an unfinished enrolment is reused, never re-minted.**
    Minting a second secret invalidates the QR the user already scanned on
    their phone, and complete_mfa_enrolment then rejects their perfectly
    good code with "Check the authenticator has the right account and that
    the device clock is correct", which sends them to look for a fault on
    their own device. A refresh, a second tab, or Back/Forward after
    scanning is enough to trigger it, so this is the ordinary case and not
    an edge one.

    Only a genuinely absent secret mints. reset_mfa is what clears
    mfa_secret_enc, so "start over with a fresh secret" remains available
    and remains an administrator action, which is where contract 8.3 puts
    it. An abandoned enrolment resumed later resumes on its original
    secret; nothing has seen that secret but the account holder.

    Minting here rather than only in the GET handler is also what keeps a
    POST with no prior GET off the 500 path: nothing enforces the browser's
    GET-then-POST order, and curl, a scripted login, a scanner, or a
    replayed request can all arrive with mfa_secret_enc still NULL. Handing
    decrypt_totp_secret a None raises TypeError, an unhandled 500 on the
    page that fronts the takeover guard.

    Returns None when the account is already fully enrolled, which the
    caller turns into a redirect. That is E-1's account-takeover guard
    surfacing at the page: contract 8.3 puts enrolment behind the password
    step alone, so an attacker holding only the password would otherwise
    scan their own QR and hold both factors. begin_mfa_enrolment refuses it
    too (MfaAlreadyEnrolledError, caught below), but only when the secret is
    NULL - a finished enrolment keeps its secret, so without the explicit
    check the decrypt path below would happily re-display it. Both are kept:
    the check covers the ordinary case, the except covers a concurrent
    request completing enrolment between the two statements.
    """
    staff = get_staff(db, username)
    if staff.mfa_enrolled:
        return None
    if staff.mfa_secret_enc is None:
        try:
            secret, uri = begin_mfa_enrolment(db, username, secret_key=secret_key)
        except MfaAlreadyEnrolledError:
            return None
        return {"qr": qr_svg(uri), "secret": secret}
    secret = decrypt_totp_secret(staff.mfa_secret_enc, secret_key=secret_key)
    return {
        "qr": qr_svg(provisioning_uri(secret, username=staff.username)),
        "secret": secret,
    }


class EnrolView(BaseView):
    name = "Set up authenticator"

    def is_visible(self, request: Request) -> bool:
        return False

    def is_accessible(self, request: Request) -> bool:
        return True

    @expose("/enrol", identity="enrol", methods=["GET", "POST"])
    async def enrol(self, request: Request) -> Response:
        runtime = get_runtime(request)

        # Refused outright, mirroring VerifyView's own first statement, and
        # for a sharper reason than VerifyView's. AdminAuth's gate
        # (admin/backend.py _may_open_pre_login_page) already makes an
        # established SESSION_KEY unreachable on this path by construction -
        # that is Task 4 round 5's fix, and it is the whole of what stands
        # between an administrator's mid-session MFA reset and the evicted
        # party re-enrolling their own authenticator under the very cookie
        # the reset was meant to neutralise. Holding that property in the
        # gate alone leaves it one relaxation away from being lost, and
        # "bounced to /admin/login" is the confusing part of the flow and so
        # the part most likely to be relaxed. Enrolment is not something an
        # established session can ever legitimately begin: a re-enrolment
        # always arrives through a fresh password step, because the
        # administrator has just issued a new password to the rightful
        # holder.
        if request.session.get(SESSION_KEY):
            return _redirect(request, "admin:index")

        # current_username, not SESSION_KEY, for the same reason
        # current_username's own docstring gives: authenticate() has already
        # decided reachability, and this only extracts *which* account. On
        # this page that is always the pending login, given the refusal
        # above.
        username = current_username(request.session)
        if not username:
            return _redirect(request, "admin:login")

        secret_key = runtime.settings.secret_key

        if request.method == "GET":
            with runtime.session_factory() as db:
                # The same helper the POST paths use, so a refresh, a second
                # tab, or Back/Forward after scanning reuses the secret the
                # user already has on their phone rather than silently
                # invalidating it. Only a genuinely absent secret mints one.
                context = _enrolment_view_context(db, username, secret_key)
                # Commits whether or not a secret was minted: a no-op commit
                # costs nothing, and leaving a freshly minted secret
                # uncommitted would show a QR the next request never sees.
                db.commit()
            if context is None:
                # Contract 8.3 / E-1's takeover guard: an enrolled account
                # re-enrols only through an administrator reset. Offering it
                # here would let anyone holding the password swap in their
                # own authenticator.
                return _redirect(request, "admin:index")
            return templates.TemplateResponse(
                request,
                "brand/enrol.html",
                context | {"csrf_token": issue_token(request.session), "error": None},
            )

        form = await request.form()
        with runtime.session_factory() as db:
            if not check_token(request.session, form.get("csrf_token")):
                context = _enrolment_view_context(db, username, secret_key)
                if context is None:
                    # The race _enrolment_view_context's own docstring
                    # describes: a concurrent request finished enrolment
                    # between the gate's check and here. Same refusal as the
                    # GET handler's except MfaAlreadyEnrolledError.
                    db.commit()
                    return _redirect(request, "admin:index")
                # Commits whether or not _enrolment_view_context minted a
                # fresh secret (a POST with no prior GET); a no-op commit
                # when it only decrypted an existing one is harmless, and
                # leaving a freshly minted secret uncommitted would show the
                # user a QR for a secret the next request's session never
                # sees, making the code they scan unusable.
                db.commit()
                context |= {
                    "csrf_token": issue_token(request.session),
                    "error": "That form expired. Please try again.",
                }
                return templates.TemplateResponse(
                    request, "brand/enrol.html", context, status_code=400
                )

            code = (form.get("code") or "").strip()
            try:
                codes = complete_mfa_enrolment(
                    db,
                    username,
                    code,
                    secret_key=secret_key,
                    now=int(time.time()),
                )
            except MfaNotEnrolledError as exc:
                db.rollback()
                context = _enrolment_view_context(db, username, secret_key)
                if context is None:
                    db.commit()
                    return _redirect(request, "admin:index")
                db.commit()
                context |= {
                    "csrf_token": issue_token(request.session),
                    "error": str(exc),
                }
                return templates.TemplateResponse(
                    request, "brand/enrol.html", context, status_code=400
                )
            db.commit()

        # Always the second factor: an account that has just enrolled has by
        # definition not yet supplied one, and the SESSION_KEY refusal at the
        # top of this handler means no established session ever reaches here.
        # This used to branch on SESSION_KEY and send an established session
        # to the index instead - a branch that encoded the negation of the
        # gate's rule, and would have quietly restored the takeover path's
        # happy ending had that rule ever been relaxed.
        return templates.TemplateResponse(
            request,
            "brand/enrol_done.html",
            {"codes": codes, "next_url": "/admin/verify"},
        )
