"""Account lifecycle service.

Contract: docs/interfaces.md 8.3. The only module that mutates ``staff`` rows,
so that the invariants below hold no matter which entry point is used.

The functions here flush but never commit: the caller owns the transaction.
That is what lets the admin panel write an audit_log entry in the same
transaction as the change it describes.
"""

import re
import secrets
import string
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from admin.audit import write_audit
from admin.models import (
    AuditLog,
    Staff,
    StaffRecoveryCode,
    StaffRole,
    StaffTotpDevice,
    utcnow,
)
from admin.security import (
    decrypt_initial_password,
    decrypt_totp_secret,
    encrypt_initial_password,
    encrypt_totp_secret,
    generate_recovery_codes,
    hash_password,
    hash_recovery_code,
    verify_recovery_code,
)
from admin.totp import (
    DEFAULT_ISSUER,
    generate_totp_secret,
    provisioning_uri,
    verify_totp,
)

#: Contract 8.3. With no email system, administrators are each other's
#: recovery path, so the system refuses to fall below two — two that exist,
#: and two that can actually log in. See _guard_admin_floor.
MIN_ACTIVE_ADMINS = 2

#: Long enough that it is never typed from memory, and drawn from a set with
#: no shell-hostile characters, since it gets read out or pasted.
_INITIAL_PASSWORD_ALPHABET = string.ascii_letters + string.digits
_INITIAL_PASSWORD_LENGTH = 20

#: ``staff.username`` is VARCHAR(64) and ``staff.display_name`` VARCHAR(128).
#: Enforced here rather than only in a form, because MySQL in non-strict mode
#: truncates silently — an account created with a 70-character username would
#: be stored under a name nobody can reproduce at the login box.
MAX_USERNAME_LENGTH = 64
MAX_DISPLAY_NAME_LENGTH = 128

#: What a username may contain, after ``_normalise_username`` has stripped and
#: casefolded it. Deliberately permissive about *which* characters — a trust
#: may want ``jane``, ``j.gerrard`` or ``jane@example.org`` and none of those
#: is wrong — and strict about the one thing that is: no whitespace, anywhere.
#: A username with a space in it is one the login box's own ``.strip()`` can
#: never reconstruct from the middle, so the account would be created and then
#: be unreachable, which is the exact failure this whole task exists to avoid.
_USERNAME_RE = re.compile(r"^[^\s]+$")


class LastAdministratorsError(RuntimeError):
    """The change would leave fewer than MIN_ACTIVE_ADMINS administrators."""


class InvalidUsernameError(RuntimeError):
    """The proposed username is empty, too long, or contains whitespace."""


class InvalidDisplayNameError(RuntimeError):
    """The proposed display name is empty or too long."""


class DuplicateUsernameError(RuntimeError):
    """An account with that username already exists.

    Refused here, before the insert, rather than left to surface as the
    UNIQUE constraint's ``IntegrityError`` — which reaches the CLI as a
    traceback and the panel as a 500.

    **This is also what stops creation becoming a way around
    ``_guard_not_self``.** Creating an account is the one operation in this
    module that mints a credential without proving anything about the account
    it belongs to, because the account does not exist yet. If it could name a
    row that *did* exist, it would be ``issue_password`` with the guard taken
    off: an administrator — or a stolen session — would "create" their own
    username and be handed a fresh password for the account already there.
    Refusing a name that is taken is what keeps creation strictly additive.
    """


class UnknownStaffError(RuntimeError):
    """No account with that username."""


class SelfRecoveryError(RuntimeError):
    """A recovery action was aimed at the account performing it."""


class AccountStillActiveError(RuntimeError):
    """Deletion was attempted on an account that has not been deactivated.

    Deliberately its own class rather than a reuse of LastAdministratorsError,
    which is the other refusal ``delete_staff`` can raise. The two say opposite
    things about what to do next — "deactivate this account first" against
    "there are not enough administrators to remove one at all" — and a caller
    that rendered the wrong message would send an administrator to perform a
    step that is not the one blocking them.
    """


class UnknownDeviceError(RuntimeError):
    """No authenticator with that id **on this account**.

    Deliberately not distinguished from "no such device anywhere". The id of
    a device belonging to somebody else and the id of one that never existed
    must produce the same answer, or the error becomes an oracle for which
    ids are real and whose they are.
    """


def generate_initial_password() -> str:
    """Generate a one-time initial password.

    ``secrets``, never ``random``: the latter is a Mersenne Twister seeded
    predictably enough that its output is recoverable.
    """
    return "".join(
        secrets.choice(_INITIAL_PASSWORD_ALPHABET)
        for _ in range(_INITIAL_PASSWORD_LENGTH)
    )


def _normalise_username(username: str) -> str:
    return username.strip().casefold()


def get_staff(session: Session, username: str) -> Staff:
    """Fetch an account by username, or raise UnknownStaffError."""
    staff = session.scalar(
        select(Staff).where(Staff.username == _normalise_username(username))
    )
    if staff is None:
        raise UnknownStaffError(f"No account named {username!r}")
    return staff


def count_active_admins(session: Session) -> int:
    """Administrator accounts that exist and have not been deactivated.

    This is the *existence* count, and it is what ``ensure_bootstrap_admins``
    asks: a freshly bootstrapped administrator cannot log in yet, so a
    bootstrap keyed on the stricter count below would re-fire on every restart
    until onboarding finished and collide on the usernames it had already
    created. Use ``count_usable_admins`` for anything that asks whether there
    is a human who can actually get in.
    """
    return int(
        session.scalar(
            select(func.count())
            .select_from(Staff)
            .where(Staff.role == StaffRole.admin, Staff.is_active.is_(True))
        )
        or 0
    )


def count_usable_admins(session: Session) -> int:
    """Administrator accounts that can complete a login today.

    Active, enrolled in MFA and past the forced password change — the three
    conditions ``require_staff`` checks. An administrator who fails any of
    them is not a recovery path for anyone: contract 8.3's layer L2 is another
    administrator *logging in* and resetting your MFA, and with no email
    system there is nothing below L2 but server shell access.

    This is deliberately narrower than ``count_active_admins``. The realistic
    failure is a small charity that onboards ``admin``, files ``admin2``'s
    printed password away and never uses it: two active administrators, one
    usable, and a floor counting the former would happily let the usable one
    be deactivated.
    """
    return int(
        session.scalar(
            select(func.count())
            .select_from(Staff)
            .where(
                Staff.role == StaffRole.admin,
                Staff.is_active.is_(True),
                Staff.mfa_enrolled_at.is_not(None),
                Staff.must_change_password.is_(False),
            )
        )
        or 0
    )


