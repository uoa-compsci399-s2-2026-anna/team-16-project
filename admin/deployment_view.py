"""``/admin/deployment`` — what the proxy in front of this panel is doing.

**The question this page answers.** An operator putting this stack behind an
edge proxy they already run — the ordinary shape on a public IPv4 whose port
443 belongs to another site — gets it right on the second or third attempt,
and until now the only way to find out whether an attempt landed was to ssh
in and read environment variables out of three containers. This page is the
read-back: configure the edge, open it, and see whether the address and the
scheme that arrive are the ones the edge is sending.

**IT CONFIGURES NOTHING, AND THAT IS THE WHOLE BOUNDARY.** nginx renders its
configuration once, at container start (``docker/web-config.sh``); changing
``KAICALC_TRUST_FORWARDED_HEADERS`` needs a re-render and a reload, and the
two application settings need a restart. A control on this page that could do
any of that would be a web form that restarts its own containers — a
privilege surface reachable by anybody who reaches the panel, which is a far
larger thing than the diagnosis it would have saved. ``README.md`` documents
the ``docker exec`` loop for actually changing a value, including that the
change does not survive the next ``up``. Nothing in this module writes
anywhere: no database session is opened except the one the role check needs,
no row is inserted, no ``audit_log`` entry is produced.

**WHAT IT CAN OBSERVE VERSUS WHAT IT CAN ONLY BE TOLD.** These are not the
same kind of fact and the page must not present them as one:

* ``SESSION_HTTPS_ONLY`` and ``PROTECTION_TRUSTED_PROXY`` are **read from
  this process's own environment** (``admin/config.py``). They are the values
  actually in force for the panel you are reading. The API is a separate
  process with its own copy of ``PROTECTION_TRUSTED_PROXY``, and this page
  cannot see that one — ``docker/compose.yaml`` hands both services the same
  variable, but a deployment that does not use that file is free to disagree.
* ``KAICALC_TRUST_FORWARDED_HEADERS`` is **not readable here at all.** It
  belongs to the ``web`` container's nginx, and ``docker/compose.yaml`` does
  not put it in this container's environment. Adding it there was considered
  and rejected: a value read from *this* process's environment is not nginx's
  setting, it is a second copy that can silently disagree with it, which is
  the defect class this repository keeps finding (see the two-copies note at
  the head of ``docker/web-config.sh``). What the page shows instead is
  first-hand evidence — the header chain that actually arrived — and what
  that chain does and does not prove.

**What the chain proves, exactly.** With the flag off, nginx sets
``X-Forwarded-For`` to ``$remote_addr`` and throws any inbound copy away, so
exactly one entry arrives. With it on, nginx sends
``$proxy_add_x_forwarded_for`` — the inbound chain with its own peer appended
— which is also exactly one entry when nothing inbound arrived. So:

* **two or more entries** proves the flag is *on* and that something in front
  sent a chain. It does **not** prove that something is the operator's edge:
  if nothing is really in front, that left-most entry was chosen by whoever
  connected, which is precisely the hazard the flag's default guards.
* **one entry** is consistent with the flag being off *and* with it being on
  with nothing sending a chain. The two are indistinguishable from here, and
  the page says so rather than guessing.
* **no ``X-Forwarded-For`` at all** means the request did not come through
  the stack's nginx — the panel's own published port (18001) was reached
  directly.

``X-Real-IP`` is the useful companion because ``docker/nginx-proxy-headers.
conf`` deliberately does **not** switch it on the flag: it is always
``$remote_addr``, the peer that nginx is actually talking to. It is therefore
the one header that states the unembellished truth about the connection, and
comparing it with the left-most forwarded entry is what makes the chain
readable.

**Displaying is not storing, and that was checked rather than assumed.**
Contract §2.3 forbids *storing* an address, a user agent or a fingerprint.
Rendering this request's own headers into a response that is thrown away when
it is sent stores nothing. But "this obviously is not recorded" is the exact
assumption that already failed here once — the nginx access log was found
writing ``$remote_addr``, ``$http_user_agent``, ``$http_referer`` and
``$http_x_forwarded_for`` on every request — so three things were checked
against the running stack rather than reasoned about:

1. the rendered ``kaicalc`` log format in ``/etc/nginx/conf.d/kaicalc.conf``
   carries ``$time_local``, ``$request``, ``$status``, ``$body_bytes_sent``
   and ``$request_time``, and no header and no address;
2. uvicorn's own access line in the ``admin`` container logs
   ``scope["client"]`` — the nginx container's network address, the same
   value for every visitor, never the forwarded one, because
   ``ProxyHeadersMiddleware`` declines to rewrite ``scope`` here (§7.8.1);
3. no ``audit_log`` row is written by loading this page.
   ``tests/admin/test_deployment_view.py`` asserts (3) by counting the table
   across the request.

**Role: administrator only** (contract §8.3). It describes the deployment's
security posture — which addresses are believed, whether the session cookie
is ``Secure``, whether the rate limit is measuring visitors or a proxy — and
that is an administrator's concern, next to the blocklist and the audit log,
not a ``staff`` one. Enforced in the three places ``AdministratorOnly``
(``admin/modelviews.py``) names, because ``@expose`` inherits none of them:
``is_visible`` and ``is_accessible`` keep it out of the sidebar, and the
explicit ``_require_admin`` at the top of the handler is the only one that
actually refuses the URL.

**Which of the first two hides the menu entry, measured.** sqladmin's
``_macros.html`` renders an item only
``{% if menu.is_visible(request) and menu.is_accessible(request) %}``, so
either predicate returning False is enough and neither is individually
load-bearing here — a mutant flipping ``is_visible`` alone to True survives
every test in ``tests/admin/test_deployment_view.py``. Dropping
``AdministratorOnly`` from the bases, which is the mistake somebody would
actually make, fails the sidebar assertion and the 403 together. Both are
still declared: the mixin is the one place the role rule is written down,
and taking a predicate off it here to save a line would put this view on a
different footing from every other administrator-only screen.

**Reachable when ``PROTECTION_ENABLED`` is false**, deliberately. That switch
turns off the panel's blocklist, its header check and its rate limit
together, and hiding the page that says so would remove the diagnosis exactly
when the deployment has least protection. The page keeps working and leads
with a finding naming the state; the other two settings it reports are not
governed by that switch at all.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

from sqladmin import BaseView, expose
from starlette.requests import Request
from starlette.responses import Response

from admin import i18n as admin_i18n
from admin.config import Settings
from admin.modelviews import AdministratorOnly
from admin.runtime import get_runtime
from db.detection import client_ip

#: Where an address came from, in the words the page prints. Derived from the
#: value ``client_ip`` returned rather than by re-deciding its branch here —
#: a second copy of that decision would be free to disagree with the one the
#: blocklist and the rate limit actually use, which is the failure this whole
#: page exists to make visible.
FROM_FORWARDED = "the left-most X-Forwarded-For entry"
FROM_CONNECTION = "the connection this panel accepted"
FROM_NOWHERE = "nothing usable — the checks that key on an address are skipped"

#: The view's name, held as a constant because it is needed twice and the
#: second use must not read the first back. ``translate_view_names`` replaces
#: ``DeploymentView.name`` with a descriptor that returns the *translated*
#: string, so ``gettext(self.name)`` would be a lookup of an already-Chinese
#: string - which happens to work (a missing key returns its own source) and
#: is exactly the kind of accident that stops working quietly. The catalogue
#: key is named here instead.
VIEW_NAME = "Deployment"


@dataclass(frozen=True)
class Observation:
    """What one request actually carried. Built per request, kept nowhere."""

    forwarded_for: str | None
    chain: tuple[str, ...]
    forwarded_proto: str | None
    real_ip: str | None
    peer: str | None
    scheme: str
    decided_address: str | None
    decided_from: str

    @property
    def through_our_nginx(self) -> bool:
        """Whether the stack's own nginx is in the path at all.

        It sets both ``X-Forwarded-For`` and ``X-Real-IP`` on every proxied
        request, in both branches of the trust flag, so the absence of both
        means the request reached the panel's published port directly.
        """
        return self.forwarded_for is not None or self.real_ip is not None


def _split_chain(raw: str | None) -> tuple[str, ...]:
    """``"a, b , c"`` → ``("a", "b", "c")``; ``None``/blank → ``()``.

    Empty elements are dropped rather than rendered as blanks: a caller can
    send ``X-Forwarded-For: ,,``, and three empty table rows would say
    nothing about a deployment while looking like three hops.
    """
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def observe(request: Request, *, trusted_proxy: bool) -> Observation:
    """Read this request's forwarding state. Reads only; records nothing."""
    forwarded_for = request.headers.get("x-forwarded-for")
    chain = _split_chain(forwarded_for)
    decided = client_ip(request, trusted_proxy=trusted_proxy)

    if decided is None:
        decided_from = FROM_NOWHERE
    elif trusted_proxy and chain and _same_address(chain[0], decided):
        decided_from = FROM_FORWARDED
    else:
        decided_from = FROM_CONNECTION

    client = request.client
    return Observation(
        forwarded_for=forwarded_for,
        chain=chain,
        forwarded_proto=request.headers.get("x-forwarded-proto"),
        real_ip=request.headers.get("x-real-ip"),
        peer=client.host if client is not None else None,
        scheme=request.url.scheme,
        decided_address=decided,
        decided_from=decided_from,
    )


