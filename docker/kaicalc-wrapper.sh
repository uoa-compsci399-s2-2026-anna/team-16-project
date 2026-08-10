#!/bin/sh
#
# Installed as /usr/local/bin/kaicalc. The `docker exec` form of the admin CLI:
#
#     docker exec kaicalc-admin kaicalc unblock 203.0.113.5
#     docker exec kaicalc-admin kaicalc issue-password alice
#     docker exec kaicalc-admin kaicalc create-staff bob "Bob Smith" --admin
#
# Identical to `kaicalc-admin <command>` except that it resolves SECRET_KEY
# first. `docker exec` does not run the image's ENTRYPOINT and inherits none of
# its environment, so the bare console script fails on a missing SECRET_KEY for
# every subcommand that touches settings - which is all of them but --help.
# See docker/entrypoint.sh for why a .env file cannot solve this.
#
# Arguments are forwarded untouched, including `--`.

exec /usr/local/bin/kaicalc-entrypoint kaicalc-admin "$@"
