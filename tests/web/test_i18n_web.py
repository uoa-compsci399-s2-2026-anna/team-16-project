"""The calculator's catalogues: complete, well-formed, and not quietly English.

**This file's job is to fail when a string loses its translation**, the same
job `tests/admin/test_i18n.py` does for the panel. Source-text keys make
rewording the English silently orphan every translation of it - the page falls
back to English and nothing raises - so the gap is caught here, on the commit
that reworded it.

What it deliberately does NOT prove: that anybody can read the page. A
catalogue can be complete and the browser can still render English, because
`t()` has to be called at the site that renders the string and a template
literal that was missed looks exactly like one that was not. That claim is
`test_i18n_browser.py`'s, against the running stack.
"""

from __future__ import annotations

import json
import re

import pytest

from tests.web import i18n_keys

#: `%(name)s`. The same syntax `admin/i18n.py` uses, so one convention covers
#: both surfaces and a translator meets it once.
PLACEHOLDER = re.compile(r"%\((\w+)\)s")

#: Strings that must survive translation untouched, wherever they appear.
#: `Kai Commitment` is the client's name; the rest are units and notation.
NEVER_TRANSLATED = ("Kai Commitment", "CO2e", "NZD")

#: Entries whose correct translation is **character-identical** to the English.
#:
#: `test_no_entry_is_blank_or_still_english` exists for a real failure: a key
#: carrying its own English value looks translated to every other test in this
#: file. But `Code` is the French word, `Name` is the German one, `Sector` is
#: the Dutch one and `No` is the Spanish one - all four arrived on the v1.30
#: table headers - and reaching for a synonym to satisfy a test would make the
#: interface worse to read in exchange for a greener suite.
#:
#: So the coincidences are declared, one language at a time, and
#: `test_every_declared_coincidence_is_a_real_one` fails on anything in here
#: that is not in fact identical. The list can only grow deliberately, and it
#: cannot outlive the entry it was written for: reword the French `Code` and
#: this fails on the next run rather than quietly permitting an English value.
IDENTICAL_BY_DESIGN = {
    "de": {"Code", "Name"},
    "es": {"No", "Sector"},
    "fr": {"Code", "Destination", "Documentation"},
    "nl": {"Code", "Sector"},
}

LANGUAGES = i18n_keys.catalogue_languages()
SOURCE = i18n_keys.source_strings()


def test_the_manifest_lists_exactly_the_catalogues_on_disk():
    """`index.json` is the browser's `glob`, and a browser cannot glob.

    `admin/i18n.py` reads its directory at import. The calculator is static
    files, so the list of languages has to be a file - which makes it the one
    place "adding a language is adding a file" is not literally true, and the
    one place two things can drift apart. This is what stops them.
    """
    on_disk = sorted(
        path.stem for path in i18n_keys.LOCALES.glob("*.json") if path.stem != "index"
    )
    assert sorted(LANGUAGES) == on_disk, (
        "web/locales/index.json and web/locales/ disagree. Every catalogue "
        "file must be listed and every listing must have a file."
    )


def test_the_manifest_repeats_each_catalogue_s_own_tag_claims():
    """The manifest decides which file to fetch, so its claims are the ones
    negotiation actually uses. A catalogue whose own `tags` said one thing
    while the manifest said another would be reachable by tags it does not
    claim and unreachable by tags it does.
    """
    for entry in json.loads(
        (i18n_keys.LOCALES / "index.json").read_text(encoding="utf-8")
    )["catalogues"]:
        catalogue = i18n_keys.catalogue(entry["language"])
        assert entry["tags"] == catalogue["tags"], entry["language"]


def test_no_tag_is_claimed_by_two_catalogues():
    """`zh-HK` answered by two files would make fetch order decide the script."""
    seen: dict[str, str] = {}
    for language in LANGUAGES:
        catalogue = i18n_keys.catalogue(language)
        for tag in [catalogue["language"], *catalogue["tags"]]:
            key = tag.lower()
            assert key not in seen or seen[key] == language, (
                f"{tag!r} is claimed by both {seen.get(key)!r} and {language!r}"
            )
            seen[key] = language


def test_traditional_chinese_claims_its_regions_by_name():
    """The one matching rule that truncation gets wrong.

    RFC 4647 lookup drops a subtag at a time, so `zh-TW` -> `zh` is what a
    plain implementation does and Simplified Chinese is what a Traditional
    reader would get. `zh-Hant` therefore claims the regions by name, and an
    exact claim is matched before any truncation runs.
    """
    traditional = i18n_keys.catalogue("zh-Hant")["tags"]
    for region in ("zh-TW", "zh-HK", "zh-MO"):
        assert region in traditional, f"{region} must reach Traditional Chinese"
    simplified = i18n_keys.catalogue("zh")["tags"]
    for region in ("zh-Hans", "zh-CN", "zh-SG"):
        assert region in simplified


