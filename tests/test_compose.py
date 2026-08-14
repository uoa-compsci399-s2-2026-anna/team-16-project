"""``docker/compose.deploy.yaml`` is a copy of ``docker/compose.yaml``, and
this is what stops it becoming a stale one.

**Why there are two files at all.** ``docker/compose.yaml`` builds the three
images from the checkout it sits in. Anyone handed only the published images -
which is how the client receives this - has no checkout to build, so a second
file names ``ghcr.io/...`` images and carries no ``build:`` block. Neither
``extends:`` nor ``include:`` can express that: the Compose specification does
not copy ``depends_on`` through ``extends``, and ``depends_on`` is precisely
the part that must not drift (a deployment that starts the API before the
one-shot migrate job has finished is the failure this whole topology exists to
prevent). ``include:`` merges rather than replaces, so the ``build:`` blocks
would come along and ``up`` would build from a source tree that is not there.

So it is a copy, and the copy is asserted rather than trusted. Everything
except the four application ``image:`` values has to survive a round trip
through the YAML parser identically: health checks, dependency order and
conditions, environment, volumes, ports, container names, restart policies,
the project name and the named volumes.

**This project has been burned by exactly this.** ``admin/templates/sqladmin/
_macros.html`` was copied out of a version of sqladmin the panel had never
run and was wrong on the day it was written; the two AST whitelists in
``admin/expressions.py`` and ``engine/evaluator.py`` diverged eight times.
Both were caught by a test that compares the copies, and neither would have
been caught by a test that checks each copy is well formed on its own.

The one thing this file cannot check is whether the tag someone deploys
actually contains the tree these two files describe - that is a property of
the registry, not of the repository. ``docs/docker-images.md`` says so out
loud.

PyYAML is a runtime dependency here, not a test-only one: it arrives with
``uvicorn[standard]`` and ``docker/constraints.txt`` pins it at 6.0.3.
"""

from __future__ import annotations

from pathlib import Path

import re

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
BUILD_FILE = REPO / "docker" / "compose.yaml"
DEPLOY_FILE = REPO / "docker" / "compose.deploy.yaml"
BUILD_WORKFLOW = REPO / ".github" / "workflows" / "_build-images.yaml"
MANIFEST_WORKFLOW = REPO / ".github" / "workflows" / "_publish-manifest.yaml"
README = REPO / "README.md"

#: The registry path CI pushes to. `_build-images.yaml` and
#: `_publish-manifest.yaml` both build it as `ghcr.io/${{ github.repository }}`,
#: which a test cannot expand, so the literal is asserted to appear in both the
#: workflow and the README as well as here - three places that have to agree,
#: and any of them changing alone fails.
IMAGE_PREFIX = "ghcr.io/uoa-compsci399-s2-2026-anna/team-16-project"

#: service name -> the image stem CI publishes for it. `migrate` runs the admin
#: image because the commands it runs are the admin CLI's.
PUBLISHED_IMAGE = {
    "migrate": "kaicalc-admin",
    "api": "kaicalc-api",
    "admin": "kaicalc-admin",
    "web": "kaicalc-web",
}

#: `db` is not one of ours. Its image must be identical in both files, digest
#: and all, so it is deliberately absent from PUBLISHED_IMAGE and is compared
#: whole below.
NOT_OURS = "db"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def build() -> dict:
    return _load(BUILD_FILE)


@pytest.fixture(scope="module")
def deploy() -> dict:
    return _load(DEPLOY_FILE)


# ---------------------------------------------------------------------------
# The two files are the same deployment
# ---------------------------------------------------------------------------


def test_same_project_name_and_volumes(build: dict, deploy: dict) -> None:
    """Everything outside `services:` is the same stack.

    The project name and the named volumes decide which containers and which
    data a command addresses. Two files that disagree here are two different
    deployments wearing one name, and `down` run against the wrong one is a
    no-op that looks like success.
    """
    for key in set(build) | set(deploy):
        if key == "services":
            continue
        assert build.get(key) == deploy.get(key), (
            f"docker/compose.yaml and docker/compose.deploy.yaml disagree on "
            f"top-level `{key}`"
        )


def test_same_services(build: dict, deploy: dict) -> None:
    assert set(build["services"]) == set(deploy["services"])
    # Anchor: a file that lost its services section would satisfy the equality
    # above and prove nothing.
    assert set(build["services"]) == {"db", "migrate", "api", "admin", "web"}


def test_database_service_is_identical(build: dict, deploy: dict) -> None:
    """`db` is compared whole, image included.

    MySQL is not one of our images, so there is no published-versus-built
    distinction to draw for it. Both files must pin the same digest: a
    deployment that silently runs a different MySQL point release from the one
    the suite was verified against is the kind of difference nobody attributes
    correctly.
    """
    assert build["services"][NOT_OURS] == deploy["services"][NOT_OURS]


