"""Interface translation for the admin panel. Contract open item O-8.

**The English source string is the key.** ``gettext("Save")`` looks up
``"Save"`` and returns ``"保存"``. There is no separate key namespace, and
that is the decision everything else here follows from:

* sqladmin's own templates already wrap their twenty-five user-visible
  strings in ``_("...")`` with the English as the msgid. Inventing keys for
  our strings would leave two schemes in one panel - ours and theirs - which
  is the outcome this module exists to avoid. Installing our callable into
  Jinja (see ``admin/app.py``) translates their strings through our
  catalogue, with no fork of their templates.
* The eighty-two field descriptions stay in ``form_args`` in English and are
  translated on the way to the browser. The English text therefore lives in
  exactly one place, so a key can never point at a description that has
  since been reworded.
* A missing key returns the English source. No sentinel, no blank label, no
  key name leaking into a page.

The cost of source-text keys is that rewording the English orphans its
translation. That is deliberately caught by ``tests/admin/test_i18n.py``
rather than at runtime: a half-translated language ships as
English-in-places, which is readable, and the test names the string that
lost its translation on the commit that reworded it.

**Why not sqladmin's own i18n.** ``sqladmin.i18n`` exists and is unusable
here for two independent reasons. It requires ``babel``, which is not
installed and which this project may not add. And it loads compiled
catalogues from a path inside its own installed package, shipping ``en``,
``de``, ``az``, ``ru`` and ``tr`` only - there is no way to add Chinese to it
from a wheel of ours. What *is* reachable is the seam underneath it,
``jinja2.ext.i18n``'s ``install_gettext_callables``, which sqladmin itself
calls and which takes any callable.

**Why not gettext proper.** Plural forms, domains and compiled ``.mo`` files
buy nothing at this size and cost a dependency and a build step, both of
which are forbidden here. A dictionary lookup is enough.
"""

from __future__ import annotations

import json
import re
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

#: The language every catalogue falls back to, and the language the source
#: strings are written in. It has no catalogue file: for English the key *is*
#: the answer.
DEFAULT_LANGUAGE = "en"

#: Set only by an explicit ``?lang=`` choice, and read on every request.
#:
#: A cookie rather than the staff session, and the reason is not preference.
#: The login page renders before a session exists, and so do ``/admin/enrol``
#: and ``/admin/verify`` - the whole enrolment flow runs before there is an
#: account at all. A person who cannot read English needs those pages in
#: their own language more than they need any other page in it. The session
#: is also cleared on logout, so a language kept there would revert to
#: English every time somebody signed out.
#:
#: Not a column on ``staff`` either: that would need a migration and a write
#: path through ``admin/accounts.py``, and the preference belongs to a
#: browser rather than to a person.
#:
#: Path "/" so the panel and the public calculator are one choice on one
#: origin - nginx serves both from the same host and port.
COOKIE_NAME = "kaicalc_lang"

#: One year. A language choice that expires is a language choice made twice.
COOKIE_MAX_AGE = 365 * 24 * 60 * 60

#: ``?lang=zh`` on any URL switches and persists. The same spelling sqladmin's
#: own middleware uses, so the two are not two things to remember.
QUERY_PARAM = "lang"

#: Where the catalogues live. Inside the package, because
#: ``[tool.setuptools.package-data]`` cannot reach outside its own package
#: directory (pyproject.toml says so for the Alembic tree) and
#: ``docker/admin.Dockerfile`` copies only ``admin/ api/ db/ engine/``. A
#: top-level ``i18n/`` directory would be in neither the wheel nor the image
#: - which is exactly how the guidance blocks went missing once already.
LOCALES_DIR = Path(__file__).parent / "locales"

#: Splits a context-qualified key. Identical English in two places that need
#: two translations is written ``_("factor set|Save")``; the lookup tries the
#: whole key first, then the text after the bar, and renders the text after
#: the bar if neither is translated. One scheme with a qualifier, not two
#: schemes. Used sparingly - with descriptions this long, collisions are
#: rare.
CONTEXT_SEPARATOR = "|"

#: Every ``%(name)s`` placeholder in a string.
#:
#: Jinja's newstyle gettext applies ``translated % variables`` *after* this
#: module returns, so a translation that drops, renames or malforms a
#: placeholder does not render wrongly - it raises KeyError or ValueError at
#: render time, or silently prints the wrong number. Both are asserted
#: against in tests/admin/test_i18n.py, which is why this pattern is module
#: level rather than buried in the test.
PLACEHOLDER = re.compile(r"%\((\w+)\)[sd]")


