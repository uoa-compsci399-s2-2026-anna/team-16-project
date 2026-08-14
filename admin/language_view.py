"""The panel's language chooser endpoint. Contract §7.7, open item O-8.

**A form post, not a script.** The panel renders through FastAPI and works with
scripting switched off, so its chooser has to as well: a ``<select>`` and a
submit button inside a plain ``<form method="post">``, which is the shape that
needs no JavaScript at all. The calculator is deliberately the opposite - it is
ES modules end to end and does not render without scripting, so its chooser is
built in ``web/js/i18n.js`` and can never exist as a dead control. One stored
choice, two mechanisms, each honest about the surface it sits on.

**Registered on the outer app, ahead of sqladmin's mount.** Two reasons, and
both are load-bearing:

* sqladmin's ``Admin()`` mounts a sub-application whose path regex matches every
  ``/admin/...`` prefix, and Starlette stops at the first matching route. A
  route registered after it is unreachable - the same ordering trap
  ``admin/app.py`` documents for ``/admin/static``.
* ``@expose`` wraps every sqladmin route in ``login_required``, and this one must
  work **before** anybody logs in. The login page is the page a person who does
  not read English needs most, and it is the one page a locked-out account can
  still reach.

**No CSRF token, and that is a decision rather than an omission.** Every other
state-changing form in this panel carries one (``admin/csrf.py``). This one
cannot, because ``issue_token`` writes into the session and would therefore mint
a signed session cookie for **every anonymous visitor who merely loads the login
page** - which today mints none. Creating a real per-visitor identifier in order
to protect a cosmetic preference is a bad trade in exactly the direction §2.3
cares about.

What is used instead is a stateless same-origin check on ``Origin``. What it has
to defend is small: the endpoint writes one value from a closed set into one
cookie that decides which words a page is rendered in. A successful forgery
changes the victim's interface language, which the visible control on the next
page changes back. No data is read, no data is written, no privilege moves.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from admin import i18n

#: Where the chooser sends a visitor back to when the form names nowhere, and
#: the only prefix it will send them to at all.
_PANEL_ROOT = "/admin"

#: The form field carrying the chosen language, and the field carrying the page
#: to return to. ``next`` is deliberately not called ``return`` or ``redirect``:
#: it is validated as a path within this panel and never used as a URL.
_FIELD = "lang"
_NEXT_FIELD = "next"


def safe_next(raw: str | None) -> str:
    """A path inside this panel, or the panel root. **Never an open redirect.**

    Three things are refused, and the second is the one that looks safe:

    * anything that is not under ``/admin``;
    * anything beginning ``//``, which a browser reads as a **protocol-relative
      URL** - ``//evil.example/admin`` passes a naive ``startswith('/')`` check
      and navigates off-site;
    * anything carrying a scheme or a host at all.

    The value is only ever a path, so it is compared as one rather than parsed
    as a URL and trusted.
    """
    if not raw:
        return _PANEL_ROOT
    candidate = raw.strip()
    if not candidate.startswith("/") or candidate.startswith("//"):
        return _PANEL_ROOT
    split = urlsplit(candidate)
    if split.scheme or split.netloc:
        return _PANEL_ROOT
    path = split.path
    if path != _PANEL_ROOT and not path.startswith(f"{_PANEL_ROOT}/"):
        return _PANEL_ROOT
    return candidate


def is_same_origin(request: Request) -> bool:
    """Whether this post came from a page this panel served.

    ``Origin`` is sent by every current browser on a form POST and cannot be
    set by the page making the request, which is what makes it usable here.
    An **absent** ``Origin`` is accepted: some privacy tooling strips it, and
    refusing those visitors would break the control for exactly the people most
    likely to be using it. That is the deliberate soft edge of a check
    protecting a cosmetic preference - see this module's docstring for the size
    of what is being defended.
    """
    origin = request.headers.get("origin")
    if origin is None:
        return True
    split = urlsplit(origin)
    return (split.scheme, split.netloc) == (request.url.scheme, request.url.netloc)


async def set_language(request: Request) -> Response:
    """Store the chosen language and return to the page the form came from.

    **303, not 302.** The response to a POST has to be fetched with GET, or a
    reload re-submits the form; 303 says that in terms rather than relying on
    the near-universal but non-normative browser behaviour of downgrading a 302.

    **An unrecognised value stores "follow the system" rather than being
    rejected.** There is no error state worth building here: every value the
    chooser can emit is valid, so an invalid one arrived from somewhere else,
    and the readable answer to it is the default rather than an error page in a
    language the visitor may not read.
    """
    form = await request.form()
    destination = safe_next(form.get(_NEXT_FIELD))

    if not is_same_origin(request):
        return RedirectResponse(destination, status_code=303)

    requested = (form.get(_FIELD) or "").strip()
    chosen = (
        i18n.FOLLOW_SYSTEM
        if requested == i18n.FOLLOW_SYSTEM or not i18n.is_supported(requested)
        else requested
    )

    response = RedirectResponse(destination, status_code=303)
    # **A write, never a delete** - including for "follow the system", which
    # stores the literal `auto`. See i18n.FOLLOW_SYSTEM for why deleting is the
    # wrong shape. `httponly=False` because web/js/i18n.js reads and writes this
    # same cookie on the calculator; `secure` is not set because the cookie
    # carries no secret and would otherwise stop working on the plain-http
    # localhost this stack is developed and demonstrated on.
    response.set_cookie(
        i18n.COOKIE_NAME,
        chosen,
        max_age=i18n.COOKIE_MAX_AGE,
        path="/",
        httponly=False,
        samesite="lax",
    )
    return response


def register(app) -> None:
    """Mount the endpoint. **Call before ``Admin()``** - see the module docstring.

    POST only. A GET that changed a stored preference would be actionable by an
    ``<img>`` tag, and would be re-run by every prefetcher that touches the page.
    """
    app.add_route(
        f"{_PANEL_ROOT}/language", set_language, methods=["POST"], name="set-language"
    )
