"""Where every ``tests/web`` suite is pointed, declared once.

**The defect this closes.** ``web/`` is baked into ``kaicalc-web``'s image by
``docker/web.Dockerfile`` (``COPY web/``), not bind-mounted, so a front-end
change is only visible to a browser test after a rebuild and a recreate of that
container. One image, one container, one port -- and three agents in three git
worktrees on three branches all driving it at once. The consequences were
observed rather than predicted (issue #149):

* a run reported **21 failures that belonged to another worktree's build** --
  the giveaway was a ``title="Percentage"`` attribute that existed nowhere in
  the failing tree;
* another lost a whole run when a rebuild landed between its own build and its
  own run;
* a third reported five failures that were somebody else's image.

Comparing ``docker inspect --format '{{.Image}}'`` against your own tag before
every batch catches contamination *after* it has happened. Pointing the suite
at a container of your own prevents it. So:

.. code-block:: console

    docker build -f docker/web.Dockerfile -t kaicalc-web:wt-149 .
    docker run -d --name kaicalc-web-wt149 --network docker_default \\
        -e KAICALC_API_ORIGIN=http://localhost:18094 -p 18094:18080 kaicalc-web:wt-149
    KAICALC_WEB_URL=http://localhost:18094 pytest tests/web/test_period_picker_browser.py

**One module rather than a constant per file, and the defect was inconsistency
rather than absence.** Counted on ``origin/main`` at v1.91 rather than taken on
trust (``git grep -l "localhost:18080" origin/main``): **49** tracked files held
the literal, **40** of them Python, **37** of those under ``tests/web`` -- and
**36 of the 37 already honoured** ``KAICALC_WEB_URL``. So an override pattern
existed and nearly every file used it. What it did not have was one meaning or
one home:

* **26** files spelled the variable as an *origin*
  (``os.environ.get("KAICALC_WEB_URL", "http://localhost:18080")``, some then
  appending ``/index.html`` themselves);
* **10** spelled it as a *page*
  (``os.environ.get("KAICALC_WEB_URL", "http://localhost:18080/index.html")``),
  so pointing the variable at a bare origin sent them to ``/`` -- which lands on
  the calculator only because ``docker/nginx.conf`` says ``index index.html``.
  The page they asked for was not the page they named;
* and **one**, ``test_horizontal_overflow.py``, had no override at all and could
  only ever be run against whatever was on :18080. It was reachable without
  surgery -- one agent pointed it at a private image with a throwaway ``-p``
  plugin that rewrote its ``BASE`` at collection time -- which is the argument
  for an environment variable rather than against one.

Both spellings are now derived here from one origin, so the variable has one
meaning and one place to read it from. Three files outside ``tests/web`` still
hold the literal with no override and are deliberately out of scope here:
``tests/admin/test_button_hint_browser.py`` and
``tests/admin/test_import_dialog_browser.py`` (correctly marked ``browser``, so
#149's first half does not reach them, and a one-line import each away from
this module), and ``tests/test_web_https_redirect.py``, whose literal is an
item in a list of hosts the redirect must handle rather than a base URL.

**The default is the compatibility story.** With no environment variable set,
``ORIGIN`` is ``http://localhost:18080`` and ``CALCULATOR`` is
``http://localhost:18080/index.html`` -- byte for byte what every file held
before -- so a plain ``docker compose -f docker/compose.yaml up`` plus
``pytest tests/web`` behaves exactly as it did.

**The rate limit is not isolated by this, and nothing in a test can isolate
it.** ``api/router.py``'s ``_limit`` keys its bucket on an HMAC of the client
IP, so every agent on this host shares one §6.5 budget: 600 ``GET``\\ s an hour,
and a tighter 120 for ``POST /calculate`` and ``POST /export/pdf`` together. A
private web container does **not** get a private budget -- one such container's
own nginx log showed exactly 30 ``429``\\ s on ``GET /api/v1/taxonomy`` for 30
failures. Run ``tests/web`` in a few large batches with
``docker compose restart api`` between them, and keep the batches few rather
than frequent, because restarting ``api`` is itself a shared-container action.

**Two more things a private container does not isolate, both observed while this
module was being written.** ``kaicalc-api`` is shared, so a restart anybody
performs lands inside whoever is mid-batch: four ``502``\\ s on ``GET
/api/v1/taxonomy`` arrived during three batches here and each one cost exactly
one test, failing as a timeout on ``[data-action="start"]`` rather than as
anything that names the cause -- so a lone timeout on a page-load selector is
worth re-running before it is believed. And ``tests/api``, ``tests/db`` and
``tests/admin`` take a host-wide lock file through
``tests/support/mysql_lock.py`` (``$TMPDIR/kaicalc-pytest-mysql-<host>-<port>
.lock``), which a backgrounded run of your own can hold against your next batch
and a killed run can leave stale.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import urllib.parse

#: What a checkout with no environment set is pointed at: the origin
#: ``docker/compose.yaml`` publishes ``web`` on. Spelled here once so the
#: meta-test in ``test_suite_isolation.py`` can find it in exactly one place.
DEFAULT_ORIGIN = "http://localhost:18080"

class WebOriginError(RuntimeError):
    """``KAICALC_WEB_URL`` does not name an origin.

    Its own class rather than a bare ``RuntimeError`` so the meta-test can
    assert the refusal happened for this reason and not for some other import
    failure that also stops collection.
    """


def _origin(raw: str, *, source: str) -> str:
    """``raw`` reduced to scheme and authority, or a refusal naming the defect.

    **This guard exists because the value was got wrong in CI and not one of the
    failures said so.** ``.github/workflows/_browser.yaml`` set it to
    ``http://localhost:18080/index.html`` -- the *page* spelling that ten files
    used before v1.91 unified them here, and the one this module's docstring
    already warns about -- so every address the suite built became
    ``/index.html/<path>``, which ``docker/nginx.conf`` answers with a 302 to
    ``/``. The browser loaded the calculator for every test in the suite.

    The run of 2026-10-06 reported **74 failures and named the cause in none of
    them**:

    * **42** in ``test_horizontal_overflow.py``, each a 15s timeout on
      ``#news-feed[aria-busy='false']`` or ``#stats-breakdown-content[aria-busy=
      'false']`` -- selectors that are not on the calculator, waited for on a
      page that was never ``/home.html`` or ``/stats.html``;
    * **32** in ``test_i18n_browser.py``, where ``.gate`` was ``null`` (
      ``getComputedStyle`` on ``null``), ``?lang=zh`` left the document at
      ``en-NZ`` and ``Vary`` was absent, because ``/index.html/admin/login`` is
      not the panel.

    Every one of them reads as a front-end defect. The job spent its 75 minutes
    on 15- and 30-second timeouts and was killed with half the suite unrun, and
    the figure that would have given it away -- ``<Page url='http://localhost:
    18080/'>`` in the assertion output -- was four thousand log lines in.

    So the value is checked where it is read, once, for every caller. **A path
    is the only thing that has actually gone wrong**, so the refusal names what
    was found rather than lecturing about URLs in general; a missing scheme is
    refused in the same breath only because ``urlsplit`` makes
    ``localhost:18080`` a *scheme* of ``localhost`` and would otherwise pass it
    silently.

    Raising at import is deliberate. It turns a wrong value into a collection
    error on every ``tests/web`` module -- which is loud, immediate, and the one
    thing 74 timeouts spread over 48 minutes was not. It also trips the
    browser job's collection floor, so the two guards are independent.
    """
    parts = urllib.parse.urlsplit(raw)
    if not parts.scheme or not parts.netloc:
        raise WebOriginError(
            f"{source} is {raw!r}, which is not an origin: it needs a scheme "
            f"and a host, as in {DEFAULT_ORIGIN!r}. `urlsplit` reads "
            f"scheme={parts.scheme!r} netloc={parts.netloc!r}, and a bare "
            f"`host:port` parses as a scheme rather than failing."
        )
    if parts.path.strip("/") or parts.query or parts.fragment:
        raise WebOriginError(
            f"{source} is {raw!r}, which names a page rather than an origin. "
            f"This module appends the path: with {raw!r} the suite would ask "
            f"for {raw.rstrip('/')}/home.html, which docker/nginx.conf answers "
            f"with a 302 to `/` -- so every test would drive the calculator "
            f"and fail waiting for a control of the page it meant. That is "
            f"exactly what the browser job did on 2026-10-06, for 74 failures "
            f"and 75 minutes. Drop the path: "
            f"{parts.scheme}://{parts.netloc}"
        )
    return f"{parts.scheme}://{parts.netloc}"


#: The nginx origin under test -- scheme, host and port, no path and no trailing
#: slash. ``/api/v1/`` and ``/admin`` are proxied through the same origin, so
#: this is the one address the whole stack answers on.
ORIGIN = _origin(
    os.environ.get("KAICALC_WEB_URL", DEFAULT_ORIGIN), source="KAICALC_WEB_URL"
)

#: The calculator page itself. Named rather than left to each caller to append,
#: because the nine files that used to bake ``/index.html`` into their default
#: were the ones a bare-origin override silently redirected.
CALCULATOR = ORIGIN + "/index.html"

#: Where the API answers. Defaults to :data:`ORIGIN` and not to a second literal:
#: nginx proxies ``/api/v1/`` from the same container, so a private web container
#: serves its own API path too, and a suite pointed at one should not keep asking
#: the shared stack for its taxonomy. ``KAICALC_API_URL`` still overrides, which
#: is the one existing caller's spelling.
API_ORIGIN = _origin(
    os.environ.get("KAICALC_API_URL", ORIGIN), source="KAICALC_API_URL"
)


#: ``docker compose`` with this repository's compose file, spelled once. No
#: ``-p``: the project comes from ``docker/compose.yaml``'s ``name: kaicalc``
#: unless ``COMPOSE_PROJECT_NAME`` overrides it, which is the behaviour every
#: caller here wants -- measured, the environment variable wins over the file.
COMPOSE_CMD = [
    "docker", "compose", "-f",
    str(pathlib.Path(__file__).resolve().parents[2] / "docker" / "compose.yaml"),
]


def compose_container(service: str, fallback: str) -> str:
    """The container running ``service`` in whichever project compose resolves to.

    **Because `container_name` is pinned and the project is not.**
    ``docker/compose.yaml`` names the ``db`` service's container
    ``kaicalc-stack-db``, and two modules here read a stored submission back
    with ``docker exec`` on that literal -- ``docker exec`` wants a container,
    not a service, so the literal was the only thing available. It does not
    follow ``COMPOSE_PROJECT_NAME``, and ``docker compose ps`` does.

    Measured: a full ``run_browser_suite.py`` against a throwaway stack on
    18091 failed four cases of ``test_contribute_submission.py`` because the
    calculation was submitted to that stack and the row was looked for in the
    development one. The message was that the flag had not moved.

    Falls back rather than raising. These modules are imported during the
    stack-free run too -- ``-m "not browser"`` deselects their cases but still
    imports them -- and that job has no docker at all, where the right answer
    is the literal this replaces and a skip further down.
    """
    try:
        done = subprocess.run([*COMPOSE_CMD, "ps", "--format", "{{.Name}}", service],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return fallback
    names = done.stdout.strip().splitlines() if done.returncode == 0 else []
    return names[0].strip() if names and names[0].strip() else fallback


def compose_stack_mismatch(compose: list[str] | None = None) -> str | None:
    """``None`` when the compose project publishes :data:`ORIGIN`, else why not.

    **For the two modules that both drive HTTP and `docker compose exec` into a
    container, where the two halves can name different stacks.**
    ``tests/admin/test_button_hint_browser.py`` and
    ``tests/admin/test_import_dialog_browser.py`` create a throwaway staff
    account with ``docker compose exec -T admin`` and then log in over HTTP.
    ``docker compose`` honours ``COMPOSE_PROJECT_NAME``; the HTTP half used to
    hold ``http://localhost:18080`` as a literal. So pointing the suite at a
    private stack created the account in one database and attempted the login
    against another.

    Measured: a full ``run_browser_suite.py`` against a throwaway stack on
    18091 produced **60 errors**, every one of them in those two modules, and
    the message was an assertion that the page was still
    ``/admin/login`` -- which reads as a broken login flow and was two
    different stacks. They are the three files this module's own docstring
    left out of scope at v1.91 as "a one-line import each away from this
    module"; this is that line, plus the check that makes it safe.

    Both halves now read :data:`ORIGIN`, which closes the literal. It does not
    close the mismatch, because ``COMPOSE_PROJECT_NAME`` and
    ``KAICALC_WEB_URL`` are two variables and an operator can set one without
    the other -- so this asks the compose project which port it publishes
    ``web`` on and compares. A disagreement is a **skip with both values in
    it**, not sixty errors that name neither.

    Returns a reason rather than raising, so ``base_url`` stays free of
    ``pytest`` -- ``run_browser_suite.py`` imports this module as a script.

    **It answers only the question it can answer, and ``None`` otherwise.** No
    docker, or a compose project with nothing published, is not a split stack;
    it is a stack that is down, which each caller's own ``_stack_is_up`` guard
    already reports. Returning a reason for those would make this a
    module-level skip in environments that merely lack a running stack --
    including the stack-free job, where docker exists and no stack does -- and
    a module-level skip removes the module's items from collection entirely.
    That would quietly break ``test_the_stack_free_selection_is_exactly_what
    _is_declared`` and the exact 998 + 3706 = 4704 partition the two selectors
    are supposed to make, which is v1.102's defect in a new costume.
    """
    compose = compose or COMPOSE_CMD
    port = urllib.parse.urlsplit(ORIGIN).port or 80
    try:
        done = subprocess.run([*compose, "port", "web", "18080"],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return None
    published = done.stdout.strip().rsplit(":", 1)[-1] if done.stdout.strip() else ""
    if not published.isdigit():  # pragma: no cover - environment guard
        return None
    if int(published) != port:
        return (
            f"SPLIT STACK. `docker compose` resolves to a project publishing "
            f"`web` on {published}, and KAICALC_WEB_URL points at {ORIGIN} "
            f"(port {port}). These tests create a throwaway staff account with "
            f"`docker compose exec -T admin` and then log in to it over HTTP: "
            f"on two different stacks the account exists in one database and "
            f"the login is attempted against the other, which fails as a login "
            f"problem and is not one. Set COMPOSE_PROJECT_NAME and "
            f"KAICALC_WEB_URL to the same stack, or unset both."
        )
    return None


def page(path: str) -> str:
    """``ORIGIN`` joined to one page or asset path.

    Takes ``"home.html"`` or ``"/home.html"`` alike, because the callers spelled
    it both ways and a double slash is a different URL to nginx's ``location``
    matching than the one the test meant.
    """
    return f"{ORIGIN}/{path.lstrip('/')}"
