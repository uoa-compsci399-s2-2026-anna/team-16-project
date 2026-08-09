<#
Kai Commitment Impact Calculator - launcher (Windows PowerShell).
run.sh is the equivalent for POSIX shells; keep the two in step.

    .\run.ps1 api      [extra uvicorn arguments...]
    .\run.ps1 admin    [extra uvicorn arguments...]
    .\run.ps1 migrate  [alembic arguments...]      (default: upgrade head)

Everything after the subcommand is forwarded untouched. `.\run.ps1 api
--reload`, `--workers 4`, `--log-level debug`, `--ssl-keyfile ...` all reach
uvicorn exactly as typed. A launcher that swallowed them would be bypassed
within a week and then rot.

Ports (both overridable by --port, see below):

    api      KAICALC_API_PORT      default 18000
    admin    KAICALC_ADMIN_PORT    default 18001
    host     KAICALC_HOST          default 127.0.0.1

Nothing here defaults to 80, 8000, 8080, 3000 or 5000. A clean machine is very
likely to have something on all of them already, and a port clash on first run
is the out-of-the-box failure this launcher exists to prevent. 18080 is
reserved for the reverse proxy - do not take it.

THREE DEPLOYMENT FACTS THIS SCRIPT IS SHAPED BY

1. `migrate` is a separate subcommand, and it must run ONCE, from ONE process,
   before `api` or `admin` starts. MySQL autocommits DDL, so two replicas
   migrating concurrently leave a half-applied schema whose only reliable
   recovery is DROP DATABASE. This is why nothing here migrates on start-up,
   and why `api` and `admin` do not depend on it having happened.

2. SECRET_KEY must be the SAME value for `api` and `admin`. Both derive the
   blocklist fingerprint key from it independently with a pinned info string,
   so two different secrets produce two different fingerprints for one
   address: a block applied in the panel silently fails to hold at the API,
   with nothing raised on either side. One .env, both processes.

3. PROTECTION_TRUSTED_PROXY defaults to false, and there will be a proxy in
   front. Left false behind one, every caller arrives as the proxy's own
   address: the per-address rate limit collapses into a single global bucket,
   and one block denies every visitor. Set it to true only once a proxy that
   overwrites X-Forwarded-For itself is genuinely in front.

See .env.example and docs/architecture.md 9.1 / 9.1.1 for the long form.
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string] $Command = '',

    # Untouched pass-through. ValueFromRemainingArguments keeps --reload,
    # --workers 4 and friends intact instead of trying to bind them.
    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]] $Rest
)

$ErrorActionPreference = 'Stop'

# Directory this script lives in - the application root.
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

# The interpreter everything is launched through. Set KAICALC_PYTHON to pick a
# specific one; otherwise `python`.
#
# Everything below runs as `python -m uvicorn` / `python -m alembic` rather
# than as the bare `uvicorn` / `alembic` console scripts, matching run.sh.
# It guarantees the tool that runs is the one installed in the same
# environment as the application, not whichever is first on PATH - and this
# script sets the location to $Root, which contains a DIRECTORY named
# `alembic`, a name collision the POSIX launcher was observed to trip over.
$Python = if ($env:KAICALC_PYTHON) { $env:KAICALC_PYTHON } else { 'python' }

function Show-Usage {
    $api = if ($env:KAICALC_API_PORT) { $env:KAICALC_API_PORT } else { '18000' }
    $adm = if ($env:KAICALC_ADMIN_PORT) { $env:KAICALC_ADMIN_PORT } else { '18001' }
    @"
usage: run.ps1 <api|admin|migrate> [arguments...]

  api      [uvicorn args]   serve the public API   (default port $api)
  admin    [uvicorn args]   serve the staff panel  (default port $adm)
  migrate  [alembic args]   alembic upgrade head, or the arguments given

Run migrate once, from one process, before starting either server.
"@
}

