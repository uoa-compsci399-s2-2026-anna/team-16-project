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
from admin.security import BCRYPT_MAX_BYTES, verify_password

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
        # current_username, not SESSION_KEY: these pages run before a second
        # factor exists, so the user is identified by the pending login.
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
                    target = "admin:index"
                else:
                    # Mid-onboarding: a second factor is still owed.
                    target = "admin:view-verify"
                return _redirect(request, target)

        context["error"] = problem
        return templates.TemplateResponse(
            request, "brand/change_password.html", context, status_code=400
        )


class EnrolView(BaseView):
    """STUB (Task 3) - replaced by the real MFA enrolment step (QR code,
    recovery codes)."""

    name = "Enrol"
    identity = "enrol"

    @expose("/enrol", methods=["GET"], identity="enrol")
    async def enrol(self, request: Request) -> Response:
        return Response(status_code=200)
