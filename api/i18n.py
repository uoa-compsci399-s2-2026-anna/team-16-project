"""The twenty languages the exported document can be written in.

**The English source string is the key**, exactly as in `web/js/i18n.js` and
`admin/i18n.py`. `translate("Total food waste", "ar")` looks up
`"Total food waste"` in `ar.json`. There is no key namespace and no second
vocabulary: the paper says what the screen says, in the same words, because it
reads the same file.

## Why there is a copy of `web/locales/` under `api/assets/`

The same reason `api/assets/fonts/` is a third copy of the brand faces, and it
is not laziness in either case. `api/` may not import `admin/`; setuptools'
package-data cannot reach outside its own package directory; and
`docker/api.Dockerfile` copies only `admin/ api/ db/ engine/` into the build
context, so a wheel or an image built from this tree has no `web/` in it at
all. A renderer that read `../web/locales/` would work on a checkout and raise
`FileNotFoundError` in production - the worst possible place for the difference
to show up. `tests/api/test_pdf_render.py` hash-compares the two directories,
so this copy cannot drift from the one nginx serves.

## The one rule that matters: a missing key is an error

`web/js/i18n.js` renders a missing key as its own English source, and that is
right for a page - a visitor who meets one untranslated label still has a
working calculator in front of them. **It is wrong for this document.** A PDF
is read later, elsewhere, by someone who cannot ask; a Tamil report with an
English heading in the middle of it looks like a corrupted file, and worse, a
document that silently rendered *entirely* in English would be indistinguishable
from one that had been asked for in English. Task 4's brief puts it plainly: a
locale whose catalogue is missing a key should be caught, not quietly rendered
in English.

So `Catalogue.gettext` raises `MissingTranslationError`. It cannot fire today -
every one of the twenty catalogues carries all 340 keys, and
`test_every_document_string_is_in_every_catalogue` asserts that for the ~25
keys this document uses, per locale, without rendering anything - but it is the
difference between a defect that fails a test and one that ships.

**This is also why the document's own copy is assembled out of strings the
catalogues already have** rather than written fresh for the export.
`tests/web/test_i18n_web.py::test_no_catalogue_carries_a_key_the_front_end_never
_asks_for` fails on a key the front end does not use, so inventing a heading
here would mean authoring twenty unreviewed machine translations *and* breaking
another stream's test. Nineteen of the twenty catalogues are machine-translated
already; adding to them is a translation-workflow decision, not a renderer's.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

#: Where the catalogues live inside this package. See the module docstring for
#: why they are not read out of `web/`.
LOCALES_DIR = Path(__file__).resolve().parent / "assets" / "locales"

#: The language the source strings are written in. It has no catalogue: for
#: English the key *is* the answer.
DEFAULT_LANGUAGE = "en"

#: `index.json` is the manifest the front end reads to build its chooser, not a
#: catalogue - it has no `language` key and loading it as one would raise at
#: import and take the whole API down. Skipped by name, and `_`-prefixed files
#: are skipped for the same reason `admin/i18n.py` skips them.
_NOT_A_CATALOGUE = ("index.json",)


class MissingTranslationError(LookupError):
    """A catalogue does not carry a string the document needs.

    Deliberately loud, and deliberately not a fallback to English. See the
    module docstring: a document that quietly reverted to English would be
    indistinguishable from one that had been asked for in English, and there
    would be nothing in the file to say which had happened.
    """


@dataclass(frozen=True)
class Catalogue:
    """One language: the strings, the direction, and the tags it answers to."""

    language: str
    strings: Mapping[str, str]
    #: `"ltr"` or `"rtl"`. **Read from the catalogue file, not guessed from
    #: the tag.** `api/pdf_render.text_direction` still exists and still
    #: derives a direction from a bare BCP-47 tag - it has to, for a locale
    #: with no catalogue at all - but where a catalogue exists its own
    #: declaration wins, because the people who wrote the translation are a
    #: better authority on which way it runs than a table of language subtags.
    direction: str = "ltr"
    #: Every BCP-47 tag this catalogue claims, itself included. This is what
    #: stops `zh-TW` reaching Simplified Chinese: RFC 4647 lookup truncates
    #: `zh-TW` to `zh`, which for Traditional Chinese is not a graceful
    #: degradation but the wrong script, so the Traditional catalogue claims
    #: those regions by name and an exact claim is matched before truncation.
    tags: tuple[str, ...] = ()

    def gettext(self, message: str) -> str:
        """The translation, or `MissingTranslationError`.

        English is the identity function - its strings are the keys - and every
        other language must actually have the key. There is no third outcome
        and in particular there is no silent English.
        """
        if self.language == DEFAULT_LANGUAGE:
            return message
        try:
            return self.strings[message]
        except KeyError:
            raise MissingTranslationError(
                f"{self.language} has no translation for {message!r}. The "
                "exported document is assembled out of strings the calculator's "
                "catalogues already carry (web/locales/*.json, copied to "
                f"{LOCALES_DIR}); a key this document uses and a catalogue does "
                "not have is a mismatch between the two, not a reason to print "
                "English in the middle of a translated report."
            ) from None


def _load() -> dict[str, Catalogue]:
    """Every catalogue, read once at import.

    English is synthesised rather than read from a file, the same way
    `admin/i18n.py` does it: its strings are the keys, so a file would map
    every string to itself and be a second place for the English wording to
    drift.
    """
    catalogues = {
        DEFAULT_LANGUAGE: Catalogue(DEFAULT_LANGUAGE, {}, "ltr", ("en", "en-NZ")),
    }
    if LOCALES_DIR.is_dir():
        for path in sorted(LOCALES_DIR.glob("*.json")):
            if path.name in _NOT_A_CATALOGUE or path.name.startswith("_"):
                continue
            raw = json.loads(path.read_text(encoding="utf-8"))
            catalogues[raw["language"]] = Catalogue(
                language=raw["language"],
                strings=raw["strings"],
                # Anything that is not exactly "rtl" is left-to-right: a typo
                # must not produce a third writing direction that no renderer
                # understands and that `dir` would emit verbatim.
                direction="rtl" if raw.get("dir") == "rtl" else "ltr",
                tags=tuple(raw.get("tags") or [raw["language"]]),
            )
    return catalogues


_CATALOGUES: dict[str, Catalogue] = _load()

_TAG_INDEX: dict[str, str] = {}
for _catalogue in _CATALOGUES.values():
    for _tag in (_catalogue.language, *_catalogue.tags):
        _TAG_INDEX.setdefault(_tag.strip().lower(), _catalogue.language)


def languages() -> tuple[str, ...]:
    """Every language this document can be written in, English included."""
    return tuple(sorted(_CATALOGUES))


def match(tag: str | None) -> str | None:
    """One BCP-47 tag to a language with a catalogue, or `None`.

    The same three rules `admin/i18n.py::match` implements, and they have to be
    the same: a visitor who is served the calculator in Traditional Chinese and
    then downloads a Simplified PDF has met one system behaving as two.

    * RFC 4647 lookup - try the whole tag, then drop the last subtag - so
      `en-NZ` reaches English and `zh-CN` reaches `zh`.
    * An exact claim first, so `zh-TW` reaches Traditional Chinese instead of
      truncating into Simplified.
    * A tag nobody claims returns `None` rather than a guess.
    """
    if not tag:
        return None
    normalised = str(tag).strip().lower().replace("_", "-")
    while normalised:
        language = _TAG_INDEX.get(normalised)
        if language is not None:
            return language
        normalised, _, _tail = normalised.rpartition("-")
    return None


def resolve(locale: str | None) -> str:
    """The language the document will actually be written in.

    A tag with no catalogue resolves to English, which is the same rule the
    calculator page follows (`web/js/i18n.js`: "if it has no catalogue the page
    renders in English"). **The document then declares `lang="en"`, not the tag
    that was asked for** - see `api/pdf_render.build_context`. Labelling an
    English document `lang="he"` would tell a screen reader to pronounce
    English as Hebrew, and would set the page right-to-left around text that
    runs the other way. The honest answer is the language on the page.
    """
    return match(locale) or DEFAULT_LANGUAGE


def catalogue(locale: str | None) -> Catalogue:
    """The `Catalogue` for a requested tag, resolved by `resolve`."""
    return _CATALOGUES[resolve(locale)]