def _same_address(candidate: str, normalised: str) -> bool:
    """Whether a raw header entry is the address ``client_ip`` returned.

    Compared as addresses, not as strings: ``client_ip`` runs its result
    through ``db.blocklist.normalise_ip``, which compresses IPv6 and strips
    the brackets an edge may send, so ``[2001:DB8::1]`` and ``2001:db8::1``
    are one address and a string comparison would report the wrong source.
    """
    try:
        return ipaddress.ip_address(candidate.strip().strip("[]")) == (
            ipaddress.ip_address(normalised)
        )
    except ValueError:
        return False


def _is_not_public(address: str | None) -> bool:
    """Anything that cannot be a visitor arriving from the public internet.

    ``ipaddress``'s own ``is_private`` is the whole test: it already covers
    loopback, link-local, RFC1918, unique-local **and** the documentation
    ranges (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24). Naming the
    predicate for what it means rather than for RFC1918 matters on the page —
    a container network address and a TEST-NET address are both "not a
    visitor", and calling either of them "private" invites an argument the
    reader does not need to have.
    """
    if not address:
        return False
    try:
        return ipaddress.ip_address(address).is_private
    except ValueError:
        return False


@dataclass(frozen=True)
class Finding:
    """One sentence about the state, and what it costs.

    ``level`` is one of ``ok`` / ``note`` / ``warn`` and decides only the
    colour. A finding says what was observed, then what follows from it —
    listing three values and leaving the reader to reason is the thing this
    page exists not to do.
    """

    level: str
    title: str
    detail: str


