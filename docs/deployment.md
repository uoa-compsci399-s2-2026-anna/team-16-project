---
title: "Kai Commitment Food Waste Impact Calculator — Deployment and Handover"
subtitle: "UoA COMPSCI 399 Capstone, Project 18 — Team 16 (502 Bad Gateway)"
date: "2026-09-12 — first AWS deployment"
---

# 1. Scope and Audience

This document exists so the deployment can be **reproduced and handed over**,
not because operating it is the team's responsibility. Per `CLAUDE.md`, DNS,
certificates, hosting and post-semester operations are explicitly out of
scope for the capstone deliverable — the deliverable is source code and
documentation. What follows is the record of the one deployment the team
actually stood up, kept close enough to reproducible fact that a teammate, a
marker, or whoever inherits the machine after the semester can act on it
without guessing.

It carries no secret of any kind. Where a real value has been deliberately
left out, this document says so and says where the real one lives — mostly
the team's out-of-band key inventory, not git. Two kinds of address are
placeholders throughout, both for the same reason: **the site sits behind
Cloudflare's proxy specifically so the origin is not directly reachable**,
and writing the instance's real address, or the operator's home network's
real address, into a committed file undoes that on the spot.

- `<origin-host>` stands for the AWS instance's own address (its public IPv4
  or its `ec2-…compute.amazonaws.com` hostname). A reader rebuilding this
  deployment substitutes the real one from AWS's own console; it is never
  written down here.
- `<operator-ipv4>/32` and `<operator-ipv6>/56` stand for the repository
  owner's home connection — a single IPv4 address and the IPv6 prefix
  delegated to their house. These identify a person's home, not a server,
  and are withheld for that reason.

# 2. The Shape of the Deployment

```
visitor
  │  HTTPS, public
  ▼
Cloudflare              — proxies and terminates the visitor-facing TLS,
(kai.99991314.xyz)        answers from cache or forwards to the origin
  │  HTTPS, Cloudflare → origin
  ▼
nginx on the host       — terminates a second TLS leg with a Cloudflare
(port 443)                Origin CA certificate, restores the caller's real
                           address (§5), proxies to loopback
  │  HTTP, loopback only
  ▼
docker/compose.yaml     — db, migrate (one-shot), api, admin, web(nginx),
stack on 127.0.0.1:18080  reached only through the outer nginx; the security
                           group does not admit 18080 from outside at all
```

Two TLS legs, not one, and each terminates a different thing. Cloudflare
terminates the leg the public actually dials — it is what gives the site a
CDN, a WAF and DDoS absorption in front of a single small instance. The host's
own nginx terminates the second leg, from Cloudflare to the origin, and is
what makes that second leg encrypted rather than a plaintext hop across the
public internet carrying real client submissions. Neither terminator is the
application: the compose stack's own `web` container still speaks plain HTTP
on `127.0.0.1:18080`, exactly as `docker/compose.yaml`'s own header block
describes it for a bare `docker compose up` — the host nginx is additive in
front of it, not a replacement for anything inside the stack.

**Why a Cloudflare Origin CA certificate and not Let's Encrypt.** Two
independent reasons, either of which would be enough on its own:

1. It is valid for fifteen years. A capstone team hands this off at the end
   of a semester; a ninety-day certificate that nobody is contracted to renew
   is a defect with a deadline built in. Fifteen years is close enough to
   "does not expire on anyone's watch" for this project's horizon.
2. It needs no port-80 challenge. The organisation's AWS account guardrail
   automatically deletes any security-group rule that opens SSH — and, more
   to the point here, any rule that opens port 80 — to the world. A
   challenge-based issuer needs exactly the inbound port the guardrail keeps
   closing, so renewal would fail silently on some future night nobody is
   watching. The Origin CA certificate needs no open inbound port at all: it
   is issued once, through the Cloudflare dashboard, and installed by hand.

# 3. Bringing It Up From Nothing

In order, because each step is silent when skipped and loud only much later:

**1. Docker and Compose, pinned, from Docker's own apt repository.** Not the
distribution's `docker.io` package — Docker's `download.docker.com/linux/ubuntu`
repository, added with the signed keyring at `/etc/apt/keyrings/docker.asc`.
Install, then pin to the versions the rest of the fleet runs and hold them so
a routine `apt upgrade` cannot drift a host onto a different Docker than
everyone else's:

