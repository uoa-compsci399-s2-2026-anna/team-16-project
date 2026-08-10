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
    resume_mfa_enrolment,
    set_password,
    unused_recovery_code_count,
)
from admin.auth import (
    PENDING_LOGIN_TTL_SECONDS,
    SESSION_KEY,
    authenticate_recovery_code,
    authenticate_totp,
    stamp_session,
)
from admin.backend import (
    PENDING_SESSION_KEY,
    _pending_login_from_session,
    current_username,
)
from admin.csrf import check_token, issue_token
from admin.models import utcnow
from admin.runtime import get_runtime
from admin.security import BCRYPT_MAX_BYTES, verify_password
from admin.totp import qr_svg

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

#: Contract 8.3 sets no policy. This was twelve and is now eight, at the
#: repository owner's decision, taken with the argument against in front of
#: them. What was weighed, recorded here so the next person to ask "why
#: eight?" reads it rather than re-deriving it:
#:
#: Against a lower floor - this panel edits the formulas a public calculator
#: quotes and reads submission records, and there is no email recovery, so a
#: compromised administrator is recovered only by another administrator or by
#: shell access. Part of why twelve cost nothing to hold is that
#: ``admin/accounts.py`` issues 20-character random passwords, and the floor
#: only ever bites on the one a person then chooses for themselves.
#:
#: For it - this is a staff panel behind TOTP MFA and the login throttle in
#: ``admin/throttle.py``, not a public signup, and a floor people work around
#: (a twelfth character appended to an eight-character password) helps nobody.
#: Eight is also what NIST SP 800-63B sets as the floor for a memorised
#: secret. Well under bcrypt's 72-byte ceiling either way.
MIN_PASSWORD_LENGTH = 8

#: Contract 8.3: "The panel prompts for regeneration once 2 codes remain."
LOW_RECOVERY_CODE_THRESHOLD = 2


def _redirect(request: Request, name: str) -> RedirectResponse:
    return RedirectResponse(request.url_for(name), status_code=302)


def _grouped(secret: str, size: int = 4) -> str:
    """Break a base32 secret into groups for transcription.

    An unbroken 32-character string is read off a screen and typed into a
    phone; groups of four are how authenticator apps present setup keys, and
    how the recovery codes on the next page are already formatted. Display
    only - the value scanned or posted is unchanged.
    """
    return " ".join(secret[i : i + size] for i in range(0, len(secret), size))


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
            # Fetched here, inside the still-open session, so stamp_session
            # below has a Staff whose session_generation was just read from
            # the row rather than trusted from the stale pending value.
            staff = get_staff(db, username) if username is not None else None
            db.commit()

        if username is None:
            # Deliberately covers both causes without naming the device
            # clock. A code that is correct but already spent is refused by
            # the replay counter, and telling that user to check their clock
            # sends them to fix something that is not broken.
            context["error"] = (
                "That code was not accepted. If you have just used this code, "
                "wait for your authenticator to show the next one - each code "
                "works only once."
            )
            return templates.TemplateResponse(
                request, "brand/verify.html", context, status_code=400
            )

        request.session.pop(PENDING_SESSION_KEY, None)
        stamp_session(request.session, staff)

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

        # min_password_length is rendered as the page's own guidance rather
        # than left to surface only in a refusal - a person should not have to
        # be told no to learn the rule. Passed, never written into the
        # template, so the number on screen cannot drift from the number
        # enforced. `username` is here for the browser's password manager,
        # not for this view: see change_password.html.
        context = {
            "csrf_token": issue_token(request.session),
            "error": None,
            "min_password_length": MIN_PASSWORD_LENGTH,
            "username": username,
        }
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

                # Refresh the handshake now that a step is behind them.
                # Choosing a password and then scanning a QR routinely
                # outlasts the five-minute pending login, and its expiry
                # lands the user back at /admin/login holding a correct new
                # password and a working authenticator, with nothing on
                # screen explaining why. Refreshed on a *completed step*
                # rather than on every page view: a pending login is a
                # one-factor credential, so idling still expires it.
                stored = request.session.get(PENDING_SESSION_KEY)
                if isinstance(stored, dict):
                    stored["expires_at"] = time.time() + PENDING_LOGIN_TTL_SECONDS
                    request.session[PENDING_SESSION_KEY] = stored

                # set_password bumped the generation, which just invalidated
                # the session this request arrived on. Re-stamp it: this user
                # changed their own password deliberately, so they are not
                # the session being evicted.
                staff = get_staff(db, username)
                if request.session.get(SESSION_KEY):
                    stamp_session(request.session, staff)

                return _redirect(request, target)

        context["error"] = problem
        return templates.TemplateResponse(
            request, "brand/change_password.html", context, status_code=400
        )