def _guard_admin_floor(session: Session, staff: Staff) -> None:
    """Refuse a change that removes an active administrator when at the floor.

    Both counts have to hold, and neither implies the other:

    * ``count_active_admins`` keeps the panel from being emptied of
      administrator accounts, including during onboarding when none of them
      can log in yet.
    * ``count_usable_admins`` keeps it from being left to administrators who
      cannot log in. Creating a third account and deactivating the only
      onboarded one would otherwise pass the first check and hand the panel to
      two accounts nobody can get into — with no email system, that is
      recoverable only by shell access to the server.

    The consequence is that no administrator can be removed until two of them
    have finished onboarding. That is the intended reading of contract 8.3's
    floor: an administrator who cannot log in is not a recovery path.
    """
    if staff.role is not StaffRole.admin or not staff.is_active:
        return
    if count_active_admins(session) <= MIN_ACTIVE_ADMINS:
        raise LastAdministratorsError(
            f"At least {MIN_ACTIVE_ADMINS} active administrator accounts must "
            "exist. Promote or create another administrator first."
        )
    if count_usable_admins(session) <= MIN_ACTIVE_ADMINS:
        raise LastAdministratorsError(
            f"At least {MIN_ACTIVE_ADMINS} administrator accounts must be able "
            "to log in — active, past the forced password change and enrolled "
            "in MFA. Finish onboarding another administrator first."
        )


def _guard_not_self(staff: Staff, actor: str, *, allow_self: bool, what: str) -> None:
    """Refuse a recovery action aimed at the account performing it.

    ``issue_password`` and ``reset_mfa`` are contract 8.3's recovery layer L2:
    what one administrator does *for* a colleague who cannot act for
    themselves. Each one deliberately skips the proof the ordinary path
    demands — issuing a password never asks for the current one, resetting an
    enrolment never asks for the device. That is the point when the person
    holding the account is locked out, and it is a privilege escalation when
    they are not: a stolen session issues itself a password, resets the
    authenticator, enrols its own, and now holds both factors outright,
    having produced neither at any point. Every step of that is audited,
    which reports the takeover and does not prevent it.

    The floor above (``_guard_admin_floor``) rests on the same reading of
    8.3 from the other side: two administrators exist so that each is the
    other's recovery path. An action a single administrator can apply to
    themselves has quietly removed the second party from a procedure whose
    entire value is that a second party was involved.

    Comparison is through ``_normalise_username`` on both sides. ``username``
    is stored casefolded and ``actor`` arrives from a session cookie written
    at login, so comparing the two raw would let one capital letter through.

    ``allow_self`` is the exemption, and it is a parameter rather than a
    property of where the guard sits: see ``admin/cli.py``, the one caller
    that passes it.
    """
    if allow_self:
        return
    if _normalise_username(actor) != staff.username:
        return
    raise SelfRecoveryError(
        f"{what} is a recovery action another administrator performs for you, "
        f"and it cannot be applied to your own account ({staff.username}). "
        "Ask the other administrator."
    )


def create_staff(
    session: Session,
    *,
    username: str,
    display_name: str,
    role: StaffRole = StaffRole.staff,
    actor: str,
    secret_key: str,
) -> tuple[Staff, str]:
    """Create an account and return it with its one-time initial password.

    Contract §8.3 forbids self-service registration, so this is the only way
    an account comes into existence — ``admin/cli.py``'s ``create-staff``,
    ``StaffAdmin.new_staff`` and ``admin/bootstrap.py`` all land here, and none
    of them carries a rule of its own.

    **The password is stored, encrypted and reversibly, until it is claimed —
    contract v1.15 item 3, and it is the one reversibly-stored credential in
    this system.** It was not, until v1.15: the value was returned and
    otherwise discarded, and losing the page that showed it meant the
    administrator had to issue a new one, which is a real nuisance once the old
    one has already been read out to the colleague. ``admin/models.py``'s note
    on ``initial_password_enc`` and ``admin/security.py``'s note above
    ``encrypt_initial_password`` between them carry what that buys and what it
    costs; the part that belongs here is the invariant:

        **This column is written here and cleared by ``set_password`` and
        ``issue_password``, and by nothing else.** A future path that changes a
        password without clearing it leaves a live, readable credential on an
        account whose password is something else — the one failure mode worth
        more than the whole feature. ``tests/admin/test_initial_password.py``
        drives every such path and asserts the column is NULL afterwards.

    The plaintext is *also* still returned, and every caller still shows it
    once at the point of creation (``brand/staff_created.html``,
    ``admin/cli.py``'s printed lines). Nothing about the reveal route replaces
    handing it over then; it only means a lost page is recoverable.

    ``secret_key`` is required and keyword-only for the same reason ``actor``
    is: an optional one is a caller that silently forgot, and here that would
    be an account created with nothing to reveal, discovered only by the
    administrator who went looking.

    ``actor`` is **required**, and keyword-only, for the reason ``reset_mfa``'s
    already is: an optional actor is a caller that silently forgot one, and
    here that would put an unattributed account into ``staff`` and an
    unattributed row into ``audit_log``. ``bootstrap`` and ``cli`` are the two
    non-human values in use.

    Refuses a username that is taken, empty, over-length or contains
    whitespace, and a display name that is empty or over-length. Every one of
    those is refused **before** anything is added to the session, so a
    rejected call leaves the transaction exactly as it found it.

    Writes its own ``audit_log`` entry, in the same transaction, the way
    ``issue_password`` does. Creation is a write like any other (§8.1) and it
    was the one mutation in this module that left no trace: an account that
    appeared with nobody named beside it was indistinguishable from one
    inserted by hand against the database.

    **``password_hash`` is deliberately absent from that payload**, and the
    reason is not redaction — ``write_audit`` would redact it anyway. It is
    that ``last_password_change`` reads the trail for exactly two shapes, one
    of which is "this entry has a ``password_hash`` key", and naming it here
    would make every account's *creation* answer the question
    ``/admin/security`` asks as "when did you last change your password".
    A newly created account has never changed its password; it was issued one,
    and it is forced to change it at first login — which writes an entry of
    its own. ``action="create"`` on ``table_name="staff"`` already says a
    credential was minted, since an account cannot exist without one.
    """
    username = _normalise_username(username)
    if not username or not _USERNAME_RE.match(username):
        raise InvalidUsernameError(
            "A username is required and cannot contain spaces. It is what "
            "this person types at the login box."
        )
    if len(username) > MAX_USERNAME_LENGTH:
        raise InvalidUsernameError(
            f"That username is too long. Use at most {MAX_USERNAME_LENGTH} "
            "characters."
        )

    display_name = display_name.strip()
    if not display_name:
        raise InvalidDisplayNameError(
            "A display name is required. It is what the panel and the audit "
            "trail show in place of the username."
        )
    if len(display_name) > MAX_DISPLAY_NAME_LENGTH:
        raise InvalidDisplayNameError(
            f"That name is too long. Use at most {MAX_DISPLAY_NAME_LENGTH} "
            "characters."
        )

    # Checked rather than caught. The UNIQUE constraint on staff.username is
    # still the real guarantee — two simultaneous creations of the same name
    # would race past this and one would fail at the flush — but that is a
    # 500 on a panel with two administrators, whereas the case that actually
    # happens (a name already in use, typed by one person) gets an
    # explanation. See DuplicateUsernameError for why this is a guard and not
    # a convenience.
    if session.scalar(select(Staff).where(Staff.username == username)) is not None:
        raise DuplicateUsernameError(
            f"An account named {username!r} already exists. Usernames cannot "
            "be reused, and creating one never replaces an existing account. "
            "If this is the same person, issue them a new password instead."
        )

    password = generate_initial_password()
    staff = Staff(
        username=username,
        display_name=display_name,
        # bcrypt, exactly as before and exactly as every other password on
        # this system. The premise that a password with must_change_password
        # set is "not yet hashed into the database" is false and has always
        # been: hash_password runs here, at creation, and password_hash is
        # NOT NULL with no default. The line below is a *second*, separate
        # copy under a different protection, not the absence of this one.
        password_hash=hash_password(password),
        initial_password_enc=encrypt_initial_password(
            password, secret_key=secret_key
        ),
        role=role,
        is_active=True,
        must_change_password=True,
        created_at=utcnow(),
        created_by=actor,
    )
    session.add(staff)
    # Before write_audit, so row_id is the real primary key rather than None.
    # Flushing but not committing is this module's convention throughout: the
    # caller owns the transaction, which is what lets the row and its audit
    # entry land or roll back together.
    session.flush()
    write_audit(
        session,
        actor=actor,
        action="create",
        table_name="staff",
        row_id=staff.id,
        before=None,
        after={
            "username": staff.username,
            "display_name": staff.display_name,
            "role": staff.role.value,
            "is_active": True,
            "must_change_password": True,
            "created_by": staff.created_by,
        },
    )
    return staff, password


