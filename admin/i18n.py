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

from jinja2.ext import Extension

#: The language every catalogue falls back to, and the language the source
#: strings are written in. It has no catalogue file: for English the key *is*
#: the answer.
DEFAULT_LANGUAGE = "en"

#: The header the panel negotiates from when there is no stored choice to
#: honour, and the only thing it *infers* about a visitor's language.
#:
#: **§2.3 forbids STORING an address, a user agent or a fingerprint. It does
#: not forbid reading a header, deciding what to render, and keeping
#: nothing** - its own wording is that "user agents, headers and paths are
#: read within a request and forgotten". This module used to decline the
#: header on the opposite reading.
#:
#: What is read here is discarded when the response is sent. Nothing derived
#: from it reaches ``submission``, ``audit_log`` or any log line. The one thing
#: that does outlive a request is the cookie below, which is not this - see
#: ``COOKIE_NAME`` for why the two are different acts.
ACCEPT_LANGUAGE_HEADER = "accept-language"

#: The one thing this system stores about a visitor's language, shared with the
#: calculator (``web/js/i18n.js``) because both surfaces are the same origin -
#: nginx serves ``/`` and ``/admin`` on one port - and a cookie at path ``/`` is
#: the only mechanism a Python process and a browser can both read.
#:
#: **A cookie rather than ``localStorage``, and the usual reason is wrong.**
#: ``localStorage`` is same-origin too and would in fact be shared between the
#: two surfaces. The decisive reason is *this* half: the panel renders
#: server-side and has to know the language before it emits any HTML, and
#: ``localStorage`` is unreadable at that moment.
#:
#: **WHY THIS IS NOT THE FINGERPRINT §2.3 FORBIDS.** Two independent properties
#: keep it outside that, and **both are needed**. This is the reason, not a
#: reassurance:
#:
#: 1. **It records something the visitor deliberately declared**, not something
#:    inferred from their browser. Reading ``Accept-Language`` and forgetting
#:    it, and storing "this visitor chose English", are different acts with
#:    different justifications. The first observes; the second obeys.
#: 2. **Its value space is closed, tiny and free of entropy** - twenty-one
#:    possible values, shared identically by everyone who picks the same
#:    language. A field that cannot distinguish two visitors cannot correlate
#:    them, whatever else it records.
#:
#: The second is the load-bearing half, and it is why the first is not the
#: argument on its own: "the person declared it" would equally justify storing a
#: name somebody typed into a form, which would be a fingerprint by any measure.
#: **It is the absence of entropy, not the presence of consent, that makes this
#: incapable of identifying anyone.**
#:
#: **Not entangled with the de-duplication token.** ``submission.token`` is a
#: different name, a different lifetime and a different purpose. This cookie
#: never reaches ``submission``, ``audit_log`` or the access log, and it neither
#: extends nor refreshes that token. Because it sits at path ``/`` - which
#: cannot be scoped away when both surfaces need it - the browser also sends it
#: to ``/api/v1/calculate``; the API receives it and ignores it, and a test says
#: so rather than leaving it to be obvious.
COOKIE_NAME = "kaicalc_lang"

#: **"Follow the system" is a stored value, not the absence of one.**
#:
#: The obvious reason is that "chose to follow" and "never chose" would
#: otherwise be indistinguishable and the chooser could not show what is in
#: effect. The stronger reason is mechanical: reverting to follow-the-system
#: becomes an ordinary write rather than a cookie deletion, and deleting a
#: cookie reliably means re-sending it with ``Max-Age=0`` and an exactly
#: matching path and domain. Get that wrong and the old value stays - the
#: chooser appears to revert and snaps back on the next page. A write has no
#: such failure mode.
FOLLOW_SYSTEM = "auto"

#: A year. Long enough that a returning staff member keeps their choice; finite
#: so an abandoned browser does not carry it forever.
COOKIE_MAX_AGE = 31536000

