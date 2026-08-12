<!--
  THE RELEASE NOTES. This file is the body of every GitHub Release, with the
  __PLACEHOLDERS__ substituted by .github/workflows/release.yaml.

  WRITE FOR SOMEONE WHO HAS NOT READ THE REPOSITORY. Whoever deploys this has
  a release page, three image names and an hour. Every deployment fact that
  can silently destroy data, silently disable a security control, or silently
  publish placeholder numbers as if they were real is stated HERE, in full, in
  the text - not linked. A link to docs/architecture.md is a link they will
  not open, and the five facts below are the five that fail quietly.

  Keep it in this file rather than in a heredoc inside the workflow: it is
  prose the team will want to edit, and .github/ is excluded by .dockerignore,
  so it can never reach an image.
-->

# Kai Commitment Food Waste Impact Calculator __VERSION__

A New Zealand food waste impact calculator: a dual-scenario calculator
(`current` vs `alternative`), a public JSON API, a staff administration panel
for the factor data, and a statistics view over the calculations the tool has
run.

Built from commit `__COMMIT__`.

---

## What is in this release

**Attached below**

| File | What it is |
| --- | --- |
| `kaicalc-__VERSION__-py3-none-any.whl` | The Python package: `api`, `admin`, `db`, `engine`, the Alembic migration tree and the `kaicalc-admin` command. |
| `kaicalc-__VERSION__.tar.gz` | The same, as a source distribution. |

**Container images** — three, each published for __ARCH_LINE__:

```
__IMAGE_PREFIX__/kaicalc-api:__VERSION__
__IMAGE_PREFIX__/kaicalc-admin:__VERSION__
__IMAGE_PREFIX__/kaicalc-web:__VERSION__
```

Each is also tagged `latest`. Prefer the version tag in anything you deploy:
`latest` moves under you the next time somebody cuts a release.

### Pulling them: log in first

**These packages are private, deliberately.** The university created this
repository as a private one and GitHub Container Registry inherits that, so an
anonymous `docker pull` returns an authentication error rather than the image.
That is expected, not a broken release — anyone holding a GitHub token can
pull.

Two commands. Use a personal access token, not your password:

```
echo "$GITHUB_TOKEN" | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin
docker pull __IMAGE_PREFIX__/kaicalc-api:__VERSION__
```

The token must carry the **`read:packages`** scope. This is the part that
catches people out: read access to the repository on its own is not enough — a
classic token with only `repo` is refused with `denied` at pull time, and the
message does not mention the scope. Create the token at
**Settings → Developer settings → Personal access tokens**, tick
`read:packages`, and try again.

---

## Running it

The supported path is the compose topology in the source tree, which is shaped
around the deployment facts below rather than leaving them to a reader:

```
git checkout __TAG__
docker compose -f docker/compose.yaml up
```

Then <http://localhost:18080/> is a working calculator — migrated, seeded, with
a published factor set and two administrator accounts whose one-time passwords
are printed by:

```
docker compose -f docker/compose.yaml logs migrate
```

Nothing binds port 80, 8000, 8080, 3000 or 5000. nginx is on 18080; the API and
the panel are additionally published on 18000 and 18001 for development, and
those two `ports:` blocks are meant to be removed in production (see fact 3).

---

## Read this before deploying anywhere that is not a laptop

Five facts. Each of them fails **silently** — no exception, no log line, no
failed health check — which is why they are here rather than in a document.

### 1. `alembic upgrade head` runs once, from one process

MySQL autocommits DDL. Two application replicas running the migration
concurrently do not deadlock and do not fail cleanly: they interleave, and the
result is a half-applied schema stamped at a revision it does not match. The
recovery from that is `DROP DATABASE`.

There is deliberately **no migrate-on-startup hook anywhere in this system**.
The shipped compose file expresses this in the topology instead — a one-shot
`migrate` service that runs to completion, with every application service
waiting on `condition: service_completed_successfully`. There is no arrangement
of that file in which two processes migrate at once, and no way to start the
application against a database that was never migrated.

If you deploy some other way — Kubernetes, a hand-rolled systemd unit, two
containers behind a load balancer — **that guarantee is yours to reproduce.**
Run the migration as a single job that completes before anything else starts.

### 2. `SECRET_KEY` must be byte-identical in the API and the admin panel

Both applications derive the IP blocklist fingerprint key from `SECRET_KEY`
independently, through HKDF-SHA256 with a pinned `info` string
(`kaicalc-blocklist-v1`). The blocklist stores `HMAC(address)`, never an
address.

Two different secrets therefore produce two different fingerprints for the same
address. A block applied in the panel is written under one fingerprint and
looked up at the API under another: **the block silently fails to hold, the
panel still shows it as active, and nothing raises on either side.**

The shipped compose file makes this structural — one generated secret in a
shared volume that every service reads. If you inject the secret yourself, from
a secret manager or an environment file, inject the *same value* everywhere.
`SECRET_KEY` is also what signs the staff session cookie, so rotating it logs
every administrator out; that part, at least, is visible.