def set_password(session: Session, username: str, new_password: str) -> None:
    """Set a password and clear the forced-change flag.

    Bumps the session generation: contract §8.3 treats a password change as
    an eviction, and leaving the old sessions live would make it cosmetic.
    The caller that is changing its *own* password must re-stamp its session
    afterwards — see ChangePasswordView.

    **Clears ``initial_password_enc``, and this is the single function that
    covers two of the three paths that matter** (contract v1.15 item 3): the
    self-service change on ``/admin/security`` and the forced change at first
    login on ``/admin/change-password`` both come through here, and so does
    ``kaicalc-admin set-password``. That is why the clearing lives in this
    function rather than in either view — a copy in one view is a clearing the
    other path does not do, and the forced change at first login is precisely
    the path that decides how long the column exists at all.

    Unconditional. Not ``if staff.initial_password_enc is not None``, which
    reads the same and is not: the point is that after this call the column is
    NULL whatever it was, so a future caller cannot arrange a state where it is
    skipped.
    """
    staff = get_staff(session, username)
    staff.password_hash = hash_password(new_password)
    staff.must_change_password = False
    staff.initial_password_enc = None
    staff.session_generation += 1


def last_password_change(session: Session, staff: Staff) -> datetime | None:
    """When this account's password was last changed, or None if unknown.

    **Read out of the audit trail, because there is no column for it and this
    task deliberately did not add one.** A ``password_changed_at`` column means
    a migration, a second writer to keep in step with every path that sets a
    password, and a value that is wrong the moment one of them forgets. Every
    password change is already an ``audit_log`` row naming the staff row it
    changed; the date is a read away.

    Two shapes count, and both are here because both exist in the trail:

    * ``after_json["changed"] == "password"`` — what the self-service screen
      and ``admin/views.py::ChangePasswordView`` write.
    * a ``password_hash`` key — what ``issue_password`` writes. The value lands
      redacted by ``write_audit``; the key surviving is what says a password
      was replaced.

    **None means "no record", and the caller must say exactly that.** It is not
    "never changed" and it must never be rendered as ``created_at`` or as
    today: rows written before the forced-change page learned to audit itself
    genuinely have no entry, and a confidently wrong date on a security screen
    is worse than an honest gap.

    Rows are read newest-first and the first match wins. The scan is over one
    account's ``staff``-table entries only, which is a handful of rows for the
    life of an account — JSON predicates differ between MySQL and SQLite, and
    this module is imported by both.

    **``AuditLog.at >= staff.created_at`` is a correctness guard, not an
    optimisation, and it is what makes ``delete_staff`` safe to have.**
    ``audit_log`` holds no foreign key to ``staff`` — deliberately, since
    ``actor`` also carries ``cli``, ``bootstrap`` and ``deploy-seed`` — so a
    deleted account's entries stay in the table naming a ``row_id`` that no
    longer resolves. ``staff.id`` is a plain autoincrement integer, and while
    MySQL 8 persists its counter across restarts and does not reuse ids,
    **SQLite hands out ``max(rowid) + 1`` and reuses one the moment a row is
    deleted**; this module is imported by both. Without this predicate, an
    account created after a deletion could be handed the dead account's id and
    inherit its password history, and ``/admin/security`` would tell a new
    member of staff their password was last changed on a date belonging to
    somebody who no longer exists.

    Scoped on the read side rather than by rewriting the deleted account's rows
    to NULL, which was the alternative. An account cannot have changed its
    password before it existed, so this excludes nothing genuine — and it
    leaves ``audit_log`` append-only. A deletion that edited historical rows
    would make the trail describe the present rather than what happened, which
    is the one property it exists to have.
    """
    rows = session.scalars(
        select(AuditLog)
        .where(
            AuditLog.table_name == "staff",
            AuditLog.row_id == staff.id,
            AuditLog.at >= staff.created_at,
        )
        .order_by(AuditLog.at.desc(), AuditLog.id.desc())
    )
    for row in rows:
        after = row.after_json or {}
        if after.get("changed") == "password" or "password_hash" in after:
            return row.at
    return None


def issue_password(
    session: Session, username: str, *, actor: str, allow_self: bool = False
) -> str:
    """Replace an account's password with a random one it must then change.

    Contract §8.3, recovery layer L2: this is half of what an administrator
    does to a compromised or locked-out colleague (reset_mfa is the other
    half). The plaintext is returned to be read out once and handed over out
    of band - there is no email system, deliberately.

    Refuses when ``actor`` names the account being acted on, unless
    ``allow_self`` says otherwise - see ``_guard_not_self``. The guard runs
    before anything is written, so a refused call leaves the row and the
    audit trail exactly as it found them; the caller that was turned away is
    the one that records the attempt, since only it knows the attempt was
    refused rather than made.

    Distinct from set_password, which clears must_change_password because the
    user chose that password themselves. An issued password is a temporary
    credential; the account is forced through the change page on next login.
    Also bumps session_generation inline, along with every other credential
    change in this module (set_password, deactivate_staff, reset_mfa) -
    there is no shared helper for it; each site increments the column
    directly since the row is already loaded at that point.
    """
    staff = get_staff(session, username)
    _guard_not_self(staff, actor, allow_self=allow_self, what="Issuing a password")
    password = generate_initial_password()
    staff.password_hash = hash_password(password)
    staff.must_change_password = True
    # The third path that changes a password, and the third that has to clear
    # the unclaimed-initial-password column (contract v1.15 item 3). Cleared
    # rather than replaced with this new value, deliberately: v1.15 scopes the
    # column to "the password the account was created with", so an issued
    # replacement stays a one-time reveal - shown on brand/issued_credential.html
    # and kept nowhere. Storing it too would widen the window from "between
    # creation and first login" to "any time an administrator has issued a
    # password and it has not been used", which is unbounded, and the loss this
    # feature exists to prevent is the loss of the *creation* page.
    #
    # Leaving it as it was would be the actual defect: the row would keep
    # offering an initial password that no longer opens the account, and the
    # administrator reading it out would be handing over a dead string.
    staff.initial_password_enc = None
    staff.session_generation += 1
    write_audit(
        session,
        actor=actor,
        action="update",
        table_name="staff",
        row_id=staff.id,
        before=None,
        after={
            "username": staff.username,
            "must_change_password": True,
            "password_hash": staff.password_hash,
        },
    )
    return password