#: The response header that stops a shared cache serving one visitor's
#: Chinese page to the next visitor. **Mandatory on every response**, not
#: only on the ones that came out translated: a cache keys on what it was
#: told varies, and an English response served without it is the entry that
#: later gets returned to a Chinese-speaking browser.
VARY_HEADER = "vary"

#: ``?lang=zh`` on any URL forces a language for that one request. **It is not
#: persisted and the chooser does not emit it** - it exists for testing,
#: screenshots and support, and a link somebody pastes into a support thread
#: must not silently re-language the recipient's browser for good.
#:
#: The parameter and the chooser cannot be mistaken for one another because they
#: share no mechanism: the chooser is a ``<form method="post">`` that writes the
#: cookie and redirects, and it never puts anything in a URL.
#:
#: **An unrecognised value is ignored**, and the request then negotiates
#: exactly as if the parameter had been absent: the browser's highest-priority
#: tag, then English. Not an error, not a redirect, and not remembered.
#:
#: It is deliberately *not* folded into the single-tag rule below. ``?lang=``
#: is somebody typing a language on purpose, which is the one signal here that
#: is not a browser setting - so a typo in it falls back to the negotiation
#: rather than consuming it. ``?lang=qq`` on a Chinese browser is still
#: Chinese.
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

    #: ``"ltr"`` or ``"rtl"``: which way this language's lines run.
    #:
    #: The key in the file is ``dir``, spelled the same as the calculator's
    #: catalogues spell it (``web/locales/*.json``), because the two sets are
    #: meant to be the same shape - the panel already vendors the
    #: calculator's list as ``_calculator_languages.json`` to name a language
    #: it does not itself have, and a second spelling would make that copy
    #: lossy.
    #:
    #: **Defaulted rather than required.** Every catalogue this panel ships
    #: is left-to-right, so requiring the key would be a migration for no
    #: reader's benefit; a catalogue that omits it is left-to-right, which is
    #: true of every language nobody bothered to mark.
    #:
    #: This is honestly untested against a real right-to-left panel: there is
    #: no RTL catalogue in ``admin/locales/`` and so no screen on which
    #: ``dir="rtl"`` can be looked at. What is tested is that the attribute
    #: follows the catalogue rather than a constant - see
    #: ``tests/admin/test_i18n_pages.py``. The panel's *layout* under RTL is
    #: not claimed to work, and the first RTL catalogue dropped into
    #: ``admin/locales/`` should expect to find layout work waiting.
    direction: str = "ltr"


