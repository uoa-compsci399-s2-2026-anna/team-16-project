"""Operational commands.

Contract: docs/interfaces.md 8.3, "Operational commands".

    python -m admin.cli create-staff <username> "<display name>" [--admin]
    python -m admin.cli delete-staff <username>
    python -m admin.cli reactivate-staff <username>
    python -m admin.cli reset-mfa <username>
    python -m admin.cli issue-password <username>
    python -m admin.cli rotate-key --old <key> --new <key>
    python -m admin.cli unblock <address>

These are the break-glass paths that outlive the project team: bootstrapping
the first administrator, recovery layer L3 when every administrator is locked
out, re-encrypting TOTP secrets after a SECRET_KEY change, and (E-8) the way
back in for an administrator who has blocked the address they are sitting
behind. They are covered by tests rather than only exercised by hand for
that reason.

Every command here is a pass-through to ``admin/accounts.py``, which is the
only module permitted to mutate ``staff`` and holds every rule. The panel
reaches the same functions. Where the two paths differ it is stated at the
call site and nowhere else — see ``cmd_delete_staff`` for the one exemption
this file takes and the two guards it deliberately does not.
"""

import argparse
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from admin.accounts import (
    AccountStillActiveError,
    DuplicateUsernameError,
    InvalidDisplayNameError,
    InvalidUsernameError,
    LastAdministratorsError,
    UnknownStaffError,
    create_staff,
    delete_staff,
    issue_password,
    reactivate_staff,
    reset_mfa,
)
from admin.audit import write_audit
from admin.bootstrap import ensure_bootstrap_admins
from admin.config import load_settings
from admin.models import StaffRole, StaffTotpDevice
from admin.security import decrypt_totp_secret, encrypt_totp_secret
from admin.seed import seed_taxonomy
from admin.taxonomy_rules import TaxonomyInvariantError
from db.blocklist import InvalidAddressError, ip_fingerprint, unblock_ip
from db.blocklist_models import IpBlock
from db.session import create_session_factory


def cmd_create_staff(
    db_session: Session,
    username: str,
    display_name: str,
    role: StaffRole,
    *,
    actor: str,
) -> tuple[str, str]:
    """Create an account. Returns (username, one-time initial password).

    Exempt from the two-administrator floor: that rule guards removal, and a
    system with no accounts has to be able to bootstrap its first one.

    **A pass-through, and it has to stay one.** ``StaffAdmin.new_staff``
    reaches the same ``create_staff``, and every rule about what a username may
    be, what is refused, and what is written to ``audit_log`` lives there.
    A check added here would be a check the panel does not have, and the path
    with fewer of them is the one that ends up mattering. This function exists
    only to keep ``main()`` from holding a service call inline, the same shape
    ``cmd_reset_mfa`` and ``cmd_issue_password`` have.

    ``create_staff`` flushes on its own behalf (it needs the primary key for
    its audit entry), so there is no flush here.
    """
    staff, password = create_staff(
        db_session,
        username=username,
        display_name=display_name,
        role=role,
        actor=actor,
    )
    return staff.username, password


# Both recovery commands below pass `allow_self=True` to admin/accounts.py's
# self-recovery guard. This is the one exemption from it, and it is
# deliberate.
#
# The guard refuses `issue_password`/`reset_mfa` aimed at the account
# performing them, because from a stolen session that pair is a complete
# account takeover rather than recovery. That reasoning needs an account
# performing them. Here there is none: layer L3 is what layer L2 (the panel,
# one administrator recovering another) falls back to when *every*
# administrator is locked out and there is no second party left to be, and
# whoever runs it already holds shell access to the server and the database
# behind it. A guard here would refuse the last way back in while stopping an
# attacker who, by definition, no longer needs this command for anything.
#
# Passed explicitly rather than left to fall out of `actor="cli"` never
# matching a username. It would, today, for every username except `cli`
# itself - `staff.username` is a plain VARCHAR with no reserved values, so
# that account can exist, and the only command able to recover it would be
# the one that refused. An exemption nobody wrote down is an accident that
# reads like a decision; this one is a decision.


def cmd_reset_mfa(db_session: Session, username: str) -> None:
    """Recovery layer L3: clear an enrolment from the server."""
    reset_mfa(db_session, username, actor="cli", allow_self=True)


def cmd_issue_password(db_session: Session, username: str) -> str:
    """Recovery layer L3: issue a password from the server when no
    administrator can. Returns the plaintext, shown once."""
    return issue_password(db_session, username, actor="cli", allow_self=True)