def deactivate_staff(session: Session, username: str) -> None:
    staff = get_staff(session, username)
    _guard_admin_floor(session, staff)
    staff.is_active = False
    staff.session_generation += 1


def reactivate_staff(session: Session, username: str) -> None:
    """Undo a deactivation.

    **This exists because ``delete_staff`` requires deactivation first**, and
    that requirement is only defensible if the step it makes mandatory is
    genuinely reversible. Without this function, deactivating the wrong account
    was a trap with two exits — leave it in the list for ever, or delete it —
    and "delete it" is the one this panel was refusing to offer at all.

    **It takes no floor guard, and none is missing.** ``_guard_admin_floor``
    refuses changes that *remove* an active administrator; this only ever adds
    one, so every count it protects moves upward or stays put.

    **It takes no self guard either, and that is a property of the system
    rather than an omission.** ``_guard_not_self`` stops an administrator
    aiming a recovery action at their own account. Here there is no such aim to
    take: ``AdminAuth.authenticate`` refuses a session whose account is not
    ``is_active``, so nobody can be signed in as the account they would be
    reactivating. A guard here could never fire, and a guard that cannot fire
    is one whose test passes against any implementation at all — including one
    that refuses everything, which is this project's recorded failure mode for
    guard tests. Stated rather than added.

    Bumps ``session_generation`` for symmetry with ``deactivate_staff``. There
    is no live session to evict — deactivation already ended them and the
    account has had none since — but the invariant every other write in this
    module holds is that a change to an account's ability to log in ends the
    cookies minted before it, and an exception to it would have to be reasoned
    about by the next caller rather than simply relied upon.
    """
    staff = get_staff(session, username)
    staff.is_active = True
    staff.session_generation += 1


def reveal_initial_password(
    session: Session, username: str, *, actor: str, secret_key: str
) -> str | None:
    """Read back an unclaimed initial password, recording that it was read.

    Contract §8.3, v1.15 item 3. Returns ``None`` when there is nothing to
    reveal — the account has changed its password, or an administrator has
    issued a replacement — and the caller must render that as its own outcome
    rather than as an error or as an empty string. "There is nothing here" and
    "here it is" are different answers and a page that blurs them would have
    somebody reading out a blank.

    **Every successful read writes an ``audit_log`` entry**, and that is the
    price of the column existing at all. A reversibly-stored credential that
    could be read without leaving a trace would mean an administrator who read
    a colleague's password before they collected it is indistinguishable from
    one who did not — which is the whole question anybody would ask after a
    compromise. ``action="reveal"`` is a value contract §2.3's enumeration
    gained in v1.15 rather than being reused as ``read`` or ``update``; both of
    those already mean something else to ``last_password_change``, which reads
    this same trail and branches on the shape of ``after_json``.

    **The entry never carries the value, in any form.** ``after`` names the
    account and says the reveal happened. The ciphertext is in
    ``REDACTED_FIELDS`` for the separate reason that ``row_to_dict`` would
    otherwise snapshot it (see ``db/repository.py``), but this call site does
    not go near it: the trail is append-only, so anything written here outlives
    the column by the life of the deployment, which would defeat the point of
    clearing it.

    No ``_guard_not_self``. Unlike ``issue_password`` and ``reset_mfa`` this
    mints nothing and clears nothing, and the self-aimed case cannot arise
    anyway: an account signed in has necessarily completed its forced password
    change, which set this column to NULL. The function would return None.

    Refuses nothing else, and holds no role check — the role floor is the
    view's (``StaffAdmin`` is administrator-only, and
    ``StaffAdmin.initial_password_page`` calls ``_require_admin`` on top of
    that, because an ``@expose`` route does not inherit ``is_accessible``).
    This module's convention is that the service layer holds the rules a CLI
    and a panel must share, and there is no CLI command for this: a shell on
    the container can read ``staff.initial_password_enc`` and ``SECRET_KEY``
    directly, so a command would add a path without adding a capability.
    """
    staff = get_staff(session, username)
    if staff.initial_password_enc is None:
        return None
    password = decrypt_initial_password(
        staff.initial_password_enc, secret_key=secret_key
    )
    write_audit(
        session,
        actor=actor,
        action="reveal",
        table_name="staff",
        row_id=staff.id,
        before=None,
        after={"username": staff.username, "revealed": "initial_password"},
    )
    return password


