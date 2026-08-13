"""Translation: the catalogue, the coverage, and what must never be translated.

**This file's job is to fail when a string loses its translation**, not to
confirm that translation happened once. Source-text keys make rewording the
English silently orphan its Chinese - the page falls back to English and
nothing raises - so the gap is caught here, on the commit that reworded it,
rather than by whoever notices a paragraph in the wrong language weeks later.

What it deliberately does NOT prove: that the page a browser renders is
readable. Six defects have reached this repository through green markup
tests, including 87 field descriptions that were green while four were
invisible behind a widget's CSS class. Rendering is checked in
test_i18n_pages.py, against a real response, by asserting a specific
translated string inside the specific element that carries it.
"""

import json
import re

import pytest
from sqladmin import ModelView

# Imported for the side effect of defining their ModelView subclasses, the
# same reason test_field_help.py imports them.
import admin.accounts_view  # noqa: F401
import admin.blocklist_views  # noqa: F401
import admin.comparison_views  # noqa: F401
import admin.factor_views  # noqa: F401
import admin.modelviews  # noqa: F401
import admin.taxonomy_views  # noqa: F401
from admin import i18n

#: Every language with a catalogue file. English is excluded: its strings are
#: its keys, so there is nothing to be missing.
TRANSLATED = [c.language for c in i18n.languages() if c.language != i18n.DEFAULT_LANGUAGE]


def _concrete_model_views() -> list[type[ModelView]]:
    found = []

    def walk(cls):
        for sub in cls.__subclasses__():
            walk(sub)
            if getattr(sub, "model", None) is not None:
                found.append(sub)

    walk(ModelView)
    return found


def _live_descriptions() -> set[str]:
    """Every field description the panel actually renders, read off the views.

    Taken from the live classes rather than from a list in this file, for the
    reason test_field_help.py gives: a hand-maintained list stops covering
    the panel the moment somebody adds a field, and nothing fails.
    """
    return {
        args["description"]
        for view in _concrete_model_views()
        for args in (getattr(view, "form_args", {}) or {}).values()
        if "description" in args
    }


def _live_view_names() -> set[str]:
    names = set()
    for view in _concrete_model_views():
        for attribute in ("name", "name_plural", "category"):
            value = getattr(view, attribute, None)
            if value:
                names.add(value)
    return names


@pytest.mark.parametrize("language", TRANSLATED)
def test_every_field_description_is_translated(language):
    """The 82 field descriptions are the whole point of this work.

    They are the domain vocabulary - upstream, downstream, prevention, factor
    set, standard mix - and they are what a staff member reads while deciding
    which number to type. An untranslated one is not a cosmetic gap; it is
    the one screen where the language barrier turns into a wrong factor.
    """
    strings = i18n.catalogue(language).strings
    missing = sorted(d for d in _live_descriptions() if d not in strings)
    assert not missing, (
        f"{len(missing)} field description(s) have no {language} translation. "
        "If you reworded one, the old key is now orphaned and the panel has "
        "silently fallen back to English on that field: " + " || ".join(missing)
    )


@pytest.mark.parametrize("language", TRANSLATED)
def test_every_view_name_and_menu_category_is_translated(language):
    """The navigation, which is what makes the panel navigable at all."""
    strings = i18n.catalogue(language).strings
    missing = sorted(n for n in _live_view_names() if n not in strings)
    assert not missing, f"untranslated in {language}: {missing}"


@pytest.mark.parametrize("language", TRANSLATED)
def test_sqladmin_s_own_chrome_is_translated(language):
    """Save, Delete, the pagination line - strings inside the installed package.

    Reachable only because sqladmin wraps them in `_()` with the English as
    the msgid, and because `install_gettext_callables` lets this panel supply
    the callable. If a future sqladmin adds a string, this test fails and the
    catalogue gets it - which is the whole reason the msgids are read out of
    the installed templates here rather than copied into a list.
    """
    strings = i18n.catalogue(language).strings
    missing = sorted(m for m in _sqladmin_msgids() if m not in strings)
    assert not missing, f"sqladmin chrome untranslated in {language}: {missing}"


def _sqladmin_msgids() -> set[str]:
    from pathlib import Path

    import sqladmin

    templates = Path(sqladmin.__file__).parent / "templates" / "sqladmin"
    pattern = re.compile(r"_\(\s*[\"']([^\"']+)[\"']")
    return {
        match
        for path in templates.rglob("*.html")
        for match in pattern.findall(path.read_text(encoding="utf-8"))
    }


@pytest.mark.parametrize("language", TRANSLATED)
def test_placeholders_survive_translation(language):
    """`%(count)s` has to come through, or the page breaks rather than reads oddly.

    Jinja applies `translated % variables` AFTER i18n.gettext returns, so a
    translation that drops or misspells a placeholder does not produce a
    clumsy sentence - it raises KeyError at render time, or prints the wrong
    number where the placeholder was meant to be. "Showing %(start)s to
    %(end)s of %(count)s items" is the concrete one: it carries the row
    counts on every list screen in the panel.
    """
    for source, translated in i18n.catalogue(language).strings.items():
        assert set(i18n.PLACEHOLDER.findall(source)) == set(
            i18n.PLACEHOLDER.findall(translated)
        ), f"placeholders differ in {language}: {source!r} -> {translated!r}"