def cmd_delete_staff(db_session: Session, username: str) -> dict:
    """Remove an account outright. Returns what was destroyed.

    **A pass-through, and it has to stay one**, the same statement
    ``cmd_create_staff`` carries from the other side. ``StaffAdmin.delete_page``
    reaches the same ``delete_staff``, and every rule — the two-administrator
    floor, the refusal of self-deletion, the requirement that the account be
    deactivated first — lives there. A check added here would be a check the
    panel does not have, and the path with fewer of them is the one that ends
    up mattering.

    ``allow_self=True``, for the reason ``cmd_reset_mfa`` and
    ``cmd_issue_password`` already carry it and no more: layer L3 has no acting
    session to be the second party, and whoever runs this already holds shell
    access to the database behind it. **It is the only guard the exemption
    reaches.** The floor and the deactivation requirement are unconditional
    here exactly as they are in the panel — the CLI is the way back in when
    every administrator is locked out, which is an argument for skipping the
    *second party*, not for letting a server-side command leave the deployment
    with one administrator.
    """
    return delete_staff(db_session, username, actor="cli", allow_self=True)


def cmd_reactivate_staff(db_session: Session, username: str) -> None:
    """Let a deactivated account log in again. Pass-through, as above."""
    reactivate_staff(db_session, username)


def cmd_rotate_key(db_session: Session, *, old_key: str, new_key: str) -> tuple[int, int]:
    """Re-encrypt every stored TOTP secret, and clear the blocklist, under a
    new SECRET_KEY. Returns (secrets re-encrypted, blocks cleared).

    Decrypts everything before writing anything. A partial rotation would
    leave some secrets readable only with the old key and some only with the
    new one, with no key that opens the whole table — unrecoverable without
    resetting every account.

    **Why the blocklist is cleared rather than re-keyed.** ``SECRET_KEY`` also
    derives the HMAC key `ip_block.ip_hmac` is computed under
    (``db/blocklist.py``'s ``BLOCKLIST_INFO``). A TOTP secret can be carried
    across a rotation because it is *encrypted* — decryptable with the old
    key, re-encryptable with the new one. A fingerprint cannot: an HMAC is
    one-way, so with the plaintext address gone there is nothing to
    re-fingerprint from. That is the whole point of storing it that way
    (§2.3), and it is why re-keying is not an option here.

    So every `ip_block` row survives a rotation as an unreachable value:
    ``is_blocked`` computes a fingerprint under the new key and matches
    nothing, ``python -m admin.cli unblock`` cannot remove the row either
    (it recomputes the same new-key fingerprint to find it), and the panel
    goes on listing it as though it were in force. **An unreachable row that
    silently stops blocking is worse than no row** — it is a protection an
    operator believes they have and does not. Deleting them makes the
    consequence of a rotation visible and actionable: the caller prints how
    many were cleared and says they must be re-applied.

    No audit entry is written for the deletion. ``write_audit`` stamps an
    ``actor``, and every row here is being removed by a key change rather
    than by a person's decision about any particular one; the operator's own
    printed output and this docstring are the record. (Contrast
    ``cmd_unblock``, which *is* one person's decision about one row.)
    """
    # Every device row, not every account. Since contract v1.13 the secrets
    # live on `staff_totp_device` and one account can hold several - a
    # rotation that walked accounts and re-encrypted "the" secret would
    # leave every second phone readable only with the old key, which is
    # precisely the half-rotated state the all-at-once ordering below exists
    # to prevent. `secret_enc` is NOT NULL, so there is no "has a secret"
    # predicate left to write: a device row exists because a secret was
    # minted for it. Unconfirmed devices are included deliberately - an
    # abandoned scan that is resumed after a rotation must still decrypt.
    devices = db_session.scalars(select(StaffTotpDevice)).all()

    # Decrypt all first; a failure here must leave the table untouched -
    # including the blocklist, which is why the delete below comes after this
    # comprehension rather than before it.
    plaintext = [
        (device, decrypt_totp_secret(device.secret_enc, secret_key=old_key))
        for device in devices
    ]

    for device, secret in plaintext:
        device.secret_enc = encrypt_totp_secret(secret, secret_key=new_key)

    blocks = db_session.scalars(select(IpBlock)).all()
    for row in blocks:
        db_session.delete(row)

    return len(plaintext), len(blocks)


