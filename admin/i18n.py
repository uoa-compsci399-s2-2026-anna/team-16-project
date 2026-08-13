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

#: The header the panel negotiates from, and the only thing it reads about a
#: visitor's language.
#:
#: **§2.3 forbids STORING an address, a user agent or a fingerprint. It does
#: not forbid reading a header, deciding what to render, and keeping
#: nothing** - its own wording is that "user agents, headers and paths are
#: read within a request and forgotten". This module used to decline the
#: header on the opposite reading, and shipped a ``?lang=`` switcher and a
#: ``kaicalc_lang`` cookie instead. Both are gone: the cookie was the only
#: thing here that outlived a request, and the switcher was a control the
#: repository owner asked not to have.
#:
#: What is read is discarded when the response is sent. Nothing derived from
#: it reaches ``submission``, ``audit_log`` or any log line.
ACCEPT_LANGUAGE_HEADER = "accept-language"

#: The response header that stops a shared cache serving one visitor's
#: Chinese page to the next visitor. **Mandatory on every response**, not
#: only on the ones that came out translated: a cache keys on what it was
#: told varies, and an English response served without it is the entry that
#: later gets returned to a Chinese-speaking browser.
VARY_HEADER = "vary"

#: ``?lang=zh`` on any URL forces a language for that one request. It is not
#: persisted anywhere and there is no control that emits it - it exists for
#: testing, screenshots and support, which is the whole of why an interface
#: with no picker still needs one.
#:
#: **An unrecognised value is ignored**, and the request then negotiates
#: exactly as if the parameter had been absent: ``Accept-Language`` first,
#: English last. Not an error, not a redirect, and not remembered.
QUERY_PARAM = "lang"

#: A single ``Accept-Language`` entry: a tag and an optional quality.
_ACCEPT_ENTRY = re.compile(r"^\s*([A-Za-z0-9*-]{1,35})\s*(?:;\s*q\s*=\s*([0-9.]{1,8})\s*)?$")

#: How much of the header is read at all. A browser sends a handful of tags;
#: anything longer is either a mistake or an attempt to make this loop
#: expensive, and the tail of a language list has never decided anything.
_MAX_ACCEPT_LENGTH = 512
_MAX_ACCEPT_ENTRIES = 24

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
    #: Every BCP-47 tag this catalogue claims, itself included.
    #:
    #: **This is what stops ``zh-TW`` reaching Simplified Chinese.** RFC 4647's
    #: lookup truncates a tag one subtag at a time, so ``zh-TW`` -> ``zh``
    #: is what a plain implementation does, and for Traditional Chinese that
    #: is not a graceful degradation - it is the wrong script. A catalogue
    #: therefore names the regions and scripts it speaks for, and an exact
    #: claim is matched before any truncation happens.
    #:
    #: In the catalogue file rather than in a table here, for the reason
    #: ``endonym`` and ``machine_translated`` are: adding a language is
    #: adding a file.
    tags: tuple[str, ...] = ()


