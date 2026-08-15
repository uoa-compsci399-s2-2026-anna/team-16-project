"""Two nginx layers, actually in front of each other, and what the application reads.

**The defect.** ``docker/nginx-proxy-headers.conf`` set both forwarded headers from what
*this* nginx observed — ``X-Forwarded-Proto`` from ``$scheme`` and ``X-Forwarded-For``
from ``$remote_addr``. Correct while this nginx is the outermost proxy; wrong the moment
somebody else's TLS terminator is in front of it, which is the ordinary shape on a public
IPv4 whose port 443 already belongs to another site. The panel is then told the request
is not on TLS, and every visitor on earth arrives as the edge's one address —
``admin/config.py`` already spells out what that costs: "the rate limit becomes one
counter shared by every visitor and a single blocked address blocks everyone".

**What replaced it.** Both headers come from ``map`` blocks in ``docker/nginx.conf``,
gated on ``KAICALC_TRUST_FORWARDED_HEADERS``. Off — the default — they resolve to exactly
what the file said before, and an inbound copy of either header is discarded. On, the
scheme is believed if it is exactly ``http`` or ``https``, and the forwarded chain is kept
with this proxy's peer appended so the left-most entry stays the visitor the edge saw.

**Why this file starts three containers rather than reading the template.** A test that
asserts ``proxy_set_header`` is present asserts that a directive is spelled correctly. It
does not assert that the right value reaches the application, and this repository's defect
list is made of that difference: a health check that reported healthy over a socket the
real server had not bound, a conversion function wrong in the third decimal that had never
been called. The property under test is what arrives at the application's socket, and
nothing short of two proxies chained together can produce it. So:

    edge (nginx, sets X-Forwarded-Proto: https and X-Forwarded-For: 203.0.113.9)
      --> mid (the real kaicalc-web:local image, the real rendered configuration)
        --> echo (an application stand-in that answers with the headers it received)

``mid`` is started twice, once per configuration, and every assertion below reads a value
out of the echo's reply.

**The address in the trusting case is a value nothing else can produce.** ``203.0.113.9``
is TEST-NET-3 (RFC 5737) and no container on the scratch network can be given it, so if it
appears in what the application received it got there from the edge and from nowhere else.

Requires Docker, ``kaicalc-web:local`` and ``kaicalc-api:local``. Skipped, never failed,
without them:

    docker build -f docker/web.Dockerfile -t kaicalc-web:local .
    docker build -f docker/api.Dockerfile  -t kaicalc-api:local  .
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import time
import uuid

import pytest

WEB_IMAGE = "kaicalc-web:local"
#: Only for its Python interpreter — the stand-in below is stdlib and touches no database.
#: Reusing an image the repository already builds avoids pulling anything.
APP_IMAGE = "kaicalc-api:local"

#: RFC 5737 TEST-NET-3. Unroutable, and not an address Docker can assign, so its presence
#: in a header the application received identifies the edge as the only possible source.
EDGE_CLIENT = "203.0.113.9"
#: What a caller reaching mid directly would try to claim. Also TEST-NET-3.
FORGED_CLIENT = "198.51.100.7"

#: A scope suffix, so a failed run cannot collide with the next one and nothing here can
#: collide with the owner's stack — every service in docker/compose.yaml pins a
#: container_name, and none of them is of this shape.
SCOPE = f"kaicalc-fwd-{uuid.uuid4().hex[:8]}"

#: The application stand-in. Answers on both upstream ports, because nginx resolves BOTH
#: `api` and `admin` at configuration load and refuses to start if either name is unknown.
ECHO_APP = """
import json, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class H(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def do_GET(self):
        body = json.dumps({
            'path': self.path,
            'headers': {k.lower(): v for k, v in self.headers.items()},
            'peer': self.client_address[0],
        }).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    do_POST = do_GET
    def log_message(self, *a):
        pass
for port in (18000, 18001):
    threading.Thread(
        target=ThreadingHTTPServer(('0.0.0.0', port), H).serve_forever, daemon=True
    ).start()
threading.Event().wait()
"""

#: The edge. A second nginx, in front of ours, doing what a TLS terminator does: it has
#: already decided the scheme and the client address, and it says so in the two headers.
EDGE_CONF = f"""
server {{
    listen 18080;
    location / {{
        proxy_pass http://mid:18080;
        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For {EDGE_CLIENT};
    }}
}}
"""


def _images_available() -> bool:
    if shutil.which("docker") is None:
        return False
    for image in (WEB_IMAGE, APP_IMAGE):
        if subprocess.run(
            ["docker", "image", "inspect", image], capture_output=True, text=True
        ).returncode != 0:
            return False
    return True


if not _images_available():  # pragma: no cover - environment guard
    pytest.skip(
        f"docker, {WEB_IMAGE} or {APP_IMAGE} is unavailable; build them with "
        "`docker build -f docker/web.Dockerfile -t kaicalc-web:local .` and "
        "`docker build -f docker/api.Dockerfile -t kaicalc-api:local .`",
        allow_module_level=True,
    )


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        # utf-8 explicitly: `text=True` decodes with the console code page, which on a
        # Windows checkout is cp936 and raises on nginx's start-up banner.
        ["docker", *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180,
    )
    if check:
        assert result.returncode == 0, f"docker {' '.join(args)} failed: {result.stderr}"
    return result


def _wait_for_http(container: str, url: str, what: str) -> None:
    """Poll until ``url`` answers from inside ``container``.

    nginx binds fast but not instantly, and a race here would look like the defect.
    """

    deadline = time.monotonic() + 60
    last = ""
    while time.monotonic() < deadline:
        probe = _docker("exec", container, "wget", "-q", "-O", "-", url, check=False)
        if probe.returncode == 0:
            return
        last = probe.stderr.strip()
        time.sleep(0.5)
    raise AssertionError(f"{what} never answered {url}: {last}")


class Stack:
    """edge -> mid -> echo, on a private network, torn down whatever happens."""

    def __init__(self, network: str) -> None:
        self.network = network
        self.echo = f"{SCOPE}-echo"
        self.edge = f"{SCOPE}-edge"
        self.mid = f"{SCOPE}-mid"
        self._mid_env: dict[str, str] | None = None

    def up(self) -> None:
        _docker("network", "create", self.network)
        # `api` and `admin` are the two upstream names docker/nginx.conf proxies to.
        _docker(
            "run", "-d", "--name", self.echo, "--network", self.network,
            "--network-alias", "api", "--network-alias", "admin",
            "--entrypoint", "python", APP_IMAGE, "-c", ECHO_APP,
        )

    def ensure_mid(self, **env: str) -> None:
        """(Re)start the real image under a given configuration, if it is not already in it.

        Idempotent rather than a plain start so that the two configurations below stay
        correct under any test ordering — a module-scoped fixture that only *starts* the
        container would hand a later test whichever configuration ran last.

        The edge is rebuilt alongside it, and that is not tidiness. nginx resolves an
        upstream name **once, at start-up**, and refuses to start at all if the name is
        unknown — the same behaviour docker/compose.yaml's `depends_on` exists for. An
        edge started before `mid` never comes up, and an edge left running across a
        `docker rm` of `mid` proxies to the address `mid` used to have.
        """

        if self._mid_env == env:
            return
        if self._mid_env is not None:
            _docker("rm", "-f", self.mid)
            _docker("rm", "-f", self.edge)
        argv = ["run", "-d", "--name", self.mid, "--network", self.network,
                "--network-alias", "mid"]
        for name, value in env.items():
            argv += ["-e", f"{name}={value}"]
        argv += [WEB_IMAGE]
        _docker(*argv)
        self._mid_env = dict(env)
        _docker(
            "run", "-d", "--name", self.edge, "--network", self.network,
            "--user", "nginx", "--entrypoint", "sh", WEB_IMAGE, "-c",
            # conf.d is empty in this image and writable by `nginx`; the stock entrypoint
            # is bypassed, so nothing but this file is loaded. base64 rather than a
            # here-doc: the configuration contains `$http_host` and newlines, and neither
            # survives being handed through `sh -c` intact.
            f"echo {_b64(EDGE_CONF)} | base64 -d > /etc/nginx/conf.d/edge.conf && "
            "exec nginx -g 'daemon off;'",
        )
        _wait_for_http(self.edge, "http://mid:18080/", "kaicalc-web")

    def through_the_edge(self, path: str) -> dict:
        """A request that really crossed two proxies. This is the whole point."""

        out = _docker(
            "exec", self.edge, "wget", "-q", "-O", "-", f"http://127.0.0.1:18080{path}"
        )
        return json.loads(out.stdout)

    def straight_at_the_proxy(self, path: str, **headers: str) -> dict:
        """A caller who can address :18080 itself, sending whatever it likes."""

        argv = ["exec", self.edge, "wget", "-q", "-O", "-"]
        for name, value in headers.items():
            argv += [f"--header={name.replace('_', '-')}: {value}"]
        argv += [f"http://mid:18080{path}"]
        return json.loads(_docker(*argv).stdout)

    def edge_address(self) -> str:
        """The edge container's address on this network — what ``mid`` sees as its peer.

        Read from Docker rather than from the reply under test, so that "the untrusting
        default reports the peer it saw" is checked against a value this test knows
        independently. Asserting only that the edge's *claimed* client is absent would
        pass just as happily against an empty header, and an empty ``X-Forwarded-For`` is
        worse than a wrong one: ``db.detection.client_ip`` normalises it to ``None`` and
        both callers then skip the blocklist and the rate limit outright.
        """

        return _docker(
            "inspect", "-f",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
            self.edge,
        ).stdout.strip()

    def mid_logs(self) -> str:
        return _docker("logs", self.mid).stdout + _docker("logs", self.mid).stderr

    def down(self) -> None:
        for name in (self.edge, self.mid, self.echo):
            _docker("rm", "-f", name, check=False)
        _docker("network", "rm", self.network, check=False)


@pytest.fixture(scope="module")
def stack():
    built = Stack(f"{SCOPE}-net")
    try:
        built.up()
        yield built
    finally:
        built.down()


@pytest.fixture
def trusting(stack: Stack) -> Stack:
    stack.ensure_mid(
        KAICALC_TRUST_FORWARDED_HEADERS="true", PROTECTION_TRUSTED_PROXY="true"
    )
    return stack


@pytest.fixture
def not_trusting(stack: Stack) -> Stack:
    stack.ensure_mid()
    return stack


# ---------------------------------------------------------------------------------------
# The untrusting default: this nginx is the outermost proxy
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/v1/probe", "/admin/probe"])
def test_the_default_reports_what_this_proxy_saw_and_nothing_it_was_told(
    not_trusting: Stack, path: str
):
    """Unchanged behaviour, asserted rather than assumed.

    An edge really is in front here and really is sending both headers. With the flag off
    they are thrown away: the scheme is the one this server was reached on, and the
    address is the peer that connected. Both location blocks, because they include the
    same file and a change that reached one and not the other is the failure the shared
    include exists to prevent.
    """

    received = not_trusting.through_the_edge(path)["headers"]

    assert received["x-forwarded-proto"] == "http", (
        "the untrusting default believed an inbound X-Forwarded-Proto"
    )
    assert EDGE_CLIENT not in received["x-forwarded-for"], (
        f"the untrusting default passed the edge's claimed client {EDGE_CLIENT} through"
    )
    assert "," not in received["x-forwarded-for"], (
        "X-Forwarded-For was appended to rather than overwritten; both applications read "
        "the left-most entry, so a caller could name their own address"
    )
    assert received["x-forwarded-for"] == not_trusting.edge_address(), (
        "X-Forwarded-For is not the peer this proxy actually saw; got "
        f"{received['x-forwarded-for']!r}"
    )


@pytest.mark.parametrize("header,forged", [
    ("X-Forwarded-Proto", "https"),
    ("X-Forwarded-For", FORGED_CLIENT),
])
def test_a_forged_header_is_ignored_when_trust_is_off(
    not_trusting: Stack, header: str, forged: str
):
    """The reason the fix is an opt-in and not "forward what came in, fall back".

    Unconditionally forwarding an inbound header hands both to whoever can reach :18080.
    A forged ``X-Forwarded-For`` names an address — out of the rate-limit bucket, out of
    the ``ip_block`` list. A forged ``X-Forwarded-Proto: http`` on a request that really
    is https takes ``Secure`` off a live staff session cookie, which is the direction that
    costs something.
    """

    received = not_trusting.straight_at_the_proxy(
        "/api/v1/probe", **{header.replace("-", "_"): forged}
    )["headers"]

    assert forged not in received[header.lower()], (
        f"a caller set {header}: {forged} and the application received it"
    )


# ---------------------------------------------------------------------------------------
# The trusting configuration: somebody else's TLS terminator is in front
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/v1/probe", "/admin/probe"])
def test_the_edges_scheme_and_client_reach_the_application(trusting: Stack, path: str):
    """The defect, in the arrangement that surfaced it.

    ``https`` because the browser really did use it, and the panel reasoning from ``http``
    reasons from a false premise. ``203.0.113.9`` left-most because that is the entry
    ``db.detection.client_ip`` reads, and without it every visitor is one address.
    """

    received = trusting.through_the_edge(path)["headers"]

    assert received["x-forwarded-proto"] == "https", (
        "the scheme the browser actually used did not survive the second proxy"
    )
    assert received["x-forwarded-for"].split(",")[0].strip() == EDGE_CLIENT, (
        "the left-most X-Forwarded-For entry is not the visitor the edge saw; "
        f"got {received['x-forwarded-for']!r}"
    )


def test_the_chain_keeps_this_proxys_own_peer_on_the_right(trusting: Stack):
    """``$proxy_add_x_forwarded_for``, not a bare pass-through.

    The visitor stays left-most, which is all either application reads, and the hop this
    deployment actually controls is still recorded for anyone diagnosing the chain.
    """

    chain = [
        entry.strip()
        for entry in trusting.through_the_edge("/api/v1/probe")["headers"][
            "x-forwarded-for"
        ].split(",")
    ]
    assert len(chain) == 2, f"expected visitor, edge; got {chain}"
    assert chain[0] == EDGE_CLIENT
    assert chain[1] == trusting.edge_address(), (
        f"the hop this deployment controls is not recorded on the right; got {chain}"
    )


@pytest.mark.parametrize("value", [
    "javascript:alert(1)",
    "https, http",
    "wss",
    "",
])
def test_only_http_or_https_is_believed_even_when_trusting(trusting: Stack, value: str):
    """The scheme is whitelisted, not passed through.

    It ends up in ``request.url.scheme``, from which Starlette assembles every absolute
    URL ``request.url_for`` builds. There are exactly two legal values and anything else
    falls back to what this server was reached on.
    """

    received = trusting.straight_at_the_proxy(
        "/api/v1/probe", X_Forwarded_Proto=value
    )["headers"]
    assert received["x-forwarded-proto"] == "http", (
        f"X-Forwarded-Proto: {value!r} was passed through to the application"
    )


def test_an_upper_case_scheme_is_normalised_rather_than_refused(trusting: Stack):
    """``HTTPS`` is believed, and arrives lower-cased. Both halves are deliberate.

    nginx matches ``map`` string keys case-insensitively, and a scheme *is*
    case-insensitive (RFC 3986 §3.1), so refusing ``HTTPS`` would refuse a legal header
    and quietly downgrade the request. What reaches the application is the map's own
    literal, not the caller's string — which is the property that matters: an application
    comparing ``scheme == "https"`` cannot be defeated by the casing an edge happens to
    use.
    """

    received = trusting.straight_at_the_proxy(
        "/api/v1/probe", X_Forwarded_Proto="HTTPS"
    )["headers"]
    assert received["x-forwarded-proto"] == "https"


def test_a_missing_inbound_header_falls_back_rather_than_arriving_empty(trusting: Stack):
    """Trust is not the same as assuming. A direct caller sends neither header.

    An empty ``X-Forwarded-For`` reaching the application is worse than none: both callers
    normalise it to ``None`` and skip the blocklist and the rate limit outright.
    """

    received = trusting.straight_at_the_proxy("/api/v1/probe")["headers"]

    assert received["x-forwarded-proto"] == "http"
    assert received["x-forwarded-for"] == trusting.edge_address(), (
        "with no inbound header to trust, X-Forwarded-For must be the peer this proxy "
        f"saw; got {received['x-forwarded-for']!r}"
    )
    assert EDGE_CLIENT not in received["x-forwarded-for"]


# ---------------------------------------------------------------------------------------
# What must NOT have changed
# ---------------------------------------------------------------------------------------


def test_no_address_is_written_to_the_access_log(trusting: Stack):
    """Contract 2.3, and the access log was found violating it once already.

    Handing nginx a header that now carries a real visitor's address makes the log format
    worth re-asserting rather than assuming: the ``kaicalc`` format keeps time, request,
    status, size and latency, and gains nothing here.
    """

    trusting.through_the_edge("/api/v1/probe")
    logged = [
        line for line in trusting.mid_logs().splitlines()
        if "/api/v1/probe" in line and "[" in line
    ]
    assert logged, "the request was not logged at all; the format assertion below is vacuous"
    for line in logged:
        assert EDGE_CLIENT not in line, f"the visitor's address reached the access log: {line}"
        assert "x-forwarded" not in line.lower(), f"a forwarded header reached the log: {line}"


def test_the_proxy_still_forwards_the_host_header_unchanged(trusting: Stack):
    """``$http_host``, port and all — the redirect fix this file already carried.

    Asserted here because the two edited lines sit directly under it and a mistake in the
    include reaches all three headers at once.
    """

    assert (
        trusting.through_the_edge("/api/v1/probe")["headers"]["host"] == "127.0.0.1:18080"
    )


# ---------------------------------------------------------------------------------------
# The setting itself
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["maybe", "yes please", "1 "])
def test_a_value_that_is_not_a_boolean_stops_the_container(value: str):
    """The same failure mode as a malformed origin, and for the same reason.

    The rendered token is interpolated into the configuration, so it has to come from a
    closed set chosen by the script rather than from the environment. A typo must not
    become a proxy quietly trusting, or quietly not trusting, whichever the operator did
    not mean.
    """

    result = subprocess.run(
        ["docker", "run", "--rm", "--user", "nginx",
         "-e", f"KAICALC_TRUST_FORWARDED_HEADERS={value}",
         WEB_IMAGE, "nginx", "-g", "daemon off;"],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode != 0, (
        f"the container started with KAICALC_TRUST_FORWARDED_HEADERS={value!r}"
    )
    assert "KAICALC_TRUST_FORWARDED_HEADERS" in result.stdout + result.stderr, (
        "the refusal must name the variable that is wrong"
    )


def test_trusting_nginx_with_untrusting_applications_is_reported_at_start_up():
    """The combination that looks configured and does nothing.

    nginx forwards the visitor's address, ``db.detection.client_ip`` ignores it, and every
    caller is still measured as this proxy. A warning rather than a refusal because this
    container's view of the applications' setting is second-hand — see docker/web-config.sh.
    """

    result = subprocess.run(
        ["docker", "run", "--rm", "--user", "nginx",
         "-e", "KAICALC_TRUST_FORWARDED_HEADERS=true",
         "-e", "PROTECTION_TRUSTED_PROXY=false",
         "--entrypoint", "sh", WEB_IMAGE, "-c",
         "/docker-entrypoint.d/16-kaicalc-config.sh"],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert "PROTECTION_TRUSTED_PROXY" in combined and "WARNING" in combined, combined

    agreed = subprocess.run(
        ["docker", "run", "--rm", "--user", "nginx",
         "-e", "KAICALC_TRUST_FORWARDED_HEADERS=true",
         "-e", "PROTECTION_TRUSTED_PROXY=true",
         "--entrypoint", "sh", WEB_IMAGE, "-c",
         "/docker-entrypoint.d/16-kaicalc-config.sh"],
        capture_output=True, text=True, timeout=120,
    )
    assert "WARNING" not in agreed.stdout + agreed.stderr, (
        "a correctly paired deployment must not be warned at"
        " - a warning nobody can act on is one everybody learns to skip"
    )