def cmd_unblock(db_session: Session, address: str, *, secret_key: str) -> bool:
    """Recovery layer for E-8's blocklist: the server-side way back in.

    Exists for the case the whole stage is designed around not causing: an
    administrator blocks the address they are sitting behind.
    ``ProtectionMiddleware`` (admin/protection.py) refuses the blocklist
    check ahead of every other rule, with no exemption even for an
    authenticated staff session, so there is no page left to click - the
    panel itself is unreachable. There is no email system either, so
    without this command the only way back would be editing the database
    by hand. Returns whether a block was actually removed, so the caller
    can tell "nothing to do" from "done".

    Looks the row up first, by the same fingerprint ``unblock_ip`` will
    recompute, purely so the audit entry below can name what was removed -
    ``unblock_ip`` itself (db/blocklist.py) returns only a bool and writes
    no audit entry, by design: auditing is the caller's job. The CLI
    audits with ``actor="cli"``, the same convention this module's own
    ``cmd_issue_password``/``issue_password`` pairing already uses for a
    write made from the server rather than through a staff session.
    """
    fp = ip_fingerprint(address, secret_key=secret_key)
    row = db_session.scalar(select(IpBlock).where(IpBlock.ip_hmac == fp))
    if row is None:
        return False

    before = {
        "reason": row.reason,
        "created_by": row.created_by,
        "created_at": row.created_at,
        "expires_at": row.expires_at,
    }
    row_id = row.id

    removed = unblock_ip(db_session, address, actor="cli", secret_key=secret_key)
    if removed:
        write_audit(
            db_session, actor="cli", action="delete", table_name="ip_block",
            row_id=row_id, before=before, after=None,
        )
    return removed


def cmd_seed_taxonomy(db_session: Session) -> dict[str, int]:
    """Load the NZ taxonomy into an empty or partially populated database."""
    return seed_taxonomy(db_session)


def report_bootstrap_result(created: list[tuple[str, str]]) -> None:
    """Print freshly created bootstrap credentials to standard output.

    Contract 8.3, "Bootstrap": the passwords are "randomly generated per
    deployment, printed once to standard output" and cannot be recovered
    afterwards. This is the only moment they exist in readable form, so the
    wording has to carry that - an operator who does not realise it will
    close the terminal.

    Shared with ``admin.app``'s startup hook rather than written twice.
    Both the CLI subcommand and application start reach the same
    ``ensure_bootstrap_admins`` and must therefore say the same thing; two
    copies would drift, and the copy an operator actually sees depends on
    which of the two paths their deployment used.

    Silent on an empty list. That is the ordinary case - every restart of an
    already-bootstrapped deployment, and every test that builds an app -
    and there is nothing to report. The CLI adds its own "nothing to do"
    line because a subcommand run by hand owes the person an acknowledgement;
    a start-up does not.
    """
    if not created:
        return
    print("Created initial administrator accounts.")
    for username, password in created:
        print(f"  {username}: {password}")
    print()
    print(
        "These passwords are shown once and cannot be recovered. Log in "
        "with both accounts now, change both passwords, and enrol both "
        "authenticators. Do not send them by email."
    )