def test_the_source_list_is_read_from_the_front_end_and_is_not_empty():
    """A regex that stopped matching would empty every coverage test below
    and turn this whole file green against no catalogue at all."""
    assert len(SOURCE) > 150, len(SOURCE)
    # A plain `t('...')` call in `calculator.js`. It was `Start calculator` until
    # the introduction screen this page opened on was removed - `/` serves
    # `home.html` now - so the canary moved to another literal in the same module
    # rather than being deleted with the screen.
    assert "Clear all calculator data and start again?" in SOURCE
    assert "Step %(step)s of %(total)s" in SOURCE
    # From `data-i18n` in index.html, which a JS-only scan would miss.
    assert "Skip to calculator" in SOURCE
    # From an indirect constant, which a `t('...')` scan alone would miss.
    assert "Supply-chain stage" in SOURCE
    assert "By waste destination" in SOURCE


def test_a_marked_element_inside_another_marked_element_is_still_extracted():
    """The four public navigation links, which the previous extractor lost.

    Each is an `<a data-i18n>` inside a `<nav data-i18n-attr="aria-label">`. The
    regex this replaced matched the outermost element carrying anything starting
    `data-i18n` - `\\b` treats `data-i18n-attr` as a match - and consumed
    everything through `</nav>`, so all four links were invisible to it on all
    three content pages. Nothing failed: a key nobody extracts is a key no
    coverage test can ask for, and translating it would have failed the
    stale-key test instead.

    Named individually rather than counted, because a count passes against four
    of something else.
    """
    for label in ("Home", "Calculator", "Statistics", "Documentation"):
        assert label in SOURCE, (
            f"the navigation label {label!r} is not in the source list, so no "
            "catalogue is required to translate it"
        )
    # And the wrapper's own attribute, which is the reason it was marked at all.
    assert "Primary navigation" in SOURCE


def test_no_marked_element_has_element_children():
    """`applyToDocument` assigns `element.textContent`, which deletes children.

    So `<p data-i18n>text <a href=...>link</a></p>` renders as a paragraph with
    the link gone - silently, in every language except English. The footer's
    transparency line is written the way that constraint requires: the sentence
    in its own `<span data-i18n>` beside the link, not a marker on the paragraph
    holding both.
    """
    for path in i18n_keys.html_pages():
        nested = i18n_keys.marked_elements(path).has_element_children
        assert not nested, (
            f"{path.name}: a data-i18n element contains element children "
            f"{nested}; applyToDocument would delete them"
        )


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_source_string_is_translated(language):
    strings = i18n_keys.catalogue(language)["strings"]
    missing = sorted(key for key in SOURCE if key not in strings)
    assert not missing, (
        f"{len(missing)} string(s) have no {language} translation. If you "
        "reworded one, its old key is now orphaned and the calculator has "
        "silently fallen back to English there: " + " || ".join(missing)
    )


@pytest.mark.parametrize("language", LANGUAGES)
def test_no_catalogue_carries_a_key_the_front_end_never_asks_for(language):
    """A stale key is a translation of text nobody can reach, and it hides a
    reworded string: the coverage test above passes on the new wording while
    the old entry sits in the file looking like work that was done."""
    extra = sorted(set(i18n_keys.catalogue(language)["strings"]) - SOURCE)
    assert not extra, f"{language} translates strings that no longer exist: {extra}"


@pytest.mark.parametrize("language", LANGUAGES)
def test_placeholders_survive_translation(language):
    """`%(count)s` has to come through, or the sentence loses its number.

    `i18n.js` substitutes after the lookup returns, so a translation that
    renames or drops a placeholder does not read oddly - it prints the
    placeholder text to the user, or silently omits the figure the sentence
    was written to carry.
    """
    for source, translated in i18n_keys.catalogue(language)["strings"].items():
        assert set(PLACEHOLDER.findall(source)) == set(
            PLACEHOLDER.findall(translated)
        ), f"placeholders differ in {language}: {source!r} -> {translated!r}"


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_client_s_name_and_the_units_are_never_translated(language):
    """Section 7.6 and the O-8 rules: `Kai Commitment` is the client's name and
    `CO2e` and `NZD` are notation. A machine pass will happily render all three
    into the target script, and a page that credits a differently-spelled
    organisation is a client-facing error rather than a translation choice."""
    for source, translated in i18n_keys.catalogue(language)["strings"].items():
        for literal in NEVER_TRANSLATED:
            if literal in source:
                assert literal in translated, (
                    f"{language}: {literal!r} did not survive {source!r} -> "
                    f"{translated!r}"
                )


