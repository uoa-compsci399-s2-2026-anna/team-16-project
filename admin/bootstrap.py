"""First-start administrator accounts.

Contract: docs/interfaces.md 8.3, "Bootstrap".

Called once at application start. A deployment then satisfies the
two-administrator rule from the moment it comes up, rather than depending on
whoever installs it remembering to run the CLI twice — and a system that
starts with a single administrator is a system one lost phone can lock out.

**There is no default password in this file, and there must never be one.**
Every password is generated per deployment by ``accounts.create_staff`` and
printed exactly once. A fixed admin/admin on a public panel is precisely how
community-sector accounts get taken over, and is the reason MFA is mandatory
in this system at all.
"""

from sqlalchemy.orm import Session

from admin.accounts import count_active_admins, create_staff
from admin.models import StaffRole

#: Two, so that the "at least two administrators" invariant holds from the
#: first start. The second is the break-glass account.
BOOTSTRAP_USERNAMES = ("admin", "admin2")

_DISPLAY_NAMES = {
    "admin": "Administrator",
    "admin2": "Second administrator (recovery)",
}


def ensure_bootstrap_admins(
    session: Session, *, secret_key: str
) -> list[tuple[str, str]]:
    """Create the initial administrator accounts if none exist.

    ``secret_key`` is threaded through to ``create_staff``, which stores each
    account's initial password encrypted under it until the account is claimed
    (contract v1.15 item 3). Required rather than defaulted: this runs at
    application start, where ``Settings`` has already been loaded and refuses
    to exist without ``SECRET_KEY``, so there is nothing to default *to* that
    would not be a second, wrong key.

    A practical consequence worth knowing: the two bootstrap passwords are
    printed once into ``docker compose logs`` and are now also recoverable from
    the panel by either administrator, until whichever of them logs in first
    changes theirs. Since a fresh deployment's most common failure is losing
    those two lines in the start-up output, that is the case this was asked for.

    Returns ``[(username, password), ...]`` for accounts actually created, in
    plaintext — this is the only moment those passwords are readable. The
    caller prints them once and never stores them.

    Returns an empty list when any active administrator already exists, so a
    restart creates nothing. Accounts that are not administrators are ignored:
    a system holding only staff accounts is still locked out and still needs
    bootstrapping.
    """
    if count_active_admins(session) > 0:
        return []

    created: list[tuple[str, str]] = []
    for username in BOOTSTRAP_USERNAMES:
        staff, password = create_staff(
            session,
            username=username,
            display_name=_DISPLAY_NAMES[username],
            role=StaffRole.admin,
            actor="bootstrap",
            secret_key=secret_key,
        )
        created.append((staff.username, password))

    session.flush()
    return created