| Package | Version |
| --- | --- |
| `docker-ce` / `docker-ce-cli` | 29.7.2 |
| `docker-compose-plugin` | v5.4.0 |

`apt-mark hold` after installing. Verify with a **fresh** login (group
membership for `docker` does not take effect in the session that ran
`usermod`): `docker run --rm hello-world` and `docker compose version`, both
without `sudo`.

**2. The application tree.** `git archive` the commit being deployed and
transfer it — this deployment used `scp` and checked a sha256 on both ends,
which is the cheap insurance against a truncated transfer that a directory
listing alone would not catch. Extract to a directory the deploying account
owns.

**3. `docker/.env`**, mode `600`, in `docker/` **next to `compose.yaml`, not
at the repository root.** This is the trap: Compose reads `.env` from the
compose file's own directory, and a file dropped at the repository root is
silently ignored — every setting quietly takes its coded-in default and
nothing announces that it happened. The file the running deployment uses
contains exactly:

```
KAICALC_PUBLIC_ORIGIN=https://<public-host>
KAICALC_TRUST_FORWARDED_HEADERS=true
KAICALC_NEWS_ORIGIN=https://kaicommitment.org.nz
KAICALC_SESSION_HTTPS_ONLY=true
```

The first three of these are not independent knobs; the first two are a pair
and the third completes the set. `KAICALC_PUBLIC_ORIGIN` is the only setting
that turns on nginx's plaintext-to-HTTPS redirect, and `docker/compose.yaml`'s
own entrypoint refuses to start with it set unless
`KAICALC_TRUST_FORWARDED_HEADERS=true` is set alongside it — without that
flag the stack's inner nginx would see `http` even for traffic that arrived
over real TLS at the outer nginx, and would redirect it to itself forever.
`KAICALC_SESSION_HTTPS_ONLY=true` belongs with them for the same reason: a
session cookie marked `Secure` while the redirect is off is accepted by the
browser and then never sent back, which is a login that fails without
telling anyone why. Set the three together or not at all.
`KAICALC_NEWS_ORIGIN` is unrelated to the other three — it names the site
the home page's news feed reads from — and is included here only because it
is one of the four values this deployment's file actually sets.

**4. The stack.** `docker compose -f docker/compose.yaml up` the first time
only if standing up against an empty database — for a machine that is
replacing another one, do the database and secret restore in §4 first, then
bring the rest of the stack up pointed at the restored volumes.

# 4. Migrating or Restoring the Database

This is the step that loses data if it is done carelessly, so it is recorded
in more detail than the others, including the check that is easy to skip and
is the one that actually matters.

**Capture, without stopping the source.**
`mysqldump --single-transaction --routines --triggers --events
--default-character-set=utf8mb4` against the live database, using the root
password read out of the source container's own environment rather than
guessed or re-typed. `--single-transaction` is what makes this safe against a
database that is still serving traffic: the dump is a single consistent
snapshot, not a set of table scans that could straddle a concurrent write.

**Restore, then compare row counts.** Bring the target's `db` service up
alone, wait for it to report healthy, pipe the dump straight in, then bring
up the rest of the stack. Compare `SELECT COUNT(*)` per table on both sides —
this deployment's restore matched exactly, all 23 tables, 1,072 rows total on
both sides, including the non-obvious ones: `factor_upstream` (732 rows),
`factor_downstream` (127), `submission` and its child tables, and the
`factor_set` rows themselves at every status (`published` **and**
`archived` — a restore that only checks the published set has not checked
whether an archived one silently lost rows).