@pytest.mark.parametrize("service", sorted(PUBLISHED_IMAGE))
def test_service_is_identical_apart_from_the_image(
    build: dict, deploy: dict, service: str
) -> None:
    """Every key except `image` (and the build path's `build:`) matches.

    This is the whole point of the file. `depends_on` and its conditions,
    the health checks, `environment`, `volumes`, `ports`, `container_name`,
    `restart`, `command` - all of it.
    """
    built = dict(build["services"][service])
    deployed = dict(deploy["services"][service])

    # Anchors. Both pops must remove something that was really there; if the
    # build file ever loses its `build:` block, or the two files come to name
    # the same image, this test would still pass while asserting much less
    # than it claims to.
    assert "build" in built, (
        f"docker/compose.yaml no longer builds `{service}` - it is supposed to "
        f"be the build-from-source path"
    )
    assert "build" not in deployed
    assert built["image"] != deployed["image"], (
        f"`{service}` names the same image in both files, so this comparison "
        f"is no longer comparing a build path against a deployment path"
    )

    built.pop("build")
    built.pop("image")
    deployed.pop("image")

    assert built == deployed, (
        f"`{service}` has drifted between docker/compose.yaml and "
        f"docker/compose.deploy.yaml. Change compose.yaml, then bring "
        f"compose.deploy.yaml back into line - the annotated original is "
        f"compose.yaml."
    )


def test_deploy_file_builds_nothing(deploy: dict) -> None:
    """A `build:` block anywhere in the deployment file is a bug.

    It is handed to people who have the images and not the source. `up` would
    fail on a missing build context, or - worse, on a machine that does have a
    checkout - succeed by building something other than the tag they asked for.
    """
    for name, service in deploy["services"].items():
        assert "build" not in service, f"`{name}` still has a build: block"


# ---------------------------------------------------------------------------
# The deployment file names images that actually get published
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("service,stem", sorted(PUBLISHED_IMAGE.items()))
def test_deploy_images_are_the_published_ones(
    deploy: dict, service: str, stem: str
) -> None:
    """`<prefix>/<stem>:<tag>`, with the prefix defaulted and the tag not."""
    image = deploy["services"][service]["image"]
    expected_head = (
        "${KAICALC_IMAGE_PREFIX:-" + IMAGE_PREFIX + "}/" + stem + ":"
    )
    assert image.startswith(expected_head), (
        f"`{service}` should run {IMAGE_PREFIX}/{stem}, but names {image!r}"
    )
    tag = image[len(expected_head) :]
    assert tag.startswith("${KAICALC_IMAGE_TAG:?"), (
        f"`{service}`'s tag must be required rather than defaulted, so that a "
        f"deployment states which build it is running: got {tag!r}"
    )


def test_the_prefix_agrees_with_what_ci_pushes() -> None:
    """The three places that spell out the registry path have to agree.

    The workflows compute it as `ghcr.io/${{ github.repository }}`, which
    cannot be expanded here, so this asserts the shape in the workflows and the
    literal in the two documents that quote it. It cannot prove the repository
    is still called that - a rename breaks all three at once, which is the
    point.
    """
    for workflow in (BUILD_WORKFLOW, MANIFEST_WORKFLOW):
        text = workflow.read_text(encoding="utf-8")
        assert "IMAGE_PREFIX: ghcr.io/${{ github.repository }}" in text, (
            f"{workflow.name} no longer pushes to ghcr.io under the "
            f"repository's own namespace"
        )
    assert IMAGE_PREFIX in BUILD_WORKFLOW.read_text(encoding="utf-8")
    assert IMAGE_PREFIX in README.read_text(encoding="utf-8")


def test_ci_builds_exactly_these_image_stems() -> None:
    """CI builds `api`, `admin` and `web` and pushes `kaicalc-<name>`.

    A fourth image, or a renamed one, has to reach the deployment file too -
    otherwise the file quietly deploys a subset and the missing service fails
    to pull with `manifest unknown`.
    """
    text = BUILD_WORKFLOW.read_text(encoding="utf-8")
    for name in ("api", "admin", "web"):
        assert f"{name}:docker/{name}.Dockerfile" in text
    assert '-t "kaicalc-${name}:ci"' in text
    assert '"${IMAGE_PREFIX}/kaicalc-${name}:${PREFIX}-${{ matrix.arch }}"' in text
    assert set(PUBLISHED_IMAGE.values()) == {
        "kaicalc-api",
        "kaicalc-admin",
        "kaicalc-web",
    }