@dataclass(frozen=True)
class Catalogue:
    """One language: its own name for itself, and whether anybody read it."""

    language: str
    endonym: str
    machine_translated: bool
    strings: Mapping[str, str]


def _load() -> dict[str, Catalogue]:
    """Read every catalogue once, at import.

    English is synthesised rather than read from a file: its strings are the
    keys, so a file would be a dictionary mapping every string to itself and
    a second place for the English wording to drift.
    """
    catalogues = {
        DEFAULT_LANGUAGE: Catalogue(DEFAULT_LANGUAGE, "English", False, {}),
    }
    if LOCALES_DIR.is_dir():
        for path in sorted(LOCALES_DIR.glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            catalogues[raw["language"]] = Catalogue(
                language=raw["language"],
                endonym=raw["endonym"],
                machine_translated=bool(raw["machine_translated"]),
                strings=raw["strings"],
            )
    return catalogues


_CATALOGUES: dict[str, Catalogue] = _load()

#: The active language for the request being served.
#:
#: A ContextVar and not a request attribute because Jinja's gettext callable
#: is handed the message and nothing else - there is no request to read. Set
#: by LanguageMiddleware before the request reaches anything that renders,
#: which is what makes it visible to the task Starlette's BaseHTTPMiddleware
#: spawns downstream (a child task copies the context at creation).
_active: ContextVar[str] = ContextVar("kaicalc_admin_language", default=DEFAULT_LANGUAGE)


def languages() -> list[Catalogue]:
    """Every language the switcher offers, English first."""
    rest = sorted(
        (c for c in _CATALOGUES.values() if c.language != DEFAULT_LANGUAGE),
        key=lambda c: c.language,
    )
    return [_CATALOGUES[DEFAULT_LANGUAGE], *rest]


def is_supported(code: str | None) -> bool:
    return code in _CATALOGUES


def set_language(code: str | None) -> str:
    """Activate `code` for this request, falling back to English.

    An unrecognised value is *ignored*, not corrected and not stored - the
    panel renders in English and the switcher shows English as selected,
    which is true.
    """
    resolved = code if is_supported(code) else DEFAULT_LANGUAGE
    _active.set(resolved)
    return resolved


def active_language() -> str:
    return _active.get()


def catalogue(code: str | None = None) -> Catalogue:
    return _CATALOGUES.get(code or active_language(), _CATALOGUES[DEFAULT_LANGUAGE])


def endonym(code: str) -> str:
    """A language's own name for itself, for the switcher."""
    return catalogue(code).endonym if is_supported(code) else code


def is_machine_translated(code: str) -> bool:
    """Whether this language shipped unread by anyone who speaks it.

    Read straight off the catalogue rather than from a list in code, so that
    adding a language is adding a file - the remaining nineteen all arrive
    the same way and none of them needs an edit here.
    """
    return is_supported(code) and catalogue(code).machine_translated


def gettext(message: str, code: str | None = None) -> str:
    """Translate one source string. English, or the source, if not translated.

    Installed into Jinja as ``_``. The signature takes no ``**variables``:
    with ``newstyle=True`` Jinja applies ``result % variables`` itself, after
    this returns.
    """
    if message is None:
        return message
    strings = catalogue(code).strings
    if message in strings:
        return strings[message]
    if CONTEXT_SEPARATOR in message:
        _context, _, bare = message.partition(CONTEXT_SEPARATOR)
        return strings.get(bare, bare)
    return message


def ngettext(singular: str, plural: str, n: int) -> str:
    """Jinja requires a plural callable; nothing in this panel uses one.

    Chinese has no plural inflection, and no string on these screens is
    pluralised in English either. Implemented rather than omitted because
    ``install_gettext_callables`` takes both, and a missing one would be an
    AttributeError on whichever template first used ``{% trans %}``.
    """
    return gettext(singular if n == 1 else plural)


def resolve(cookie: str | None, requested: str | None) -> tuple[str, bool]:
    """Decide the language for a request, and whether to persist the choice.

    ``?lang=`` wins over the cookie and is the only thing that sets it, so a
    language is only ever remembered because somebody asked for it.

    **No ``Accept-Language``.** The header is a fingerprinting signal, and
    contract §2.3's position is that this system reads nothing about a
    visitor it was not given on purpose. Its absence here is a decision, not
    an omission - see the O-8 entry in docs/architecture.md §10.
    """
    if is_supported(requested):
        return requested, True  # type: ignore[return-value]
    if is_supported(cookie):
        return cookie, False  # type: ignore[return-value]
    return DEFAULT_LANGUAGE, False


class _TranslatedAttribute:
    """A class attribute that answers in the language of the current request.

    Installed by ``translate_view_names`` over a ModelView's ``name``,
    ``name_plural`` and ``category``.

    **Why a descriptor and not a catalogue entry.** sqladmin composes its own
    headings as ``_("New %(name)s", name=model_view.name)`` - it translates
    the sentence and interpolates the model's name into it *untranslated*.
    So a fully translated catalogue still renders "新建Constant" at the top
    of every create form, and there is no hook in between: the heading sits
    between two blocks in sqladmin's create.html rather than inside one, so
    it cannot be overridden, and the variable is resolved before our gettext
    ever sees it.

    Translating the attribute itself is what closes that, and it closes the
    same gap in five other places at once - the list page's own heading
    (``{{ model_view.name_plural }}``, which sqladmin does not wrap at all),
    the delete modal's ``data-name``, and the menu.

    Idempotent by construction: ``translate_view_names`` skips a class that
    already has one, so building two apps in one process (which the test
    suite does constantly) cannot wrap a wrapper and translate a translation.
    """

    __slots__ = ("source",)

    def __init__(self, source: str) -> None:
        self.source = source

    def __get__(self, instance, owner=None) -> str:
        return gettext(self.source)


def translate_view_names(views) -> None:
    """Make every registered view's name, plural and category translatable.

    Applied to the concrete classes rather than to a shared base because the
    subclasses assign ``name = "Constant"`` as plain class attributes, which
    shadow anything a base class defines.
    """
    for view in views:
        cls = type(view) if not isinstance(view, type) else view
        for attribute in ("name", "name_plural", "category"):
            current = vars(cls).get(attribute)
            if isinstance(current, str) and current:
                setattr(cls, attribute, _TranslatedAttribute(current))


def install(env) -> None:
    """Give a Jinja environment ``_()`` and the switcher's globals.

    Called four times, and it has to be: this panel renders from FOUR Jinja
    environments, not one. sqladmin builds its own per ``Admin`` instance,
    and ``admin/views.py``, ``admin/dryrun_views.py`` and
    ``admin/self_service_view.py`` each construct a module-level
    ``Jinja2Templates`` of their own (``admin/accounts_view.py`` and
    ``admin/blocklist_views.py`` borrow one of those).

    That matters more than it looks. ``brand/base.html`` is shared by the
    login gauntlet and the security screen, so a ``_()`` in it is rendered
    through several of these environments; an environment that never had the
    callables installed raises ``'_' is undefined`` on that page and nowhere
    else. Which is precisely the failure this project has already paid for
    twice - a template that works everywhere the tests look and not on the
    one screen they do not.

    ``jinja2.ext.i18n`` has to be added explicitly here. sqladmin adds it to
    its own environment; a bare ``Jinja2Templates`` has never heard of it,
    and ``install_gettext_callables`` is a method the extension installs.
    """
    env.add_extension("jinja2.ext.i18n")
    env.install_gettext_callables(gettext, ngettext, newstyle=True)
    env.globals["kaicalc_language"] = active_language
    env.globals["kaicalc_languages"] = languages
    env.globals["kaicalc_endonym"] = endonym


class LanguageMiddleware:
    """Resolves the active language, and persists an explicit choice.

    Pure ASGI rather than BaseHTTPMiddleware. The ContextVar has to be set in
    the same context the response is rendered in; a pure ASGI middleware
    awaits the application directly and spawns no task, so there is no
    question about which context the value lands in.

    Installed outermost (added last in ``create_app``), so that even a
    request ProtectionMiddleware refuses has a language resolved.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        from starlette.requests import HTTPConnection

        conn = HTTPConnection(scope)
        language, persist = resolve(
            conn.cookies.get(COOKIE_NAME), conn.query_params.get(QUERY_PARAM)
        )
        set_language(language)

        if not persist:
            await self.app(scope, receive, send)
            return

        async def send_with_cookie(message):
            if message["type"] == "http.response.start":
                from starlette.datastructures import MutableHeaders

                headers = MutableHeaders(scope=message)
                headers.append(
                    "set-cookie",
                    f"{COOKIE_NAME}={language}; Path=/; Max-Age={COOKIE_MAX_AGE}; "
                    "SameSite=Lax",
                )
            await send(message)

        await self.app(scope, receive, send_with_cookie)