**Why a row count is not evidence by itself.** Two databases can agree on
every row count and still disagree on what a stored calculation actually
means, because **every `submission` stamps the `factor_set_id` it was
computed against**, precisely so a historical result stays reproducible even
after the active factor set changes. A restore step that reordered rows,
truncated a `DECIMAL` column, or silently renumbered a foreign key would very
likely still produce matching counts while breaking exactly that property.
The check that actually exercises it: pick a submission that predates the
migration, load its stamped factor set with
`db.repository.load_factor_bundle`, run it back through `engine.calculate` on
**both** machines, canonicalise the result to sorted-key JSON, and hash it.
This deployment did that against a submission's factor set that was already
`archived` on both ends — not the currently published one — specifically
because an archived set that still computes correctly is the stronger claim.
Identical sha256 on both machines is the evidence a row count cannot give:
that the numbers this system will quote to the client have not moved.

**A restore mutates the row counts you are about to check, if you are not
careful about ordering.** A smoke-test `POST /api/v1/calculate` (not a
dry-run one) mints a real submission the moment it runs, because one
calculation equals one submission in this system by design (`CLAUDE.md`,
§ Privacy and Data Constraints). Take every count comparison and the
recomputation hash **before** any non-dry-run smoke test, or the two sides
will disagree by exactly the number of test calls made.

# 5. The `kaicalc_secret` Volume

One file lives in it: the generated signing secret, mounted into every
application service and the migrate job so all of them read the same value.
It is what every administrator session and every staff proof are signed
with — issue a new one and every session and every proof silently invalidate
at once, which is indistinguishable from an outage to whoever is logged in
when it happens. It therefore migrates **with** the data, never regenerated
on the new machine.