def delete_staff(
    session: Session, username: str, *, actor: str, allow_self: bool = False
) -> dict:
    """Remove an account outright. Returns what was destroyed, for the caller.

    **Why this is a hard delete and not a tombstone.** The question a delete
    has to answer on this project is what becomes of the audit trail, which is
    a deliverable in its own right — an entry that can no longer say who did
    the thing is a worse outcome than a list with an extra row in it. The
    answer, established by reading every reference to ``staff`` rather than
    assumed: **``audit_log`` has no foreign key to this table**, in the model
    or in ``0002_audit_log.py``'s DDL. ``audit_log.actor`` is
    ``VARCHAR(128)``, and it has to be, because it also carries ``cli``,
    ``bootstrap``, ``deploy-seed`` and ``unknown`` — none of which is or could
    be a row here. So the trail already stores identity the way a tombstone
    would have been introduced to make it store it, and every entry a deleted
    account wrote stays complete and still names its author.

    The same holds for the other three denormalised identity columns —
    ``staff.created_by``, ``factor_set.published_by``, ``ip_block.created_by``.
    All text, none a foreign key, none affected.

    A tombstone was rejected on the merits: it leaves the row in ``staff``, the
    username taken, and the list one query parameter away from showing what was
    hidden — which is not the capability that was asked for — while buying
    nothing, since the property it is normally bought for is already held.

    **What *is* destroyed with the account** is exactly its credentials:
    ``staff_recovery_code`` and ``staff_totp_device`` cascade, at the database
    (``ondelete="CASCADE"``) and through the ORM (``delete-orphan``). That is
    the correct outcome and not a side effect to be worked around — a stored
    TOTP secret must not outlive the account it authenticates.

    **The one hazard, and where it is closed.** ``last_password_change`` reads
    the trail by ``(table_name='staff', row_id)``, and a deleted id can be
    handed out again under SQLite. That is fixed in ``last_password_change``
    itself, on the read side, so this function does not have to rewrite history
    to be safe; see its docstring.

    Four guards, in the order they are cheapest to explain:

    * **Deactivation first.** Refused unless ``is_active`` is already False.
      Deletion is then bookkeeping on a row that ``deactivate_staff`` has
      already made inert — it evicted the sessions and bumped the generation —
      rather than an eviction racing a request in flight. It also puts the
      floor check on the path twice, and the first of the two runs while the
      account still counts toward it.
    * **The two-administrator floor**, via ``_guard_admin_floor``, which §8.3
      names for deletion in the same breath as deactivation and demotion. In
      practice a deactivated account is already past it — the guard returns
      early for a row that is not active — which is precisely why the check at
      deactivation time is the one that bites, and why this call stays here
      rather than being dropped as redundant: it is the guard for the case
      where the two steps are ever decoupled.
    * **No self-deletion**, via ``_guard_not_self``. An administrator deleting
      their own account has removed the second party from the procedure whose
      whole value is that a second party was involved, and has done to the
      floor in one action what deactivation is refused for doing.
    * Re-authentication, which belongs to the caller: it is a proof about the
      request, and this module never sees one.

    Writes its own ``audit_log`` entry, inside the caller's transaction, the
    way ``create_staff`` does. It is the entry most worth having — after the
    commit it is the only record that the account ever existed — so it carries
    the whole identity rather than a reference to a row that is about to stop
    resolving.
    """
    staff = get_staff(session, username)
    _guard_not_self(staff, actor, allow_self=allow_self, what="Deleting an account")
    if staff.is_active:
        raise AccountStillActiveError(
            f"{staff.username!r} is still active. Deactivate the account first, "
            "then delete it. Deactivating ends its sessions and refuses its "
            "next login, and it can be undone; deleting cannot."
        )
    _guard_admin_floor(session, staff)

    # Snapshotted before the delete, because after it there is nothing left to
    # read and this is the only record the account ever existed.
    removed = {
        "username": staff.username,
        "display_name": staff.display_name,
        "role": staff.role.value,
        "is_active": staff.is_active,
        "created_at": staff.created_at,
        "created_by": staff.created_by,
        # Named as counts, never as rows. The point of saying so at all is that
        # an administrator reading the trail can see that a second factor and a
        # set of recovery codes went with the account, which is what makes the
        # deletion irreversible in the way that matters.
        "totp_devices_destroyed": len(staff.totp_devices),
        "recovery_codes_destroyed": len(staff.recovery_codes),
    }
    staff_id = staff.id

    session.delete(staff)
    # Before the audit entry, so a database-level refusal (a foreign key added
    # later without this function being revisited) surfaces here rather than
    # after a row claiming the deletion happened has been written.
    session.flush()
    write_audit(
        session,
        actor=actor,
        action="delete",
        table_name="staff",
        row_id=staff_id,
        before=removed,
        after=None,
    )
    return removed


def set_role(session: Session, username: str, role: StaffRole) -> None:
    staff = get_staff(session, username)
    if staff.role is StaffRole.admin and role is not StaffRole.admin:
        _guard_admin_floor(session, staff)
    staff.role = role


# --- MFA enrolment ----------------------------------------------------------

#: Contract 8.3. The panel prompts for regeneration once 2 remain.
RECOVERY_CODE_COUNT = 5


class MfaNotEnrolledError(RuntimeError):
    """The account has no usable TOTP enrolment, or the code offered to
    complete enrolment was wrong."""


class MfaAlreadyEnrolledError(RuntimeError):
    """The account has a finished enrolment, so a new one cannot be begun.

    A sibling of MfaNotEnrolledError rather than a reuse of it: the two say
    opposite things about the account, and a login page that catches the wrong
    one would either leak that an enrolment exists or silently swallow the
    refusal that stops an account being taken over.
    """


class LastAuthenticatorError(RuntimeError):
    """Removing this device would leave the account with no second factor."""


class TooManyDevicesError(RuntimeError):
    """The account already holds MAX_TOTP_DEVICES authenticators.

    Deliberately *not* LastAuthenticatorError, which it briefly reused. The
    two are opposite conditions - too few second factors against too many -
    and ``_remove_device`` catches LastAuthenticatorError to render the
    "this is your only authenticator" refusal. Sharing the class meant a
    caller that hit the ceiling could be shown a message telling it the
    account had no second factor left, which is the reverse of the truth and
    the kind of wrong answer somebody acts on.
    """


class DuplicateDeviceNameError(RuntimeError):
    """The account already has an authenticator under that name."""


class InvalidDeviceNameError(RuntimeError):
    """The proposed authenticator name is empty or over-length."""


#: Longest authenticator name accepted, matching ``staff_totp_device.name``'s
#: VARCHAR(64).
#:
#: **Here rather than in the view that used to own it**, because it is now
#: enforced in two places for two different reasons: ``rename_totp_device``
#: refuses an over-length name, and ``brand/security.html`` renders it as a
#: ``maxlength``. A constant defined in the view and a limit enforced in the
#: service layer are two numbers, and MySQL in non-strict mode truncates the
#: difference silently — an authenticator would be stored under a name nobody
#: typed. ``admin/self_service_view.py`` imports it from here.
MAX_DEVICE_NAME_LENGTH = 64


#: What the onboarding enrolment calls the first device. Anything the person
#: has not named themselves has to be called something, and this is what
#: appears in the authenticator app and on the security screen until they
#: enrol a second one and give it a name of its own.
DEFAULT_DEVICE_NAME = "Authenticator"

#: A hard ceiling, so that "enrol another" cannot quietly become an unbounded
#: list of live secrets on one account. Two phones is the case this exists
#: for; four leaves room for a tablet and a hardware token without the number
#: ever being the thing that blocks somebody mid-recovery.
MAX_TOTP_DEVICES = 4


def _sync_mfa_enrolled_at(staff: Staff) -> None:
    """Keep ``staff.mfa_enrolled_at`` honest about ``staff.totp_devices``.

    ``mfa_enrolled_at`` is derived state (see its own comment in
    admin/models.py): it exists so that the four onboarding gates in
    ``admin/backend.py``, ``require_staff_username`` and
    ``count_usable_admins`` can go on asking one indexed column instead of
    each growing an EXISTS subquery. Derived state that more than one place
    writes is derived state that drifts, so **this is the only writer**, and
    every function below that can add or remove a device ends by calling it.

    It sets the timestamp only on the transition from none to some. An
    account that enrols a second phone has not become enrolled a second
    time, and moving the timestamp forward would rewrite when its second
    factor came into force.
    """
    has_enrolled = any(d.enrolled_at is not None for d in staff.totp_devices)
    if has_enrolled and staff.mfa_enrolled_at is None:
        staff.mfa_enrolled_at = utcnow()
    elif not has_enrolled:
        staff.mfa_enrolled_at = None


