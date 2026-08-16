# The published images, and how to run them

Two different things get confused with each other, and the confusion is the reason most
"I can't pull the image" reports happen. Read this table before anything else.

| You have… | Use | GHCR login? |
| --- | --- | --- |
| **the source** (this repository) | `docker compose -f docker/compose.yaml up -d` | **No.** Nothing of ours is pulled. |
| **the images** and no source | `docker compose -f docker/compose.deploy.yaml up -d` | **Yes**, with the right token scope |

`docker/compose.yaml` **builds** `kaicalc-api:local`, `kaicalc-admin:local` and
`kaicalc-web:local` from the tree in front of you. If that is what you are doing, GHCR is
irrelevant and you can stop reading after §1.

`docker/compose.deploy.yaml` **pulls** the images CI published and runs them through the
identical topology. It exists because the images previously had no consumer anywhere in
the repository: `git grep ghcr.io` found the workflows that push them and one sentence of
README, and nothing that starts them. Anyone handed the images had to invent their own
orchestration, which is how you end up with one container running and no front door.

---

## 1. If you have the source

```bash
docker compose -f docker/compose.yaml up -d
```

First run takes a few minutes (three image builds, plus the MySQL pull). Then the
calculator is at <http://localhost:18080/>, the API under
<http://localhost:18080/api/v1/> and the panel at <http://localhost:18080/admin>. That one
port is the only one published: nginx is the only way in, which is what lets the rate
limit and the blocklist key on the visitor rather than on the proxy.
`docker/compose.direct-ports.yaml` puts the API back on 18000 and the panel on 18001 for
development, and turns that guarantee off in the same file. README's *How to run it* is the
full version,
including the three things that stop this command on a machine that has never run it.

---

## 2. If you have the images

### 2.1 They live here

```
ghcr.io/uoa-compsci399-s2-2026-anna/team-16-project/kaicalc-api
ghcr.io/uoa-compsci399-s2-2026-anna/team-16-project/kaicalc-admin
ghcr.io/uoa-compsci399-s2-2026-anna/team-16-project/kaicalc-web
```

The path is namespaced under the **repository**, not bare under the organisation.
`.github/workflows/_build-images.yaml` builds it as `ghcr.io/${{ github.repository }}` and
says why.

### 2.2 Which tag — this catches most people

| Tag | Published by | Immutable? | Pin a deployment to it? |
| --- | --- | --- | --- |
| `X.Y.Z`, e.g. `1.2.0` | the release workflow | yes | **yes — this is the one** |
| `latest` | the release workflow | no, moves every release | no |
| `<short-sha>`, e.g. `612064a` | CI, every push to `main` | yes | yes, for one specific build |
| `alpha` | CI, same run | no, moves to the head of `main` | no |

**Check what exists before you pin.** At the time of writing no release had been cut, so
there was no `latest` and no version tag; a `docker pull …:latest` that fails with
`manifest unknown` means the tag does not exist, not that anything is broken. The current
list is on the repository's **Packages** page, or:

```bash
gh api "/orgs/uoa-compsci399-s2-2026-anna/packages/container/team-16-project%2Fkaicalc-api/versions" \
  --jq '.[].metadata.container.tags[]'
```

### 2.3 The token needs `read:packages`, and `gh auth login` does not grant it

The repository is private and GHCR inherits that, so an anonymous pull is refused. That is
expected, not a broken build.

**Repository access is not enough.** A token carrying `repo` alone is refused at pull time
with `denied`, and the message does not name the missing scope. It must carry
**`read:packages`**. A default `gh auth login` token does not: the scopes it asks for are
`gist`, `project`, `read:org`, `repo`, `workflow`, and with those even *listing* packages
answers

```
You need at least read:packages scope to list packages.  (HTTP 403)
```

Two ways to get one. Either add the scope to the token `gh` already holds:

```bash
gh auth refresh -h github.com -s read:packages
gh auth token | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin
```

or create a classic personal access token by hand — **GitHub → Settings → Developer
settings → Personal access tokens → Tokens (classic)**, tick `read:packages`:

```bash
export CR_PAT=ghp_xxxxxxxxxxxxxxxxxxxx
echo "$CR_PAT" | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin
```

PowerShell:

```powershell
$env:CR_PAT = "ghp_xxxxxxxxxxxxxxxxxxxx"
$env:CR_PAT | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin
```

The token is a credential. Pass it through the environment or standard input as above;
never put it in a compose file, a script or a commit.

### 2.4 Start the stack

```bash
export KAICALC_IMAGE_TAG=1.2.0        # required. No default, deliberately.
docker compose -f docker/compose.deploy.yaml pull
docker compose -f docker/compose.deploy.yaml up -d
```

Then <http://localhost:18080/>, and the two administrator one-time passwords are in
`docker compose -f docker/compose.deploy.yaml logs migrate`. Everything else — the ports
and their environment variables, `SECRET_KEY`, the admin CLI, teardown — is exactly as
README describes for the build path, because it is the same topology.

