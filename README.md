# team-16-project

COMPSCI 399 project repository for Team 16 - 502 Bad Gateway.

**Kai Commitment Food Waste Impact Calculator** — a New Zealand food waste impact
calculator built for the Kai Commitment, an initiative of the New Zealand Food Waste
Champions 12.3 Trust.

> ### The factors currently shipped are placeholder data
>
> The client has not yet supplied real New Zealand emissions factors (open item **O-1**).
> Everything in this repository runs on a mock factor set so the full pipeline could be
> built and exercised end to end. While a mock factor set is the published one, a
> **non-dismissible placeholder-data banner appears on every results view and every
> export**, and that behaviour cannot be switched off. The numbers the calculator
> produces today are structurally correct and substantively meaningless. Do not quote
> them.
>
> Replacing them is a data task, not a code task: load a real factor set through the
> admin panel and publish it. Nothing needs to be rebuilt or redeployed.

**Contents**

- [What this is](#what-this-is)
- [How to run it](#how-to-run-it) — Docker, one command
- [How to develop on it](#how-to-develop-on-it)
- [Where the real documentation lives](#where-the-real-documentation-lives)

---

## What this is

| | |
| --- | --- |
| Client | Juliet Gerrard, with the Kai Commitment (NZ Food Waste Champions 12.3 Trust) |
| Course | University of Auckland COMPSCI 399 Capstone, Project 18 |
| Team | Team 16 — 502 Bad Gateway, five members |
| Deliverable | **Source code and documentation.** DNS, certificates, hosting and post-semester operations are out of scope. |

The product is modelled on the US ReFED Impact Calculator but uses New Zealand data and
New Zealand (Ministry for the Environment) definitions, and adds an **economic cost metric
that ReFED does not provide**.

**Every calculation is a comparison.** A visitor describes what happens to their food
waste today (the *current* scenario) and what could happen instead (the *alternative*
scenario). Both are calculated, and the difference between them is the net benefit. Doing
less harm by simply wasting less is expressed through a special destination, `prevention`,
whose factors are all zero — so the two scenarios always move the same mass and net
benefit cannot be inflated by quietly assuming less waste in the alternative.

**Nothing about the model is compiled in.** Food categories, destinations, sectors,
metrics, constants, equivalences and the *formulas themselves* are rows in the database,
edited by staff through the admin panel. Adding a metric means inserting a row and
writing one formula — not changing code and redeploying.

**Nothing identifying is ever collected.** No IP address, no user agent, no browser
fingerprint is stored. Each calculation is saved as an anonymous submission so that the
aggregate statistics can be built, and any statistical bucket with fewer than five
records is merged into `other` on the server before it is published.

What runs today:

- a public calculator at `/` — a six-step wizard, dual scenario, results and comparison
  screens, and a published-methodology page at `/methodology.html`;
- a public JSON API at `/api/v1/` — `taxonomy`, `calculate`, `factors`, `stats`;
- a staff admin panel at `/admin` — the taxonomy, the factor sets and their
  draft/publish/rollback lifecycle, editable formulas, the audit log, staff accounts with
  TOTP two-factor authentication, and an IP blocklist.

The public statistics page and its charts are not built. One known defect in the deployed
system is recorded as open item **O-6** (the seeded bucket-to-kilogram conversions are
placeholders too). **O-9** — `/admin/try`, the staff dry-run view, answering
`UNAUTHORIZED` in a deployed stack — was closed on 2026-08-12. See
`docs/architecture.md` §10 for the full list.

---

## How to run it

Docker is the answer for anyone who is not developing. One command brings up the
database, migrates it, seeds the New Zealand taxonomy, publishes the mock factor set,
creates two administrator accounts and starts the calculator behind nginx.

```bash
docker compose -f docker/compose.yaml up -d
```

Then open **<http://localhost:18080/>**, which is the home page. The calculator itself
is at `/index.html`, and every page links to every other.

That command builds the images from this checkout. If you were handed the **published
images** instead of the source, the file to use is `docker/compose.deploy.yaml` — same
topology, no build, see [Released images and the wheel](#released-images-and-the-wheel).

### Three things that stop the command above

**1. Your user has to be able to talk to the Docker daemon.** On Linux, being in `sudo`
is not the same as being in `docker`, and the command fails with:

```
permission denied while trying to connect to the Docker daemon socket at
unix:///var/run/docker.sock
```

Either prefix every command with `sudo`, or add yourself to the group once:

```bash
sudo usermod -aG docker "$USER"
newgrp docker          # or log out and back in — the group is read at login
```

Membership is a root-equivalent grant on that host. On a shared machine, `sudo` per
command is the smaller decision.

**2. If `18080` is already taken, `up` fails — cleanly, and that is a recent improvement.**
Only one port is published now, so there is only one port that can clash:

```bash
KAICALC_WEB_PORT=18090 docker compose -f docker/compose.yaml up -d
```

**This used to be the worst failure in this file, and it is worth knowing what it looked
like, because you can still reach it deliberately.** `api` and `admin` were once published
on `18000` and `18001` as well. Compose starts them together, so whichever one could not
bind its port failed while the other started and reported **healthy** — and `web` never
started at all, because it waits on `api: service_healthy` **and** `admin: service_healthy`:

```
Error response from daemon: driver failed programming external connectivity on endpoint
kaicalc-admin: failed to bind port 0.0.0.0:18001/tcp: ... address already in use

$ docker ps                          # what you look at afterwards
kaicalc-api          Up 30 seconds (healthy)
kaicalc-stack-db     Up 35 seconds (healthy)

$ docker compose -f docker/compose.yaml ps -a      # what actually happened
SERVICE   STATUS
admin     Created
api       Up 30 seconds (healthy)
db        Up 35 seconds (healthy)
migrate   Exited (0)
web       Created
```

— one application container, healthy, and nothing on <http://localhost:18080/>. The bind
error is printed once, by the `up` that failed. `docker ps` afterwards shows a
plausible-looking subset with no error in it, because a container that was **created and
never started** is not a container `docker ps` lists. `ps -a` is the command that shows
what is missing rather than what is there — keep that reflex; it is the general lesson and
it applies to any `depends_on: service_healthy` chain.

Those two ports are now opt-in (see **[Reaching the API and the panel
directly](#reaching-the-api-and-the-panel-directly)** below), so the shipped `up` cannot
produce that state. If you turn them back on, it can again, and
`KAICALC_API_PORT` / `KAICALC_ADMIN_PORT` move them.

**3. One instance per host.** `docker/compose.yaml` pins `name: kaicalc` and a fixed
`container_name:` for every service, so a second copy of this repository in another
directory does **not** start a second stack: the same command from there adopts or
recreates the containers the first one is running. This is deliberate — it is what makes
`docker exec kaicalc-admin kaicalc …` a command anyone can paste — but it means a stack
you did not start can be the one you just restarted. `docker compose ls -a` names the
directory each project was started from.

Nothing binds port 80, 8000, 8080, 3000 or 5000 — a clean machine very often has
something on all of them already. **nginx is the only way in, and that is a security
property rather than a tidiness one** (see the next section for what it buys):

| Variable | Default | What |
| --- | --- | --- |
| `KAICALC_WEB_PORT` | `18080` | nginx — the calculator, the API and the panel. **The only port published.** |
| `KAICALC_API_PORT` | `18000` | the API, direct. **Not published** unless you overlay `docker/compose.direct-ports.yaml`; then this chooses the host port. |
| `KAICALC_ADMIN_PORT` | `18001` | the panel, direct. Same — opt-in only. |
| — | not published | MySQL. Reachable only from inside the compose network. |

#### Reaching the API and the panel directly

`http://localhost:18000/docs` is a real convenience, so there is a supported way back:

```bash
docker compose -f docker/compose.yaml -f docker/compose.direct-ports.yaml up -d
```

Pass **both** `-f` flags to every later command against that stack — `down`, `ps`, `logs`
— or compose is describing a different project.

**That overlay also sets `PROTECTION_TRUSTED_PROXY=false`, and it must.** With the
applications reachable without going through nginx, a caller can send any
`X-Forwarded-For` it likes and be measured as that address — out of the rate limit and out
of the blocklist, so a block a staff member applied stops holding. The two facts move
together, which is why they live in one file instead of in two places and a warning. The
cost of using it is the one the default exists to avoid: every visitor arriving through
nginx shares **one** rate-limit bucket and **one** blocklist entry again. Fine on a laptop;
not fine anywhere the public can reach, and `/admin/deployment` will say so.

**If you were reaching `:18000` or `:18001` before**, this is what changed and this is how
to get it back. Most of what you were doing does not need it:

| You were opening | Through nginx instead | |
| --- | --- | --- |
| `:18001/admin` | `:18080/admin` | the same panel, and the route was always there |
| `:18000/api/v1/…` | `:18080/api/v1/…` | verified: `taxonomy`, `factors`, `stats` all answer `200` |
| `:18000/docs`, `:18000/openapi.json` | **nothing** | the overlay is the only way |

That last row is the one real loss, and it is deliberate rather than an oversight: nginx
routes `/api/v1/` and `/admin` and nothing else to the applications, so FastAPI's
interactive documentation — which sits at the API's root, outside `/api/v1/` — is not on
the public origin and is not being put there. Use the overlay when you want it.

nginx routes everything by default, so a headless server needs no further configuration:

| Path | Serves |
| --- | --- |
| `/` | the calculator (static HTML, CSS and ES modules) |
| `/api/v1/` | the public JSON API |
| `/admin` | the staff panel |

`/admin` being routed on the public origin is a deliberate decision, not an oversight —
on a headless server there is no other way in, because the panel is a browser-only
surface (two-factor enrolment is a QR code). The panel has TOTP, per-account login
lockout, a server-side IP blocklist, headless-client detection and a per-address rate
limit. **Whoever deploys this is still expected to put a WAF, an IP allowlist or a
network boundary in front of `/admin`.** To remove the route, delete the single
`location /admin` block in `docker/nginx.conf`; the cost of removing it is spelled out
there.

**`/admin` answers `403 Refused.` to `curl`, and that is the panel working.** It is a
browser-only surface — two-factor enrolment is a QR code — so `admin/protection.py`
refuses any caller that is plainly a script: no `User-Agent` at all, a `User-Agent` naming
one (`curl/`, `python-requests`, `python-httpx`, `wget`, `go-http-client`), or an HTML
navigation with no `Sec-Fetch-Mode` header, which every browser has sent since 2020.
Nothing about the check is stored — the header is read, judged and forgotten inside the
one request, which is what keeps it inside the no-fingerprinting rule.

```bash
$ curl -s -o /dev/null -w '%{http_code}\n' http://localhost:18080/admin/login
403                       # correct. curl says it is curl.
$ curl -s -o /dev/null -w '%{http_code}\n' -A 'Mozilla/5.0' http://localhost:18080/admin/login
200                       # the check is a floor, not a fingerprint
```

So: **open `/admin/login` in a browser** to see whether the panel is up, and do not build
a monitor or an uptime check on a `GET /admin` — it will report a healthy panel as down.
`/api/v1/` applies no such check and is scriptable on purpose. This is also why the
`admin` service's health check in `docker/compose.yaml` is a TCP connect rather than an
HTTP request.

### The first administrator

The stack creates two administrator accounts on first start and prints their one-time
passwords:

```bash
docker compose -f docker/compose.yaml logs migrate
```

```
Created initial administrator accounts.
  admin: nEzVAJpKqCLvB1UPOFdP
  admin2: wWJv6nKE206RFhYbjkrK
```

Sign in at <http://localhost:18080/admin/login>. Each account is walked through a forced
password change and then two-factor enrolment before it reaches the panel; scan the QR
code with any authenticator app and save the recovery codes it shows you.

If a password is lost before that account has changed it, the **other** administrator can
read it back from `/admin/staff/list`. If both are lost, nobody can log in, and the way
back is a command on the container:

```bash
docker exec kaicalc-admin kaicalc issue-password admin
```

`kaicalc`, **not** `kaicalc-admin`. The second is the console script and it fails under
`docker exec` with `MissingSettingError: SECRET_KEY is not set`, because `docker exec`
does not run the image's entrypoint and inherits none of the environment it builds.
`kaicalc` is a wrapper that resolves the secret first and then calls the same command.
`kaicalc-admin --help` does work, which is exactly what makes the wrong form look correct
until the moment it is needed.

Everything after `kaicalc` is a subcommand:

```bash
docker exec kaicalc-admin kaicalc create-staff bob "Bob Smith" --admin
docker exec kaicalc-admin kaicalc issue-password alice     # mint a new password
docker exec kaicalc-admin kaicalc reset-mfa alice          # clear a lost authenticator
docker exec kaicalc-admin kaicalc unblock 203.0.113.5      # locked yourself out of /admin
docker exec kaicalc-admin kaicalc rotate-key --old <k> --new <k>
docker exec kaicalc-admin kaicalc --help
```

`unblock` exists for the case the protection layer is designed around not causing: an
administrator blocks the address they are sitting behind, and there is then no page left
to click. `.env.example` documents three routes back in, in the order to try them.

### What to set in the environment

**`SECRET_KEY` above all.** It signs staff session cookies, and it derives both the key
that encrypts two-factor secrets at rest and the key that fingerprints blocked addresses.
The API and the panel must hold the **same** value: they each derive the blocklist
fingerprint independently, so two different secrets mean a block applied in the panel
silently never matches at `/api/v1/`, with nothing raised on either side.

Out of the box the entrypoint generates one into a shared Docker volume on first start,
once, and every service reads that same file — so the stack works on a clean machine with
no configuration at all. **For anything that is not a laptop, set it explicitly** (in the
environment, or from a secret manager) and the generation path becomes a no-op:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Changing it later invalidates every enrolled authenticator. Run `kaicalc rotate-key
--old <old> --new <new>` **before** restarting with the new value; it re-encrypts every
stored secret in one transaction, and it clears the IP blocklist, telling you how many
rows it removed (an address is stored as an HMAC, and an HMAC cannot be re-keyed — the
rows would otherwise survive matching nobody). Re-apply any blocks that are still needed.

The other settings. The application ones are documented at length in **`.env.example`**,
each with the failure it prevents; the `KAICALC_*` ones are **compose** variables, which
`docker compose -f docker/compose.yaml` reads from the environment or from `docker/.env`
and never from the root `.env` that `.env.example` describes — those carry their reasoning
in `docker/compose.yaml` at the point of use, and below:

| Setting | Note |
| --- | --- |
| `MYSQL_ROOT_PASSWORD`, `MYSQL_USER`, `MYSQL_PASSWORD` | change these for anything that is not a laptop |
| `KAICALC_SESSION_HTTPS_ONLY` | defaults to `false` so the shipped plain-http stack is usable. **Set it to `true` the moment TLS is in front.** |
| `KAICALC_NEWS_ORIGIN` | the client's WordPress site, as `scheme://host[:port]` with no trailing slash. **Set it empty and the home page removes its news section entirely** — a supported deployment, not a degraded one. Details below. |
| `KAICALC_API_ORIGIN` | empty, and it should stay empty unless the API is on its own origin. Details below. |
| `KAICALC_NEWS_IMAGE_ORIGINS` | space-separated, `img-src` only, for a WordPress media library on a CDN rather than on the site origin. |
| `KAICALC_TRUST_FORWARDED_HEADERS` | defaults to `false`. **Set it true when another proxy — one you operate — terminates TLS in front of this stack.** Left false there, the panel is told the request is not on TLS and every visitor arrives as your edge's one address. Set true while this nginx is directly reachable, and any caller can claim any address. Details below. |
| `PROTECTION_TRUSTED_PROXY` | **`docker/compose.yaml` sets it `true`**, which is what makes the rate limit and the blocklist per-visitor rather than per-proxy. Safe only because that file publishes nothing but nginx's port; `docker/compose.direct-ports.yaml` republishes the other two and puts this back to `false` in the same file. The *code* default (`./run.sh`, no compose) is `false` — a process with no proxy in front must not believe the header. |
| `PROTECTION_ENABLED` | the escape hatch if the panel's protection layer locks everyone out. Every setting is read once at start-up, so edit *and restart*. |
| `LOGIN_MAX_FAILURES`, `LOGIN_LOCKOUT_MINUTES` | login throttling. Lockout counters live in process memory, so `docker compose -f docker/compose.yaml restart admin` clears every lockout immediately. |

### Where the front end gets its origins

**No domain is baked into any image.** `KAICALC_NEWS_ORIGIN`, `KAICALC_API_ORIGIN` and
`KAICALC_NEWS_IMAGE_ORIGINS` are read once at container start by `docker/web-config.sh`,
which writes **both** consumers from them: the `connect-src` and `img-src` of the public
Content-Security-Policy in `docker/nginx.conf`, and `web/js/config.js`, the ES module the
front end imports. One value each, two outputs, so the policy and the page cannot name
different hosts. `docker/compose.yaml` is the only file in this repository that mentions
the client's site, and it is deployment configuration rather than an artefact — DNS,
certificates and hosting are out of scope for this project.

That is a fix, not a refinement. The domain used to be written out twice — in
`web/js/api.js` as the WordPress base and in `docker/nginx.conf` as a `connect-src` entry
— and the two had to agree while failing in opposite directions when they did not: a
wrong policy means the news quietly does not load, and a wrong URL means the browser goes
and asks a domain nobody chose.

These are **compose variables, not application settings**, so they belong in the
environment or in `docker/.env` — not in the root `.env` that `.env.example` describes,
which `docker compose -f docker/compose.yaml` never reads (Compose takes its project
directory from the compose file's own directory). Same as `KAICALC_WEB_PORT` above.

```bash
# The client's site (the default), no news at all, and the API on its own origin:
KAICALC_NEWS_ORIGIN=https://kaicommitment.org.nz  docker compose -f docker/compose.yaml up -d web
KAICALC_NEWS_ORIGIN=                              docker compose -f docker/compose.yaml up -d web
KAICALC_API_ORIGIN=https://api.example.org        docker compose -f docker/compose.yaml up -d web

# What is actually in force. Read both — the whole point is that they agree:
curl -sI http://localhost:18080/ | grep -i content-security-policy
curl -s  http://localhost:18080/js/config.js
```

An empty value is honoured rather than defaulted: `docker/compose.yaml` writes
`${KAICALC_NEWS_ORIGIN-…}` without the colon, so `KAICALC_NEWS_ORIGIN=` means *no feed*
and only an absent variable falls back to the client's site. With the colon there would
be no way to turn the feed off short of editing the compose file.

**An unset news origin removes the home page's news section rather than reporting an
outage.** Most deployments of this calculator have no WordPress behind them, so unset is a
supported arrangement; leaving the heading standing over "temporarily unavailable" would
describe a fault nobody caused, and guessing a domain would be worse.

**`KAICALC_API_ORIGIN` should stay empty unless you mean it.** The front end builds
`/api/v1`, a relative path, and nginx routes it on — same-origin is the designed topology
and needs no configuration. It is settable because splitting the API onto its own
subdomain otherwise means editing `web/js/api.js` *and* `connect-src` in
`docker/nginx.conf`, or every call is refused by our own policy with nothing in the
failure pointing at nginx. Weigh it as you would the nginx configuration itself: this is
the front end of a tool whose numbers are the product. It is readable only from the
container's environment, never from anything a visitor can put in a URL; a value that is
not a bare `scheme://host[:port]` **stops the container from starting** rather than
reaching the policy; and `connect-src` is generated from the same string, so the page can
reach the one origin you named and no other.

### Putting it behind a TLS terminator you already run

A public IPv4 has one port 443, and it is often already taken. The ordinary answer is to
front this stack with the nginx (or Caddy, or Traefik) that already holds it:

```
browser --https--> your edge :443 --http--> this stack :18080
```

DNS, certificates and hosting are out of scope for this project. Working correctly *behind*
somebody else's terminator is not, and it needs one variable:

```bash
KAICALC_TRUST_FORWARDED_HEADERS=true \
  docker compose -f docker/compose.yaml up -d
```

(`PROTECTION_TRUSTED_PROXY=true` used to be needed on that line too. It is the default
now — the api and admin ports are no longer published, so the applications can already
believe the header this nginx sends them. This variable is the *other* hop.)

**What each half does.** nginx sends `X-Forwarded-Proto` and `X-Forwarded-For` to the API
and the panel. By default it builds both from what *it* saw — which is right while it is
the outermost proxy, and wrong behind an edge: the panel is then told the request is not
on TLS, and every visitor on earth arrives as the edge's single address, so the API's
per-visitor rate limits become one site-wide counter and one blocklist entry denies
everyone. `KAICALC_TRUST_FORWARDED_HEADERS=true` makes nginx pass the edge's values
through instead: the scheme if it is exactly `http` or `https`, and the forwarded chain
with the visitor left-most.

**Turn it on only when the edge is the only way in.** While `:18080` is reachable
directly, any caller can send both headers — claiming an address the rate limit and the
blocklist then measure, and stripping `Secure` off a live staff session cookie by claiming
the request is plain http. That is why it is off by default and why it is a *separate*
variable from `PROTECTION_TRUSTED_PROXY`: that one says the applications may believe the
header **our** nginx sends, this one says our nginx may believe the header **it receives**.
Setting this without that leaves nginx forwarding an address the applications ignore —
the container warns about it at start-up.

**Unpublishing the api and admin ports did not settle this one, and assuming it did is the
mistake worth naming.** That change decided who can reach the *applications*, which is why
`PROTECTION_TRUSTED_PROXY` could become the default. It decided nothing about who can open
a socket to `:18080` — on a laptop and on a bare VPS, anyone can — and that is the only
question this variable asks. It stays `false` until *you* know an edge you run is the only
route in. Set `KAICALC_SESSION_HTTPS_ONLY=true` at the same time.

```bash
# What is actually in force:
docker logs kaicalc-web 2>&1 | grep 'forwarded headers'
```

**Reading it back from a browser instead.** Sign in to the panel as an administrator and
open **Deployment** (`/admin/deployment`) — *through the edge you are configuring*, not on
the panel's direct port. It shows the `X-Forwarded-For` chain that actually arrived, in
order, `X-Forwarded-Proto`, and the address the applications decided on, then says whether
the settings cohere. A chain of two whose left-most entry is your own public address means
it worked; one entry that is a container address means it did not. The page **configures
nothing** — changing any of these values still means the `docker exec` sequence below or a
restart — and it is careful about the difference between what it read and what it inferred:
`KAICALC_TRUST_FORWARDED_HEADERS` lives in another container and cannot be read from there
at all, so the page reports the evidence rather than claiming to know the setting.

**Trying a setting without a restart.** Getting an edge proxy right usually takes a few
attempts, and rebuilding the container for each one is slow enough to discourage checking.
nginx reloads its configuration without dropping a connection, and the script that renders
that configuration can be re-run with a different value:

```bash
# 1. Re-render with the value you want to try. It prints what it decided.
docker exec -e KAICALC_TRUST_FORWARDED_HEADERS=true kaicalc-web \
  /docker-entrypoint.d/16-kaicalc-config.sh

# 2. Check the result parses before asking nginx to adopt it.
docker exec kaicalc-web nginx -t

# 3. Reload. Existing connections finish on the old workers; nothing is dropped.
docker exec kaicalc-web nginx -s reload
```

**This does not persist, and that is the trap.** The container starts from
`docker/compose.yaml` and the environment, so the next restart silently returns to whatever
is written there — including a restart nobody performed deliberately, such as a host reboot
or a `docker compose up` after an unrelated change. The setting that reverts is a security
one: an edge-fronted deployment that quietly goes back to `false` starts telling the panel
every visitor shares one address.

So use the reload to **find** the right value, then write it into `docker/compose.yaml` or
your `.env` and bring the stack up normally. The line printed by step 1 and the one printed
at start-up are the same sentence, which is what lets you confirm the two agree.

The same three steps work for `KAICALC_NEWS_ORIGIN` and `KAICALC_API_ORIGIN`; the script
re-renders `web/js/config.js` alongside the CSP, so the front end and the header stay in
step even mid-experiment.

### Stopping it

```bash
docker compose -f docker/compose.yaml down       # keeps the data and the secret
docker compose -f docker/compose.yaml down -v    # destroys both — new secret,
                                                 # new administrator passwords,
                                                 # empty database
```

### Released images and the wheel

- **Releases** are cut by the `Release` workflow (`.github/workflows/release.yaml`),
  triggered by hand with a version number of the form `X.Y.Z`. It commits that version to
  the default branch, tags that commit, runs the full test suite against MySQL 8, builds
  three multi-architecture images tagged `X.Y.Z` and `latest`, and attaches a wheel and an
  sdist to a GitHub Release.
- **CI** (`.github/workflows/ci.yaml`) runs the same test suite on every push and pull
  request, and on `main` publishes alpha images tagged `alpha` and by short commit SHA.
  Alpha versions are `<base>.dev0+<sha>`, which sorts *below* every release and can never
  be mistaken for one.
- Images are `ghcr.io/uoa-compsci399-s2-2026-anna/team-16-project/kaicalc-{api,admin,web}`.
  Deployers should pin the version tag rather than `latest`.

**Running them: `docker/compose.deploy.yaml`.** Same five services, same health checks,
same dependency order, same environment, same ports — no `build:` block and no source
tree. It is the file to hand someone along with the images.

```bash
# 1. A token with read:packages. `gh auth login` does NOT grant that scope.
gh auth refresh -h github.com -s read:packages        # then:
gh auth token | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin

# 2. Choose a tag. There is no default — `up` fails and tells you to set one.
export KAICALC_IMAGE_TAG=alpha        # or a release X.Y.Z, or a short commit SHA

docker compose -f docker/compose.deploy.yaml pull
docker compose -f docker/compose.deploy.yaml up -d
```

| Tag | Published by | Pin a deployment to it? |
| --- | --- | --- |
| `X.Y.Z` | the release workflow | **yes** |
| `latest` | the release workflow, moves every release | no |
| `<short-sha>` | CI, on every push to `main`, immutable | yes, for a specific build |
| `alpha` | CI, moves to the head of `main` | no |

`pull` before `up` is not decoration: Compose's default pull policy is `missing`, so a
moving tag already in your local image store is reused and the registry is never asked.

The two compose files are **the same stack** — same project name, same container names —
so run one or the other, not both. `tests/test_compose.py` asserts that every key except
the four application `image:` values is identical between them, which is what stops the
deployment path quietly drifting from the one that gets tested. Scopes, private-registry
behaviour, architectures and the error messages each failure produces are in
**`docs/docker-images.md`**.

---

## How to develop on it

### The stack

Python 3.11+ / FastAPI / SQLAlchemy 2.x with Alembic / MySQL 8 / `sqladmin` for the admin
panel / `pytest`.

The front end is plain HTML, CSS and JavaScript ES modules. **No React, no Node.js, no
build step** — what is in `web/` is what the browser runs. The team had no front-end
framework experience, and with no framework there is no build tooling, so the stack closes
cleanly on Python. Chart.js is the charting library `docs/architecture.md` §2 selects, but
nothing in `web/` imports it yet; the charts belong to the unbuilt statistics page.

Expression evaluation is hand-rolled over the standard library's `ast`, deliberately not
`simpleeval` — see below.

### Getting a checkout running

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows;  source .venv/bin/activate elsewhere
pip install -e ".[dev]" -c docker/constraints.txt

docker compose up -d            # the DEVELOPMENT DATABASE ONLY, MySQL on host port 3307
cp .env.example .env            # then fill in SECRET_KEY
```

**`-c docker/constraints.txt` is not optional, and leaving it off is not a slower
install — it is a different one.** `pyproject.toml` states every dependency as a `>=`
floor, so without the constraint file pip resolves each floor to whatever PyPI published
most recently, and your checkout is running different software from the two images, which
build with that same file. The gap is silent while it is small. It has already cost this
project once: the pin moved to sqladmin 0.31.0 on 10 August, the desk stayed on 0.30.0,
and `admin/templates/sqladmin/_macros.html` — a vendored copy of a template out of that
package, added on 13 August — was therefore copied from a version the panel has never
run, and was wrong the moment it was written. `tests/admin/test_i18n.py` now fails
if the installed version is not the pinned one, so a skewed environment reports itself
rather than surfacing later as an unrelated-looking test failure.

Use it on `pip install -r requirements.txt` too — that file now carries the constraint
itself, so plain `-r requirements.txt` is already correct.

Two compose files, two jobs, and confusing them wastes an afternoon:

- **`docker-compose.yml`** (repository root) — one MySQL container on host port 3307, for
  the test suite and for running the application from a checkout. It starts no
  application service.
- **`docker/compose.yaml`** — the whole deployment, as described above. Its database is
  not published at all, so the two run side by side.

Then, against the development database:

```bash
./run.sh migrate                # alembic upgrade head. run.ps1 is the PowerShell twin
kaicalc-admin seed-taxonomy     # the NZ taxonomy. Idempotent; safe on every deploy
python docker/seed_mock_factors.py   # a published mock factor set. Does nothing if any
                                     # factor set already exists
./run.sh api                    # http://127.0.0.1:18000
./run.sh admin                  # http://127.0.0.1:18001
```

Everything after the subcommand is forwarded untouched, so `./run.sh api --reload` and
`./run.sh migrate current` both work.

**Run `migrate` once, from one process, before either server starts.** MySQL autocommits
DDL, so two processes running `alembic upgrade head` concurrently interleave and leave a
half-applied schema stamped at a revision it does not match; recovery is `DROP DATABASE`.
Nothing in this system migrates on start-up, for that reason. `SECRET_KEY` must already
be in the environment before `alembic upgrade head` will run at all — a migration cannot
be the step that first establishes the environment.

Without the last two seeding steps a correctly installed system answers
`503 NO_PUBLISHED_FACTOR_SET` to every public endpoint. That is expected, not a fault: the
taxonomy is the vocabulary and the factors are the data.

The front end can be developed with no backend at all, against the contract fixtures.
From the **repository root**:

```bash
python -m http.server 8000
# then http://127.0.0.1:8000/web/index.html?mock=1
```

### The layering, and the invariants that hold it up

```
web/    static front end          — renders; calculates nothing but unit conversion
  │  HTTP / JSON
api/    FastAPI                   — validation, rate limiting, error envelopes
  │
  ├── engine/   pure computation
  └── admin/    sqladmin + custom views
        │
db/     SQLAlchemy models and the repository
```

Violating any of the following silently breaks a decision that was made deliberately.
`docs/architecture.md` §3.1 is the long form.

- **`db/repository.py` is the only code that touches the database.** No other module may
  import SQLAlchemy.
- **The engine is a pure function.** `engine.calculate(request, bundle)` takes a
  pre-loaded `FactorBundle` and returns a result. No database access, no file access, no
  system clock. This is what makes the golden test suite meaningful.
- **Metrics are data, not code.** The engine iterates over `metric` rows and evaluates
  each one's stored formula. The test for correctness: adding a metric must mean inserting
  a row and writing one formula, never editing a call site.
- **`DECIMAL` everywhere; `FLOAT` and `DOUBLE` are prohibited.** `decimal.Decimal` on the
  Python side. In JSON, decimals are transmitted **as strings** (`"qty_kg": "1200.500"`)
  because JavaScript's `Number` is a double. The front end may call `Number()` for display
  and charting only, never for a calculation.
- **`code` is the cross-layer identifier.** Requests and responses use the `code` column,
  never the auto-increment `id`. The front end must never learn a primary key.
- **A formula computes one line; the engine performs the summation.**
  `line_value = f(qty_kg, upstream, downstream, const_*)`, then
  `metric_total = Σ line_value`. This is why the expression language needs no arrays, no
  loops and no `sum()` — which is what keeps the evaluator's security boundary
  unambiguous.
- **Impact calculation happens server-side, in exactly one place.** When staff change a
  formula there is no possibility of two sides disagreeing.
- **Taxonomy groupings are tables, not enums.** Which destinations count as waste is an
  MfE definition that may be revised, so `destination_group.is_waste` stays configurable.

### The expression evaluator

There are **two independent AST whitelists and they must agree**:
`admin/expressions.py` validates a formula when staff save it, and `engine/evaluator.py`
evaluates it when the calculator runs. `tests/test_evaluator.py` runs one corpus through
both and fails when they diverge. That test is the only thing stopping them drifting —
eight divergences have been found and closed that way, one of which made the engine
silently evaluate `round(qty_kg)` for a formula written as `round(qty_kg, ndigits=2)`.

If you change one whitelist, change the other, and add the case to the corpus.

### Tests

The suite needs a **real MySQL 8**, not SQLite. `ENUM`, `VARBINARY`, `DECIMAL` precision
and collation ordering all behave differently, the two functional `COALESCE` unique
indexes do not exist on SQLite at all, and a MySQL `CHECK` violation surfaces as
`OperationalError` where SQLite raises `IntegrityError` — which this suite asserts
directly. `tests/conftest.py` connects to `root:devroot@127.0.0.1:3307` (the root
`docker-compose.yml`'s credentials) and creates its own scratch databases.

```bash
docker compose up -d            # MySQL on 3307, if it is not already running
python -m pytest                # the whole suite; takes several minutes
python -m pytest -m "not db"    # skips everything that needs the database
python -m pytest tests/golden   # the correctness suite alone
```

Run one suite at a time. Two concurrent runs share one MySQL server and the admin tests
deadlock against each other.

Two directories carry more weight than the rest:

- **`tests/fixtures/*.json` — the executable form of the contract.** Backend contract
  tests assert that real responses match these shapes, and the front end develops against
  them directly. A contract change that does not reach these files is not finished.
- **`tests/golden/case_*/` — the correctness suite.** Nine cases, three files each
  (`bundle.json`, `request.json`, `expected.json`). Every engine change must leave all of
  them passing. This suite is the only evidence that the calculator computes correctly.

CI runs the same suite against MySQL 8 on every push and pull request, on Python 3.11 —
the version the images run.

### Changing the contract

`docs/interfaces.md` governs every cross-module call. A contract change requires all three
steps, every time:

1. update `docs/interfaces.md` (and its change log);
2. tell the whole team;
3. update `tests/fixtures/*.json`.

Renaming a field unilaterally is the single largest source of rework on a five-person
project.

---

## Where the real documentation lives

| File | What it is |
| --- | --- |
| **`docs/interfaces.md`** | **The contract.** Database schema, engine domain objects, repository signatures, the full REST request and response shapes, front-end module signatures, error codes, fixture conventions. The single source of truth. |
| **`docs/architecture.md`** | System layering, the calculation model, factor-set versioning, the team split, implementation order (§9), deployment notes (§9.1), and the **open items (§10)** — including O-1, the missing real factors. |
| `.env.example` | Every setting, with the failure each one prevents. |
| `docker/compose.yaml`, `docker/nginx.conf` | The deployment topology and the routing, both heavily commented. |
| `docker/compose.deploy.yaml`, `docs/docker-images.md` | The same topology run from the published images, and everything about pulling them: token scopes, which tags exist, and what each error message means. |
| `web/README.md` | The front-end module layout and the no-backend development mode. |
| `db/README.md` | The schema and public API module. |

The English `.md` files under `docs/` are canonical and committed; `.docx` versions are
generated artefacts and are not tracked:

```bash
pandoc docs/interfaces.md -o docs/interfaces.docx --toc --toc-depth=2
```

Front-end work follows `Kai Commitment_Brand Guidelines_v1-Oct25.pdf`. Palette: White
`#FFFFFF`, Kale `#003223` (primary), Orange `#FF5032`, Pea `#28C882`, Blueberry `#005AE6`,
Beetroot `#87005A`, Banana `#FFD76E`, Lavender `#E6BEFF`. Headings in Geologica Bold, body
in Kumbh Sans Regular, both self-hosted under `web/assets/fonts/`.