def _load() -> dict[str, Catalogue]:
    """Read every catalogue once, at import.

    English is synthesised rather than read from a file: its strings are the
    keys, so a file would be a dictionary mapping every string to itself and
    a second place for the English wording to drift.
    """
    catalogues = {
        DEFAULT_LANGUAGE: Catalogue(
            DEFAULT_LANGUAGE, "English", False, {}, ("en",), "ltr"
        ),
    }
    if LOCALES_DIR.is_dir():
        for path in sorted(LOCALES_DIR.glob("*.json")):
            # A leading underscore means "data this module reads, not a
            # catalogue" - today just the vendored calculator language list.
            # Without this, `_calculator_languages.json` would be loaded as a
            # language and raise KeyError on `raw["language"]` at import,
            # taking the whole panel down at start-up.
            if path.name.startswith("_"):
                continue
            raw = json.loads(path.read_text(encoding="utf-8"))
            catalogues[raw["language"]] = Catalogue(
                language=raw["language"],
                endonym=raw["endonym"],
                machine_translated=bool(raw["machine_translated"]),
                strings=raw["strings"],
                tags=tuple(raw.get("tags") or [raw["language"]]),
                # Anything that is not exactly "rtl" is left-to-right. A
                # typo must not produce a third writing direction that no
                # renderer understands and that `dir` would emit verbatim.
                direction="rtl" if raw.get("dir") == "rtl" else "ltr",
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

    **This is what the chooser lists, and it lists only what the panel has.**
    The panel ships English and Chinese; the calculator ships twenty-one. A
    visitor who chose Tamil on the calculator does not get a dead Tamil entry
    here - they get the message ``unavailable_choice`` produces, beside a list
    of the two languages that actually exist.
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


#: What the visitor *chose*, which is not what is rendered. With ``auto`` stored
#: and a Chinese browser the panel is Chinese and the chooser still reads
#: "Follow the system" - the chooser has to show the choice, not its
#: consequence, or picking the language it already displays would be a no-op
#: that looks like a bug.
_choice: ContextVar[str] = ContextVar("kaicalc_admin_choice", default=FOLLOW_SYSTEM)

#: The endonym of a chosen language this panel has no catalogue for, or ``None``.
_unavailable: ContextVar[str | None] = ContextVar(
    "kaicalc_admin_unavailable", default=None
)


def active_choice() -> str:
    return _choice.get()


def active_unavailable_choice() -> str | None:
    return _unavailable.get()


def catalogue(code: str | None = None) -> Catalogue:
    return _CATALOGUES.get(code or active_language(), _CATALOGUES[DEFAULT_LANGUAGE])


def endonym(code: str) -> str:
    """A language's own name for itself, for a notice written in it."""
    return catalogue(code).endonym if is_supported(code) else code


def html_lang() -> str:
    """The value for ``<html lang>``: the language actually being rendered.

    **The rendered language, never the stored choice.** When somebody has
    chosen a language this panel has no catalogue for - Arabic, say, chosen
    on the calculator, which has twenty-one to the panel's two - the panel
    renders English, and this returns English. Announcing the choice instead
    would tell a screen reader to read English words with Arabic phonetics,
    which is the same class of defect as announcing Chinese as English and no
    better for having been well meant. ``active_language()`` is already the
    resolved language for exactly this reason; nothing here re-derives it.

    ``en-NZ`` for English specifically: the copy is New Zealand English and
    the region is part of it. Every other language is its bare code, because
    no other catalogue here is regional.

    One function so that the panel proper and the five gate pages cannot
    disagree. ``brand/base.html`` used to spell this expression out in Jinja
    and was the only place it existed; the panel proper now needs the same
    answer (see ``_HtmlElement`` below), and two copies of a conditional is
    how the two surfaces would come to differ.
    """
    active = active_language()
    return "en-NZ" if active == DEFAULT_LANGUAGE else active


def html_dir() -> str:
    """The value for ``<html dir>``: ``"ltr"`` or ``"rtl"``.

    Read off the catalogue of the language being rendered, by the same rule
    and for the same reason as ``html_lang()`` - the direction has to be the
    direction of the words on the screen, so a choice the panel cannot honour
    gets English's direction and not its own.

    **Emitted even though no catalogue here is right-to-left**, and that is a
    deliberate call rather than an oversight. It costs one attribute; it means
    the first RTL catalogue added to ``admin/locales/`` announces itself
    instead of rendering Arabic letters in a document declared left-to-right;
    and an absent ``dir`` is not neutral - it inherits, and the panel would
    keep asserting left-to-right by omission. What it does **not** do is make
    the panel's layout mirror. See ``Catalogue.direction``.
    """
    return catalogue().direction


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
    * A tag nobody claims returns ``None`` rather than a guess. ``negotiate``
      turns that into English; it does **not** try the visitor's next
      preference, and the reason is written out there.

    ``*`` reaches nothing by either route: no catalogue claims it and it has no
    subtag to drop. That is what makes ``Accept-Language: *`` mean English.

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

    **Ordering happens here, and the single-tag rule in ``negotiate`` is
    applied to the result.** That sequence is the whole content of
    ``zh;q=0.8, en;q=0.9`` reaching English: rank first, then take the head.
    Taking the head of the written header instead would answer Chinese.

    ``*`` is **kept, and ranked like any other tag.** It used to be dropped on
    the reading that "anything" is what falling through to English already
    does - which was true while the negotiation walked the list, and stopped
    being true when it stopped walking. Dropped, ``*, zh`` would promote
    ``zh`` into the one slot that decides, and a header whose first statement
    is "no preference" would answer Chinese. Kept, it holds its own rank, no
    catalogue claims it, and ``*`` at the head means English - while
    ``zh, *;q=0.5`` still means Chinese, because ``*`` is not at the head.

    A malformed entry is dropped rather than defaulted. ``en;q=high`` is not
    a request for English at full quality - it is a header this code does not
    understand, and guessing at it is how a parser starts making decisions on
    input it cannot read. A dropped entry does not hold a rank, so
    ``en;q=high, zh`` is a header whose highest-priority *readable* tag is
    ``zh``; a header with no readable tag at all is an empty list, which
    ``negotiate`` answers with English.
    """
    if not header:
        return []
    ranked: list[tuple[float, int, str]] = []
    for position, raw in enumerate(header[:_MAX_ACCEPT_LENGTH].split(",")[:_MAX_ACCEPT_ENTRIES]):
        entry = _ACCEPT_ENTRY.match(raw)
        if entry is None:
            continue
        tag, quality = entry.group(1), entry.group(2)
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

    **Then the highest-priority tag the browser sent, and only that one. If it
    has no catalogue the answer is English; the rest of the list is not
    consulted.**

    This is the rule most likely to be "fixed" back, because walking the list
    is what RFC 4647 lookup does with a list and is the more obvious reading of
    a header that offers several languages. It was the behaviour here until
    2026-08-13, and the repository owner ruled against it. The reasoning:

    * **A browser's language list does not reliably describe what a person can
      read.** The first entry is usually deliberate. The second and third are
      frequently residue - a preinstalled system locale, an input method added
      once, a setting changed years ago and forgotten. Honouring them as a
      genuine second language means letting an unreliable signal override a
      reliable fallback.
    * **English is a safe floor for this audience and an unfamiliar language is
      not.** Everyone who reaches this calculator or this panel reads English;
      that is the assumption the ruling makes explicit. So the worst outcome
      under this rule is a page in English, and the worst outcome under the
      walk is a page in a language the reader does not have - which the reader
      cannot even navigate out of, because there is no picker.

    What the rule does **not** change is the lookup *within* that one tag.
    ``en-NZ`` still reaches English by truncation, ``zh-CN`` still reaches
    ``zh``, and a catalogue's own claims still beat truncation - the panel has
    no Traditional catalogue, but on the calculator ``zh-TW`` still reaches
    Traditional Chinese. Removing the walk down the list is not removing the
    match.

    Ordering is still by quality and happens first (``parse_accept_language``),
    so ``zh;q=0.8, en;q=0.9`` has ``en`` at its head and answers English. The
    single tag consulted is the highest-*priority* one, not the first one
    written.
    """
    forced = match(requested)
    if forced is not None:
        return forced
    preferred = parse_accept_language(accept_language)
    if not preferred:
        return DEFAULT_LANGUAGE
    return match(preferred[0]) or DEFAULT_LANGUAGE


def stored_choice(cookie: str | None) -> str | None:
    """What the visitor chose, or ``None`` when there is nothing to honour.

    ``auto`` and an unrecognised value both answer ``None``, which is what makes
    a stale cookie harmless: one naming a language that has since been removed
    negotiates from the browser instead of rendering a blank panel.

    **A tag, not a literal.** The value is matched through the same lookup as
    everything else, so a cookie written by the calculator saying ``zh-Hant``
    resolves here rather than being compared as a string - the two surfaces
    write one cookie and do not share a catalogue set.
    """
    if not cookie or cookie == FOLLOW_SYSTEM:
        return None
    return match(cookie)


def resolve(
    cookie: str | None,
    accept_language: str | None,
    requested: str | None = None,
) -> str:
    """The language to render this one request in. **The order is the contract.**

    ``?lang=`` first, as a one-request override that writes nothing; then the
    stored choice; then the browser's highest-priority tag; then English. The
    same order ``web/js/i18n.js::resolve`` implements, because two surfaces that
    answered one visitor differently would be the exact defect a shared rule
    exists to prevent.

    ``?lang=`` is matched separately rather than treated as a stored choice, so
    an unrecognised value falls through to the cookie instead of consuming it.
    """
    forced = match(requested)
    if forced is not None:
        return forced
    chosen = stored_choice(cookie)
    if chosen is not None:
        return chosen
    # **A choice this panel cannot honour lands on English, not on the browser.**
    # The visitor deliberately overrode their browser's setting; falling back to
    # the very setting they replaced would be honouring a preference they had
    # already rejected. English is the stated safe floor, and it is what the
    # message beside the chooser promises - a message that said "shown in
    # English" beside a Chinese page would be worse than no message.
    #
    # This branch is narrower than it looks. It needs a stored language that no
    # panel catalogue claims *even after truncation*, so `zh-Hant` does not
    # reach it - `match` truncates it to `zh` and the panel renders Chinese.
    # Tamil, Thai and Arabic reach it; a Chinese variant never does.
    if unavailable_choice(cookie) is not None:
        return DEFAULT_LANGUAGE
    return negotiate(accept_language)


def unavailable_choice(cookie: str | None) -> str | None:
    """The endonym of a chosen language the **calculator** has but the panel does not.

    ``None`` when there is nothing to say: no choice, "follow the system", a
    choice the panel can honour, or a value nobody claims on either surface.

    This exists because the two surfaces do not ship the same language set, and
    the three obvious ways to handle that are all worse. Silently rendering
    English pretends no choice was made. Showing "Follow the system" selected is
    a lie about what is stored. A dead entry in a two-item list is noise. So the
    panel renders English, lists what it has, and **says which language it could
    not give them, in that language's own name.**

    **The cookie is not touched.** The tempting implementation quietly rewrites
    it to ``auto`` on the way past, which would destroy the calculator's
    language from an unrelated screen - a staff member who set the calculator to
    Tamil and then opened the panel would find the calculator in English
    afterwards, with nothing on either screen explaining why.
    """
    if not cookie or cookie == FOLLOW_SYSTEM:
        return None
    if match(cookie) is not None:
        return None
    return _CALCULATOR_ENDONYMS.get(cookie.strip().lower())


#: A **vendored copy** of ``web/locales/index.json``, and it has to be a copy.
#:
#: ``docker/admin.Dockerfile`` copies ``admin/ api/ db/ engine/`` and no
#: ``web/``, and ``[tool.setuptools.package-data]`` cannot reach outside its own
#: package - the same pair of constraints that put the catalogues inside
#: ``admin/`` rather than in a top-level ``i18n/``. Reading the calculator's own
#: manifest works in a checkout and returns nothing in the built image, which
#: would make the "not available in the panel" message a feature that passes
#: every test and renders on no deployed screen.
#:
#: A copy can drift, so ``tests/admin/test_i18n.py`` compares it byte-for-byte
#: against ``web/locales/index.json`` and fails when they diverge. That is the
#: same arrangement this repository already uses for the vendored sqladmin
#: macros: vendor, then anchor the copy with a test that cannot pass on a no-op.
CALCULATOR_LANGUAGES_FILE = "_calculator_languages.json"


def _calculator_endonyms() -> dict[str, str]:
    """Every language the calculator offers, by tag, to its own name.

    Used only to name a language the panel cannot render. A missing or
    unreadable file leaves the map empty, which makes ``unavailable_choice``
    return ``None`` and renders the panel in English - the same outcome as
    before this existed, never an error page.
    """
    manifest = LOCALES_DIR / CALCULATOR_LANGUAGES_FILE
    try:
        raw = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    index: dict[str, str] = {}
    for entry in raw.get("catalogues", []):
        endonym_ = entry.get("endonym")
        if not endonym_:
            continue
        for tag in (entry.get("language"), *(entry.get("tags") or [])):
            if tag:
                index.setdefault(str(tag).strip().lower(), endonym_)
    return index


_CALCULATOR_ENDONYMS: dict[str, str] = _calculator_endonyms()


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


#: The template whose ``<html>`` element the panel proper renders from, and
#: the exact opening tag sqladmin writes into it.
#:
#: Both are matched literally. A tolerant regular expression here would keep
#: "working" across an upstream edit by matching something that is no longer
#: the same element, which is the failure mode this whole arrangement exists
#: to avoid - see ``_HtmlElement``.
SQLADMIN_BASE_TEMPLATE = "sqladmin/base.html"
SQLADMIN_HTML_ELEMENT = '<html lang="en">'

#: What the panel emits in its place. ``lang`` and ``dir`` are function calls
#: rather than values because this substitution happens **once**, when Jinja
#: compiles the template; the calls are what run per request.
_KAICALC_HTML_ELEMENT = '<html lang="{{ kaicalc_html_lang() }}" dir="{{ kaicalc_html_dir() }}">'

_HTML_ELEMENT_SKEW = (
    "admin/i18n.py cannot find {expected!r} in sqladmin's own {template!r}, so "
    "the panel proper would go on announcing every page as English whatever "
    "language it is in.\n\n"
    "THE LIKELY CAUSE IS A VERSION SKEW, NOT A BUG IN THIS REPOSITORY. This "
    "rewrite is written against the sqladmin pinned in docker/constraints.txt, "
    "and it is that pin - not whatever is installed on this machine - that the "
    "images build and CI installs. Check the installed version against the pin "
    "first: tests/admin/test_i18n.py::"
    "test_the_installed_sqladmin_is_the_version_pinned_for_the_images asserts "
    "exactly that, and a failure there means this message is a symptom and the "
    "pin is the thing to fix.\n\n"
    "If the versions DO match, sqladmin has changed its base template and the "
    "literal above needs to be updated by hand to whatever it now writes."
)


class _HtmlElement(Extension):
    """Puts the rendered language on the panel proper's ``<html>`` element.

    **The problem this solves, and why the obvious two answers are both
    wrong.** sqladmin's ``templates/sqladmin/base.html`` opens with a
    hard-coded ``<html lang="en">``, and that element sits OUTSIDE every
    ``{% block %}`` the file defines. So the seam that works for ``topbar``
    (see ``templates/sqladmin/layout.html``) does not reach it: a child
    template can replace any block in that file and still cannot touch its
    first line. Nor can a shadowing template that only ``{% extends %}`` the
    original, for the same reason.

    **Why this is not a second vendored file.** The panel already copies one
    sqladmin template (``templates/sqladmin/_macros.html``) and that copy has
    already cost this project once: on the 0.30.0 -> 0.31.0 move it went on
    serving 0.30.0's markup, the menu stayed pinned open, and 0.31.0's new
    menu-persistence JavaScript matched no element and did nothing, silently.
    Copying ``base.html`` would take on the same debt for a worse ratio - the
    file is forty-odd lines of stylesheet links, script tags and a ``<title>``,
    every one of which would then be frozen at today's sqladmin, to change one
    attribute on one line.

    **What this does instead.** It rewrites that one line in sqladmin's own
    source as Jinja compiles it. Nothing is copied, so nothing can go stale:
    an upgrade that adds a stylesheet or a meta tag to ``base.html`` is picked
    up in full, and only the ``<html>`` element is ours.

    Compilation happens once per environment, so the cost is one string
    replacement at start-up, not per request. The two attributes are Jinja
    calls, so it is the *call* that is baked in and the *language* that is
    negotiated per request.

    **The drift guard, which this needs as much as the vendored macro does.**
    A rewrite that silently matches nothing is worse than no rewrite, because
    it looks installed. So a source this cannot find its literal in raises
    here rather than rendering, and ``_HTML_ELEMENT_SKEW`` names version skew
    as the first thing to check - the lesson from the macro copy, whose
    failure was reported as "the copy has drifted" and sent the reader to edit
    the wrong file. ``tests/admin/test_i18n.py`` asserts the literal is
    present in the installed package, so an upgrade fails the suite before it
    can ever raise in front of a staff member.
    """

    def preprocess(self, source, name, filename=None):
        # Only sqladmin's own base template. Ours (`brand/base.html`) writes
        # the same two attributes in Jinja directly and must not be touched
        # twice, and no other template in this panel has an `<html>` element.
        if name != SQLADMIN_BASE_TEMPLATE:
            return source
        if SQLADMIN_HTML_ELEMENT not in source:
            raise RuntimeError(
                _HTML_ELEMENT_SKEW.format(
                    expected=SQLADMIN_HTML_ELEMENT, template=SQLADMIN_BASE_TEMPLATE
                )
            )
        return source.replace(SQLADMIN_HTML_ELEMENT, _KAICALC_HTML_ELEMENT, 1)


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
    # Added to all four environments, though only sqladmin's ever loads the
    # template it acts on: the extension keys off the template NAME, so the
    # three that render `brand/` templates never trigger it, and installing it
    # in one place is what keeps "which environment got it" from becoming a
    # question again. It must go in before anything is rendered - Jinja
    # preprocesses at compile time and caches the result - and `install()` is
    # called at application build time, before any request.
    env.add_extension(_HtmlElement)
    env.install_gettext_callables(gettext, ngettext, newstyle=True)
    env.globals["kaicalc_language"] = active_language
    env.globals["kaicalc_html_lang"] = html_lang
    env.globals["kaicalc_html_dir"] = html_dir
    env.globals["kaicalc_machine_translated"] = lambda: is_machine_translated(
        active_language()
    )
    env.globals["kaicalc_languages"] = languages
    env.globals["kaicalc_choice"] = active_choice
    env.globals["kaicalc_unavailable_choice"] = active_unavailable_choice
    env.globals["kaicalc_follow_system"] = FOLLOW_SYSTEM


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
        cookie = conn.cookies.get(COOKIE_NAME)
        set_language(
            resolve(
                cookie,
                conn.headers.get(ACCEPT_LANGUAGE_HEADER),
                conn.query_params.get(QUERY_PARAM),
            )
        )
        # The **resolved** code, not the raw cookie. A calculator-written
        # `zh-CN` has to come back as `zh` or it would match no option in the
        # chooser and the browser would silently select the first one - the
        # panel would render Chinese while its own control claimed otherwise.
        _choice.set(stored_choice(cookie) or FOLLOW_SYSTEM)
        _unavailable.set(unavailable_choice(cookie))

        async def send_with_vary(message):
            if message["type"] == "http.response.start":
                from starlette.datastructures import MutableHeaders

                headers = MutableHeaders(scope=message)
                existing = headers.get(VARY_HEADER, "")
                present = {part.strip().lower() for part in existing.split(",")}
                # **``Cookie`` as well as ``Accept-Language``, now that a stored
                # choice can decide the response.** Declaring only the header
                # would let a shared cache serve one staff member's chosen
                # Chinese page to the next visitor whose browser asked for
                # English - the precise failure `Vary` exists to prevent, and
                # the more dangerous half because the cookie *overrides* the
                # header. The cost is nil: the panel is authenticated, already
                # carries `Vary: Cookie` on session responses, and no shared
                # cache stores it.
                #
                # The static origin is deliberately untouched. The calculator's
                # chooser is read by JavaScript after the response arrives, so
                # every visitor is still served a byte-identical index.html and
                # `Vary` there would mean a cached copy of the HTML, the
                # stylesheet and both font faces per visitor.
                missing = [
                    token
                    for token in ("Accept-Language", "Cookie")
                    if token.lower() not in present
                ]
                if missing:
                    headers[VARY_HEADER] = ", ".join(
                        ([existing] if existing else []) + missing
                    )
            await send(message)

        await self.app(scope, receive, send_with_vary)