@pytest.mark.parametrize("language", LANGUAGES)
def test_no_entry_is_blank_or_still_english(language):
    """A key present with its English value is worse than a missing key.

    A missing key falls back to English and the coverage test above names it.
    A key whose value is the English source looks translated to every test
    here and is not - it lets a language be signed off complete while its
    entries are placeholders.
    """
    allowed = IDENTICAL_BY_DESIGN.get(language, set())
    offenders = [
        source
        for source, translated in i18n_keys.catalogue(language)["strings"].items()
        if not translated.strip() or (translated == source and source not in allowed)
    ]
    assert not offenders, f"{language} entries are blank or still English: {offenders}"


@pytest.mark.parametrize("language", sorted(IDENTICAL_BY_DESIGN))
def test_every_declared_coincidence_is_a_real_one(language):
    """The allowlist above is an exception, so it has to keep earning itself.

    A key listed here whose translation is *not* identical is a hole: the
    exception stops being about a coincidence and starts being a place where an
    English value could sit unnoticed. Reword the French `Code` and this fails
    on the next run, which is the whole point of declaring them.
    """
    strings = i18n_keys.catalogue(language)["strings"]
    for source in sorted(IDENTICAL_BY_DESIGN[language]):
        assert source in strings, f"{language}: {source!r} is no longer a key at all"
        assert strings[source] == source, (
            f"{language}: {source!r} is translated as {strings[source]!r}, so it is "
            "not a coincidence any more and must leave IDENTICAL_BY_DESIGN"
        )


@pytest.mark.parametrize("language", LANGUAGES)
def test_each_catalogue_declares_its_own_metadata(language):
    """Adding a language is adding a file, so the file carries its own label.

    The machine-translation notice is driven by `machine_translated` here and
    not by a list in `i18n.js`, and the writing direction by `dir` - so
    Arabic and Urdu arrive right-to-left without an edit to any module.
    """
    catalogue = i18n_keys.catalogue(language)
    assert catalogue["language"] == language
    assert catalogue["endonym"].strip()
    assert isinstance(catalogue["machine_translated"], bool)
    assert catalogue["tags"]
    assert catalogue.get("dir", "ltr") in ("ltr", "rtl")


def test_only_chinese_is_unflagged_and_every_other_language_says_so():
    """The two promises, made visibly different rather than recorded in a doc.

    English is written by hand. Simplified Chinese is the language the team
    reads and the one with speakers who will notice a wrong word. Everything
    else went through a machine pass and nobody has read it, and the interface
    has to say so - which is what `machine_translated` drives.
    """
    unflagged = [
        language
        for language in LANGUAGES
        if not i18n_keys.catalogue(language)["machine_translated"]
    ]
    assert unflagged == ["zh"], (
        "exactly one calculator catalogue may be unflagged. Flagging Chinese "
        "would put a notice on a language that has reviewers; unflagging any "
        "other would make a claim about a review that has not happened."
    )


def test_arabic_and_urdu_are_the_right_to_left_pair():
    """Declared in the catalogue rather than in a list in `i18n.js`, which is
    what lets `<html dir>` follow from the file alone."""
    rtl = sorted(
        language
        for language in LANGUAGES
        if i18n_keys.catalogue(language).get("dir") == "rtl"
    )
    assert rtl == ["ar", "ur"]


def test_the_stylesheet_carries_no_physical_direction_left():
    """RTL is layout, not a `dir` attribute.

    Arabic and Urdu ship only because every `margin-left`, `padding-left`,
    `border-left` and `text-align: left` that carried meaning became its
    `-inline-start` form. One physical property added back is one element
    that does not mirror, and a half-mirrored page is worse than an
    unmirrored one - which is the trade this project chose against.
    """
    css = (i18n_keys.WEB / "css" / "styles.css").read_text(encoding="utf-8")
    # Comments explain the rule and quote the property names it forbids.
    body = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    offenders = re.findall(
        r"(?:margin|padding|border)-(?:left|right)[-\w]*\s*:|text-align\s*:\s*(?:left|right)",
        body,
    )
    assert not offenders, f"physical direction in styles.css: {offenders}"


def test_the_bar_chart_offset_is_a_logical_property():
    """The one inline style in the front end, and the one that draws a side.

    A diverging comparison bar is offset from a centre line. Under
    `dir="rtl"` a physical `margin-left` would put the negative half on the
    same side as the positive half - two opposite quantities drawn on top of
    each other.
    """
    source = (i18n_keys.WEB / "js" / "improvement.js").read_text(encoding="utf-8")
    assert "margin-inline-start:${offset}%" in source