def _build_parser() -> argparse.ArgumentParser:
    # Both spellings reach this parser: `kaicalc-admin <command>` from an
    # installed package (pyproject.toml's [project.scripts]) and
    # `python -m admin.cli <command>` from a checkout. argparse's own default
    # for prog would print "cli.py", which is neither, so name the one an
    # operator is most likely to have - the console script, which is what
    # `docker exec` uses - and note the other alongside it.
    parser = argparse.ArgumentParser(
        prog="kaicalc-admin",
        epilog="Without the package installed, run the same commands as "
        "`python -m admin.cli <command>` from a checkout.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-staff", help="Create a staff account")
    create.add_argument("username")
    create.add_argument("display_name")
    create.add_argument(
        "--admin", action="store_true", help="Create an administrator account"
    )

    reset = sub.add_parser("reset-mfa", help="Clear an account's MFA enrolment")
    reset.add_argument("username")

    issue = sub.add_parser(
        "issue-password", help="Issue a random password and force a change at next login"
    )
    issue.add_argument("username")

    delete = sub.add_parser(
        "delete-staff",
        help="Delete a deactivated account outright, with its authenticators "
        "and recovery codes. What it did stays in the audit log",
    )
    delete.add_argument("username")

    reactivate = sub.add_parser(
        "reactivate-staff", help="Let a deactivated account log in again"
    )
    reactivate.add_argument("username")

    rotate = sub.add_parser("rotate-key", help="Re-encrypt TOTP secrets")
    rotate.add_argument("--old", required=True)
    rotate.add_argument("--new", required=True)

    unblock = sub.add_parser(
        "unblock", help="Remove a block from an address (E-8's escape hatch)"
    )
    unblock.add_argument("address")

    sub.add_parser(
        "bootstrap", help="Create the initial administrator accounts if none exist"
    )

    sub.add_parser(
        "seed-taxonomy",
        help="Load the NZ food loss and waste taxonomy into an empty or "
        "partially populated database",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    settings = load_settings()
    factory = create_session_factory(settings.database_url)

    with factory() as db_session:
        if args.command == "create-staff":
            role = StaffRole.admin if args.admin else StaffRole.staff
            try:
                username, password = cmd_create_staff(
                    db_session, args.username, args.display_name, role, actor="cli"
                )
            except (
                DuplicateUsernameError,
                InvalidDisplayNameError,
                InvalidUsernameError,
            ) as exc:
                # Before this, a name already in use reached the operator as
                # an IntegrityError traceback out of the flush, which reads
                # like the command is broken rather than like the argument is
                # - the same reason `unblock` catches InvalidAddressError.
                print(f"Refused: {exc}")
                return 1
            db_session.commit()
            print(f"Created {username} ({role.value}).")
            print(f"Initial password: {password}")
            print(
                "Hand this over in person or by phone. Do not send it by email, "
                "and do not reuse it."
            )
            print(
                "If it is lost before they use it, the account is not: run "
                f"`kaicalc-admin issue-password {username}` for another one."
            )
        elif args.command == "reset-mfa":
            cmd_reset_mfa(db_session, args.username)
            db_session.commit()
            print(
                f"Cleared MFA for {args.username}. They will be asked to enrol "
                "again on their next login."
            )
        elif args.command == "issue-password":
            try:
                password = cmd_issue_password(db_session, args.username)
            except UnknownStaffError:
                print(f"No such account: {args.username}")
                return 1
            db_session.commit()
            print(f"Issued a new password for {args.username}.")
            print(f"  Password: {password}")
            print("  They must change it at their next login. Hand it over in person.")
        elif args.command == "delete-staff":
            try:
                removed = cmd_delete_staff(db_session, args.username)
            except UnknownStaffError:
                print(f"No such account: {args.username}")
                return 1
            except (AccountStillActiveError, LastAdministratorsError) as exc:
                # Refused rather than crashed, the same treatment
                # `create-staff` gives a name already in use: an operator
                # reaching for this is usually tidying up, and a traceback out
                # of a service function reads like the command is broken
                # rather than like the account is not ready to be removed.
                print(f"Refused: {exc}")
                return 1
            db_session.commit()
            print(f"Deleted {removed['username']} ({removed['role']}).")
            print(
                f"  Destroyed {removed['totp_devices_destroyed']} "
                f"authenticator(s) and {removed['recovery_codes_destroyed']} "
                "recovery code(s) with it."
            )
            print(
                "  Everything the account did stays in the audit log and still "
                "names it. The username is now free to reuse."
            )
        elif args.command == "reactivate-staff":
            try:
                cmd_reactivate_staff(db_session, args.username)
            except UnknownStaffError:
                print(f"No such account: {args.username}")
                return 1
            db_session.commit()
            print(
                f"Reactivated {args.username}. Their password and authenticator "
                "are unchanged, so they can log in as before."
            )
        elif args.command == "rotate-key":
            count, cleared = cmd_rotate_key(
                db_session, old_key=args.old, new_key=args.new
            )
            db_session.commit()
            print(f"Re-encrypted {count} TOTP secret(s).")
            if cleared:
                print(
                    f"Cleared {cleared} IP block(s). A block is stored as an "
                    "HMAC of the address under a key derived from SECRET_KEY, "
                    "and an HMAC cannot be re-keyed - the rows would have "
                    "survived the rotation matching nobody, and could not "
                    "have been removed with `unblock` either. Re-apply any "
                    "that are still needed from /admin/ip-block/block."
                )
            print("Update SECRET_KEY in .env and restart the application.")
        elif args.command == "unblock":
            try:
                removed = cmd_unblock(
                    db_session, args.address, secret_key=settings.secret_key
                )
            except InvalidAddressError as exc:
                # An operator reaching for this command is usually locked out
                # and in a hurry. Without this branch a mistyped address left
                # them staring at an ipaddress traceback from four frames
                # down, which reads like the command is broken rather than
                # like the argument is. Echoing the address back is safe here
                # and nowhere else: this is their own terminal, not a page
                # every staff member can read.
                print(f"{args.address!r} is not a valid address. {exc}")
                return 1
            db_session.commit()
            if removed:
                print(f"Unblocked {args.address}.")
            else:
                print(f"No block found for {args.address}. Nothing to do.")
        elif args.command == "bootstrap":
            created = ensure_bootstrap_admins(db_session)
            db_session.commit()
            if not created:
                print("Administrator accounts already exist. Nothing to do.")
            report_bootstrap_result(created)
        elif args.command == "seed-taxonomy":
            try:
                created = cmd_seed_taxonomy(db_session)
            except TaxonomyInvariantError as exc:
                print(f"Refused to seed: {exc}")
                return 1
            db_session.commit()
            print("Seeded the taxonomy.")
            for table, count in created.items():
                print(f"  {table:<18}{count:>2} created")

    return 0


if __name__ == "__main__":
    sys.exit(main())
