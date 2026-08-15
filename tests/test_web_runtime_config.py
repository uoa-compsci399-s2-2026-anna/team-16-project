"""One environment variable, two consumers, asserted against the real image.

**The defect.** The client's production domain used to be written out twice: in
``web/js/api.js`` as the WordPress base, and in ``docker/nginx.conf`` as a ``connect-src``
entry. They had to agree, and they fail asymmetrically when they do not — a wrong policy
makes the news quietly not load, and a wrong URL sends the browser to ask a domain nobody
chose. Neither failure raises anything. This project's deliverable is source code and
documentation, with DNS and hosting explicitly out of scope, so no domain belongs in a
built image at all.

**What replaced it.** ``docker/web-config.sh`` runs from nginx's ``/docker-entrypoint.d/``
before the server binds, and writes both consumers from the same three variables: the
``connect-src`` and ``img-src`` of the public Content-Security-Policy, and the
``NEWS_ORIGIN`` / ``API_ORIGIN`` exports of ``web/js/config.js``.

**Why this file exists rather than an assertion on the source.** ``docker/nginx.conf`` now
carries ``${KAICALC_CSP_CONNECT_SRC}`` and ``${KAICALC_CSP_IMG_SRC}``, and a test that
reads the template can only confirm the placeholders are spelled correctly. Whether the
configured value *arrives* — in the header a browser is sent and in the module a browser
imports, as the same string — is a property of the running container and of nothing else.
This project's defect list is full of the difference: a conversion function wrong in the
third decimal place that had never been called, a health check that reported healthy over
a socket the real server had not bound. So every assertion below sets a value that is not
the default and reads it back out of the image.

Requires Docker and a built ``kaicalc-web:local``. Skipped, never failed, without them:

    docker build -f docker/web.Dockerfile -t kaicalc-web:local .
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "kaicalc-web:local"

#: Deliberately not the client's domain and not anything a default could produce. If one
#: of these turns up in a rendered header, it got there from the environment this test
#: set and from nowhere else.
NEWS = "https://news.example.test"
API = "https://api.example.test:8443"
CDN = "https://cdn.example.test"

#: Where the entrypoint script writes, inside the image.
CONF = "/etc/nginx/conf.d/kaicalc.conf"
CONFIG_JS = "/usr/share/nginx/html/js/config.js"
SCRIPT = "/docker-entrypoint.d/16-kaicalc-config.sh"

_MARKER = "===kaicalc==="


def _docker_is_available() -> bool:
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(
        ["docker", "image", "inspect", IMAGE],
        capture_output=True,
        text=True,
    )
    return probe.returncode == 0


if not _docker_is_available():  # pragma: no cover - environment guard
    pytest.skip(
        f"docker or the {IMAGE} image is unavailable; build it with "
        "`docker build -f docker/web.Dockerfile -t kaicalc-web:local .`",
        allow_module_level=True,
    )


def _run(env: dict[str, str], command: str) -> subprocess.CompletedProcess:
    """Run ``command`` in a throwaway container as the unprivileged user nginx runs as."""

    argv = ["docker", "run", "--rm", "--user", "nginx"]
    for name, value in env.items():
        argv += ["-e", f"{name}={value}"]
    argv += ["--entrypoint", "sh", IMAGE, "-c", command]
    return subprocess.run(argv, capture_output=True, text=True, timeout=120)


def _render(**env: str) -> tuple[str, str]:
    """Run the real entrypoint script, and return the rendered conf and config.js."""

    result = _run(env, f"{SCRIPT} >/dev/null && cat {CONF} && echo '{_MARKER}' && cat {CONFIG_JS}")
    assert result.returncode == 0, f"{SCRIPT} failed: {result.stderr}"
    conf, _, config_js = result.stdout.partition(_MARKER)
    return conf, config_js


def _directives(conf: str) -> dict[str, list[str]]:
    header = re.search(
        r'add_header\s+Content-Security-Policy\s+"([^"]+)"\s+always\s*;', conf, flags=re.DOTALL
    )
    assert header, "the rendered configuration serves no Content-Security-Policy"
    parsed: dict[str, list[str]] = {}
    for raw in header.group(1).split(";"):
        tokens = raw.split()
        if tokens:
            parsed[tokens[0]] = tokens[1:]
    return parsed


def _exports(config_js: str) -> dict[str, str]:
    return dict(re.findall(r"export const (\w+) = '([^']*)'", config_js))


# ---------------------------------------------------------------------------------------
# The configured deployment
# ---------------------------------------------------------------------------------------


def test_one_news_origin_reaches_the_policy_and_the_page_as_the_same_string():
    """The whole point, in one assertion: they cannot disagree because they are one value.

    The origin set here is not the default and is not the client's domain, so neither
    output can be produced by anything except the environment.
    """

    conf, config_js = _render(KAICALC_NEWS_ORIGIN=NEWS)
    directives = _directives(conf)

    assert NEWS in directives["connect-src"], (
        "connect-src does not carry the configured news origin; the browser would refuse "
        "the fetch the page is about to make"
    )
    assert _exports(config_js)["NEWS_ORIGIN"] == NEWS, (
        "config.js does not carry the configured news origin; the page would ask nobody, "
        "or ask a host the policy has not been told about"
    )
    assert _exports(config_js)["NEWS_ORIGIN"] in directives["connect-src"]


def test_img_src_follows_the_news_origin():
    """A change of position, and it is recorded on ``createNewsCard`` in web/js/home.js.

    ``img-src`` was kept at ``'self' data:`` because widening it meant *guessing* the media
    origin. The origin is configuration now, so there is no guess left to make — and the
    directive grants nothing new in practice, because ``connect-src`` already reaches that
    host.
    """

    directives = _directives(_render(KAICALC_NEWS_ORIGIN=NEWS)[0])
    assert directives["img-src"] == ["'self'", "data:", NEWS]


def test_a_media_cdn_reaches_img_src_and_only_img_src():
    """WordPress libraries commonly serve from a host that is not the site's.

    It is a separate variable because it is a separate permission: a CDN that may serve
    images must not thereby become a host the page may ``fetch()`` from.
    """

    conf, _ = _render(KAICALC_NEWS_ORIGIN=NEWS, KAICALC_NEWS_IMAGE_ORIGINS=CDN)
    directives = _directives(conf)
    assert CDN in directives["img-src"]
    assert CDN not in directives["connect-src"]


def test_the_api_origin_reaches_connect_src_and_the_page_together():
    """The trap that made this worth doing at all.

    Same-origin is the designed topology and stays the default. Split the API onto its own
    subdomain without this, and the relative path in api.js and the ``connect-src`` in
    nginx.conf both have to change — two copies that must agree, with a silent refusal
    when they do not.
    """

    conf, config_js = _render(KAICALC_API_ORIGIN=API)
    assert API in _directives(conf)["connect-src"]
    assert _exports(config_js)["API_ORIGIN"] == API


def test_two_variables_naming_one_origin_are_not_listed_twice():
    conf, _ = _render(KAICALC_NEWS_ORIGIN=NEWS, KAICALC_API_ORIGIN=NEWS)
    connect = _directives(conf)["connect-src"]
    assert connect == ["'self'", NEWS], connect


# ---------------------------------------------------------------------------------------
# The unconfigured deployment
# ---------------------------------------------------------------------------------------


def test_an_unconfigured_deployment_names_no_host_at_all():
    """The default, and the state most deployments of this will be in.

    ``'self'`` and ``data:`` and nothing else — which is what the policy said before this
    change, minus the client's domain. Unset is not an error: there is no WordPress to
    read, so the home page removes its news section rather than requesting a guess.
    """

    conf, config_js = _render()
    directives = _directives(conf)

    assert directives["connect-src"] == ["'self'"]
    assert directives["img-src"] == ["'self'", "data:"]
    assert _exports(config_js) == {"NEWS_ORIGIN": "", "API_ORIGIN": ""}


def test_an_explicitly_empty_origin_is_honoured_rather_than_defaulted():
    """`${VAR-default}` in compose, not `${VAR:-default}`.

    Otherwise ``KAICALC_NEWS_ORIGIN=`` in a .env falls back to the client's site and there
    is no way to turn the feed off short of editing the compose file.
    """

    conf, config_js = _render(KAICALC_NEWS_ORIGIN="", KAICALC_API_ORIGIN="")
    assert _directives(conf)["connect-src"] == ["'self'"]
    assert _exports(config_js)["NEWS_ORIGIN"] == ""


# ---------------------------------------------------------------------------------------
# The failure modes
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "https://evil.test/wp-json",   # a path
        "https://evil.test/",          # a trailing slash
        "evil.test",                   # no scheme
        "*.evil.test",                 # a wildcard
        "https://evil.test'; script-src 'unsafe-inline",  # a directive break
        "ftp://evil.test",             # not a web scheme
    ],
)
def test_a_malformed_origin_stops_the_container_rather_than_the_feed(value: str):
    """Refusing to start is the correct failure, and it is also the injection guard.

    A typo in an API origin must not become a calculator quietly showing somebody else's
    numbers, and a typo in a news origin must not become a policy that silently refuses
    the feed. The same check is what makes it safe to interpolate the value into a CSP
    header and into a single-quoted JavaScript literal: nothing that reaches either output
    can hold a quote, a semicolon, a space or a newline.

    This starts nginx for real, because the property under test is that the stock
    entrypoint's ``set -e`` carries our non-zero exit all the way out — the script runs
    inside a ``find | while read`` subshell, and an exit that stayed in the subshell would
    leave the container serving a policy nobody meant.
    """

    result = subprocess.run(
        ["docker", "run", "--rm", "--user", "nginx", "-e", f"KAICALC_NEWS_ORIGIN={value}",
         IMAGE, "nginx", "-g", "daemon off;"],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode != 0, (
        f"the container started with KAICALC_NEWS_ORIGIN={value!r}; a malformed origin "
        "must stop it, not reach the policy"
    )
    assert "KAICALC_NEWS_ORIGIN" in result.stdout + result.stderr, (
        "the refusal must name the variable that is wrong"
    )


def test_envsubst_does_not_eat_nginx_own_variables():
    """The sharp edge on the usual answer.

    ``envsubst`` with no shell-format argument substitutes every ``$name`` it finds, and
    this configuration is full of them. Every one would become the empty string, producing
    valid nginx syntax that logs blank lines for every request and redirects to nothing.
    ``docker/web-config.sh`` names exactly two variables, so nothing else can be touched
    whatever ends up in the container's environment.
    """

    conf, _ = _render(KAICALC_NEWS_ORIGIN=NEWS)
    for variable in ("$time_local", "$request", "$status", "$body_bytes_sent",
                     "$request_time", "$uri", "$scheme", "$http_host",
                     # The forwarded-header maps. `$remote_addr` and
                     # `$proxy_add_x_forwarded_for` eaten would forward an EMPTY client
                     # address to both applications, which `db.detection.client_ip`
                     # normalises to None - skipping the blocklist and the rate limit
                     # outright, for every caller, with nothing raised anywhere.
                     "$remote_addr", "$http_x_forwarded_proto",
                     "$proxy_add_x_forwarded_for"):
        assert variable in conf, f"envsubst ate nginx's own {variable}"
    # The header's own prose names `${KAICALC_CSP_*}`, which is not a variable name and is
    # left alone; the directives are what must have been substituted.
    for values in _directives(conf).values():
        for value in values:
            assert "$" not in value, f"a placeholder survived unrendered: {value}"


def test_the_image_ships_no_loadable_configuration_of_its_own():
    """conf.d is empty until the entrypoint fills it.

    ``docker/nginx.conf`` copied straight into ``conf.d`` would be loaded with its
    placeholders still in it, and the natural "fix" for that is to put a literal domain
    back — which is the defect. The image cannot serve without the entrypoint running.
    """

    result = _run({}, f"ls /etc/nginx/conf.d/ | wc -l && test -f /etc/nginx/kaicalc.conf.template && echo template-present")
    assert result.returncode == 0, result.stderr
    assert result.stdout.split()[0] == "0", (
        f"/etc/nginx/conf.d is not empty before the entrypoint runs: {result.stdout}"
    )
    assert "template-present" in result.stdout


# ---------------------------------------------------------------------------------------
# The checked-in default
# ---------------------------------------------------------------------------------------


def test_the_repository_copy_of_config_js_is_a_drop_in_for_the_generated_one():
    """A plain checkout has no entrypoint, so web/js/config.js has to stand on its own.

    Same export names, both empty: the front end works from a checkout with no news feed
    and a relative API path, and the container overwrites the file with the same shape.
    """

    checked_in = _exports((ROOT / "web" / "js" / "config.js").read_text(encoding="utf-8"))
    generated = _exports(_render()[1])
    assert checked_in == generated == {"NEWS_ORIGIN": "", "API_ORIGIN": ""}
