"""Operational commands.

Contract: docs/interfaces.md 8.3, "Operational commands".

    python -m admin.cli create-staff <username> "<display name>" [--admin]
    python -m admin.cli reset-mfa <username>
    python -m admin.cli issue-password <username>
    python -m admin.cli rotate-key --old <key> --new <key>

These are the break-glass paths that outlive the project team: bootstrapping
the first administrator, recovery layer L3 when every administrator is locked
out, and re-encrypting TOTP secrets after a SECRET_KEY change. They are
covered by tests rather than only exercised by hand for that reason.
"""

import argparse
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from admin.accounts import UnknownStaffError, create_staff, issue_password, reset_mfa
from admin.bootstrap import ensure_bootstrap_admins
from admin.config import load_settings
from admin.models import Staff, StaffRole
from admin.security import decrypt_totp_secret, encrypt_totp_secret
from admin.seed import seed_taxonomy
from admin.taxonomy_rules import TaxonomyInvariantError
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
    """
    staff, password = create_staff(
        db_session,
        username=username,
        display_name=display_name,
        role=role,
        actor=actor,
    )
    db_session.flush()
    return staff.username, password


def cmd_reset_mfa(db_session: Session, username: str) -> None:
    """Recovery layer L3: clear an enrolment from the server."""
    reset_mfa(db_session, username)


def cmd_issue_password(db_session: Session, username: str) -> str:
    """Recovery layer L3: issue a password from the server when no
    administrator can. Returns the plaintext, shown once."""
    return issue_password(db_session, username, actor="cli")


def cmd_rotate_key(db_session: Session, *, old_key: str, new_key: str) -> int:
    """Re-encrypt every stored TOTP secret under a new SECRET_KEY.

    Decrypts everything before writing anything. A partial rotation would
    leave some secrets readable only with the old key and some only with the
    new one, with no key that opens the whole table — unrecoverable without
    resetting every account.
    """
    accounts = db_session.scalars(
        select(Staff).where(Staff.mfa_secret_enc.is_not(None))
    ).all()

    # Decrypt all first; a failure here must leave the table untouched.
    plaintext = [
        (staff, decrypt_totp_secret(staff.mfa_secret_enc, secret_key=old_key))
        for staff in accounts
    ]

    for staff, secret in plaintext:
        staff.mfa_secret_enc = encrypt_totp_secret(secret, secret_key=new_key)

    return len(plaintext)


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
    parser = argparse.ArgumentParser(prog="python -m admin.cli")
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

    rotate = sub.add_parser("rotate-key", help="Re-encrypt TOTP secrets")
    rotate.add_argument("--old", required=True)
    rotate.add_argument("--new", required=True)

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
            username, password = cmd_create_staff(
                db_session, args.username, args.display_name, role, actor="cli"
            )
            db_session.commit()
            print(f"Created {username} ({role.value}).")
            print(f"Initial password: {password}")
            print(
                "Hand this over in person or by phone. Do not send it by email, "
                "and do not reuse it."
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
        elif args.command == "rotate-key":
            count = cmd_rotate_key(db_session, old_key=args.old, new_key=args.new)
            db_session.commit()
            print(f"Re-encrypted {count} TOTP secret(s).")
            print("Update SECRET_KEY in .env and restart the application.")
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
