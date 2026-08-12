#!/bin/sh
#
# Container entrypoint for kaicalc-api, kaicalc-admin and the migrate job.
#
# It does exactly one thing before handing over: it makes sure SECRET_KEY is
# set, to the SAME value in every container of the deployment, and to a value
# that survives a restart. Then it execs whatever it was given.
#
# WHY THIS EXISTS AT ALL
#
# Both applications refuse to start without SECRET_KEY (api/app.py raises
# "SECRET_KEY is required"; admin/config.py raises MissingSettingError), and
# they are right to: the blocklist fingerprint of contract 2.3 is
# HMAC(address) under a key derived from it, and an app that cannot compute a
# fingerprint is an app where a block applied in the panel silently does
# nothing. So a first `docker compose up` on a clean machine would fail on a
# missing secret unless something generates one.
#
# Two failure modes have to be avoided together, and only one of them is
# obvious:
#
#   1. No secret at all -> the stack does not start. Obvious, loud, fixable.
#   2. A FRESH secret on every restart, or a DIFFERENT secret per container.
#      Silent. Every staff session is invalidated on restart, every enrolled
#      TOTP secret becomes undecryptable, and - the one nothing raises on -
#      api and admin derive DIFFERENT fingerprints for the same address, so a
#      block applied in the panel never matches at /api/v1/, with no error on
#      either side.
#
# Hence: one file, in a named volume shared by every service, created once.
#
# HOW IT IS CREATED WITHOUT A RACE
#
# In the shipped topology only the `migrate` service is running when the file
# is first needed, and api/admin wait on it with
# `condition: service_completed_successfully`, so there is no concurrency to
# lose. The `ln` below makes it safe anyway: hard-linking onto an existing
# name fails atomically, so two processes racing cannot produce two secrets -
# the loser's temporary file is discarded and it reads the winner's value.
# `mv` would NOT be safe here; it would silently overwrite.
#
# An explicitly supplied SECRET_KEY always wins and nothing is generated. That
# is the production path: put a real secret in the environment (or in a Docker
# secret file read into it) and this script becomes a no-op.

set -eu

STATE_DIR=${KAICALC_STATE_DIR:-/var/lib/kaicalc}
KEY_FILE="$STATE_DIR/secret_key"

if [ -z "${SECRET_KEY:-}" ]; then
    if [ ! -s "$KEY_FILE" ]; then
        mkdir -p "$STATE_DIR" 2>/dev/null || true
        tmp="$KEY_FILE.$$"
        # Subshell so the umask does not leak into the application process.
        # token_urlsafe(48) is 64 characters of base64url over 384 bits.
        ( umask 077; python -c 'import secrets; print(secrets.token_urlsafe(48))' > "$tmp" )
        # Atomic create-if-absent. Failure here means somebody else won the
        # race, which is a success for our purposes: their value is the one.
        ln "$tmp" "$KEY_FILE" 2>/dev/null || true
        rm -f "$tmp"
    fi

    if [ ! -s "$KEY_FILE" ]; then
        echo "kaicalc-entrypoint: SECRET_KEY is unset and $KEY_FILE could not" >&2
        echo "  be created. Mount a writable volume at \$KAICALC_STATE_DIR" >&2
        echo "  (default /var/lib/kaicalc), or set SECRET_KEY explicitly." >&2
        exit 1
    fi

    SECRET_KEY=$(cat "$KEY_FILE")
    export SECRET_KEY
    echo "kaicalc-entrypoint: SECRET_KEY taken from $KEY_FILE (generated on" \
         "first start; delete the volume to rotate, and read the rotate-key" \
         "warning in .env.example before you do)."
fi

# A NOTE FOR `docker exec`
#
# `docker exec` starts a process from the container's CONFIGURED environment
# and inherits nothing this script exported. So
#
#     docker exec kaicalc-admin kaicalc-admin unblock 203.0.113.5
#
# - E-8's escape hatch, the command an operator reaches for while locked out -
# dies with "SECRET_KEY is not set. Copy .env.example to .env and fill it in.",
# which is true and unhelpful inside a container.
#
# Writing a /app/.env for python-dotenv to find does NOT fix it, and this was
# tried before it was written down: load_dotenv() calls find_dotenv(), which
# walks up from the directory of the MODULE THAT CALLED IT
# (site-packages/admin/config.py), not from the working directory. A .env in
# /app is never looked at.
#
# The fix is /usr/local/bin/kaicalc, a two-line wrapper that runs this script
# and then the CLI:
#
#     docker exec kaicalc-admin kaicalc unblock 203.0.113.5
#
# Everything after `kaicalc` is a `kaicalc-admin` subcommand.

exec "$@"