def assess(observation: Observation, settings: Settings) -> list[Finding]:
    """Turn one observation plus this process's settings into findings.

    Pure: no request, no clock, no database. That is what lets every
    combination below be driven directly, including the ones that need a
    deployment this repository cannot start.
    """
    findings: list[Finding] = []

    if not settings.protection_enabled:
        findings.append(Finding(
            "warn",
            "PROTECTION_ENABLED is false, so this panel is applying none of it",
            "The blocklist, the scripted-client header check and the per-address "
            "rate limit are all off together — there is no finer switch. "
            "PROTECTION_TRUSTED_PROXY below still decides which address would be "
            "used if it were on, so the reading is still worth taking. The public "
            "API is a separate process and does not read this variable at all: "
            "its rate limit and its blocklist are unaffected by this being false.",
        ))

    if not observation.through_our_nginx:
        detail = (
            "Neither X-Forwarded-For nor X-Real-IP arrived, and the stack's nginx "
            "sets both on every request it proxies. So this request reached the "
            "panel's own published port (18001 in docker/compose.yaml) rather "
            "than passing through the proxy — nothing on this page describes the "
            "proxied path."
        )
        if settings.protection_trusted_proxy:
            findings.append(Finding(
                "warn",
                "This request bypassed the proxy, and this panel trusts forwarded addresses",
                detail + " That combination is the dangerous one: with "
                "PROTECTION_TRUSTED_PROXY true and the panel directly reachable, a "
                "caller who connects to it can put any address in X-Forwarded-For "
                "and be measured as that address — out of the rate-limit bucket and "
                "out of the blocklist. Remove the api and admin ports: blocks from "
                "docker/compose.yaml, or set PROTECTION_TRUSTED_PROXY back to false.",
            ))
        else:
            findings.append(Finding(
                "note",
                "This request did not come through the stack's nginx",
                detail + " Open the panel through the proxy (the /admin path on the "
                "web service's port) to read the deployment you are configuring.",
            ))
    elif len(observation.chain) >= 2:
        findings.append(Finding(
            "note",
            "A chain of "
            f"{len(observation.chain)} arrived, so KAICALC_TRUST_FORWARDED_HEADERS is on",
            "With the flag off, nginx overwrites X-Forwarded-For with the peer it "
            "saw and exactly one entry arrives; a longer chain can only be produced "
            "by the trusting branch. The left-most entry is what the front-most "
            "proxy reported the visitor to be. This page cannot tell you whether "
            "that proxy is yours: if nothing you operate is really in front, that "
            "entry was chosen by whoever connected — which is why the flag defaults "
            "to off.",
        ))
    else:
        findings.append(Finding(
            "note",
            "One forwarded entry, which does not say whether the trust flag is on",
            "With KAICALC_TRUST_FORWARDED_HEADERS off, nginx sends the peer it saw. "
            "With it on and nothing in front sending a chain, it sends the same "
            "value. The two are identical from here and this page will not guess "
            "between them. If you have just configured an edge and expected two "
            "entries, the edge is not sending X-Forwarded-For, or its traffic is "
            "not reaching this stack.",
        ))

    findings.extend(_assess_address(observation, settings))
    findings.append(_assess_scheme(observation, settings))
    return findings


