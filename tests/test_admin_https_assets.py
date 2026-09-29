"""The panel's own assets, requested through the real stack, behind real https.

**The defect.** ``run.sh``'s ``serve()`` built the uvicorn command line and injected
``--port`` and ``--host`` when the caller had not supplied them, and nothing else.
uvicorn's own ``--forwarded-allow-ips`` defaults to ``127.0.0.1``; the peer that
actually connects to ``admin`` is the ``web`` container's address on the compose
network, never ``127.0.0.1``, so uvicorn's ``ProxyHeadersMiddleware`` declined to
touch the ASGI scope at all. ``request.url.scheme`` stayed ``http`` no matter what
nginx sent, and sqladmin builds every asset URL and every redirect ``Location`` from
that scheme (``request.url_for``). A browser that reached ``/admin/`` over real https
was served a page whose stylesheets and scripts were absolute ``http://`` URLs -
mixed content, blocked outright, an unstyled panel.

**The fix.** ``run.sh`` fact 4 (its own header comment carries the long form): when
``PROTECTION_TRUSTED_PROXY`` is true - the shipped default, because nothing but this
stack's own nginx can reach ``admin`` or ``api`` - ``serve()`` now passes
``--forwarded-allow-ips '*'`` and ``--no-access-log`` together, never one without the
other. The second flag matters as much as the first: the same middleware pass that
fixes the scheme also rewrites ``scope["client"]`` from the same ``X-Forwarded-For``
header, and uvicorn's default access log would otherwise start printing the
*visitor's real address* on every line - contract §2.3 forbids storing one at all.

**Why this drives the real stack rather than importing ``admin.app``.** A test that
called ``create_app()`` directly and asserted on ``request.url.scheme`` would prove
that Starlette can be made to agree with itself - not that ``run.sh`` actually says
the words that make uvicorn read the header in the first place. The property under
test is what ``python -m uvicorn admin.app:create_app --factory ...`` does when
launched exactly as ``docker/admin.Dockerfile``'s ``CMD`` launches it, behind exactly
the nginx image this stack ships. Nothing short of the containers proves that.

**Why it reaches the dashboard rather than the login page.** The login page,
``/admin/change-password`` and ``/admin/enrol`` are hand-written branded templates
(``admin/templates/brand/``) that link their own two stylesheets by a plain,
root-relative ``href`` - they never call ``request.url_for`` and never exhibited this
defect. The twelve-odd `<link>`/`<script>` tags the bug report describes are
sqladmin's *own* base layout (tabler, tabler-icons, fontawesome, select2, flatpickr,
jQuery, main.css/js), served from ``request.url_for("admin:statics", ...)``, and that
template renders only once a session is fully authenticated - password changed, an
authenticator enrolled, a TOTP code verified. So the fixture drives the whole
first-login handshake with a real, computed TOTP code (``pyotp``, already a runtime
dependency - see ``admin/totp.py``) rather than asserting on a page that was never
broken.

**Why a legitimate request needs specific headers.** ``/admin/`` refuses a plain
forwarded request with 403 - that is E-8's ``ProtectionMiddleware``
(``admin/protection.py``), not this defect: ``db.detection.looks_automated`` refuses
any caller with no ``User-Agent``, a ``User-Agent`` naming a known scripting tool
(``curl``, ``wget``, ``python-requests`` are all on the list), or an HTML ``Accept``
with no ``Sec-Fetch-Mode``. The headers this file sends imitate a real browser
navigation for exactly that reason - asserting on the 403 body would prove nothing
about the scheme.

Requires Docker, ``kaicalc-web:local`` and ``kaicalc-admin:local``. Skipped, never
failed, without them:

    docker build -f docker/web.Dockerfile   -t kaicalc-web:local   .
    docker build -f docker/admin.Dockerfile -t kaicalc-admin:local .

Slow - it stands up a real MySQL, migrates it, seeds two bootstrap administrators and
runs a full password-change-and-enrol handshake against one of them - so it is one
test, not a parametrized sweep, and it is skipped by the same guard as
``tests/test_web_forwarded_headers.py`` rather than folded into that file: this one
needs a migrated database and that one deliberately does not.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
import uuid

import pytest

try:
    import pyotp
except ImportError:  # pragma: no cover - already a runtime dependency
    pyotp = None

try:
    import httpx
except ImportError:  # pragma: no cover - already a runtime dependency
    httpx = None

WEB_IMAGE = "kaicalc-web:local"
ADMIN_IMAGE = "kaicalc-admin:local"
#: Pinned by digest for the same reason docker/compose.yaml pins it - a floating tag
#: is not the database this was verified against.
DB_IMAGE = "mysql@sha256:7dcddc01f13bab2f15cde676d44d01f61fc9f99fe7785e86196dfc07d358ae2b"

#: A scope suffix so a failed run cannot collide with the next one, and nothing here
#: can collide with a real deployment: docker/compose.yaml pins container_name to
#: exactly kaicalc-web / kaicalc-admin / kaicalc-stack-db and none of those names is
#: of this shape.
SCOPE = f"kaicalc-https-assets-{uuid.uuid4().hex[:8]}"

#: A real browser navigation, not a script. db.detection.looks_automated refuses
#: anything without these - see the module docstring's last section.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    ),
    "Accept": "text/html",
    "Sec-Fetch-Mode": "navigate",
}
#: What this test's forged edge claims. Neither web nor admin ever sees a listener on
#: this name; it only has to be a bare host, valid enough for url_for to build a
#: Location and a <link href> with.
FORWARDED_HOST = "kai.example.test"


def _tool_available() -> bool:
    if shutil.which("docker") is None or pyotp is None or httpx is None:
        return False
    for image in (WEB_IMAGE, ADMIN_IMAGE):
        if subprocess.run(
            ["docker", "image", "inspect", image], capture_output=True, text=True
        ).returncode != 0:
            return False
    return True


if not _tool_available():  # pragma: no cover - environment guard
    pytest.skip(
        f"docker, {WEB_IMAGE}, {ADMIN_IMAGE}, pyotp or httpx is unavailable; build "
        "the images with `docker build -f docker/web.Dockerfile -t "
        "kaicalc-web:local .` and `docker build -f docker/admin.Dockerfile -t "
        "kaicalc-admin:local .`",
        allow_module_level=True,
    )


def _docker(*args: str, check: bool = True, timeout: int = 180) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["docker", *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    if check:
        assert result.returncode == 0, f"docker {' '.join(args)} failed: {result.stderr}"
    return result


class Stack:
    """db -> migrate (once) -> admin -> web, on a private network, torn down after."""

    def __init__(self, network: str) -> None:
        self.network = network
        self.db = f"{SCOPE}-db"
        self.admin = f"{SCOPE}-admin"
        self.web = f"{SCOPE}-web"
        self.secret_key = f"test-only-{uuid.uuid4().hex}"
        self.database_url = "mysql+pymysql://kaicalc:devpass@db:3306/kaicalc"
        self.web_port: int | None = None
        self.migrate_log: str = ""

    def up(self) -> None:
        _docker("network", "create", self.network)
        _docker(
            "run", "-d", "--name", self.db, "--network", self.network,
            "--network-alias", "db",
            "-e", "MYSQL_ROOT_PASSWORD=devroot", "-e", "MYSQL_DATABASE=kaicalc",
            "-e", "MYSQL_USER=kaicalc", "-e", "MYSQL_PASSWORD=devpass",
            DB_IMAGE,
            "--character-set-server=utf8mb4", "--collation-server=utf8mb4_unicode_ci",
        )
        self._wait_for_db()

        # docker/init.sh's whole job, run once - see docker/compose.yaml's own
        # `migrate` service for why this must be exactly one process. `run`
        # without `-d` blocks until it exits and this captures its stdout
        # directly: `--rm` removes the container the instant it exits, and a
        # later `docker logs` on a name that has already been reclaimed is a
        # race this test does not need to run.
        self.migrate_log = _docker(
            "run", "--rm", "--name", f"{SCOPE}-migrate", "--network", self.network,
            "-e", f"DATABASE_URL={self.database_url}",
            "-e", f"SECRET_KEY={self.secret_key}",
            ADMIN_IMAGE, "/app/init.sh",
            timeout=300,
        ).stdout

        # network-alias'd as BOTH admin and api: docker/nginx.conf resolves both
        # upstream names at start-up and refuses to start if either is unknown, and
        # nothing here ever exercises /api/v1/, so one container answering to both
        # names is enough.
        _docker(
            "run", "-d", "--name", self.admin, "--network", self.network,
            "--network-alias", "admin", "--network-alias", "api",
            "-e", f"DATABASE_URL={self.database_url}",
            "-e", f"SECRET_KEY={self.secret_key}",
            "-e", "PROTECTION_TRUSTED_PROXY=true",
            ADMIN_IMAGE,
        )
        _docker(
            "run", "-d", "--name", self.web, "--network", self.network,
            "-p", "127.0.0.1::18080",
            "-e", "KAICALC_TRUST_FORWARDED_HEADERS=true",
            WEB_IMAGE,
        )
        self.web_port = self._published_port()
        self._wait_for_http(f"http://127.0.0.1:{self.web_port}/", "kaicalc-web")
        # nginx resolves the `admin` upstream once, at start-up, and answering `/`
        # above only proves nginx itself is up - the `admin` container's own
        # lifespan hook does a real database round trip
        # (admin/accounts.py::ensure_bootstrap_admins) before uvicorn ever accepts a
        # connection, and a request proxied there before that finishes is a 502,
        # not a race this test wants to report as the defect under test.
        self._wait_for_http(
            f"http://127.0.0.1:{self.web_port}/admin/login", "kaicalc-admin, through nginx"
        )

    def _wait_for_db(self) -> None:
        deadline = time.monotonic() + 90
        last = ""
        while time.monotonic() < deadline:
            probe = _docker(
                "exec", self.db, "mysql", "--protocol=TCP", "-h", "127.0.0.1",
                "-P", "3306", "-u", "kaicalc", "-pdevpass", "-D", "kaicalc",
                "-e", "SELECT 1", check=False,
            )
            if probe.returncode == 0:
                return
            last = probe.stderr.strip()
            time.sleep(2)
        raise AssertionError(f"database never became reachable: {last}")

    def _published_port(self) -> int:
        out = _docker("port", self.web, "18080/tcp").stdout.strip()
        # "0.0.0.0:54321" or "127.0.0.1:54321" - the port is the part after the colon.
        return int(out.rsplit(":", 1)[-1])

    def _wait_for_http(self, url: str, what: str) -> None:
        """Poll until ``url`` answers with something other than a proxy error.

        502/503/504 are nginx's own replies when it is up but the upstream is not
        yet accepting connections - a real response, so a bare "did this raise"
        check would call the stack ready one hop too early.
        """
        deadline = time.monotonic() + 60
        last: str | None = None
        while time.monotonic() < deadline:
            try:
                response = httpx.get(url, timeout=3)
                if response.status_code not in (502, 503, 504):
                    return
                last = f"HTTP {response.status_code}"
            except httpx.HTTPError as exc:  # pragma: no cover - timing dependent
                last = str(exc)
            time.sleep(0.5)
        raise AssertionError(f"{what} never answered {url}: {last}")

    def down(self) -> None:
        for name in (self.web, self.admin, self.db):
            _docker("rm", "-f", name, check=False)
        _docker("rm", "-f", f"{SCOPE}-migrate", check=False)
        _docker("network", "rm", self.network, check=False)


@pytest.fixture(scope="module")
def stack():
    built = Stack(f"{SCOPE}-net")
    try:
        built.up()
        yield built
    finally:
        built.down()


class Browser:
    """A forged edge, one hop from ``web``: sends X-Forwarded-Proto/Host itself,
    exactly what ``docker/nginx.conf``'s trusting branch believes from anything that
    connects when ``KAICALC_TRUST_FORWARDED_HEADERS`` is on. Cookies are carried by
    hand rather than through a jar: the panel's own session cookie is marked
    ``Secure``, and a jar that honours that attribute correctly - as it should -
    would refuse to resend it over the plain http this test's own client necessarily
    speaks to ``web``'s published port.
    """

    def __init__(self, base_url: str) -> None:
        self._base = base_url
        self._cookie: str | None = None

    def _headers(self) -> dict[str, str]:
        headers = {
            **BROWSER_HEADERS,
            "X-Forwarded-Proto": "https",
            "Host": FORWARDED_HOST,
        }
        if self._cookie:
            headers["Cookie"] = self._cookie
        return headers

    def request(self, method: str, path: str, data: dict | None = None) -> httpx.Response:
        response = httpx.request(
            method, self._base + path, headers=self._headers(), data=data,
            follow_redirects=False, timeout=15,
        )
        set_cookie = response.headers.get("set-cookie")
        if set_cookie:
            self._cookie = set_cookie.split(";", 1)[0]
        return response


def _csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert match, f"no csrf_token field in the response:\n{html[:1000]}"
    return match.group(1)


def _totp_secret(enrol_html: str) -> str:
    match = re.search(r'class="key">([^<]+)</code>', enrol_html)
    assert match, f"no hand-entry TOTP secret on the enrolment page:\n{enrol_html[:1000]}"
    return match.group(1).replace(" ", "")


@pytest.fixture(scope="module")
def dashboard_html(stack: Stack) -> str:
    """Complete a real first-login handshake and return the authenticated
    dashboard's HTML - the one page that actually renders sqladmin's own asset
    bundle. See the module docstring for why the login/change-password/enrol pages
    themselves are the wrong pages to assert on.
    """
    browser = Browser(f"http://127.0.0.1:{stack.web_port}")

    # docker/init.sh's bootstrap step prints two administrators' one-time
    # passwords; read the same log a human operator would.
    log = stack.migrate_log
    match = re.search(r"^\s*admin:\s*(\S+)\s*$", log, re.MULTILINE)
    assert match, f"no bootstrap password for 'admin' in migrate's log:\n{log[-2000:]}"
    one_time_password = match.group(1)

    login = browser.request(
        "POST", "/admin/login", data={"username": "admin", "password": one_time_password}
    )
    assert login.status_code == 302, login.text[:1000]
    assert login.headers["location"].startswith("https://"), (
        f"the login redirect is not https: {login.headers['location']!r}"
    )

    change_password_page = browser.request("GET", "/admin/change-password")
    assert change_password_page.status_code == 200, change_password_page.text[:1000]
    token = _csrf_token(change_password_page.text)
    new_password = f"Correct-Horse-{uuid.uuid4().hex}!"
    change_password = browser.request(
        "POST", "/admin/change-password",
        data={"csrf_token": token, "password": new_password, "confirm": new_password},
    )
    assert change_password.status_code == 302, change_password.text[:1000]
    assert change_password.headers["location"].startswith("https://")

    enrol_page = browser.request("GET", "/admin/enrol")
    assert enrol_page.status_code == 200, enrol_page.text[:1000]
    token = _csrf_token(enrol_page.text)
    secret = _totp_secret(enrol_page.text)
    code = pyotp.TOTP(secret).now()
    enrol = browser.request("POST", "/admin/enrol", data={"csrf_token": token, "code": code})
    # Success renders the recovery codes directly (200), not a redirect - and
    # enrolling completes the session, so what follows is already authenticated.
    assert enrol.status_code == 200, enrol.text[:2000]

    dashboard = browser.request("GET", "/admin/")
    assert dashboard.status_code == 200, dashboard.text[:1000]
    return dashboard.text


def _asset_urls(html: str) -> list[str]:
    return re.findall(r'(?:href|src)="((?:https?:)?//[^"]+|https?://[^"]+)"', html)


def test_every_admin_static_asset_is_https_or_relative(dashboard_html: str):
    """The bug, in the arrangement that reported it: a browser on real https,
    served a dashboard whose stylesheets and scripts came back http://.

    Counted, not just asserted absent-of-one: the report was twelve stylesheet
    blocks and five script blocks in a single page load, and this checks the whole
    set sqladmin's own layout actually ships (tabler, tabler-icons, fontawesome,
    select2, flatpickr, main.css; jQuery, tabler, select2, flatpickr, main.js).
    """
    urls = _asset_urls(dashboard_html)
    assert urls, "no href/src attributes found at all; the fixture is not exercising the page"

    http_urls = [u for u in urls if u.startswith("http://")]
    assert not http_urls, (
        f"{len(http_urls)} asset URL(s) are plain http:// on a request the panel "
        f"was told arrived over https: {http_urls}"
    )

    statics = [u for u in urls if "/admin/statics/" in u]
    assert len(statics) >= 10, (
        "expected sqladmin's own asset bundle (six-plus stylesheets, five scripts) "
        f"in the dashboard response; found {len(statics)}: {statics}"
    )
    for url in statics:
        assert url.startswith("https://"), (
            f"a sqladmin-served asset is not absolute https: {url!r}"
        )


def test_the_anonymous_redirect_to_login_is_https(stack: Stack, dashboard_html: str):
    """request.url_for builds this Location the same way it builds every asset
    href above - checked on its own because a redirect a browser follows
    silently is exactly the kind of mixed-content step a screenshot would miss.
    ``dashboard_html`` is a parameter only to order this after the handshake
    that proves the panel and the database are actually up.
    """
    browser = Browser(f"http://127.0.0.1:{stack.web_port}")
    anon = browser.request("GET", "/admin/")
    assert anon.status_code in (302, 307), anon.text[:500]
    assert anon.headers["location"].startswith("https://"), (
        f"the anonymous redirect to the login page is not https: "
        f"{anon.headers['location']!r}"
    )