def _load() -> dict[str, Catalogue]:
    """Read every catalogue once, at import.

    English is synthesised rather than read from a file: its strings are the
    keys, so a file would be a dictionary mapping every string to itself and
    a second place for the English wording to drift.
    """
    catalogues = {
        DEFAULT_LANGUAGE: Catalogue(DEFAULT_LANGUAGE, "English", False, {}, ("en",)),
    }
    if LOCALES_DIR.is_dir():
        for path in sorted(LOCALES_DIR.glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            catalogues[raw["language"]] = Catalogue(
                language=raw["language"],
                endonym=raw["endonym"],
                machine_translated=bool(raw["machine_translated"]),
                strings=raw["strings"],
                tags=tuple(raw.get("tags") or [raw["language"]]),
            )
    return catalogues


_CATALOGUES: dict[str, Catalogue] = _load()


def _tag_index(catalogues: Mapping[str, Catalogue]) -> dict[str, str]:
    """Every claimed tag, lower-cased, to the language that claims it.

    A tag claimed twice is a bug in the catalogue files rather than a
    precedence question, and ``test_i18n.py`` fails on it - two catalogues
    both answering to ``zh-HK`` would make the winner depend on filename
    order.
    """
    index: dict[str, str] = {}
    for catalogue_ in catalogues.values():
        for tag in (catalogue_.language, *catalogue_.tags):
            index.setdefault(tag.strip().lower(), catalogue_.language)
    return index


_TAG_INDEX: dict[str, str] = _tag_index(_CATALOGUES)

#: The active language for the request being served.
#:
#: A ContextVar and not a request attribute because Jinja's gettext callable
#: is handed the message and nothing else - there is no request to read. Set
#: by LanguageMiddleware before the request reaches anything that renders,
#: which is what makes it visible to the task Starlette's BaseHTTPMiddleware
#: spawns downstream (a child task copies the context at creation).
_active: ContextVar[str] = ContextVar("kaicalc_admin_language", default=DEFAULT_LANGUAGE)


def languages() -> list[Catalogue]:
    """Every language with a catalogue, English first.

    No screen renders this any more - there is no picker. It is what the
    test suite parametrises over, and what a future surface with a picker
    would read.
    """
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
    panel renders in English, which is the readable answer and is what
    ``negotiate`` would have produced anyway.
    """
    resolved = code if is_supported(code) else DEFAULT_LANGUAGE
    _active.set(resolved)
    return resolved


def active_language() -> str:
    return _active.get()


def catalogue(code: str | None = None) -> Catalogue:
    return _CATALOGUES.get(code or active_language(), _CATALOGUES[DEFAULT_LANGUAGE])


def endonym(code: str) -> str:
    """A language's own name for itself, for a notice written in it."""
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


def match(tag: str | None) -> str | None:
    """One BCP-47 tag to a language with a catalogue, or ``None``.

    Not equality. Three things have to hold and none of them follows from
    comparing strings:

    * ``en-NZ`` reaches English, and ``zh-CN`` and ``zh-Hans`` reach ``zh``.
      That is RFC 4647 lookup: try the whole tag, then drop the last subtag,
      and repeat.
    * ``zh-TW`` and ``zh-HK`` reach Traditional Chinese and **must not** fall
      through to Simplified. Truncation alone would send both to ``zh``, so
      the Traditional catalogue claims them by name (``Catalogue.tags``) and
      an exact claim is tried before any truncation.
    * A tag nobody claims returns ``None`` rather than a guess, so the caller
      can try the visitor's next-preferred language before giving up on
      English.

    A single-letter or grandfathered subtag is dropped by the same loop it
    would confuse: ``i-klingon`` truncates to ``i``, which nothing claims.
    """
    if not tag:
        return None
    normalised = tag.strip().lower().replace("_", "-")
    while normalised:
        language = _TAG_INDEX.get(normalised)
        if language is not None:
            return language
        head, _, _tail = normalised.rpartition("-")
        normalised = head
    return None


def parse_accept_language(header: str | None) -> list[str]:
    """The tags of an ``Accept-Language`` header, most wanted first.

    Quality values are honoured, which is the whole reason this is not a
    ``split(",")``: ``zh;q=0.8, en;q=0.9`` means English, and a parser that
    reads the header in written order gets that backwards. ``q=0`` means
    *not acceptable* and the tag is dropped rather than ranked last.

    ``*`` is dropped: it says "anything", which is what falling through to
    English already does, and ranking it would let a wildcard outrank a real
    preference further down the header.

    A malformed entry is dropped rather than defaulted. ``en;q=high`` is not
    a request for English at full quality - it is a header this code does not
    understand, and guessing at it is how a parser starts making decisions on
    input it cannot read.
    """
    if not header:
        return []
    ranked: list[tuple[float, int, str]] = []
    for position, raw in enumerate(header[:_MAX_ACCEPT_LENGTH].split(",")[:_MAX_ACCEPT_ENTRIES]):
        entry = _ACCEPT_ENTRY.match(raw)
        if entry is None:
            continue
        tag, quality = entry.group(1), entry.group(2)
        if tag == "*":
            continue
        if quality is None:
            weight = 1.0
        else:
            try:
                weight = float(quality)
            except ValueError:
                continue
            if not 0.0 <= weight <= 1.0 or weight == 0.0:
                continue
        # `position` keeps the header's own order for equal qualities, which
        # RFC 9110 leaves to the server and every browser expects.
        ranked.append((-weight, position, tag))
    return [tag for _weight, _position, tag in sorted(ranked)]


def negotiate(accept_language: str | None, requested: str | None = None) -> str:
    """The language to render this one request in. Nothing is persisted.

    ``?lang=`` first and only as an override - there is no control that emits
    it, and an unrecognised value is ignored, after which the request
    negotiates as though it had not been there at all.

    Then the browser's own ordered preference. Then English, which needs no
    catalogue because its strings are its keys.
    """
    forced = match(requested)
    if forced is not None:
        return forced
    for tag in parse_accept_language(accept_language):
        language = match(tag)
        if language is not None:
            return language
    return DEFAULT_LANGUAGE


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
    """Give a Jinja environment ``_()`` and the language globals.

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
    env.globals["kaicalc_machine_translated"] = lambda: is_machine_translated(
        active_language()
    )


class LanguageMiddleware:
    """Negotiates the language for one request, and says so in ``Vary``.

    Pure ASGI rather than BaseHTTPMiddleware. The ContextVar has to be set in
    the same context the response is rendered in; a pure ASGI middleware
    awaits the application directly and spawns no task, so there is no
    question about which context the value lands in.

    Installed outermost (added last in ``create_app``), so that even a
    request ProtectionMiddleware refuses has a language negotiated - and
    carries ``Vary``, which matters most on exactly those responses. A 403
    cached without it is served to everyone.

    **``Vary: Accept-Language`` is appended rather than assigned.** FastAPI's
    own machinery sets ``Vary: Cookie`` on session responses, and replacing
    the header would drop that and let a cache serve one staff member's
    session-dependent page to another. Appended to whatever is already there,
    and skipped when the value is already present so a re-entrant mount
    cannot produce ``Vary: Accept-Language, Accept-Language``.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        from starlette.requests import HTTPConnection

        conn = HTTPConnection(scope)
        set_language(
            negotiate(
                conn.headers.get(ACCEPT_LANGUAGE_HEADER),
                conn.query_params.get(QUERY_PARAM),
            )
        )

        async def send_with_vary(message):
            if message["type"] == "http.response.start":
                from starlette.datastructures import MutableHeaders

                headers = MutableHeaders(scope=message)
                existing = headers.get(VARY_HEADER, "")
                present = {part.strip().lower() for part in existing.split(",")}
                if "accept-language" not in present:
                    headers[VARY_HEADER] = (
                        f"{existing}, Accept-Language" if existing else "Accept-Language"
                    )
            await send(message)

        await self.app(scope, receive, send_with_vary)