# ---------------------------------------------------------------------------
# The database health check, in both files
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", [BUILD_FILE, DEPLOY_FILE], ids=["build", "deploy"])
def test_db_healthcheck_cannot_pass_over_the_init_socket(path: Path) -> None:
    """The check must speak TCP, and must run a statement.

    Measured, and the reason this test exists. `mysqladmin ping -h localhost`
    reported the database healthy 2.6 seconds before it existed: mysql:8.0
    initialises behind a temporary server started with `--skip-networking`
    ("ready for connections ... port: 0"), `-h localhost` makes a client use
    the UNIX socket and so reach it, and `mysqladmin ping` exits 0 while
    printing `Access denied`. Compose marked `db` healthy, `migrate` started,
    and `migrate` died on `[Errno 111] Connection refused` 0.6 s before the
    real server bound 3306 - taking `api`, `admin` and `web` with it, because
    they wait on `service_completed_successfully`.

    `start_period` does not help: it suppresses failures inside the window, not
    a premature success.

    Two properties, therefore, and both are load-bearing:

    * it connects over TCP to an address, never to `localhost`, so the
      init-time socket is unreachable to it;
    * it executes a statement as the application's own user against the
      application's own database, so nothing but the fully initialised server
      with the grants applied can answer it. `ping` is not enough even over
      TCP - it is answered before the grants exist.
    """
    compose = _load(path)
    test = compose["services"]["db"]["healthcheck"]["test"]
    assert test[0] == "CMD", "a shell form would reintroduce shell quoting here"
    argv = test[1:]
    joined = " ".join(argv)

    assert argv[0] == "mysql", (
        f"`mysqladmin ping` exits 0 against the initialisation-time server. "
        f"Got {argv[0]!r}"
    )
    assert "--protocol=TCP" in argv, "the check must force TCP explicitly"
    assert "127.0.0.1" in argv, "the check must connect to an address"
    assert "localhost" not in joined, (
        "`localhost` makes a mysql client use the UNIX socket, which is exactly "
        "how the old check reached the temporary init server and passed"
    )
    assert "-e" in argv and "SELECT 1" in argv, (
        "the check must run a statement, not merely open a connection"
    )
    assert "-P" in argv and "3306" in argv


@pytest.mark.parametrize("path", [BUILD_FILE, DEPLOY_FILE], ids=["build", "deploy"])
def test_db_healthcheck_uses_the_application_credentials(path: Path) -> None:
    """The health check and the application must read the same variables.

    Otherwise the check can go on passing as root against a server on which
    `MYSQL_USER` was never created - which is the state `migrate` fails in, and
    the state this check exists to keep it out of.
    """
    db = _load(path)["services"]["db"]
    argv = db["healthcheck"]["test"][1:]
    environment = db["environment"]

    assert environment["MYSQL_USER"] in argv
    assert environment["MYSQL_DATABASE"] in argv
    assert f"-p{environment['MYSQL_PASSWORD']}" in argv


# ---------------------------------------------------------------------------
# The access log, and the four fields it is not allowed to hold
# ---------------------------------------------------------------------------


def test_the_access_log_format_names_no_identifying_field() -> None:
    """`docker/nginx.conf`'s `kaicalc` format, held to §2.3.

    The base image's `main` format logged `$remote_addr`, `$http_user_agent`,
    `$http_referer` and `$http_x_forwarded_for` on every request to a
    calculator whose stated position is that it stores nothing identifying
    about a visitor. That was live until 2026-08-12. The format was rewritten;
    this is what stops it being rewritten back, one variable at a time.

    **`$http_cookie` is in the list for a newer reason.** The language chooser
    (§7.7) stores `kaicalc_lang` at `path=/`, which cannot be scoped narrower
    when the calculator at `/` and the panel at `/admin` both read it - so the
    cookie rides along on every request logged here, including every
    `POST /api/v1/calculate`. Its value space is closed and carries no entropy,
    so logging it would not identify anyone on its own; it would sit on the same
    line as a path and a timestamp, and "this is read and forgotten" would stop
    being true of the deployment while staying true of the database.

    Asserted against the directive rather than the whole file, because the
    file's own comments name all five variables in order to forbid them - a
    substring search over the raw text passes or fails on the prose instead of
    on the format, which is the defect `tests/web/i18n_keys.py` was already
    bitten by once.
    """
    conf = (REPO / "docker" / "nginx.conf").read_text(encoding="utf-8")

    body = re.sub(r"^\s*#.*$", "", conf, flags=re.M)
    formats = re.findall(r"log_format\s+\w+\s+(.*?);", body, flags=re.S)
    assert formats, "no log_format directive found in docker/nginx.conf"

    forbidden = (
        "$remote_addr",
        "$http_user_agent",
        "$http_referer",
        "$http_x_forwarded_for",
        "$http_cookie",
        "$http_accept_language",
        "$binary_remote_addr",
    )
    for declared in formats:
        for variable in forbidden:
            assert variable not in declared, (
                f"{variable} is back in the access log format: {declared.strip()}"
            )

    # Anchored: a format that had lost its contents would satisfy every
    # assertion above. The log still has to be worth keeping.
    joined = " ".join(formats)
    for needed in ("$status", "$request"):
        assert needed in joined, f"the access log no longer records {needed}"

    # And the server actually uses the constrained format rather than declaring
    # it and falling back to the base image's `main`.
    assert re.search(r"access_log\s+\S+\s+kaicalc\s*;", body), (
        "the server block does not use the kaicalc log format"
    )