It moves as a file, not as plaintext left sitting in a home directory
longer than the copy takes: read it out of the source container directly
(`docker cp` from the running application container, not from the volume's
backing path on disk), transfer it, write it into a freshly created
`kaicalc_secret` volume on the target through a disposable container that
mounts the volume and nothing else, matching the source's mode (`600`) and
owning uid/gid (`10001`, the application's non-root user) exactly, and delete
the transferred copy on both ends once `docker exec … stat` on the target
confirms the mode, ownership and byte length all match. Compose will report
that the volume "already exists but was not created by Compose" the first
time the full stack comes up afterwards — expected, because it was created
by hand in the step above, and harmless.

# 6. Cloudflare: Two Things That Will Bite Whoever Comes Next

**SSL mode must be Full (strict), and the order of operations matters more
than the setting.** Flexible mode leaves the Cloudflare-to-origin leg
unencrypted across the public internet — acceptable for a static brochure
site, not for one that carries real client food-waste submissions. But
install the origin certificate and bring up the host nginx's 443 listener
**before** switching Cloudflare to Full (strict), not after: if the origin is
already redirecting plain HTTP to HTTPS while Cloudflare is still set to
Flexible, Cloudflare's own request to the origin arrives as HTTP, gets
redirected to HTTPS, and Cloudflare — still in Flexible — refuses to follow
it over TLS. The two settings loop and Cloudflare answers the visitor with a
520, which looks exactly like a broken origin and is actually a switch
flipped in the wrong order.

**Every request arrives from Cloudflare unless nginx is told otherwise, and
that is not cosmetic.** `real_ip_header CF-Connecting-IP;` together with
`set_real_ip_from` covering Cloudflare's published ranges restores the
visitor's real address before anything downstream sees the connection. Skip
it and every request nginx proxies inward carries the same one apparent
source address — Cloudflare's own edge — regardless of who actually sent it.
The comment at the top of `admin/protection.py` spells out what that does to
this application specifically: its rate limiter counts per apparent address,
so with every caller sharing one address they also share one counter, and one
caller making a request a second can exhaust the limit for every
unauthenticated caller in the deployment — turning the anti-abuse mechanism
into a denial-of-service switch anyone can flip by accident. The ranges
themselves come from `https://www.cloudflare.com/ips-v4` and `/ips-v6`, fetched
directly rather than copied from a blog post, and **they change**: Cloudflare
adds and retires ranges over time, so whoever next edits
`/etc/nginx/conf.d/cloudflare-realip.conf` should re-fetch both URLs rather
than trust that this file is still current.

# 7. The Access Model

`ubuntu` is the administrator account — full `sudo`, the account Docker,
nginx and the certificate were installed under. Four teammate accounts sit in
the `docker` group (so each can run and inspect the stack without `sudo`) and
in `oplimited`, and are **not** in `sudo` at all.

`oplimited`'s policy, `/etc/sudoers.d/50-oplimited`, is a **blacklist**: it
lists the commands a member may not run under `sudo`, rather than the ones
they may. State that plainly to whoever edits it next, because a blacklist
**fails open** — anything not named in the file is implicitly permitted, so
the file is long precisely to keep that implicit-allow surface small, and a
line accidentally dropped from it silently grants back whatever that line
was blocking. For the same reason, the right way to carry this policy to a
new host is to copy the file byte-for-byte (and check its mode is `440`
root-owned and that `visudo -c` still passes with it installed) rather than
to reconstruct it from memory or from a summary of what it does.

fail2ban runs three jails: `sshd` (the standard filter), `sshd-pubkey` (a
custom filter, kept byte-identical to the previous host's so the two stay in
policy lockstep), and `recidive` (escalates against repeat offenders across
jails). Its `ignoreip` **must** contain the operator's own address
(`<operator-ipv4>/32`, alongside `127.0.0.1/8` and `::1`) — port 22 admits
exactly one external source at the security-group level, so a fail2ban rule
that locked the operator's own connection out would have **no local recovery
path**: there is no second door into this host if the one door bans the only
key that opens it.

# 8. The Traps That Cost Time Today

The most useful part of a document like this is the list nobody writes down,
so here it is, each with the reason rather than just the symptom:

- **A bare `curl` against the site returns 403 and looks like a broken
  deployment.** It is not. `db/detection.py` refuses any request that
  carries `Accept: text/html` — an HTML navigation — without a
  `Sec-Fetch-Mode` header, on the reasoning that every browser shipped since
  2020 sends `Sec-Fetch-*` on a real navigation, so a caller with the one
  and not the other is imitating a browser rather than being one. Send the
  header (`-H 'Sec-Fetch-Mode: navigate'`) or use an actual browser to test.

- **`docker/seed_mock_factors.py` does nothing on a machine that has been
  seeded before**, and says so rather than updating anything. It checks
  whether *any* factor set already exists — draft, published or archived —
  and if one does, it makes no change at all, on purpose: once staff have
  touched the factor tables through the admin panel, silently reasserting a
  placeholder set over their work would be the single most destructive thing
  this script could do. The consequence for a redeploy: dropping a new
  `mock-factors.json` onto an already-seeded machine changes nothing until
  the new set is loaded and published through the admin panel deliberately.

- **`docker compose down -v` destroys the database volume along with the
  secret.** It is the correct command for tearing a throwaway environment
  down to nothing, and it is never the right command on a machine holding
  real submissions. `down` (no `-v`) keeps both.

- **Recreating `api` without `web` yields a 502.** The stack's own nginx
  resolves its upstream hostnames when its configuration loads, not per
  request, so an `api` container that changed identity after `web` last
  reloaded is invisible to it until `web` itself is recreated or reloaded.

- **A failed SSH attempt that times out is not the same fault as one that is
  refused, and the two were confused today.** A timeout means the packets
  are being dropped before anything answers — a security-group rule or a
  host firewall rejecting silently — while a connection refused means the
  host is reachable and nothing is listening on that port. Telling the two
  apart correctly is what turns a dead end into "check the security group"
  instead of an hour spent debugging sshd.

# 9. Rollback and Decommissioning

The previous machine is still running, untouched, throughout everything
above: nothing was written to it, its containers' uptimes were checked and
found unchanged at the end of each phase, and it kept serving the live site
the whole time this migration was carried out. It is the rollback path, and
it stays that way until someone deliberately decides otherwise.

Before it is turned off, at minimum: DNS/Cloudflare must be pointed at the
new host and have been observed serving correctly for some real span of
time, not just a single successful smoke test; every submission created on
the new host since cutover must be something the team is content to call the
system of record (there is no path to merge submissions made independently
on both machines after the point they diverge); and the `kaicalc_secret` and
database on the new host must be the ones the team intends to keep, since
after the old machine is gone there is no second copy to fall back to. None
of that is a decision this document makes — it is the repository owner's
call, and it should stay that way, per `CLAUDE.md`'s framing of hosting and
post-semester operations as out of the capstone's scope. This document exists
so that decision can be made from evidence, not so that it is made here.