def _device_label(username: str, device_name: str) -> str:
    """What the authenticator app shows under the issuer.

    Task 1 established that the issuer names the *system* and the account
    name distinguishes two administrators of it. A second device on one
    account needs a third distinction, and it has to be here: both devices
    carry the same issuer and the same username, so an app listing them
    would otherwise show two identical entries and the person deleting the
    lost phone would be guessing which. The default device is left as the
    bare username so that an account with one authenticator reads exactly as
    it always has.

    **The device name goes in the account half, never in the issuer.**
    Putting it in the issuer reads better - `Kai Commitment Admin — Backup
    phone` - and breaks the grouping Task 1 exists to create: authenticators
    group by issuer, so one account's two devices would land in two separate
    groups, which is the problem Task 1 solved, reintroduced one level down.

    The separator is a middle dot rather than parentheses because the app
    renders `issuer:account` as `issuer (account)`, so a parenthesised
    account nests: `Kai Commitment Admin (walker (Backup phone))`. With the
    dot it reads `Kai Commitment Admin (walker · Backup phone)` - same
    grouping, same information, one level of brackets.
    """
    if device_name == DEFAULT_DEVICE_NAME:
        return username
    return f"{username} · {device_name}"


def _pending_device(staff: Staff, name: str) -> "StaffTotpDevice | None":
    for device in staff.totp_devices:
        if device.enrolled_at is None and device.name == name:
            return device
    return None


def pending_totp_device(
    session: Session, username: str, *, device_name: str = DEFAULT_DEVICE_NAME
) -> "StaffTotpDevice | None":
    """The unconfirmed device of that name, or None.

    Exists for the enrolment pages, whose defining property is that an
    unfinished enrolment is **reused, never re-minted** — see
    ``admin/views.py::_enrolment_view_context``. Minting a second secret
    invalidates the QR already sitting on somebody's phone and then blames
    their device clock for the code that follows.
    """
    return _pending_device(get_staff(session, username), device_name)


def resume_mfa_enrolment(
    session: Session,
    username: str,
    *,
    secret_key: str,
    issuer: str = DEFAULT_ISSUER,
    device_name: str = DEFAULT_DEVICE_NAME,
) -> tuple[str, str] | None:
    """The (secret, URI) of an enrolment already in progress, or None.

    The read-only half of ``begin_mfa_enrolment``: it mints nothing. A page
    that has to re-render its QR — after a rejected code, an expired form,
    a refresh — calls this, so that the person's phone keeps the entry it
    already holds. Minting a second secret there invalidates the code they
    are looking at and then tells them to check their device clock, which is
    the one instruction guaranteed to waste their time.

    Living here rather than in the view keeps ``_device_label`` in one
    place: the label an authenticator shows must be identical on the first
    render and every re-render, or the app shows two entries for one device.
    """
    staff = get_staff(session, username)
    device = _pending_device(staff, device_name)
    if device is None:
        return None
    secret = decrypt_totp_secret(device.secret_enc, secret_key=secret_key)
    return secret, provisioning_uri(
        secret, username=_device_label(staff.username, device_name), issuer=issuer
    )


def begin_mfa_enrolment(
    session: Session,
    username: str,
    *,
    secret_key: str,
    issuer: str = DEFAULT_ISSUER,
    device_name: str = DEFAULT_DEVICE_NAME,
    allow_additional: bool = False,
) -> tuple[str, str]:
    """Start enrolment: store a fresh encrypted secret, return it and the URI.

    The secret is persisted here, already encrypted, on a
    ``staff_totp_device`` row whose ``enrolled_at`` stays NULL — so
    ``mfa_enrolled`` is unmoved and ``require_staff`` still refuses an
    account that has no other device. Enrolment completes only once the user
    produces a correct code, which is the only evidence the authenticator
    really holds the secret; without that step a mis-scanned QR locks someone
    out on their next login.

    Persisting it now, rather than handing it back for the caller to carry
    between the two requests, keeps the plaintext secret out of the browser
    entirely — off the form, out of the history, out of any request log.

    Calling this again for the same ``device_name`` replaces that unfinished
    enrolment's secret, which is what should happen when someone abandons the
    page and starts over.

    **``allow_additional`` is the whole of the multi-device change at this
    layer, and its default is False on purpose.** Without it, an account with
    a finished enrolment is refused: contract §8.3 puts the *onboarding*
    enrolment page behind the password step alone, since a user with no
    second factor has to be able to reach it, so an attacker holding only the
    password would otherwise scan their own QR code, complete the enrolment
    and hold both factors without ever needing the real owner's
    authenticator. That reasoning is about a caller that has proved one
    factor. The self-service security screen has proved two — it sits behind
    a fully established session, which ``admin/backend.py`` grants only after
    a second factor — and re-authenticates on top of that, so it passes
    ``allow_additional=True``. ``admin/views.py``'s EnrolView, the page the
    guard was written for, does not and must not.
    """
    staff = get_staff(session, username)
    if staff.mfa_enrolled_at is not None and not allow_additional:
        raise MfaAlreadyEnrolledError(
            f"{username!r} has already enrolled an authenticator. An "
            "administrator must reset it before a new one can be enrolled."
        )

    existing = _pending_device(staff, device_name)
    if existing is None:
        # A *confirmed* device under this name is a different case from an
        # unconfirmed one and must not be overwritten: that would replace a
        # working phone's secret with one nobody has scanned yet, and the
        # account would be one removal away from having no usable factor.
        if any(d.name == device_name for d in staff.totp_devices):
            raise DuplicateDeviceNameError(
                f"This account already has an authenticator called "
                f"{device_name!r}. Give the new one a different name."
            )
        if len(staff.totp_devices) >= MAX_TOTP_DEVICES:
            raise TooManyDevicesError(
                f"This account already has {MAX_TOTP_DEVICES} authenticators, "
                "which is the maximum. Remove one you no longer use first."
            )

    secret = generate_totp_secret()
    encrypted = encrypt_totp_secret(secret, secret_key=secret_key)
    if existing is None:
        staff.totp_devices.append(
            StaffTotpDevice(
                name=device_name,
                secret_enc=encrypted,
                enrolled_at=None,
                last_counter=None,
                created_at=utcnow(),
            )
        )
    else:
        existing.secret_enc = encrypted
        existing.last_counter = None

    # A no-op in every reachable case — the row just added or replaced is
    # unconfirmed, so `has_enrolled` is unmoved. Called anyway, because
    # _sync_mfa_enrolled_at's docstring claims that *every* function here which
    # can add or remove a device ends by calling it, and this was the one that
    # did not. Nothing had broken; the divergence between what the helper says
    # about itself and what the code does is the thing being closed, since the
    # next person to add a writer will read that claim and rely on it.
    _sync_mfa_enrolled_at(staff)

    return secret, provisioning_uri(
        secret, username=_device_label(staff.username, device_name), issuer=issuer
    )