### 3. `PROTECTION_TRUSTED_PROXY` defaults to false, and there will be a proxy in front

It states one fact about the deployment: *is there a reverse proxy in front of
this?* Left false behind one, every caller arrives as the proxy's own address,
and the rate limits collapse into shared counters.

**The cost is concrete.** The API allows 600 GETs and 120 calculates per hour,
keyed on the caller's address. Behind a proxy with this false, every caller *is*
the proxy — so those become **600 GETs and 120 calculates per hour site-wide**
rather than per visitor. One enthusiastic afternoon in a classroom rate-limits
the public site.

Set it to `true` **only once you have confirmed the proxy overwrites
`X-Forwarded-For`** rather than appending to it. The applications read the
left-most entry, so a proxy that appends lets any caller name their own address
— which is worse than the shared counter: one visitor can forge another's
address, and a single block can then deny everyone. The nginx configuration
shipped here overwrites the header with `$remote_addr`; that has been verified
against an echo upstream.

It is a **paired edit**, never one without the other:

* set `PROTECTION_TRUSTED_PROXY=true`, **and**
* delete the `ports:` blocks from the `api` and `admin` services.

While those ports are published, a caller can bypass nginx entirely and send
whatever `X-Forwarded-For` they like — and with the flag now true, it will be
believed.

### 4. `SESSION_HTTPS_ONLY` is false in the shipped compose file

Deliberately. The out-of-the-box stack is plain HTTP on `localhost:18080`, and
with the flag true the browser accepts the login, receives a `Secure` cookie,
refuses to send it back, and bounces straight to the login page — a panel
nobody can enter.

**Anyone putting TLS in front must set it to `true`**, at the same time as
removing the direct `ports:` blocks in fact 3. Over HTTPS, leaving it false is
what lets a single plain-HTTP navigation on the admin origin hand a live staff
session to anyone watching the network.

The setting belongs to the admin panel — the public API holds no session — and
the panel logs a warning at start-up for as long as it is false. That warning
is the only thing that will remind you, so it is worth reading
`docker compose logs admin` once after any change to the front of the stack.

### 5. The emissions factors in this release are placeholders

The Kai Commitment has not yet supplied the real New Zealand emissions factors
(tracked as open item O-1). Everything this release computes is arithmetically
correct and **numerically meaningless**: the seeded factor set is derived from
the engine's canonical test case, not from published New Zealand data.

That factor set is flagged `is_mock = true`, and while it is, the placeholder
warning banner on every results view and every export is **mandatory and
non-dismissible**. Do not remove it, do not make it dismissible, and do not
publish a screenshot of a result without it.

Replacing the factors is a data task in the admin panel and needs no code
change and no new release: clone the published set into a new draft, edit the
factors, turn `is_mock` off **on the draft**, then publish it. In that order —
the panel refuses to change `is_mock` on a set that is already published,
precisely so that nobody can switch the banner off while the numbers
underneath are still placeholders.

---

## What `pip install` alone does *not* give you

Installing the wheel gives you the code, the `kaicalc-admin` command and the
migration tree. It does **not** give you a working calculator:

* `kaicalc-admin seed-taxonomy` creates the taxonomy, but nothing in the
  package creates a **factor set**, and
* with no published factor set, `get_published_factor_set_id` raises — which
  takes `GET /taxonomy` down along with `POST /calculate`, so the calculator
  page cannot even build its dropdowns.

Only the container path seeds one today (`docker/seed_mock_factors.py`, run as
step 3 of the `migrate` service). Moving that into
`kaicalc-admin seed-mock-factors` is tracked as blocking work; until it lands,
**a pip install is a library install, and the compose topology is the way to
run the system.**

---

## Known limitations in this release

* **The factors are mock** (O-1, above). Every release until the client
  supplies real data ships placeholder numbers behind a mandatory banner.
* **The cost metric is waste levy and disposal cost only** (O-2). Whether the
  value of the wasted food itself is included, and at what price, is
  unresolved; the food-value constant defaults to zero.
* **The unit presets are placeholder conversions too** (O-6). The bucket and
  wheelie-bin sizes that convert a volume to kilograms have not been measured
  by the client. Every one of those rows says so in its `source_note`, but
  they sit in front of the mock-data banner rather than behind it: a visitor
  entering "three wheelie bins" is converted by an estimate. Replace them
  before the calculator is published.
* **No deployment has run behind a real reverse proxy.** The
  `X-Forwarded-For` handling in fact 3 is correct by construction and verified
  against a test upstream, not in production.
* **Hosting, DNS, TLS and post-semester operation are out of scope** for this
  project. The deliverable is source code, documentation and these artefacts.

---

Scope note for anything built on the statistics view: the numbers describe the
calculations run *in this tool*, by a self-selected set of visitors. They are
not a measurement of New Zealand.