def _assess_address(observation: Observation, settings: Settings) -> list[Finding]:
    """Whether the address in force is the visitor's, and what it costs when
    it is not."""
    findings: list[Finding] = []
    trusted = settings.protection_trusted_proxy

    if not trusted and len(observation.chain) >= 2:
        findings.append(Finding(
            "warn",
            "The visitor's address is being forwarded and this panel is ignoring it",
            "nginx believed an inbound chain and passed it on, but "
            "PROTECTION_TRUSTED_PROXY is false, so db/detection.py's client_ip "
            "reads the connection instead and every visitor on earth is measured "
            "as one address. Concretely: one shared rate-limit bucket, and one "
            "ip_block row that denies everyone. This is the combination that looks "
            "configured and does nothing — docker/web-config.sh warns about it at "
            "container start too. Set both, or neither.",
        ))
    elif trusted and not observation.chain:
        findings.append(Finding(
            "warn",
            "This panel trusts X-Forwarded-For and no X-Forwarded-For arrived",
            "PROTECTION_TRUSTED_PROXY is true, so client_ip will believe that "
            "header whenever it is present — and on this request it was not. The "
            "address in force fell back to the connection. Whatever is meant to be "
            "setting the header is not in the path.",
        ))
    elif trusted:
        findings.append(Finding(
            "ok",
            "The address in force is the one the chain reported",
            "PROTECTION_TRUSTED_PROXY is true and the left-most forwarded entry is "
            "what the blocklist and the rate limit are keyed on. That is correct "
            "only while nothing can reach api/ or admin/ except through this "
            "stack's nginx — see the ports: blocks in docker/compose.yaml.",
        ))
    else:
        findings.append(Finding(
            "ok",
            "The address in force is the connection this panel accepted",
            "PROTECTION_TRUSTED_PROXY is false, so X-Forwarded-For is ignored "
            "entirely and no caller can name their own address. This is the "
            "shipped default and it is right whenever this panel is directly "
            "reachable.",
        ))

    if _is_not_public(observation.decided_address):
        findings.append(Finding(
            "note",
            "The address in force "
            f"({observation.decided_address}) is not a public address",
            "Loopback, a private range, a link-local address or a documentation "
            "range. Expected on a laptop, and expected inside a container network "
            "where the panel is measuring nginx rather than a visitor. On a "
            "deployment reached over the internet it is the symptom of the state "
            "above: what is being rate-limited and blocked is a proxy, not the "
            "people behind it.",
        ))
    return findings


