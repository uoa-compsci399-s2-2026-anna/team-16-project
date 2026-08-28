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

FOUR DEPLOYMENT FACTS THIS SCRIPT IS SHAPED BY

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

4. PROTECTION_TRUSTED_PROXY is also what this script hands uvicorn, as
   --forwarded-allow-ips. Left unset uvicorn trusts only 127.0.0.1, so behind
   a container-network proxy - which is never 127.0.0.1 - `request.url.scheme`
   stays http no matter what nginx sends: sqladmin builds every asset URL and
   redirect from that scheme, so a panel reached over real https serves
   http:// stylesheets and scripts - mixed content, blocked by the browser.
   Get-ServeArgv passes --forwarded-allow-ips * when and only when this same
   variable is true, trusting whichever peer actually connected no more than
   db/detection.py already trusts it for the address - and --no-access-log
   travels with it in the same condition, because trusting the peer for
   scheme also hands uvicorn's own ProxyHeadersMiddleware the address: it
   rewrites scope["client"] from the same X-Forwarded-For header, and
   uvicorn's default access log prints that on every line. Left on, this
   turns a log line that was always one constant, non-identifying container
   address into the visitor's real one - an address stored, which contract
   2.3 forbids outright. run.sh carries the long form of this argument,
   including why `*` and not a pinned address, and why this is the one
   existing switch and not a second one.

See .env.example and docs/architecture.md 9.1 / 9.1.1 for the long form.
#>

# NO param() BLOCK, AND NO [CmdletBinding()]. This is deliberate, and it is
# the difference between this script forwarding arguments and only appearing
# to.
#
# PowerShell binds parameters by unambiguous PREFIX, and it does so before
# ValueFromRemainingArguments is ever consulted. With a
# `param($Command, [string[]]$Rest)` block, every one of these went wrong:
#
#   .\run.ps1 migrate -c alembic.ini current
#       -c is a prefix of -Command, so "alembic.ini" bound $Command and the
#       script exited 2 with "unknown subcommand 'alembic.ini'".
#
#   .\run.ps1 api --workers 4 -v
#       -v is a prefix of CmdletBinding's own -Verbose, so it was consumed
#       and NEVER REACHED UVICORN. Silently: the server started, one flag
#       short, with nothing said about it.
#
# The hit list was -c and -r (this script's own parameters) plus -v -d -e -w
# -i -o -p (the common parameters CmdletBinding adds). run.sh forwards all of
# them, so the two launchers were not equivalent, and the half that failed was
# the half that failed quietly.
#
# The automatic $args variable is populated only when there is no param()
# block, and it is not subject to any of that binding: it is the raw argument
# vector. $args[0] is the subcommand, everything after it is forwarded.
$ErrorActionPreference = 'Stop'

if ($args.Count -gt 0) {
    $Command = [string] $args[0]
} else {
    $Command = ''
}
if ($args.Count -gt 1) {
    $Rest = @($args[1..($args.Count - 1)])
} else {
    $Rest = @()
}

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

# Fact 4. Mirrors run.sh's `trusts_forwarding_proxy` on the same vocabulary
# admin/config.py and api/app.py already parse PROTECTION_TRUSTED_PROXY on -
# case-insensitive 1/true/yes/on, 0/false/no/off - and refuses, loudly, to
# guess at anything else, throwing rather than defaulting one way. Unset
# means false, the same default both processes fall back to.
function Test-TrustedForwardingProxy {
    $raw = "$env:PROTECTION_TRUSTED_PROXY".Trim().ToLowerInvariant()
    switch ($raw) {
        { $_ -in @('1', 'true', 'yes', 'on') } { return $true }
        { $_ -in @('', '0', 'false', 'no', 'off') } { return $false }
        default {
            throw "run.ps1: PROTECTION_TRUSTED_PROXY=$env:PROTECTION_TRUSTED_PROXY is " +
                  "not a recognised boolean. Use true or false."
        }
    }
}

# Builds the argument vector, and does NOT run it. A function that ran uvicorn
# would have to return its exit code, and in PowerShell a native command's
# stdout goes to the success stream - so `$code = Invoke-Serve ...` would
# capture every line uvicorn printed along with the code, and the operator
# would watch a silent server. The caller runs it and reads $LASTEXITCODE.
function Get-ServeArgv {
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
    if (Test-TrustedForwardingProxy) {
        # See fact 4 above and run.sh's own comment for why this is
        # PROTECTION_TRUSTED_PROXY, why it is `*` and not an address, and why
        # --no-access-log is not a separate decision from this one.
        if (-not (Test-HasFlag '--forwarded-allow-ips' $Argv)) {
            $injected += @('--forwarded-allow-ips', '*')
        }
        if ((-not (Test-HasFlag '--access-log' $Argv)) -and (-not (Test-HasFlag '--no-access-log' $Argv))) {
            $injected += @('--no-access-log')
        }
    }

    # Both apps are factories, not module-level `app` objects.
    # The leading comma stops PowerShell unrolling the array on return.
    return , (@('-m', 'uvicorn', $Target, '--factory') + $injected + $Argv)
}

# A checkout that has had `pip install -r requirements.txt` run but not
# `pip install .` has no kaicalc on sys.path; an installed environment has it
# twice, from the same files. Harmless either way, and it means the launcher
# works before the package is installed.
if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$Root;$env:PYTHONPATH"
} else {
    $env:PYTHONPATH = $Root
}

# Run from the application root, so that .env is found: python-dotenv searches
# from the working directory upwards, and .env lives here. Nothing else
# depends on it - admin/app.py resolves its templates and static files
# relative to its own package directory, so the panel itself starts from
# anywhere.
#
# Push/Pop rather than Set-Location: invoked as `.\run.ps1` this runs in the
# caller's own session, and leaving their prompt in a directory they did not
# choose is a rude thing for a launcher to do. The finally block runs on
# `exit` as well as on a throw.
$code = 0
Push-Location -LiteralPath $Root
try {
    switch ($Command) {
        'api' {
            $port = if ($env:KAICALC_API_PORT) { $env:KAICALC_API_PORT } else { '18000' }
            $argv = Get-ServeArgv 'api.app:create_app' $port $Rest
            & $Python @argv
            $code = $LASTEXITCODE
        }
        'admin' {
            $port = if ($env:KAICALC_ADMIN_PORT) { $env:KAICALC_ADMIN_PORT } else { '18001' }
            $argv = Get-ServeArgv 'admin.app:create_app' $port $Rest
            & $Python @argv
            $code = $LASTEXITCODE
        }
        'migrate' {
            $ini = Resolve-AlembicIni
            if ($Rest.Count -eq 0) {
                & $Python '-m' 'alembic' '-c' $ini 'upgrade' 'head'
            } else {
                & $Python '-m' 'alembic' '-c' $ini @Rest
            }
            $code = $LASTEXITCODE
        }
        { $_ -in @('', 'help', '-h', '--help') } {
            Show-Usage
        }
        default {
            [Console]::Error.WriteLine("run.ps1: unknown subcommand '$Command'")
            [Console]::Error.WriteLine((Show-Usage))
            $code = 2
        }
    }
} finally {
    Pop-Location
}

exit $code
