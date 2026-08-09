# kaicalc-api - the public JSON API (api.app:create_app), on port 18000.
#
# Build context is the REPOSITORY ROOT, not this directory:
#     docker build -f docker/api.Dockerfile -t kaicalc-api:local .
# docker/compose.yaml sets `context: ..` for the same reason - the wheel is
# built from pyproject.toml at the root.
#
# THIS FILE AND docker/admin.Dockerfile ARE THE SAME IMAGE WITH A DIFFERENT
# CMD. That is deliberate and worth keeping: `api` and `admin` are two
# independent ASGI applications built from one wheel; neither mounts the other
# and api/ may not import admin/. Two images, one build recipe. If you change
# anything above the CMD here, change it there too.
#
# What this image does NOT contain: the front end. `web/` is served by
# kaicalc-web (nginx). An API image that also served the static files would
# make the reverse proxy optional, and the moment it is optional somebody
# deploys without it and `web/js/api.js`'s relative API_BASE stops being
# same-origin.

# ---------------------------------------------------------------------------
# Stage 1 - build the wheel
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS build

WORKDIR /src

# Only what the wheel is built from. Listed file by file rather than `COPY . .`
# so that a change to web/ or to a document does not invalidate this layer, and
# so that nothing can be swept in by accident - .dockerignore is the second
# line of defence here, not the only one.
COPY pyproject.toml MANIFEST.in ./
COPY alembic.ini ./
COPY alembic/ ./alembic/
COPY admin/ ./admin/
COPY api/ ./api/
COPY db/ ./db/
COPY engine/ ./engine/

# `pip wheel`, NOT `pip install -e .`.
#
# setuptools' PEP 660 editable backend drops [tool.setuptools.data-files]
# entirely: an editable install produces NO <prefix>/share/kaicalc at all, so
# the whole Alembic migration tree - alembic.ini, env.py and all nine
# revisions - would silently be absent from the image, and the migrate service
# would fail with "cannot find alembic.ini" on a machine that has never seen
# this repository. See the warning above [tool.setuptools.data-files] in
# pyproject.toml.
RUN pip wheel --no-deps --no-cache-dir --wheel-dir /wheels .

# ---------------------------------------------------------------------------
# Stage 2 - runtime
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

# PYTHONUNBUFFERED is load-bearing, not hygiene. Python block-buffers stdout
# when it is a pipe, which is what `docker compose logs` gives it. Without
# this, admin/cli.py's `report_bootstrap_result` - the one place the initial
# administrator passwords are ever printed - can sit in a 8 KiB buffer and
# never reach the log at all if the process is killed. A system nobody can log
# into is not out-of-the-box.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Non-root. uid/gid pinned so a bind-mounted volume has predictable ownership.
RUN groupadd --system --gid 10001 kaicalc \
 && useradd --system --uid 10001 --gid kaicalc --home-dir /app --shell /usr/sbin/nologin kaicalc

COPY --from=build /wheels/*.whl /tmp/wheels/
RUN pip install --no-cache-dir /tmp/wheels/*.whl && rm -rf /tmp/wheels

WORKDIR /app

# run.sh is mode 100755 in git (`git ls-tree HEAD run.sh` -> 100755). COPY
# preserves that, so no chmod is needed and none is done: a chmod here would
# mask a regression in the index mode, and that regression is invisible on a
# Windows checkout with core.filemode=false - it was a real defect on this
# branch, caught only by reading the index. If this image ever fails with
# "permission denied" on run.sh, the bug is in git, not here.
COPY run.sh /app/run.sh
COPY docker/entrypoint.sh /usr/local/bin/kaicalc-entrypoint
COPY docker/kaicalc-wrapper.sh /usr/local/bin/kaicalc
COPY docker/init.sh /app/init.sh
COPY docker/seed_mock_factors.py docker/mock-factors.json /app/

# init.sh and the seed script are only used by the `migrate` service, which
# runs the admin image. They are here as well so the two Dockerfiles stay
# byte-comparable above the CMD; they cost a few kilobytes and remove a reason
# for the files to drift.
RUN chmod +x /usr/local/bin/kaicalc-entrypoint /usr/local/bin/kaicalc /app/init.sh \
 && mkdir -p /var/lib/kaicalc \
 && chown kaicalc:kaicalc /app /var/lib/kaicalc

USER kaicalc

# 0.0.0.0 because a container that binds 127.0.0.1 is reachable from nothing.
# The ports are run.sh's own defaults, restated here so `docker run` without
# compose behaves the same way. Nothing binds 80, 8000, 8080, 3000 or 5000.
ENV KAICALC_HOST=0.0.0.0 \
    KAICALC_API_PORT=18000 \
    KAICALC_ADMIN_PORT=18001 \
    KAICALC_STATE_DIR=/var/lib/kaicalc

EXPOSE 18000

# The entrypoint resolves SECRET_KEY (see docker/entrypoint.sh) and execs the
# command. `docker exec` does NOT run it, which is why a CLI command that
# touches settings goes through the `kaicalc` wrapper installed above:
#     docker exec kaicalc-api kaicalc <subcommand>
# `docker exec kaicalc-api kaicalc-admin --help` works either way - argparse
# prints help and exits before load_settings() is reached.
ENTRYPOINT ["/usr/local/bin/kaicalc-entrypoint"]

# The only line that differs from docker/admin.Dockerfile.
CMD ["/app/run.sh", "api"]