**There is no default tag.** `up` without `KAICALC_IMAGE_TAG` fails immediately with the
message that asks you to set one, rather than quietly resolving `latest` and running
something nobody chose. A deployment should be able to say which build it is running.

**`pull` before `up` is not decoration.** Compose's default pull policy is `missing`, so a
moving tag (`alpha`, `latest`) that is already in your local image store is reused and the
registry is never consulted. With a pinned `X.Y.Z` it makes no difference.

**Override the prefix** with `KAICALC_IMAGE_PREFIX` if you have mirrored the images into
another registry.

### 2.5 Architecture

`linux/amd64` is always published. `linux/arm64` is best-effort — that build leg is
allowed to fail without failing the run, so a tag can ship amd64 only. On Apple Silicon,
if a pull reports no matching manifest:

```bash
docker pull --platform linux/amd64 ghcr.io/uoa-compsci399-s2-2026-anna/team-16-project/kaicalc-api:alpha
```

It runs under emulation: slower, correct. To see what a tag actually holds:

```bash
docker buildx imagetools inspect ghcr.io/uoa-compsci399-s2-2026-anna/team-16-project/kaicalc-api:alpha
```

---

## 3. What the tag pins, and what it does not

The images are self-contained. `docker/web.Dockerfile` bakes in the static front end and
`docker/nginx.conf`; `docker/admin.Dockerfile` bakes in `docker/init.sh` and the mock
factor data. `docker/compose.deploy.yaml` mounts no source at all. So:

- the tag pins the routing and the seed data as well as the code;
- **editing `docker/nginx.conf` and re-running `compose.deploy.yaml` changes nothing.**
  That is `docker/compose.yaml`'s job;
- the two compose files being identical is a statement about *this* repository, not about
  the tag you deployed. A tag built from an older commit carries that commit's nginx
  configuration and that commit's health checks.

The one thing that is **not** pinned by the tag is the database: both files run the same
MySQL 8.0 image, by digest, and `tests/test_compose.py` requires the digests to match.

---

## 4. How the two compose files are kept from drifting

They are two files describing one deployment, so the second one is a copy, and the copy is
asserted rather than trusted. `tests/test_compose.py` parses both and requires every key
except the four application `image:` values to be identical: health checks, `depends_on`
and its conditions, environment, volumes, ports, container names, the project name, the
named volumes.

`extends:` was the obvious alternative and does not work here — the Compose specification
does not copy `depends_on` through `extends`, and `depends_on` is exactly the part that
must not drift. `include:` merges rather than replaces, so the `build:` blocks would come
along and `up` would try to build from a source tree that is not there.

**Change `docker/compose.yaml`, then bring `docker/compose.deploy.yaml` back into line.**
The annotated original — every comment explaining why a setting is what it is — is
`compose.yaml`. The test names the file that has drifted.

---

## 5. Error messages, and what each one means

| What you see | What it means | Fix |
| --- | --- | --- |
| `required variable KAICALC_IMAGE_TAG is missing a value` | You did not choose a tag | §2.4 |
| `denied` / `denied: denied` | Logged in, but the token lacks `read:packages` | §2.3 |
| `unauthorized: authentication required` | Not logged in to `ghcr.io` at all | §2.3 |
| `manifest unknown` / `not found` | That tag does not exist — usually `latest` or a version before any release was cut | §2.2 |
| `no matching manifest for linux/arm64` | That tag shipped amd64 only | `--platform linux/amd64`, §2.5 |
| Login succeeds, pull still denied | Authenticated as an account without access to this private repository | Check the account is in the organisation |
| `Head …/kaicalc-api:local: denied` | You are running `compose.yaml` with `--no-build` on a machine that has never built | Drop `--no-build`, or use `compose.deploy.yaml` |
| `SECRET_KEY is required` | A container started by hand, without the image's entrypoint | Use compose, or pass `SECRET_KEY` |
| `permission denied … /var/run/docker.sock` | Your user is not in the `docker` group | README, *Three things that stop the command above* |
| `address already in use` | 18080 was taken, or 18000/18001 were with the direct-ports overlay in force; see the symptom in README | `KAICALC_WEB_PORT`, and `KAICALC_API_PORT`/`KAICALC_ADMIN_PORT` with that overlay |

Check a login is really working:

```bash
docker logout ghcr.io
gh auth token | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin
docker pull ghcr.io/uoa-compsci399-s2-2026-anna/team-16-project/kaicalc-web:alpha
```

---

## 6. Short answer, for pasting into chat

> If you have the repository, you do not need to pull anything:
> `docker compose -f docker/compose.yaml up -d` builds the images locally and never
> touches GHCR. If you have only the images, use `docker/compose.deploy.yaml` — log in to
> `ghcr.io` with a token carrying the **`read:packages`** scope (repository access alone
> is refused, and `gh auth login` does not grant it: `gh auth refresh -h github.com -s
> read:packages`), then set `KAICALC_IMAGE_TAG` to the tag you want and
> `pull` before `up`. There is no default tag on purpose.