@pytest.mark.parametrize("language", TRANSLATED)
def test_the_value_placeholder_in_an_equivalence_is_never_translated(language):
    """`{value}` is staff-typed template syntax, not prose.

    It appears inside a field description as an example. A translation that
    localised it would teach a staff member to type a placeholder the
    calculator does not substitute, and the results page would then show a
    sentence with no number in it.
    """
    for source, translated in i18n.catalogue(language).strings.items():
        if "{value}" in source:
            assert "{value}" in translated, f"{language}: {source!r}"


@pytest.mark.parametrize("language", TRANSLATED)
def test_catalogue_has_no_empty_or_untranslated_entries(language):
    """A key present with an English value is worse than a missing key.

    A missing key falls back to English and is reported by the tests above.
    A key whose value is the English source looks translated to every one of
    those tests and is not - it would let a language be signed off as
    complete while its entries were placeholders.
    """
    offenders = [
        source
        for source, translated in i18n.catalogue(language).strings.items()
        if not translated.strip() or translated == source
    ]
    # Identical strings are legitimate for codes and units only; none exist
    # in the catalogue today, and anything added has to justify itself here.
    assert not offenders, f"{language} entries are blank or still English: {offenders}"


def test_an_unknown_language_renders_english_rather_than_failing():
    assert i18n.gettext("Save", code="qq") == "Save"
    assert i18n.set_language("qq") == i18n.DEFAULT_LANGUAGE
    assert i18n.set_language(None) == i18n.DEFAULT_LANGUAGE


def test_a_missing_key_returns_its_own_english_source():
    assert i18n.gettext("a string nobody has translated", code="zh") == (
        "a string nobody has translated"
    )


def test_a_context_qualified_key_falls_back_to_the_bare_text():
    """`_("somewhere|Save")` must never render the qualifier to a user."""
    assert i18n.gettext("factor set|Save", code="zh") == "保存"
    assert i18n.gettext("factor set|Untranslated", code="zh") == "Untranslated"


def test_english_carries_no_machine_translation_notice():
    assert not i18n.is_machine_translated("en")


def test_chinese_carries_no_machine_translation_notice():
    """Chinese is reviewed, by the people who use the panel every day.

    This is the assertion that keeps the two promises distinct. If Chinese
    ever gets flagged as machine translated, the switcher would say so and
    the claim this project makes about it would be false.
    """
    assert not i18n.is_machine_translated("zh")


def test_resolve_prefers_an_explicit_choice_and_only_then_persists():
    assert i18n.resolve(cookie=None, requested="zh") == ("zh", True)
    assert i18n.resolve(cookie="zh", requested=None) == ("zh", False)
    assert i18n.resolve(cookie="zh", requested="qq") == ("zh", False)
    assert i18n.resolve(cookie=None, requested=None) == ("en", False)


def test_the_catalogue_file_declares_its_own_metadata():
    """Adding a language is adding a file - so the file has to carry its label.

    The machine-translation notice is driven by `machine_translated` here and
    not by a list in code, because nineteen more languages arrive through a
    machine pass and none of them should need an edit to a Python module.
    """
    for language in TRANSLATED:
        path = i18n.LOCALES_DIR / f"{language}.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["language"] == language
        assert raw["endonym"].strip()
        assert isinstance(raw["machine_translated"], bool)
        assert raw["strings"]


def test_the_vendored_macros_copy_still_matches_its_original():
    """admin/templates/sqladmin/_macros.html is a copy; copies drift silently.

    Compared with the `_()` calls stripped back out, so the only difference
    this tolerates is the translation itself. A sqladmin upgrade that edits
    the original fails here - loudly, at the moment of the upgrade - instead
    of quietly reverting a translated menu or leaving the panel rendering an
    older version of sqladmin's own form markup.
    """
    from pathlib import Path

    import sqladmin

    original = (
        Path(sqladmin.__file__).parent / "templates" / "sqladmin" / "_macros.html"
    ).read_text(encoding="utf-8")

    ours = (
        Path(admin.__file__).parent / "templates" / "sqladmin" / "_macros.html"
    ).read_text(encoding="utf-8")
    # Drop our leading explanatory Jinja comment, then undo the five edits.
    ours = ours[ours.index("{% macro menu_category") :]
    undone = ours.replace("_(menu.display_name)", "menu.display_name")
    undone = undone.replace("_(sub_menu.display_name)", "sub_menu.display_name")
    undone = undone.replace("_(field.description)", "field.description")
    undone = undone.replace('_("This is a required field")', '"This is a required field"')

    assert undone.strip() == original.strip(), (
        "the installed sqladmin's _macros.html no longer matches the copy in "
        "admin/templates/sqladmin/. Re-copy it and re-apply the five `_()` "
        "edits listed in that file's header comment."
    )


import admin  # noqa: E402  - imported for __file__ in the test above
