#!/bin/sh
#
# The `migrate` service's whole job: bring an empty database up to a state in
# which the calculator works, then exit 0.
#
# Everything here runs ONCE, from ONE process, before `api` or `admin` starts.
# That is enforced in the topology, not by convention: compose.yaml gives this
# service `depends_on: db: service_healthy`, and both app services depend on
# THIS service with `condition: service_completed_successfully`.
#
# WHY ONE PROCESS MATTERS. MySQL autocommits DDL. Two replicas running
# `alembic upgrade head` concurrently do not deadlock and do not fail cleanly -
# they interleave, and the result is a half-applied schema stamped at a
# revision it does not actually match. Recovery is DROP DATABASE. There is no
# migration-on-startup hook anywhere in this system for exactly that reason.
#
# `set -e` is what makes each step a precondition of the next. If the migration
# fails, nothing is seeded; if seeding fails, no administrator is created; and
# the service exits non-zero, so `service_completed_successfully` is not
# satisfied and api/admin never start against a broken database.

set -eu

echo "==> 1/4 alembic upgrade head"
# run.sh resolves alembic.ini beside itself first, then
# <sys.prefix>/share/kaicalc/alembic.ini. In this image only the second exists
# (the migration tree ships as wheel data-files - see pyproject.toml), and the
# ini's `script_location = %(here)s/alembic` resolves against its own location,
# which is why one unmodified ini works in both a checkout and an install.
/app/run.sh migrate

echo "==> 2/4 kaicalc-admin seed-taxonomy"
# Idempotent: matches on `code`, creates what is missing, and never updates an
# existing row. A staff member who renames a destination in the panel keeps
# their name across every later deployment.
kaicalc-admin seed-taxonomy

echo "==> 3/4 mock factor set"
# Not part of admin/cli.py - see the header of seed_mock_factors.py for why it
# is here and why it should eventually move. Does nothing if any factor set
# already exists.
python /app/seed_mock_factors.py

echo "==> 4/4 kaicalc-admin bootstrap"
# Creates the two initial administrator accounts if none exist and prints
# their one-time passwords. Run HERE, in a service that runs once, rather than
# left to admin/app.py's start-up hook, for one reason: this output has to be
# findable. A lifespan hook prints into whichever replica happened to start
# first, and it prints again in a rebuilt container that finds an empty
# database. `docker compose logs migrate` is one place, always.
#
# The panel's own start-up hook still runs and is still the safety net; it is
# idempotent and prints nothing when accounts already exist, which after this
# step they do.
#
# CAPTURED rather than streamed, and the reason is the block below. This script
# used to close by telling every operator that the passwords were "printed
# above" - unconditionally, on every restart of an already-bootstrapped
# deployment, where nothing had printed at all. The sentence was false on every
# run but the first, and it is read by exactly the person hunting for a password
# that was never there.
#
# `kaicalc-admin bootstrap` cannot signal which run this is any other way: it
# exits 0 in both cases, and it has to, because `set -e` above means a non-zero
# exit aborts the migrate job and stops the whole stack. Its output is the only
# discriminator - `report_bootstrap_result` is silent unless it created
# something. `BOOTSTRAP_CREATED_MARKER` in admin/cli.py is that line, and
# `test_operator_guidance.py` fails if this file and that constant stop
# agreeing, in either direction.
#
# Spell that file's name WITHOUT its directory, here and anywhere else in this
# script. The test that reads it scans this file as plain text - shell gives it
# no AST to work with - and treats every `/admin/<segment>` it finds as a panel
# URL an operator is being told to open, then fails if the panel does not serve
# it. A test path written in full contains one, inside `tests` + `/admin/` +
# the file name, so the guard dutifully reports that this script sends
# operators to a 404. The guard is right; the comment was wrong. Twice.
#
# The passwords live in a shell variable for the length of one `printf` and land
# in exactly the log they would have streamed to, so this is not a new place
# they exist. It is never exported; no child process inherits it. The cost is
# that this step's output appears all at once rather than line by line, which
# for a command that takes well under a second is not a cost.
BOOTSTRAP_OUTPUT="$(kaicalc-admin bootstrap)"
printf '%s\n' "$BOOTSTRAP_OUTPUT"

echo
# Single-quoted: a backtick inside a double-quoted string is command
# substitution, and `docker compose logs migrate` inside the container printed
# "docker: not found" in the middle of the sentence.
case "$BOOTSTRAP_OUTPUT" in
*'Created initial administrator accounts.'*)
echo 'Database ready. The administrator passwords above are printed here and'
echo 'nowhere else - `docker compose logs migrate` will still have them, but'
echo 'only until this container is removed OR REBUILT: compose recreates it'
echo 'whenever its image changes, and a recreated container starts with an'
echo 'empty log. Read them now.'
echo 'They are also kept encrypted until each account sets a password of its'
echo 'own, so one lost line can be read back by the OTHER administrator from'
echo '/admin/staff/list. Losing both means nobody can log in: run'
# `kaicalc`, not `kaicalc-admin` - and the difference is not cosmetic here even
# though the bootstrap call above invokes `kaicalc-admin` correctly. That call
# runs inside this script, which the image's ENTRYPOINT started, so SECRET_KEY
# is already resolved. The reader of THIS sentence is at a host shell and will
# reach for `docker exec`, which does not run the entrypoint and inherits none
# of the environment it builds; the bare console script then dies with
# `MissingSettingError: SECRET_KEY is not set`. `kaicalc` is the wrapper that
# resolves the secret first (docker/kaicalc-wrapper.sh).
#
# This sentence is read by somebody locked out of the panel, so the one form it
# names has to be the one that works from where they are standing. Do not
# "correct" it to match the bootstrap call above. Both branches below say it,
# because both are read by somebody who has lost a password.
echo '`docker exec kaicalc-admin kaicalc issue-password admin` from the host.'
;;
*)
echo 'Database ready. Administrator accounts already existed, so this run'
echo 'created none and printed NO passwords - there is nothing above to find.'
echo 'That is the ordinary state of every start after the first.'
echo 'A password an account has not yet claimed can still be read by the OTHER'
echo 'administrator from /admin/staff/list. Once it has been claimed, or if'
echo 'both are lost, mint a fresh one: run'
echo '`docker exec kaicalc-admin kaicalc issue-password admin` from the host.'
;;
esac