def _enrolment_view_context(
    db, username: str, secret_key: str, issuer: str
) -> dict | None:
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
    replayed request can all arrive with no device row at all. Since v1.13
    that case is a `resume_mfa_enrolment` returning None rather than a
    `decrypt_totp_secret(None)` raising TypeError, but the branch that
    answers it is the same one and is still required.

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
    # Resume first, mint only if there is nothing to resume. Both branches go
    # through admin/accounts.py so that the otpauth:// label is built in one
    # place: this branch used to pass a bare `staff.username` while
    # begin_mfa_enrolment passed `_device_label(username, DEFAULT_DEVICE_NAME)`.
    # Identical output today - the default device's label *is* the bare
    # username - and two entries on the person's phone the moment that stops
    # being true, because a re-render after a rejected code would encode a
    # different label from the one they scanned.
    #
    # `resume` finds the *unconfirmed* device, never merely "a device": since
    # contract v1.13 an account can hold several, and the one this page is
    # finishing is the one whose enrolled_at is still NULL. Reading any other
    # device's secret would render a QR for an authenticator the person
    # already has, and complete_mfa_enrolment would refuse the code it made.
    resumed = resume_mfa_enrolment(db, username, secret_key=secret_key, issuer=issuer)
    if resumed is None:
        try:
            resumed = begin_mfa_enrolment(
                db, username, secret_key=secret_key, issuer=issuer
            )
        except MfaAlreadyEnrolledError:
            return None
    secret, uri = resumed
    return {"qr": qr_svg(uri), "secret": secret,
            "secret_grouped": _grouped(secret)}


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
                context = _enrolment_view_context(
                    db, username, secret_key, runtime.settings.totp_issuer
                )
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
                context = _enrolment_view_context(
                    db, username, secret_key, runtime.settings.totp_issuer
                )
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
                context = _enrolment_view_context(
                    db, username, secret_key, runtime.settings.totp_issuer
                )
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
            # Establish the session here rather than sending the user on to
            # the second-factor page. Both factors are already proven by this
            # point: the gate admitted this request on a valid pending login,
            # which is the password step, and complete_mfa_enrolment has just
            # verified a genuine TOTP code.
            #
            # Asking again was not extra security - it was the same evidence
            # requested twice, and it could not succeed. Enrolment records its
            # accepted time step in mfa_last_counter, so the code still on the
            # user's authenticator is refused as a replay, while the page
            # tells them to check their device clock. Users hit that, waited
            # out the window, and often exhausted the five-minute pending
            # login in the process, landing back at a login page with a
            # correct new password and a working authenticator.
            #
            # The account is re-read rather than trusted from the pending
            # value, for the same reason _complete_login re-reads it: a
            # deactivation landing between the two steps must take effect.
            staff = get_staff(db, username)
            established = staff.is_active and staff.mfa_enrolled
            if established:
                # _complete_login is the only other place that stamps this,
                # so a login completed here is indistinguishable from one
                # completed at /admin/verify.
                staff.last_login_at = utcnow()
            db.commit()

        if not established:
            return _redirect(request, "admin:login")

        runtime.throttle.clear(username)
        request.session.pop(PENDING_SESSION_KEY, None)
        stamp_session(request.session, staff)

        return templates.TemplateResponse(
            request,
            "brand/enrol_done.html",
            {
                "codes": codes,
                "next_url": "/admin/",
                # Named so the page, and anything copied off it, says which
                # system and which account these belong to. Somebody holding
                # recovery codes for more than one system cannot tell them
                # apart by their contents alone.
                "issuer": runtime.settings.totp_issuer,
                "username": username,
            },
        )
