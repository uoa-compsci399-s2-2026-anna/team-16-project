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
kaicalc-admin bootstrap

echo
# Single-quoted: a backtick inside a double-quoted string is command
# substitution, and `docker compose logs migrate` inside the container printed
# "docker: not found" in the middle of the sentence.
echo 'Database ready. The administrator passwords above are shown ONCE and'
echo 'cannot be recovered - `docker compose logs migrate` will still have'
echo 'them until the container is removed.'