# Locate alembic.ini. In a checkout or a container built from one it sits
# beside this script; in a wheel-only install it is under the environment's
# share/kaicalc/, installed alongside the migration tree in the same relative
# arrangement so that the ini's own `script_location = %(here)s/alembic`
# resolves either way.
function Resolve-AlembicIni {
    if ($env:KAICALC_ALEMBIC_INI) { return $env:KAICALC_ALEMBIC_INI }

    $local = Join-Path $Root 'alembic.ini'
    if (Test-Path -LiteralPath $local) { return $local }

    $prefix = & $Python -c 'import sys; print(sys.prefix)'
    if ($prefix) {
        $shared = Join-Path $prefix 'share\kaicalc\alembic.ini'
        if (Test-Path -LiteralPath $shared) { return $shared }
    }

    throw @"
run.ps1: cannot find alembic.ini (looked beside this script and in
         (python -c 'import sys;print(sys.prefix)')\share\kaicalc).
         Set KAICALC_ALEMBIC_INI to its path.
"@
}

# True when the caller already passed the named uvicorn flag, in either
# `--flag value` or `--flag=value` form. If they did, we inject nothing and
# their argument is forwarded untouched.
function Test-HasFlag {
    # Not named $Args: that is an automatic variable in PowerShell.
    param([string] $Name, [string[]] $Argv)
    foreach ($a in $Argv) {
        if ($a -eq $Name -or $a.StartsWith("$Name=")) { return $true }
    }
    return $false
}

function Invoke-Serve {
    param([string] $Target, [string] $DefaultPort, [string[]] $Argv)

    if ($null -eq $Argv) { $Argv = @() }
    $injected = @()
    if (-not (Test-HasFlag '--host' $Argv)) {
        $bindHost = if ($env:KAICALC_HOST) { $env:KAICALC_HOST } else { '127.0.0.1' }
        $injected += @('--host', $bindHost)
    }
    if (-not (Test-HasFlag '--port' $Argv)) {
        $injected += @('--port', $DefaultPort)
    }

    # Both apps are factories, not module-level `app` objects.
    & $Python '-m' 'uvicorn' $Target '--factory' @injected @Argv
    exit $LASTEXITCODE
}

# Run from the application root. admin/app.py mounts StaticFiles(directory=
# "admin/static") and sqladmin's templates_dir="admin/templates" - both
# relative to the working directory, so the panel only starts from here.
# python-dotenv also looks for .env from the working directory upwards.
Set-Location -LiteralPath $Root

# A checkout that has had `pip install -r requirements.txt` run but not
# `pip install .` has no kaicalc on sys.path; an installed environment has it
# twice, from the same files. Harmless either way, and it means the launcher
# works before the package is installed.
if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$Root;$env:PYTHONPATH"
} else {
    $env:PYTHONPATH = $Root
}

if ($null -eq $Rest) { $Rest = @() }

switch ($Command) {
    'api' {
        $port = if ($env:KAICALC_API_PORT) { $env:KAICALC_API_PORT } else { '18000' }
        Invoke-Serve 'api.app:create_app' $port $Rest
    }
    'admin' {
        $port = if ($env:KAICALC_ADMIN_PORT) { $env:KAICALC_ADMIN_PORT } else { '18001' }
        Invoke-Serve 'admin.app:create_app' $port $Rest
    }
    'migrate' {
        $ini = Resolve-AlembicIni
        if ($Rest.Count -eq 0) {
            & $Python '-m' 'alembic' '-c' $ini 'upgrade' 'head'
        } else {
            & $Python '-m' 'alembic' '-c' $ini @Rest
        }
        exit $LASTEXITCODE
    }
    { $_ -in @('', 'help', '-h', '--help') } {
        Show-Usage
    }
    default {
        [Console]::Error.WriteLine("run.ps1: unknown subcommand '$Command'")
        [Console]::Error.WriteLine((Show-Usage))
        exit 2
    }
}
