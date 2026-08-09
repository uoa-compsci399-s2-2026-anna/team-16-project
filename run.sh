#!/usr/bin/env sh
#
# Kai Commitment Impact Calculator - launcher (POSIX sh).
# run.ps1 is the equivalent for Windows PowerShell; keep the two in step.
#
#   ./run.sh api      [extra uvicorn arguments...]
#   ./run.sh admin    [extra uvicorn arguments...]
#   ./run.sh migrate  [alembic arguments...]      (default: upgrade head)
#
# Everything after the subcommand is forwarded untouched. `./run.sh api
# --reload`, `--workers 4`, `--log-level debug`, `--ssl-keyfile ...` all reach
# uvicorn exactly as typed. A launcher that swallowed them would be bypassed
# within a week and then rot.
#
# Ports (both overridable by --port, see below):
#
#   api      KAICALC_API_PORT      default 18000
#   admin    KAICALC_ADMIN_PORT    default 18001
#   host     KAICALC_HOST          default 127.0.0.1
#
# Nothing here defaults to 80, 8000, 8080, 3000 or 5000. A clean machine is
# very likely to have something on all of them already, and a port clash on
# first run is the out-of-the-box failure this launcher exists to prevent.
# 18080 is reserved for the reverse proxy - do not take it.
#
# THREE DEPLOYMENT FACTS THIS SCRIPT IS SHAPED BY
#
# 1. `migrate` is a separate subcommand, and it must run ONCE, from ONE
#    process, before `api` or `admin` starts. MySQL autocommits DDL, so two
#    replicas migrating concurrently leave a half-applied schema whose only
#    reliable recovery is DROP DATABASE. This is why nothing here migrates on
#    start-up, and why `api` and `admin` do not depend on it having happened.
#
# 2. SECRET_KEY must be the SAME value for `api` and `admin`. Both derive the
#    blocklist fingerprint key from it independently with a pinned info
#    string, so two different secrets produce two different fingerprints for
#    one address: a block applied in the panel silently fails to hold at the
#    API, with nothing raised on either side. One .env, both processes.
#
# 3. PROTECTION_TRUSTED_PROXY defaults to false, and there will be a proxy in
#    front. Left false behind one, every caller arrives as the proxy's own
#    address: the per-address rate limit collapses into a single global
#    bucket, and one block denies every visitor. Set it to true only once a
#    proxy that overwrites X-Forwarded-For itself is genuinely in front.
#
# See .env.example and docs/architecture.md 9.1 / 9.1.1 for the long form.

set -eu

# Directory this script lives in - the application root. Resolved so the
# script works when invoked by an absolute path, through a symlink on PATH,
# or from any working directory.
ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)

# The interpreter everything is launched through. Set KAICALC_PYTHON to pick a
# specific one; otherwise `python`, falling back to `python3` for the
# distributions that ship only that name.
#
# Everything below runs as `$PYTHON -m uvicorn` / `$PYTHON -m alembic` rather
# than as the bare `uvicorn` / `alembic` console scripts. Two reasons, and the
# second one is not theoretical:
#
#   - it guarantees the tool that runs is the one installed in the same
#     environment as the application, not whichever happens to be first on
#     PATH; and
#   - this script does `cd "$ROOT"`, and $ROOT contains a DIRECTORY named
#     `alembic`. On any shell whose PATH has an empty component (a stray `::`
#     or trailing `:`, which means "the working directory"), bare `alembic`
#     resolves to that directory and the launcher dies with
#     "exec: alembic: cannot execute: Is a directory". Observed here, not
#     imagined.
PYTHON=${KAICALC_PYTHON:-}
if [ -z "$PYTHON" ]; then
    if command -v python >/dev/null 2>&1; then
        PYTHON=python
    elif command -v python3 >/dev/null 2>&1; then
        PYTHON=python3
    else
        echo "run.sh: no python interpreter on PATH. Set KAICALC_PYTHON." >&2
        exit 1
    fi
fi

usage() {
    cat <<EOF
usage: run.sh <api|admin|migrate> [arguments...]

  api      [uvicorn args]   serve the public API   (default port ${KAICALC_API_PORT:-18000})
  admin    [uvicorn args]   serve the staff panel  (default port ${KAICALC_ADMIN_PORT:-18001})
  migrate  [alembic args]   alembic upgrade head, or the arguments given

Run migrate once, from one process, before starting either server.
EOF
}

# Locate alembic.ini. In a checkout or a container built from one it sits
# beside this script; in a wheel-only install it is under the environment's
# share/kaicalc/, installed alongside the migration tree in the same relative
# arrangement so that the ini's own `script_location = %(here)s/alembic`
# resolves either way.
resolve_alembic_ini() {
    if [ -n "${KAICALC_ALEMBIC_INI:-}" ]; then
        printf '%s\n' "$KAICALC_ALEMBIC_INI"
        return 0
    fi
    if [ -f "$ROOT/alembic.ini" ]; then
        printf '%s\n' "$ROOT/alembic.ini"
        return 0
    fi
    prefix=$("$PYTHON" -c 'import sys; print(sys.prefix)' 2>/dev/null) || prefix=''
    if [ -n "$prefix" ] && [ -f "$prefix/share/kaicalc/alembic.ini" ]; then
        printf '%s\n' "$prefix/share/kaicalc/alembic.ini"
        return 0
    fi
    echo "run.sh: cannot find alembic.ini (looked beside this script and in" >&2
    echo "        \$(\$PYTHON -c 'import sys;print(sys.prefix)')/share/kaicalc)." >&2
    echo "        Set KAICALC_ALEMBIC_INI to its path." >&2
    return 1
}

# True when the caller already passed the named uvicorn flag, in either
# `--flag value` or `--flag=value` form. If they did, we inject nothing and
# their argument is forwarded untouched.
has_flag() {
    want=$1
    shift
    for arg in "$@"; do
        case "$arg" in
            "$want" | "$want"=*) return 0 ;;
        esac
    done
    return 1
}

serve() {
    target=$1
    default_port=$2
    shift 2

    if ! has_flag --port "$@"; then
        set -- --port "$default_port" "$@"
    fi
    if ! has_flag --host "$@"; then
        set -- --host "${KAICALC_HOST:-127.0.0.1}" "$@"
    fi

    # Both apps are factories, not module-level `app` objects.
    exec "$PYTHON" -m uvicorn "$target" --factory "$@"
}

command=${1:-}
if [ $# -gt 0 ]; then
    shift
fi

# Run from the application root, so that .env is found: python-dotenv searches
# from the working directory upwards, and .env lives here. Nothing else
# depends on it - admin/app.py resolves its templates and static files
# relative to its own package directory, so the panel itself starts from
# anywhere.
cd "$ROOT"

# A checkout that has had `pip install -r requirements.txt` run but not
# `pip install .` has no kaicalc on sys.path; an installed environment has it
# twice, from the same files. Harmless either way, and it means the launcher
# works before the package is installed.
PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONPATH

case "$command" in
    api)
        serve api.app:create_app "${KAICALC_API_PORT:-18000}" "$@"
        ;;
    admin)
        serve admin.app:create_app "${KAICALC_ADMIN_PORT:-18001}" "$@"
        ;;
    migrate)
        ini=$(resolve_alembic_ini)
        if [ $# -eq 0 ]; then
            exec "$PYTHON" -m alembic -c "$ini" upgrade head
        fi
        exec "$PYTHON" -m alembic -c "$ini" "$@"
        ;;
    -h | --help | help | '')
        usage
        ;;
    *)
        echo "run.sh: unknown subcommand '$command'" >&2
        usage >&2
        exit 2
        ;;
esac
