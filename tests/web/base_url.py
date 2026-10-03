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

**One module rather than a constant per file, because a constant per file is
exactly how this happened.** Thirty-seven files under ``tests/web`` carried
their own ``BASE``/``ROOT``/``ORIGIN``/``WEB_BASE``/``CALCULATOR_URL`` line, and
they did not agree with each other: twenty-odd spelled the variable as an
*origin* (``http://localhost:18080``) and nine spelled it as a *page*
(``http://localhost:18080/index.html``), so one environment variable meant two
different things depending on which file read it. Setting it to a bare origin
happened to work for the second group only because ``docker/nginx.conf`` says
``index index.html`` -- the page they asked for was not the page they named.
Both spellings are now derived here from one origin, so the variable has one
meaning.

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
"""

from __future__ import annotations

import os

#: What a checkout with no environment set is pointed at: the origin
#: ``docker/compose.yaml`` publishes ``web`` on. Spelled here once so the
#: meta-test in ``test_suite_isolation.py`` can find it in exactly one place.
DEFAULT_ORIGIN = "http://localhost:18080"

#: The nginx origin under test -- scheme, host and port, no path and no trailing
#: slash. ``/api/v1/`` and ``/admin`` are proxied through the same origin, so
#: this is the one address the whole stack answers on.
ORIGIN = os.environ.get("KAICALC_WEB_URL", DEFAULT_ORIGIN).rstrip("/")

#: The calculator page itself. Named rather than left to each caller to append,
#: because the nine files that used to bake ``/index.html`` into their default
#: were the ones a bare-origin override silently redirected.
CALCULATOR = ORIGIN + "/index.html"

#: Where the API answers. Defaults to :data:`ORIGIN` and not to a second literal:
#: nginx proxies ``/api/v1/`` from the same container, so a private web container
#: serves its own API path too, and a suite pointed at one should not keep asking
#: the shared stack for its taxonomy. ``KAICALC_API_URL`` still overrides, which
#: is the one existing caller's spelling.
API_ORIGIN = os.environ.get("KAICALC_API_URL", ORIGIN).rstrip("/")


def page(path: str) -> str:
    """``ORIGIN`` joined to one page or asset path.

    Takes ``"home.html"`` or ``"/home.html"`` alike, because the callers spelled
    it both ways and a double slash is a different URL to nginx's ``location``
    matching than the one the test meant.
    """
    return f"{ORIGIN}/{path.lstrip('/')}"