def _assess_scheme(observation: Observation, settings: Settings) -> Finding:
    """Whether the session cookie's ``Secure`` flag matches the transport the
    visitor is actually on."""
    proto = (observation.forwarded_proto or "").lower()

    if not settings.session_https_only and proto == "https":
        return Finding(
            "warn",
            "TLS terminates in front and the staff session cookie is not Secure",
            "X-Forwarded-Proto says https, so a browser reached this deployment "
            "over TLS — and SESSION_HTTPS_ONLY is false, so the cookie carrying "
            "admin access is issued without the Secure attribute and a browser "
            "will send it over plain http. One http:// navigation on this origin "
            "hands over a live staff session, including to anyone on the same "
            "network. Set SESSION_HTTPS_ONLY=true.",
        )
    if not settings.session_https_only:
        return Finding(
            "note",
            "The staff session cookie is not marked Secure",
            "Correct where the panel really is served over plain http — a local "
            "run, or a first container start before TLS is arranged. Nothing on "
            "this request contradicts that: X-Forwarded-Proto is "
            f"{proto or 'absent'}. But note that with "
            "KAICALC_TRUST_FORWARDED_HEADERS off, that header reports the hop into "
            "this stack's nginx and says nothing about an edge in front of it — so "
            "turn the trust flag on first, then read this line again.",
        )
    return Finding(
        "ok",
        "The staff session cookie is marked Secure",
        "SESSION_HTTPS_ONLY is true, so the cookie is issued with the Secure "
        "attribute and a browser will not return it over plain http. You are "
        "reading this page with that cookie, which is itself the evidence that "
        "the transport it is being sent over is one the browser accepts.",
    )


class DeploymentView(AdministratorOnly, BaseView):
    """The page.

    ``AdministratorOnly`` first in the bases so that its ``is_visible`` and
    ``is_accessible`` win over ``BaseView``'s permissive defaults — sqladmin
    consults both for the sidebar entry, and neither for this ``@expose``
    route. See the module docstring for what each of the three guards
    actually stops.
    """

    name = VIEW_NAME
    icon = "fa-solid fa-network-wired"

    def _session_maker_for(self, request: Request):
        """``AdministratorOnly`` reads the role out of whatever this returns.

        A ``BaseView`` has no ``session_maker`` — sqladmin sets that only on
        the ``ModelView``s registered through ``add_view`` — so the role
        lookup goes through this application's own ``Runtime`` instead. See
        ``admin/runtime.py`` for why that is per-app state and not a global.
        """
        return get_runtime(request).session_factory

    @expose("/deployment", identity="deployment", methods=["GET"])
    async def deployment(self, request: Request) -> Response:
        # `identity` named explicitly: sqladmin falls back to the *method*
        # name for a BaseView's route name, which is how the dry-run page
        # ended up reachable as `admin:view-try_scenario`.
        #
        # This line is the one that actually refuses the URL. `is_accessible`
        # above is consulted for the sidebar entry and for the routes
        # sqladmin generates itself; `@expose` wraps this handler in
        # `login_required` and nothing else, so without this call any
        # signed-in account could open the page whatever the sidebar showed.
        self._require_admin(request)

        settings = get_runtime(request).settings
        observation = observe(
            request, trusted_proxy=settings.protection_trusted_proxy
        )
        return await self.templates.TemplateResponse(
            request,
            "brand/deployment.html",
            {
                # Through the catalogue, on the same key the menu entry
                # uses. sqladmin's layout renders `title` as the page heading
                # verbatim — it applies no `_()` of its own — so a literal
                # here puts an English heading directly above a Chinese menu
                # entry naming the same page. The panel has one instance of
                # that already (`/admin/getting-started`, whose heading stays
                # English in Chinese); it is a pre-existing defect on that
                # page and not one worth adding a second of here.
                "title": admin_i18n.gettext(VIEW_NAME),
                "observation": observation,
                "settings": settings,
                "findings": assess(observation, settings),
            },
        )
