"""A plaintext public visitor is sent to https, and nothing else in the stack is.

**The deployment.** A router can forward two things to one host:

    public 443    --> the operator's edge --http--> this stack :18080
    public 18080  ------------------------------->  this stack :18080

The second is a plaintext bypass of the first, and it serves ``/admin`` in the clear.
Once ``KAICALC_SESSION_HTTPS_ONLY`` is true — which it must be the moment TLS is in front
— the panel's session cookie carries ``Secure``, so the browser accepts the login, refuses
to send the cookie back over http, and bounces to the login page with nothing said. The
usual answer is a redirect on port 80, and an operator may deliberately have no listener
there, which leaves this stack's own nginx as the only thing in the plaintext path.

**The trap this module exists to hold shut.** ``docker/nginx.conf`` is one server block,
and it answers the image's own ``HEALTHCHECK`` (``wget --spider
http://127.0.0.1:18080/``), the operator's LAN address, and every request the *TLS* path
delivers — because the edge speaks http to us. None of the first two carries
``X-Forwarded-Proto``. So an unconditional "no https, therefore redirect" sends the health
check a 307 to a host it cannot resolve, compose marks the container unhealthy,
``restart: unless-stopped`` restarts it, and the stack loops. And keying on the ``Host``
alone loops the TLS path itself, because the edge forwards the public ``Host`` — a request
that already *is* https gets redirected to https, forever.

The rule therefore needs **both** facts, and only a public visitor on the bypass has both:
the browser's scheme is ``http`` **and** the ``Host`` is the configured public one.

**Why this file starts containers rather than reading the template.** A test that finds
``return 307`` in the rendered configuration asserts that a directive is spelled
correctly. It asserts nothing about which callers receive it, which is the entire
question, and this repository's defect list is made of that difference — a health check
that reported healthy over a socket the real server had not bound, a diagnostics page
reporting a broken state as ``ok``. So every assertion below reads a status line off a
socket, and the health-check assertion watches Docker's own health state through a
restart rather than reasoning about what wget would have done.

    edge (nginx: sets X-Forwarded-Proto: https, forwards the client's Host unchanged)
      --> mid (the real kaicalc-web:local image, the real rendered configuration,
               the real baked-in HEALTHCHECK)
        --> echo (an application stand-in, and the client these tests speak from)

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
APP_IMAGE = "kaicalc-api:local"

#: The public origin under test. `.test` is reserved (RFC 6761) and resolves nowhere, so
#: nothing in these tests can accidentally reach it — and if it turns up in a `Location`
#: it got there from the environment this module sets and from nowhere else.
PUBLIC_HOST = "calc.example.test"
PUBLIC_ORIGIN = f"https://{PUBLIC_HOST}"

#: What the image's own HEALTHCHECK sends as its Host, and what the operator's LAN
#: address looks like: a host name that is not the public one.
HEALTHCHECK_HOST = "127.0.0.1:18080"
LAN_HOST = "10.0.0.130:18080"

SCOPE = f"kaicalc-tls-{uuid.uuid4().hex[:8]}"

ECHO_APP = """
import json, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class H(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def do_GET(self):
        body = json.dumps({'path': self.path}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        self.rfile.read(length)
        self.do_GET()
    def log_message(self, *a):
        pass
for port in (18000, 18001):
    threading.Thread(
        target=ThreadingHTTPServer(('0.0.0.0', port), H).serve_forever, daemon=True
    ).start()
threading.Event().wait()
"""

#: The edge. What a TLS terminator does: it has already decided the scheme, and it passes
#: the client's own Host through — which is exactly what makes the naive Host-only rule
#: loop, and exactly what this configuration has to survive.
EDGE_CONF = """
server {
    listen 18080;
    location / {
        proxy_pass http://mid:18080;
        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-Proto https;
    }
}
"""

#: One request, no redirect following, arbitrary method and Host. `http.client` rather
#: than wget because the properties under test are the exact status code and the exact
#: `Location`, and because a POST has to be a POST — wget's `--spider` sends HEAD.
PROBE = """
import http.client, json, sys
method, path, headers = json.loads(sys.argv[1])
c = http.client.HTTPConnection(headers.pop('_host'), 18080, timeout=15)
body = b'{}' if method == 'POST' else None
if body is not None:
    headers['Content-Type'] = 'application/json'
c.request(method, path, body=body, headers=headers)
r = c.getresponse()
print(json.dumps({'status': r.status, 'location': r.getheader('Location') or ''}))
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
        encoding="utf-8", errors="replace", timeout=240,
    )
    if check:
        assert result.returncode == 0, f"docker {' '.join(args)} failed: {result.stderr}"
    return result


class Stack:
    """echo -> (client), edge -> mid -> echo, on a private network, torn down regardless."""

    def __init__(self, network: str) -> None:
        self.network = network
        self.echo = f"{SCOPE}-echo"
        self.edge = f"{SCOPE}-edge"
        self.mid = f"{SCOPE}-mid"
        self._mid_env: dict[str, str] | None = None

    def up(self) -> None:
        _docker("network", "create", self.network)
        _docker(
            "run", "-d", "--name", self.echo, "--network", self.network,
            "--network-alias", "api", "--network-alias", "admin",
            "--entrypoint", "python", APP_IMAGE, "-c", ECHO_APP,
        )

    def ensure_mid(self, **env: str) -> None:
        """(Re)start the real image under a given configuration, if it is not already in it.

        Idempotent rather than a plain start, so the configurations below stay correct
        under any test ordering. The edge is rebuilt alongside it because nginx resolves an
        upstream name once, at start-up: an edge left running across a ``docker rm`` of
        ``mid`` proxies to the address ``mid`` used to have.

        **No ``--no-healthcheck`` and no override.** The image's own ``HEALTHCHECK`` is the
        thing under test in ``test_the_health_check...`` below, so ``mid`` must run it
        exactly as ``docker/compose.yaml`` would.
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
            "--network-alias", "edge",
            "--user", "nginx", "--entrypoint", "sh", WEB_IMAGE, "-c",
            f"echo {_b64(EDGE_CONF)} | base64 -d > /etc/nginx/conf.d/edge.conf && "
            "exec nginx -g 'daemon off;'",
        )
        self._wait_for_listener("mid")
        self._wait_for_listener("edge")

    def _wait_for_listener(self, alias: str) -> None:
        """Poll until ``alias`` answers anything at all on 18080.

        Deliberately indifferent to the status code: with the redirect configured, ``mid``
        answers a probe carrying no Host of its own with a 200 and one carrying the public
        Host with a 307, and both mean "listening". A readiness check that waited for 200
        would be asserting the thing under test, in the one place where getting it wrong
        looks like a timeout rather than a failure.
        """

        deadline = time.monotonic() + 90
        last = ""
        while time.monotonic() < deadline:
            probe = _docker(
                "exec", self.echo, "python", "-c",
                "import http.client,sys;"
                f"c=http.client.HTTPConnection('{alias}',18080,timeout=5);"
                "c.request('GET','/');print(c.getresponse().status)",
                check=False,
            )
            if probe.returncode == 0:
                return
            last = probe.stderr.strip()
            time.sleep(0.5)
        raise AssertionError(f"{alias} never answered on 18080: {last}")

    def request(self, path: str = "/", *, host: str, through_edge: bool = False,
                method: str = "GET", **headers: str) -> dict:
        """One request at ``mid`` (or through ``edge``), with a chosen Host, no following."""

        # Underscores are not legal in a header name and nginx drops such headers
        # outright (`underscores_in_headers off`), so a test that sent one would be
        # asserting against a header the server never saw.
        sent = {name.replace("_", "-"): value for name, value in headers.items()}
        sent["Host"] = host
        sent["_host"] = "edge" if through_edge else "mid"
        out = _docker(
            "exec", self.echo, "python", "-c", PROBE,
            json.dumps([method, path, sent]),
        )
        return json.loads(out.stdout)

    def health(self) -> str:
        return _docker(
            "inspect", "-f", "{{.State.Health.Status}}", self.mid
        ).stdout.strip()

    def health_log(self) -> list[dict]:
        return json.loads(
            _docker("inspect", "-f", "{{json .State.Health.Log}}", self.mid).stdout
        ) or []

    def wait_for_health(self, want: str = "healthy", timeout: float = 150) -> str:
        deadline = time.monotonic() + timeout
        seen = ""
        while time.monotonic() < deadline:
            seen = self.health()
            if seen == want:
                return seen
            if seen == "unhealthy" and want != "unhealthy":
                break
            time.sleep(1)
        raise AssertionError(
            f"{self.mid} health is {seen!r}, wanted {want!r}. Probe log: "
            f"{json.dumps(self.health_log(), indent=2)}"
        )

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
def redirecting(stack: Stack) -> Stack:
    """The deployment that prompted this: an edge in front, and a plaintext bypass of it."""

    stack.ensure_mid(
        KAICALC_PUBLIC_ORIGIN=PUBLIC_ORIGIN,
        KAICALC_TRUST_FORWARDED_HEADERS="true",
        PROTECTION_TRUSTED_PROXY="true",
    )
    return stack


@pytest.fixture
def unconfigured(stack: Stack) -> Stack:
    """Every deployment that exists today. Nothing may change for it."""

    stack.ensure_mid()
    return stack


# ---------------------------------------------------------------------------------------
# The public visitor who arrived in the clear — the one caller that must be redirected
# ---------------------------------------------------------------------------------------


def test_a_plaintext_request_for_the_public_host_is_sent_to_https(redirecting: Stack):
    """The whole point, read off a socket rather than out of the configuration."""

    answer = redirecting.request("/admin", host=PUBLIC_HOST)

    assert answer["status"] == 307, (
        f"a plaintext request for {PUBLIC_HOST} was answered {answer['status']}, not a "
        "redirect; /admin stays readable on the wire and a Secure session cookie is "
        "accepted and then never sent back"
    )
    assert answer["location"] == f"{PUBLIC_ORIGIN}/admin", answer


def test_the_redirect_keeps_the_path_and_the_query_string(redirecting: Stack):
    """``$request_uri``, not ``$uri``.

    ``$uri`` is the decoded path with the query string already gone, and the mistake
    survives testing because ``/`` has none — the visitor lands on the site but not on the
    page they asked for.
    """

    answer = redirecting.request("/methodology.html?lang=mi&x=1%202", host=PUBLIC_HOST)
    assert answer["location"] == f"{PUBLIC_ORIGIN}/methodology.html?lang=mi&x=1%202", answer


def test_the_status_is_307_so_a_post_stays_a_post_and_nothing_is_cached(
    redirecting: Stack,
):
    """Both halves of the code are load-bearing, and the usual framing gets one wrong.

    "301 is cached forever, so use 308" trades nothing away: RFC 9110 §15.4.9 makes 308
    permanent and cacheable on exactly the same terms as 301, so either would be
    remembered by browsers long after this variable was unset — stranding an operator who
    wants the plaintext path back, with no server-side way to withdraw it. 302 would
    solve that and rewrite ``POST /api/v1/calculate`` to a ``GET``. 307 is the only code
    that keeps the method without the permanent cache, so this asserts the number and not
    merely "some redirect".
    """

    answer = redirecting.request("/api/v1/calculate", host=PUBLIC_HOST, method="POST")

    assert answer["status"] == 307, (
        f"POST was answered {answer['status']}; 301 and 308 are permanently cacheable "
        "and 302 rewrites the method to GET"
    )
    assert answer["location"] == f"{PUBLIC_ORIGIN}/api/v1/calculate", answer


def test_the_api_path_is_redirected_rather_than_carved_out(redirecting: Stack):
    """A decision, not an oversight, and it is the opposite of the ``error_page`` one.

    ``/api/v1/`` sets ``proxy_intercept_errors off`` precisely so a missing endpoint
    answers with §9's JSON envelope rather than an HTML redirect — so leaving it out of
    *this* rule would have been consistent-looking. It is excluded from the wrong thing.
    Carving it out would leave ``POST /api/v1/calculate`` — the one request carrying a
    visitor's own figures, which are then persisted — as the only thing still readable on
    the wire on the public hostname, which inverts the point of the rule. The cost is
    stated in ``docker/nginx.conf``: a JSON client that does not follow redirects sees a
    307 where it expected a body, and gets a ``Location`` naming where to go.
    """

    for path in ("/api/v1/taxonomy", "/api/v1/stats"):
        answer = redirecting.request(path, host=PUBLIC_HOST)
        assert answer["status"] == 307, (path, answer)
        assert answer["location"] == f"{PUBLIC_ORIGIN}{path}", answer


def test_a_spoofed_public_host_gets_the_configured_origin_and_not_its_own(
    redirecting: Stack,
):
    """Anyone who can reach :18080 can claim the public Host. That has to cost nothing.

    The ``Location`` is the configured literal, never a string derived from the request,
    so the only thing spoofing the header buys is a redirect to the real site. If this
    ever echoed the caller's own Host it would be an open redirect on the public origin.
    """

    answer = redirecting.request("/", host="evil.example.test")
    assert answer["status"] == 200, (
        "an unrecognised Host was redirected; only the configured public host may be"
    )

    answer = redirecting.request("/", host=PUBLIC_HOST, X_Forwarded_Host="evil.example.test")
    assert answer["location"] == f"{PUBLIC_ORIGIN}/", (
        f"the redirect target came from the request: {answer['location']!r}"
    )


# ---------------------------------------------------------------------------------------
# Everything else that legitimately speaks http to this port — none of it may be redirected
# ---------------------------------------------------------------------------------------


def test_the_tls_path_is_not_redirected_into_a_loop(redirecting: Stack):
    """The failure a Host-only rule produces, through a real second proxy.

    The edge forwards the client's own ``Host``, so the request that arrives here carries
    the public name and ``X-Forwarded-Proto: https``. Keyed on the Host alone this is a
    307 to itself and the site is unreachable over TLS — the *worse* half of the trap,
    because the health check keeps passing while every real visitor loops.
    """

    answer = redirecting.request("/", host=PUBLIC_HOST, through_edge=True)
    assert answer["status"] == 200, (
        f"a request that arrived over TLS was answered {answer['status']} to "
        f"{answer['location']!r} — that is an infinite redirect on the https path"
    )


def test_the_health_checks_own_host_is_not_redirected(redirecting: Stack):
    """``wget --spider http://127.0.0.1:18080/`` sends ``Host: 127.0.0.1:18080``.

    It carries no ``X-Forwarded-Proto``, so a rule keyed on the scheme alone answers it
    307. wget follows redirects, cannot resolve the public name from inside the container,
    exits non-zero — and ``restart: unless-stopped`` turns that into a restart loop for
    the whole stack.
    """

    answer = redirecting.request("/", host=HEALTHCHECK_HOST)
    assert answer["status"] == 200, (
        f"the health check's own request was answered {answer['status']}"
    )


def test_the_operators_lan_address_is_not_redirected(redirecting: Stack):
    """A plaintext path somebody is keeping on purpose is not the one being closed.

    The bypass that has to be redirected is the one a *public visitor* reaches by the
    site's own name. Reaching the same port by its LAN address is how the operator
    diagnoses the stack, and a scheme-only rule would take it away.
    """

    answer = redirecting.request("/", host=LAN_HOST)
    assert answer["status"] == 200, (
        f"http://{LAN_HOST}/ was answered {answer['status']}"
    )


def test_the_public_host_matches_on_any_port_it_is_published_on(redirecting: Stack):
    """``$host``, not ``$http_host``: lower-cased, and with the port stripped.

    One key has to match the bypass visitor arriving on ``:18080`` and the edge's request
    arriving with no port at all. ``$http_host`` would need a key per published port and
    would stop matching the day the operator publishes a third.
    """

    for host in (PUBLIC_HOST, f"{PUBLIC_HOST}:18080", f"{PUBLIC_HOST}:8443",
                 PUBLIC_HOST.upper()):
        answer = redirecting.request("/", host=host)
        assert answer["status"] == 307, (host, answer)


# ---------------------------------------------------------------------------------------
# The health check, watched rather than reasoned about
# ---------------------------------------------------------------------------------------


def test_the_health_check_reaches_healthy_and_survives_a_restart(redirecting: Stack):
    """The claim this change has to earn, and the only way to earn it is to watch it.

    The image's ``HEALTHCHECK`` is baked in and runs here exactly as
    ``docker/compose.yaml`` would run it. A configuration that redirected it would show up
    as ``unhealthy`` after five failed probes, and — under ``restart: unless-stopped`` —
    as a stack that restarts itself indefinitely. So: wait for ``healthy``, restart the
    container, wait for ``healthy`` again, then watch it stay there across further probes
    rather than sampling it once at the moment it first passed.

    Every probe's exit code is asserted, not just the summary. A ``HEALTHCHECK`` that has
    not run yet reports ``starting``, and treating that as "not unhealthy" is how a health
    assertion passes vacuously.
    """

    redirecting.wait_for_health("healthy")

    _docker("restart", redirecting.mid)
    redirecting.wait_for_health("healthy")

    # nginx's HEALTHCHECK interval is 15s; two further probes, watched.
    settled = time.monotonic() + 35
    while time.monotonic() < settled:
        assert redirecting.health() == "healthy", (
            "the container left `healthy` after starting: "
            f"{json.dumps(redirecting.health_log(), indent=2)}"
        )
        time.sleep(2)

    log = redirecting.health_log()
    assert len(log) >= 2, f"too few probes to be evidence of anything: {log}"
    for entry in log:
        assert entry["ExitCode"] == 0, (
            f"a health probe failed: {entry}. The check requests / with "
            "`Host: 127.0.0.1:18080`, so a rule that redirects it takes the stack down."
        )


# ---------------------------------------------------------------------------------------
# The unconfigured deployment — every deployment that exists today
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host", [PUBLIC_HOST, HEALTHCHECK_HOST, LAN_HOST, "localhost:18080"]
)
def test_an_unset_public_origin_redirects_nothing_at_all(unconfigured: Stack, host: str):
    """Unset is the state every existing deployment is in, and it must change nothing.

    Including a request for the public host itself: with no origin configured there is no
    public host, and ``http://localhost:18080/`` — the shipped ``docker compose up`` — has
    to keep answering 200.
    """

    answer = unconfigured.request("/", host=host)
    assert answer["status"] == 200, (
        f"an unconfigured deployment redirected {host} to {answer['location']!r}"
    )
    assert answer["location"] == "", answer


def test_an_unset_public_origin_renders_a_map_entry_that_cannot_fire(unconfigured: Stack):
    """Belt and braces, and both are deliberate.

    Unset renders the single map entry as ``"http:" ""``. Its **value** is empty, and the
    ``if`` treats an empty variable as false, so nothing redirects even if the key matched.
    And the **key** cannot match: ``$host`` falls back to ``server_name`` (``_``) when no
    Host header arrives and is never the empty string. Asserted because the second
    protection is the one a later edit could remove without noticing the first.
    """

    rendered = _docker(
        "exec", unconfigured.mid, "cat", "/etc/nginx/conf.d/kaicalc.conf"
    ).stdout
    assert '"http:"      "";' in rendered.replace("\t", " "), (
        "the unset map entry is not the inert one this depends on:\n"
        + "\n".join(l for l in rendered.splitlines() if "http:" in l)
    )


def test_a_request_with_no_host_header_at_all_is_not_redirected(unconfigured: Stack):
    """HTTP/1.0 sends no Host, and ``$host`` then falls back to ``server_name``.

    The unset map's key is the empty string. If ``$host`` could ever *be* empty, every
    HTTP/1.0 request on an unconfigured deployment would match it — which is why the empty
    **value** is the protection that actually carries this, and why it is worth proving
    the fallback is ``_`` rather than assuming it.
    """

    out = _docker(
        "exec", unconfigured.echo, "python", "-c",
        "import socket,sys;"
        "s=socket.create_connection(('mid',18080),10);"
        "s.sendall(b'GET / HTTP/1.0\\r\\n\\r\\n');"
        "print(s.recv(64).decode('latin-1').splitlines()[0])",
    ).stdout
    assert "200" in out, f"a Host-less request was answered {out.strip()!r}"


# ---------------------------------------------------------------------------------------
# The setting itself: what stops a deployment that cannot work
# ---------------------------------------------------------------------------------------


def _refuses(**env: str) -> subprocess.CompletedProcess:
    argv = ["docker", "run", "--rm", "--user", "nginx"]
    for name, value in env.items():
        argv += ["-e", f"{name}={value}"]
    argv += [WEB_IMAGE, "nginx", "-g", "daemon off;"]
    return subprocess.run(
        argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120,
    )


@pytest.mark.parametrize("value", [
    "https://calc.example.test/",          # a trailing slash
    "https://calc.example.test/admin",     # a path
    "calc.example.test",                   # no scheme
    "*.calc.example.test",                 # a wildcard
    "https://calc.example.test' 'x",       # a token break
])
def test_a_malformed_public_origin_stops_the_container(value: str):
    """The same guard as the other two origins, and for the same second reason.

    The value is interpolated into a ``Location``, so nothing reaching it may carry a
    quote, a semicolon, a space or a newline. Refusing to start is the correct failure:
    the alternative is a redirect nobody meant, on the one path that is meant to make the
    site safe.
    """

    result = _refuses(
        KAICALC_PUBLIC_ORIGIN=value, KAICALC_TRUST_FORWARDED_HEADERS="true"
    )
    assert result.returncode != 0, f"the container started with {value!r}"
    assert "KAICALC_PUBLIC_ORIGIN" in result.stdout + result.stderr


def test_an_http_public_origin_stops_the_container():
    """``http://`` here is a redirect from plaintext to plaintext.

    Depending on the host that is either a no-op or an infinite loop, and in neither
    reading is it what anybody meant by this variable. There is no correct value that is
    not https, so this is a refusal rather than a coercion — silently upgrading the
    operator's string would be the same class of guess this repository keeps removing.
    """

    result = _refuses(
        KAICALC_PUBLIC_ORIGIN=f"http://{PUBLIC_HOST}",
        KAICALC_TRUST_FORWARDED_HEADERS="true",
    )
    assert result.returncode != 0, "an http:// public origin started the container"
    combined = result.stdout + result.stderr
    assert "KAICALC_PUBLIC_ORIGIN" in combined and "https" in combined, combined


def test_a_public_origin_without_forwarded_trust_stops_the_container():
    """The combination that would take the site down, refused at start-up.

    With trust off, ``$kaicalc_client_proto`` is the constant ``$scheme`` — ``http`` for
    every request including the ones the browser made over https — so the edge's own
    traffic matches the redirect key and every page load becomes an infinite redirect.
    There is no deployment in which this pair is correct.

    **It is a refusal where the ``PROTECTION_TRUSTED_PROXY`` mismatch is a warning, and
    that is not an inconsistency.** That one is second-hand: this container is told the
    applications' setting by a compose file it need not have been started from, so
    refusing could refuse a correct deployment on the strength of a variable it cannot
    really see. Both values here are read by this script, from this container's own
    environment, and both govern this same nginx.
    """

    result = _refuses(KAICALC_PUBLIC_ORIGIN=PUBLIC_ORIGIN)
    assert result.returncode != 0, (
        "the container started with a public origin and no forwarded trust; every "
        "request through the edge would redirect to itself"
    )
    combined = result.stdout + result.stderr
    assert "KAICALC_PUBLIC_ORIGIN" in combined, combined
    assert "KAICALC_TRUST_FORWARDED_HEADERS" in combined, (
        "the refusal must name the other half of the pair, or the operator cannot act on it"
    )


def test_the_correct_pairing_starts_and_says_what_it_decided():
    """A refusal is only useful if the configuration it demands actually works."""

    result = subprocess.run(
        ["docker", "run", "--rm", "--user", "nginx",
         "-e", f"KAICALC_PUBLIC_ORIGIN={PUBLIC_ORIGIN}",
         "-e", "KAICALC_TRUST_FORWARDED_HEADERS=true",
         "-e", "PROTECTION_TRUSTED_PROXY=true",
         "--entrypoint", "sh", WEB_IMAGE, "-c",
         "/docker-entrypoint.d/16-kaicalc-config.sh"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert PUBLIC_ORIGIN in combined and "307" in combined, combined
    assert "WARNING" not in combined, combined