def complete_mfa_enrolment(
    session: Session,
    username: str,
    code: str,
    *,
    secret_key: str,
    now: int,
    device_name: str = DEFAULT_DEVICE_NAME,
) -> list[str]:
    """Finish enrolment and return the one-time recovery codes.

    Reads the secret stored by ``begin_mfa_enrolment`` on the device of the
    same name. Raises MfaNotEnrolledError if enrolment was never begun, or if
    the code does not verify. Recovery codes are returned in plaintext
    because this is the only moment they exist in readable form; only their
    hashes are stored.

    **Recovery codes are minted for the account's first confirmed device
    only, and the return value is empty for any later one.** They are the
    account's fallback when *no* authenticator is available (contract §8.3's
    layer L1), not a per-device credential — minting five more on a second
    phone would leave the person holding two printed sheets with no way to
    tell which is current, while the older sheet stayed just as valid. The
    caller renders the codes when it gets them and says nothing about them
    when it does not.
    """
    staff = get_staff(session, username)
    device = _pending_device(staff, device_name)
    if device is None:
        raise MfaNotEnrolledError(
            "Enrolment has not been started for this account."
        )

    secret = decrypt_totp_secret(device.secret_enc, secret_key=secret_key)
    counter = verify_totp(secret, code, now=now)
    if counter is None:
        raise MfaNotEnrolledError(
            "That code did not match. Check the authenticator has the right "
            "account and that the device clock is correct."
        )

    first = not staff.enrolled_totp_devices
    device.enrolled_at = utcnow()
    device.last_counter = counter
    _sync_mfa_enrolled_at(staff)

    if not first:
        return []

    codes = generate_recovery_codes(RECOVERY_CODE_COUNT)
    for code_value in codes:
        session.add(
            StaffRecoveryCode(
                staff_id=staff.id,
                code_hash=hash_recovery_code(code_value),
                created_at=utcnow(),
            )
        )
    return codes


def verify_staff_totp(
    session: Session, username: str, code: str, *, secret_key: str, now: int
) -> bool:
    """Verify a login TOTP against any enrolled device, advancing its counter.

    Raises MfaNotEnrolledError when the account has not enrolled: contract 8.3
    requires that an unenrolled account cannot reach anything, so treating it
    as a plain failed code would hide a state that has to be handled.

    The gate is ``enrolled_totp_devices``, never the presence of a secret. An
    enrolment that was begun and abandoned leaves a perfectly usable secret
    on a row whose ``enrolled_at`` is still NULL; accepting it here would let
    a half-finished enrolment satisfy the login second factor. Completing an
    enrolment is the separate job of complete_mfa_enrolment, which reads that
    row directly for exactly that reason.

    **Each device carries its own replay counter and only the device that
    matched has its counter advanced.** A counter shared across devices would
    be wrong in the direction that locks people out: two phones hold two
    secrets and emit two different codes for the same time step, so a login
    on one would push a shared counter past the step the other's current,
    entirely unused code belongs to, and that code would be refused as a
    replay for the rest of its life. The failure would surface as "the backup
    phone does not work", intermittently, only on accounts with two devices.

    A malformed or undecryptable secret on one device does not stop the
    others being tried: ``verify_totp`` never raises, and returns None for an
    unusable secret.
    """
    staff = get_staff(session, username)
    devices = staff.enrolled_totp_devices
    if not devices:
        raise MfaNotEnrolledError(f"{username!r} has not enrolled an authenticator")

    for device in devices:
        secret = decrypt_totp_secret(device.secret_enc, secret_key=secret_key)
        counter = verify_totp(
            secret, code, now=now, last_counter=device.last_counter
        )
        if counter is not None:
            device.last_counter = counter
            return True
    return False


def list_totp_devices(session: Session, username: str) -> list["StaffTotpDevice"]:
    """Every device on the account, confirmed or not, in enrolment order."""
    return list(get_staff(session, username).totp_devices)


def remove_totp_device(
    session: Session, username: str, device_id: int
) -> "StaffTotpDevice":
    """Remove one authenticator, returning the row that was removed.

    ``device_id`` is looked up **within the account**, never globally. That
    is what makes one person's security screen unable to act on another's:
    an id belonging to somebody else is simply not found here, so the
    refusal is a property of this function rather than of whichever view
    happens to call it.

    Refuses to remove the last confirmed device. An account whose only
    second factor is deleted does not announce itself — it goes on working
    exactly as before until the next login, at which point ``require_staff``
    refuses it and the person is sent back through onboarding enrolment.
    Silently downgrading an account to password-only is the failure this
    guard exists to stop, and it is the reason the screen offers "remove"
    beside "add" rather than as a way to start over.

    Recovery codes deliberately do not count as the other factor. They are
    single-use fallbacks for when no authenticator is to hand (§8.3 layer
    L1), and five of them run out; an account holding only recovery codes is
    an account counting down.

    Unconfirmed devices are removable at any time, including the last one —
    an abandoned scan is not a factor and never was.

    **Bumps ``session_generation``, here rather than in the caller.** Removing
    an authenticator is a credential change, and this module is where every
    other one lives (``set_password``, ``issue_password``, ``deactivate_staff``,
    ``reset_mfa``) precisely so that no entry point can perform one without the
    eviction. The usual reason a device is removed is that it is out of the
    owner's hands; a caller that removed the row and left the sessions live
    would leave whoever holds the phone signed in. Removing an *unconfirmed*
    device bumps it too — the value of the invariant is that it holds without
    the caller having to reason about which case it is in, and an extra
    generation costs one re-stamp.
    """
    staff = get_staff(session, username)
    device = next((d for d in staff.totp_devices if d.id == device_id), None)
    if device is None:
        raise UnknownDeviceError(
            "That authenticator is not on this account."
        )

    if device.enrolled_at is not None and len(staff.enrolled_totp_devices) <= 1:
        raise LastAuthenticatorError(
            "This is the only authenticator on the account, so removing it "
            "would leave the account with a password and nothing else. Add "
            "the replacement first, then remove this one."
        )

    staff.totp_devices.remove(device)
    _sync_mfa_enrolled_at(staff)
    staff.session_generation += 1
    return device


