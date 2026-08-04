"""Operational commands.

Contract: docs/interfaces.md 8.3, "Operational commands".

    python -m admin.cli create-staff <username> "<display name>" [--admin]
    python -m admin.cli reset-mfa <username>
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

from admin.accounts import create_staff, reset_mfa
from admin.bootstrap import ensure_bootstrap_admins
from admin.config import load_settings
from admin.models import Staff, StaffRole
from admin.security import decrypt_totp_secret, encrypt_totp_secret
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

    rotate = sub.add_parser("rotate-key", help="Re-encrypt TOTP secrets")
    rotate.add_argument("--old", required=True)
    rotate.add_argument("--new", required=True)

    sub.add_parser(
        "bootstrap", help="Create the initial administrator accounts if none exist"
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
            else:
                print("Created initial administrator accounts.")
                for username, password in created:
                    print(f"  {username}: {password}")
                print()
                print(
                    "These passwords are shown once and cannot be recovered. Log in "
                    "with both accounts now, change both passwords, and enrol both "
                    "authenticators. Do not send them by email."
                )

    return 0


if __name__ == "__main__":
    sys.exit(main())