def rename_totp_device(
    session: Session, username: str, device_id: int, new_name: str
) -> "StaffTotpDevice":
    """Change one authenticator's name. Returns the row, renamed.

    **Why the name is kept and made editable, rather than dropped for the
    ordinal.** Every name on this screen ends up saying nothing — the onboarding
    device is called ``Authenticator`` because something had to call it
    something, and the second one gets whatever its owner typed while looking at
    a form that gave them no reason to think about it. That is not evidence the
    column is useless; it is evidence that a name nobody can revise is a name
    nobody invests in. The ordinal already exists (``number`` on the security
    screen) and identifies the row without saying anything about the device,
    which is precisely the gap the name is supposed to fill when somebody is
    deciding which phone to remove.

    **What renaming does not do, and the screen must say so.** The name reaches
    the authenticator app through ``_device_label``'s ``otpauth://`` URI, and
    that URI is consumed once, at the scan. Renaming afterwards changes this
    list and nothing on the phone. Re-minting the secret so the app could be
    relabelled is not an option worth having: it would invalidate a working
    second factor to correct a caption.

    So this is a note against a row, and it is treated as one — no
    ``session_generation`` bump, because nothing about the account's
    credentials has changed. Compare ``remove_totp_device``, which bumps
    precisely because it has.

    ``device_id`` is looked up **within the account**, the same property
    ``remove_totp_device`` holds and for the same reason: another account's
    device id must be a not-found here rather than a rename somewhere else, so
    that the scoping is a property of the service layer and not of whichever
    view calls it.

    The account already holding that name is refused rather than left to
    ``uq_staff_totp_device_name``, which would surface as an IntegrityError at
    the flush — a 500 on the panel, and a traceback on the CLI. Renaming a
    device to the name it already has is allowed and is a no-op, since the
    uniqueness check has to exclude the row being renamed for that to be true.
    """
    staff = get_staff(session, username)
    device = next((d for d in staff.totp_devices if d.id == device_id), None)
    if device is None:
        raise UnknownDeviceError("That authenticator is not on this account.")

    new_name = new_name.strip()
    if not new_name:
        raise InvalidDeviceNameError(
            "Give the authenticator a name, so you can tell it apart later."
        )
    if len(new_name) > MAX_DEVICE_NAME_LENGTH:
        raise InvalidDeviceNameError(
            f"That name is too long. Use at most {MAX_DEVICE_NAME_LENGTH} "
            "characters."
        )
    # `d is not device` is what makes renaming a device to its current name a
    # no-op rather than a refusal — without it the row would collide with
    # itself, and the person correcting a typo in their own device's name
    # would be told the name was taken by the device they were editing.
    if any(d.name == new_name and d is not device for d in staff.totp_devices):
        raise DuplicateDeviceNameError(
            f"This account already has an authenticator called {new_name!r}. "
            "Give this one a different name."
        )

    device.name = new_name
    return device


def discard_unconfirmed_devices(
    session: Session, username: str
) -> list["StaffTotpDevice"]:
    """Destroy every unconfirmed enrolment on the account. Returns what went.

    **An unconfirmed device cannot be continued, so it is not a state.** The
    secret and the QR exist only in the response to the re-authenticated POST
    that minted them — ``begin_mfa_enrolment`` persists the secret encrypted
    precisely so the plaintext never has to be carried between two requests.
    Once that page is gone the row can only ever be destroyed, so
    "Set-up not finished" was never a stage of anything; it was litter that
    only a manual Remove cleared, and it is a stored TOTP secret that has
    never authenticated anything, left lying about for no benefit.

    It is also actively in the way. An abandoned row holds its name against
    ``begin_mfa_enrolment``'s ``DuplicateDeviceNameError`` and counts toward
    ``MAX_TOTP_DEVICES``, so four abandoned scans leave an account unable to
    add a real authenticator at all.

    **What guarantees this cannot destroy a confirmed device.** The filter is
    ``enrolled_at is None`` and there is no other term in it. It removes rows
    one at a time through the relationship and **never calls ``.clear()``** —
    the operation that makes ``reset_mfa`` correct is the operation that would
    make this catastrophic, and the two functions sit close enough together in
    this file to be confused by somebody editing quickly.

    **The decision of *when* to call this belongs to the caller, and that split
    is deliberate.** This function knows what may be destroyed; the page knows
    when somebody has left it. Folding the reap into ``begin_mfa_enrolment``
    would put it on ``admin/views.py::EnrolView``'s path too, where the
    opposite property is load-bearing: onboarding lands on ``/admin/enrol`` by
    GET and ``_enrolment_view_context`` deliberately *resumes* an unfinished
    enrolment across those GETs, because re-minting invalidates the code
    already sitting on somebody's phone and then blames their device clock for
    it. A reaper in the shared layer would break onboarding to tidy the
    self-service screen. Callers: ``admin/self_service_view.py`` only, on a GET
    of the page and before beginning a new enrolment.

    Ends with ``_sync_mfa_enrolled_at`` like every other function here that can
    remove a device. It is a no-op whenever a confirmed device remains, which
    on the only path that calls this is always — ``/admin/security`` is
    unreachable until ``mfa_enrolled_at`` is set — and it is called anyway, so
    that the helper's claim to be the sole writer of that column holds without
    a caller having to reason about which case it is in.
    """
    staff = get_staff(session, username)
    discarded = [d for d in staff.totp_devices if d.enrolled_at is None]
    for device in discarded:
        staff.totp_devices.remove(device)
    _sync_mfa_enrolled_at(staff)
    return discarded


def unused_recovery_code_count(session: Session, username: str) -> int:
    staff = get_staff(session, username)
    return int(
        session.scalar(
            select(func.count())
            .select_from(StaffRecoveryCode)
            .where(
                StaffRecoveryCode.staff_id == staff.id,
                StaffRecoveryCode.used_at.is_(None),
            )
        )
        or 0
    )


def consume_recovery_code(session: Session, username: str, code: str) -> bool:
    """Spend one recovery code. Returns False if it is unknown or already used."""
    staff = get_staff(session, username)
    candidates = session.scalars(
        select(StaffRecoveryCode).where(
            StaffRecoveryCode.staff_id == staff.id,
            StaffRecoveryCode.used_at.is_(None),
        )
    ).all()
    for candidate in candidates:
        if verify_recovery_code(code, candidate.code_hash):
            candidate.used_at = utcnow()
            return True
    return False


def reset_mfa(
    session: Session, username: str, *, actor: str, allow_self: bool = False
) -> None:
    """Clear enrolment and every recovery code.

    Contract 8.3 recovery layers L2 (another administrator) and L3 (the
    server-side CLI) both land here. The account is forced back through
    enrolment on its next login.

    ``actor`` is taken for ``_guard_not_self`` alone - this function writes no
    audit entry of its own, and its callers snapshot ``mfa_enrolled_at``
    themselves before it mutates the row. It is keyword-only and **required**
    on purpose: a default would mean a caller that forgot it silently skipped
    the guard, which is the failure mode the guard exists to close. A caller
    that genuinely has no acting session says so with ``allow_self=True``,
    visibly, at the call site.
    """
    staff = get_staff(session, username)
    _guard_not_self(
        staff, actor, allow_self=allow_self, what="Resetting the authenticator"
    )
    # **Every** device, not one of them. This is the reset an administrator
    # performs on an account they believe is compromised or locked out, and
    # leaving a second phone enrolled would leave whatever the reset was
    # meant to remove still holding a working second factor. Cleared through
    # the relationship for the same reason recovery_codes are below.
    staff.totp_devices.clear()
    _sync_mfa_enrolled_at(staff)
    staff.session_generation += 1
    # Clear through the relationship rather than a bulk delete. Staff declares
    # cascade="all, delete-orphan", so this removes the rows AND keeps the
    # session's view of them correct. A bulk delete with
    # synchronize_session=False empties the table but leaves an already-loaded
    # staff.recovery_codes reporting the deleted rows — and sqladmin renders
    # relationships directly, so an administrator would see recovery codes
    # that no longer exist for an account they had just reset.
    staff.recovery_codes.clear()
