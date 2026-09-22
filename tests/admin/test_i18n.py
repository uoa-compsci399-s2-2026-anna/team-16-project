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
from pathlib import Path
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
import admin.submission_views  # noqa: F401
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


def _base_views() -> list[type]:
    """The eight BaseViews: Getting started, My security, Deployment, ...

    Enumerated separately because they are not ModelView subclasses, and the
    first version of this file walked only ModelView - which is exactly how
    three English entries survived in a navigation menu whose other eight
    were Chinese. The tests were green; the panel was visibly half
    translated. Found by opening it in a browser, which is the only thing
    that finds this class of gap.
    """
    from sqladmin import BaseView, ModelView as _ModelView

    import admin.deployment_view  # noqa: F401
    import admin.dryrun_views  # noqa: F401
    import admin.getting_started_view  # noqa: F401
    import admin.self_service_view  # noqa: F401
    import admin.views  # noqa: F401

    found = []

    def walk(cls):
        for sub in cls.__subclasses__():
            walk(sub)
            # `identity` is empty on the class - sqladmin fills it at
            # registration - so a view is recognised by carrying a `name`
            # instead. ModelView inherits BaseView, hence the exclusion.
            if not issubclass(sub, _ModelView) and getattr(sub, "name", None):
                found.append(sub)

    walk(BaseView)
    return found


def _live_view_names() -> set[str]:
    names = set()
    for view in [*_concrete_model_views(), *_base_views()]:
        for attribute in ("name", "name_plural", "category"):
            value = getattr(view, attribute, None)
            if isinstance(value, str) and value:
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


@pytest.mark.parametrize(
    "tag, expected",
    [
        # RFC 4647 lookup: truncate a subtag at a time.
        ("en", "en"),
        ("en-NZ", "en"),
        ("en-nz", "en"),
        ("en_NZ", "en"),
        ("zh", "zh"),
        ("zh-CN", "zh"),
        ("zh-Hans", "zh"),
        ("zh-Hans-CN", "zh"),
        ("zh-SG", "zh"),
        # A tag nobody claims resolves to nothing, so the caller can try the
        # visitor's NEXT preference instead of jumping to English.
        ("fr", None),
        ("fr-CA", None),
        ("i-klingon", None),
        ("", None),
        (None, None),
        ("...", None),
        ("x", None),
    ],
)
def test_a_tag_is_matched_by_lookup_rather_than_by_equality(tag, expected):
    assert i18n.match(tag) == expected


def test_the_panel_has_no_traditional_catalogue_so_zh_tw_degrades_to_simplified():
    """Stated rather than assumed, because the calculator does NOT do this.

    `web/locales/` ships `zh-Hant`, which claims `zh-TW`, `zh-HK` and `zh-MO`
    by name so that truncation never reaches Simplified. The panel ships no
    Traditional catalogue, so the same tags truncate to `zh` here - the wrong
    script, and still the most readable thing available. If a Traditional
    catalogue is ever added to admin/locales/ with those claims, this test
    fails and is the reminder to delete it.
    """
    assert "zh-hant" not in i18n._TAG_INDEX
    assert i18n.match("zh-TW") == "zh"


def test_a_catalogue_s_claims_are_what_make_a_tag_reachable():
    """The claim mechanism, tested where it is load-bearing rather than where
    it happens to be exercised.

    Every tag `admin/locales/zh.json` claims is ALSO reachable by truncation -
    `zh-CN` truncates to `zh` on its own - so deleting the claims changes
    nothing about the panel as it ships, and no test of the panel would
    notice. The calculator's `zh-Hant` is the case that needs them, and this
    builds it here so the mechanism is held in the module that implements it.
    """
    traditional = i18n.Catalogue(
        language="zh-Hant",
        endonym="中文（繁體）",
        machine_translated=True,
        strings={},
        tags=("zh-Hant", "zh-TW", "zh-HK", "zh-MO"),
    )
    index = i18n._tag_index(
        {"zh": i18n.catalogue("zh"), "zh-Hant": traditional, "en": i18n.catalogue("en")}
    )
    # Claimed by name, and truncation would have sent all three to `zh`.
    assert index["zh-tw"] == "zh-Hant"
    assert index["zh-hk"] == "zh-Hant"
    assert index["zh-mo"] == "zh-Hant"
    # While the Simplified claims still point where they should.
    assert index["zh-cn"] == "zh"
    assert index["zh-hans"] == "zh"


@pytest.mark.parametrize(
    "header, expected",
    [
        (None, []),
        ("", []),
        ("en", ["en"]),
        # Ranked, not read in written order. This one line is the reason the
        # header is parsed at all rather than split on commas.
        ("zh;q=0.8, en;q=0.9", ["en", "zh"]),
        ("zh-CN,zh;q=0.9,en;q=0.8", ["zh-CN", "zh", "en"]),
        # Equal quality keeps the header's own order.
        ("de;q=0.5, fr;q=0.5", ["de", "fr"]),
        # q=0 means "not acceptable" and is dropped, not ranked last.
        ("en;q=0, zh", ["zh"]),
        # `*` is RANKED, not dropped. Dropping it would promote the tag behind
        # it into the one slot `negotiate` consults, so a header whose first
        # statement is "no preference" would answer Chinese.
        ("*", ["*"]),
        ("*, zh", ["*", "zh"]),
        ("zh, *;q=0.5", ["zh", "*"]),
        # And it is ranked by quality like anything else, so a wildcard offered
        # as a last resort does not outrank a real preference.
        ("*;q=0.1, zh;q=0.9", ["zh", "*"]),
        # Malformed entries are dropped rather than guessed at. A dropped entry
        # holds no rank - there is no quality to rank it by, which is the whole
        # reason it was dropped - so the next readable tag becomes the head.
        ("en;q=high, zh", ["zh"]),
        ("!!!, zh", ["zh"]),
        ("en;q=1.5, zh", ["zh"]),
        # Nothing readable at all, which `negotiate` answers with English.
        ("en;q=high", []),
        ("!!!", []),
    ],
)
def test_accept_language_is_parsed_by_quality(header, expected):
    assert i18n.parse_accept_language(header) == expected


def test_a_pathological_accept_language_header_is_bounded():
    """A header is untrusted input, and this loop runs on every request."""
    assert i18n.parse_accept_language("de," * 5000 + "zh") == ["de"] * 24


@pytest.mark.parametrize(
    "header, requested, expected",
    [
        # The browser decides, with no query string and nothing stored.
        ("zh-CN,zh;q=0.9,en;q=0.8", None, "zh"),
        ("en-NZ,en;q=0.9", None, "en"),
        (None, None, "en"),
        # THE RULE: only the highest-priority tag is consulted. `fr` has no
        # panel catalogue, and the `zh` behind it does not get a turn.
        ("fr-CA,fr;q=0.9,zh;q=0.8", None, "en"),
        ("fr-CA,fr;q=0.9", None, "en"),
        # ...and its pair, which is what makes the line above mean something.
        # A negotiator that simply always answered English would pass every
        # assertion about an unsupported tag and fail these.
        ("zh,fr", None, "zh"),
        ("zh-CN,fr;q=0.9,de;q=0.8", None, "zh"),
        # Ordering happens BEFORE the rule, not after: `en` is the
        # highest-priority tag here even though `zh` is written first.
        ("zh;q=0.8, en;q=0.9", None, "en"),
        # And the same mechanism the other way round, so that "ranked first"
        # cannot be confused with "English wins".
        ("en;q=0.4, zh;q=0.9", None, "zh"),
        # Truncation still applies to that one tag - the rule removed the walk
        # down the list, not the lookup within a tag.
        ("zh-Hans-CN", None, "zh"),
        ("zh-SG,en", None, "zh"),
        # `*` at the head is "no preference", which is English. Behind a real
        # preference it decides nothing.
        ("*", None, "en"),
        ("*, zh", None, "en"),
        ("zh, *", None, "zh"),
        ("*;q=0.1, zh;q=0.9", None, "zh"),
        # q=0 is an explicit refusal, so the tag is not the head - it is not in
        # the list at all. `zh` behind a refused `en` is still the head.
        ("en;q=0, zh", None, "zh"),
        ("zh;q=0", None, "en"),
        # A header with nothing readable in it, and an absent one.
        ("en;q=high", None, "en"),
        ("!!!", None, "en"),
        ("", None, "en"),
        # `?lang=` overrides the header, for testing, screenshots and support.
        ("en-NZ", "zh", "zh"),
        ("en-NZ", "zh-CN", "zh"),
        # An unrecognised override is ignored and does NOT consume the single
        # slot: the header still decides, and lands on English only because
        # its own head has no catalogue.
        ("zh-CN", "qq", "zh"),
        ("en-NZ", "qq", "en"),
        ("fr,zh", "qq", "en"),
    ],
)
def test_negotiation_reads_the_header_honours_the_override_and_keeps_nothing(
    header, requested, expected
):
    assert i18n.negotiate(header, requested) == expected


def test_only_the_highest_priority_tag_is_consulted():
    """The rule, stated on its own rather than only as parametrised rows.

    **Both halves are the test.** `fr-CA, zh, en` answering English proves
    nothing by itself - it passes against a negotiator that has stopped
    matching anything at all. The second assertion is what separates "the
    list is not walked" from "the matcher is broken", and the third keeps the
    tag-claim mechanism inside the same statement: the rule takes away the
    walk between tags, not the lookup inside one.
    """
    assert i18n.negotiate("fr-CA,zh;q=0.9,en;q=0.8") == "en"
    assert i18n.negotiate("zh-CN,fr;q=0.9,en;q=0.8") == "zh"
    assert i18n.match("zh-Hans") == "zh"


def test_no_catalogue_claims_a_tag_another_one_claims():
    """Two catalogues answering to `zh-HK` would make filename order decide."""
    seen: dict[str, str] = {}
    for catalogue in i18n.languages():
        for tag in (catalogue.language, *catalogue.tags):
            key = tag.lower()
            assert key not in seen or seen[key] == catalogue.language, (
                f"{tag!r} is claimed by both {seen.get(key)!r} and "
                f"{catalogue.language!r}"
            )
            seen[key] = catalogue.language


def test_the_stored_choice_is_the_only_thing_that_is_persisted():
    """One cookie, one closed value space, and nothing else.

    **This test replaced its own opposite.** It used to assert that
    ``COOKIE_NAME`` did not exist, so that reinstating a cookie had to be a
    deliberate act rather than a quiet one. It was, and this is what the same
    guard looks like on the other side of that decision: the bound is no longer
    "nothing is stored" but "only this is stored, and only these values".

    The value space is the assertion that matters. It is what makes the cookie
    incapable of identifying anybody - twenty-two possible values shared
    identically by everyone who picks the same language, with no entropy for a
    correlator to key on. A field that could carry a free-form string would be a
    fingerprint however it got there, so the closed set is checked rather than
    assumed.
    """
    assert i18n.COOKIE_NAME == "kaicalc_lang"
    assert i18n.FOLLOW_SYSTEM == "auto"

    permitted = {i18n.FOLLOW_SYSTEM, *(c.language for c in i18n.languages())}
    for value in permitted:
        assert i18n.stored_choice(value) is not None or value == i18n.FOLLOW_SYSTEM

    # Anything outside the set resolves to "no stored choice", never to itself.
    for rejected in ("", "  ", "en; DROP", "../../etc/passwd", "x" * 400):
        assert i18n.stored_choice(rejected) is None


async def _post_language(body: bytes, origin: bytes | None = b"http://testserver"):
    """Drive `set_language` directly, with no database and no login.

    A bare Request rather than the ASGI client because this endpoint
    deliberately sits outside sqladmin's `login_required` wrapper and outside
    the session entirely - building a logged-in client to exercise it would
    test something other than what ships.
    """
    from starlette.requests import Request

    from admin import language_view

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    headers = [
        (b"content-type", b"application/x-www-form-urlencoded"),
        (b"host", b"testserver"),
    ]
    if origin is not None:
        headers.append((b"origin", origin))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/admin/language",
        "headers": headers,
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
        "root_path": "",
    }
    return await language_view.set_language(Request(scope, receive))


def _set_cookies(response):
    return [value for header, value in response.raw_headers if header == b"set-cookie"]


@pytest.mark.asyncio
async def test_no_identifier_is_written_alongside_the_language():
    """The language cookie must not become a way to correlate anyone.

    Named because the tempting implementation stamps a visitor id beside the
    preference "to remember it better". The response is inspected rather than
    the module, because that is where such a thing would actually appear.
    """
    response = await _post_language(b"lang=zh")
    cookies = _set_cookies(response)
    assert len(cookies) == 1, f"more than the language cookie was set: {cookies}"
    written = cookies[0].decode()
    assert written.startswith(f"{i18n.COOKIE_NAME}=zh;")
    assert "Path=/" in written
    assert "SameSite=lax" in written.replace("SameSite=Lax", "SameSite=lax")
    # Readable by web/js/i18n.js on the calculator, which is the whole of why
    # one cookie can serve both surfaces.
    assert "HttpOnly" not in written


@pytest.mark.asyncio
async def test_follow_the_system_is_written_down_rather_than_deleted():
    """"Follow the system" is a stored value, not a cleared cookie.

    A deletion would have to re-send the cookie with `Max-Age=0` and an exactly
    matching path and domain; get that wrong and the old value survives, so the
    chooser appears to revert and snaps back on the next page. A write cannot
    fail that way, which is the mechanical half of why `auto` exists.

    The `max-age` assertion is the one that kills the tempting fix: a deletion
    would set it to 0.
    """
    written = _set_cookies(await _post_language(b"lang=auto"))[0].decode()
    assert written.startswith(f"{i18n.COOKIE_NAME}=auto;")
    assert "Max-Age=0" not in written
    assert f"Max-Age={i18n.COOKIE_MAX_AGE}" in written


@pytest.mark.asyncio
async def test_a_value_the_panel_does_not_know_stores_follow_the_system():
    """No error page, in a language the visitor may not read.

    Every value the chooser can emit is valid, so an invalid one arrived from
    somewhere else and the readable answer is the default.
    """
    for body in (b"lang=qq", b"lang=", b"", b"lang=%2Fetc%2Fpasswd"):
        written = _set_cookies(await _post_language(body))[0].decode()
        assert written.startswith(f"{i18n.COOKIE_NAME}=auto;"), body


@pytest.mark.asyncio
async def test_a_cross_origin_post_changes_nothing():
    """The stateless stand-in for the CSRF token this endpoint cannot carry.

    admin/language_view.py says why there is no token: issuing one would mint a
    session cookie for every anonymous visitor to the login page, which is a
    real identifier created to protect a cosmetic preference.
    """
    response = await _post_language(b"lang=zh", origin=b"http://evil.example")
    assert _set_cookies(response) == []
    # Still a redirect rather than an error: nothing was attempted, so there is
    # nothing to report to the visitor whose browser was used.
    assert response.status_code == 303


@pytest.mark.asyncio
async def test_the_return_path_cannot_leave_the_panel():
    """`next` is a path inside /admin or it is the panel root.

    The middle case is the one that looks safe: `//evil.example/admin` passes a
    naive `startswith('/')` check and navigates off-site.
    """
    from admin.language_view import safe_next

    assert safe_next("/admin/constant/list") == "/admin/constant/list"
    assert safe_next("/admin") == "/admin"
    for hostile in (
        "//evil.example/admin",
        "http://evil.example/admin",
        "https://evil.example",
        "/etc/passwd",
        "/admins-elsewhere",
        "admin/constant/list",
        None,
        "",
    ):
        assert safe_next(hostile) == "/admin", hostile


def test_a_choice_the_panel_cannot_honour_renders_english_and_is_named():
    """Tamil chosen on the calculator: English here, said so, cookie untouched.

    The three wrong answers this pins down, in order of temptation:

    * render English silently, which pretends no choice was made;
    * show "Follow the system" as selected, which is a lie about what is stored;
    * rewrite the cookie to `auto`, which destroys the calculator's language
      from an unrelated screen.

    The third is the dangerous one and it cannot be caught here - a resolution
    function has no response to write to - so `set_language` above owns the
    write and this test owns the reading. The pair is the assertion.
    """
    assert i18n.resolve("ta", "zh-CN") == "en"
    assert i18n.unavailable_choice("ta") == "தமிழ்"

    # The control that separates "the message works" from "everything is
    # unavailable": a language the panel does have is honoured and says nothing.
    assert i18n.resolve("zh", "en") == "zh"
    assert i18n.unavailable_choice("zh") is None

    # And a Chinese variant reaches Chinese by truncation rather than falling
    # into this branch, which is what keeps it narrow.
    assert i18n.resolve("zh-Hant", "en") == "zh"
    assert i18n.unavailable_choice("zh-Hant") is None


def test_the_vendored_calculator_language_list_matches_the_calculator_s_own():
    """A copy that can drift, held to its original.

    admin/i18n.py has to vendor `web/locales/index.json` because
    docker/admin.Dockerfile copies no `web/` and package-data cannot reach
    outside its own package. Reading the calculator's file directly would work
    in this checkout and return nothing in the built image, making the
    "not available in the panel" message a feature that passes every test and
    renders on no deployed screen.

    Compared as parsed JSON and then as bytes: the first says the data agrees,
    the second says nobody reformatted one of them and left the two to drift on
    the next edit.
    """
    vendored = i18n.LOCALES_DIR / i18n.CALCULATOR_LANGUAGES_FILE
    original = (
        Path(__file__).resolve().parents[2] / "web" / "locales" / "index.json"
    )
    assert vendored.is_file(), "the vendored calculator language list is missing"
    assert json.loads(vendored.read_text(encoding="utf-8")) == json.loads(
        original.read_text(encoding="utf-8")
    )
    assert vendored.read_bytes() == original.read_bytes()

    # Anchored: a vendored file that had lost its content would satisfy an
    # equality test against an equally empty original.
    assert len(json.loads(vendored.read_text(encoding="utf-8"))["catalogues"]) == 20


def test_the_vendored_list_is_not_loaded_as_a_catalogue():
    """A leading underscore means "data", not "a language".

    Without the skip in `_load`, `_calculator_languages.json` is read as a
    catalogue and raises KeyError on `raw["language"]` at import - which takes
    the whole panel down at start-up, not just this feature.
    """
    assert "_calculator_languages" not in {c.language for c in i18n.languages()}
    assert {c.language for c in i18n.languages()} == {"en", "zh"}


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
        # `tags` is what stops `zh-TW` reaching Simplified Chinese, and it
        # lives in the file for the same reason the two above do: adding a
        # language is adding a file. It must claim its own code, or the
        # catalogue answers to no tag at all.
        assert language in [t.lower() for t in raw.get("tags", [language])]


def _repository_root():
    from pathlib import Path

    import admin

    return Path(admin.__file__).parent.parent


def _normalised(name: str) -> str:
    """PEP 503 package-name normalisation, so `SQLAlchemy` matches `sqlalchemy`."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _pinned_version(package: str) -> str | None:
    """The version docker/constraints.txt freezes, or None if it names none.

    That file is the authority on which version this repository is written
    against: both images build with it and CI installs with it. Whatever
    happens to be in a developer's site-packages is not.
    """
    pin = re.compile(r"^([A-Za-z0-9._-]+)==([^\s;#]+)")
    text = (_repository_root() / "docker" / "constraints.txt").read_text(
        encoding="utf-8"
    )
    for line in text.splitlines():
        match = pin.match(line.strip())
        if match and _normalised(match.group(1)) == _normalised(package):
            return match.group(2)
    return None


def _installed_version(package: str) -> str:
    from importlib.metadata import version

    return version(package)


def _undo_the_translation_edits(ours: str) -> str:
    """Our copy, reduced to what it was copied from.

    Drops the leading explanatory Jinja comment, then reverses the five `_()`
    edits listed in that comment - so the only difference this can leave
    behind is a difference sqladmin made.
    """
    ours = ours[ours.index("{% macro menu_category") :]
    for translated, plain in (
        ("_(menu.display_name)", "menu.display_name"),
        ("_(sub_menu.display_name)", "sub_menu.display_name"),
        ("_(field.description)", "field.description"),
        ('_("This is a required field")', '"This is a required field"'),
    ):
        ours = ours.replace(translated, plain)
    return ours


def _macros_complaint(
    ours: str, original: str, installed: str | None, pinned: str | None
) -> str | None:
    """What is wrong with the vendored copy, or None if nothing is.

    Split out from the test so the two failures it can report are themselves
    testable, and because they are two different failures. THE MESSAGE IS THE
    DELIVERABLE HERE: the previous version of this check said only "the copy
    has drifted, re-copy it", which sent the first reader of a red CI run to
    inspect a file that was a faithful copy of the sqladmin on their own
    machine. The cause was that CI had a different sqladmin, and no part of
    the message pointed there.
    """
    if _undo_the_translation_edits(ours).strip() == original.strip():
        return None

    if installed != pinned:
        return (
            f"admin/templates/sqladmin/_macros.html does not match the installed "
            f"sqladmin, and the installed sqladmin is not the pinned one: "
            f"{installed} is installed, docker/constraints.txt pins {pinned}. "
            f"THE COPY IS PROBABLY FINE - fix the environment first. Install "
            f"with the constraints file (`pip install -e \".[dev]\" "
            f"-c docker/constraints.txt`) and run this again. Only if it still "
            f"fails on {pinned} is the copy itself out of date."
        )

    return (
        f"admin/templates/sqladmin/_macros.html no longer matches sqladmin "
        f"{installed}'s own _macros.html, and {installed} is the version "
        f"docker/constraints.txt pins - so this is real drift, not a version "
        f"skew. Re-copy the original and re-apply the five `_()` edits listed "
        f"in that file's header comment, and check whether sqladmin added "
        f"markup that its own CSS or JS depends on."
    )


def _installed_macros() -> str:
    from pathlib import Path

    import sqladmin

    return (
        Path(sqladmin.__file__).parent / "templates" / "sqladmin" / "_macros.html"
    ).read_text(encoding="utf-8")


def _our_macros() -> str:
    return (
        _repository_root() / "admin" / "templates" / "sqladmin" / "_macros.html"
    ).read_text(encoding="utf-8")


def test_the_installed_sqladmin_is_the_version_pinned_for_the_images():
    """Asserted on its own, because it is its own cause with its own fix.

    docker/constraints.txt is what the two images build with and what CI
    installs with; pyproject.toml states only a `>=0.20` floor. A developer
    who installs from that floor gets whatever PyPI published most recently,
    which is how this repository spent a release cycle with 0.30.0 on the
    desk and 0.31.0 in the image - the vendored macros copy was taken from
    the wrong one, and the panel shipped 0.30.0's menu markup on 0.31.0.
    """
    pinned = _pinned_version("sqladmin")
    assert pinned is not None, "docker/constraints.txt no longer pins sqladmin"
    installed = _installed_version("sqladmin")
    assert installed == pinned, (
        f"sqladmin {installed} is installed but docker/constraints.txt pins "
        f"{pinned}, so this suite is not testing the software that ships. "
        f"Install with the constraints file: "
        f'pip install -e ".[dev]" -c docker/constraints.txt'
    )


def _installed_sqladmin_base() -> str:
    from pathlib import Path

    import sqladmin

    return (
        Path(sqladmin.__file__).parent / "templates" / "sqladmin" / "base.html"
    ).read_text(encoding="utf-8")


def test_the_html_element_rewrite_still_finds_the_line_it_rewrites():
    """The drift guard for the one sqladmin line the panel rewrites in place.

    `admin/i18n.py::_HtmlElement` replaces `<html lang="en">` in sqladmin's own
    `base.html` as Jinja compiles it, which is how the panel proper announces
    the language it is actually in without a second vendored template. A
    rewrite that matches nothing is worse than no rewrite, because it looks
    installed and fails silently - the exact shape of the _macros drift, where
    a stale copy suppressed the original and 0.31.0's menu JavaScript matched
    no element with nothing in any log.

    So this fails at the moment of the upgrade rather than in front of a staff
    member. It is deliberately a **separate** failure from the version-pin test
    above, for the reason written there: reporting a version skew as "the
    rewrite is wrong" sends the reader to edit the wrong file.
    """
    assert i18n.SQLADMIN_HTML_ELEMENT in _installed_sqladmin_base(), (
        f"sqladmin {_installed_version('sqladmin')} no longer writes "
        f"{i18n.SQLADMIN_HTML_ELEMENT!r} in its own base.html, so the panel "
        f"proper would go back to announcing every page as English. If the "
        f"installed version does not match the pin "
        f"({_pinned_version('sqladmin')}), fix that first - this failure is a "
        f"symptom of it. If it does, update admin/i18n.py's literal by hand."
    )


def test_the_rewrite_is_applied_to_sqladmin_s_template_and_to_nothing_else():
    """The extension does something, and does it only where it is meant to.

    The assertion above proves the literal is still findable; it does not
    prove the extension is wired in, and an extension that was never added to
    the environment would pass it. This renders the substitution through a
    real Jinja environment instead.

    The second half is what stops it over-reaching: `brand/base.html` writes
    the same two attributes itself, and a rewrite keyed on anything looser
    than the template name would hit it twice.
    """
    from jinja2 import Environment, DictLoader

    env = Environment(loader=DictLoader({}))
    i18n.install(env)

    source = _installed_sqladmin_base()
    rewritten = env.preprocess(source, i18n.SQLADMIN_BASE_TEMPLATE)
    assert '<html lang="{{ kaicalc_html_lang() }}" dir="{{ kaicalc_html_dir() }}">' in rewritten
    assert i18n.SQLADMIN_HTML_ELEMENT not in rewritten

    # Any other template name is returned untouched.
    assert env.preprocess(source, "brand/base.html") == source


def test_a_base_template_without_the_line_is_refused_rather_than_ignored():
    """The mutation this guard exists to catch, exercised directly.

    A `preprocess` that returned the source unchanged when it could not find
    its literal would pass every other test in this file: the rewrite would
    simply stop happening, and the panel would quietly go back to `lang="en"`
    on every page. So the not-found path is asserted to raise, and the message
    is asserted to name the version-skew cause - which is the half that sends
    the next reader to the right file.
    """
    from jinja2 import Environment, DictLoader

    env = Environment(loader=DictLoader({}))
    i18n.install(env)

    with pytest.raises(RuntimeError) as raised:
        env.preprocess("<html lang='en'>", i18n.SQLADMIN_BASE_TEMPLATE)

    message = str(raised.value)
    assert "docker/constraints.txt" in message
    assert "VERSION SKEW" in message.upper()


def _installed_sqladmin_list() -> str:
    from pathlib import Path

    import sqladmin

    return (
        Path(sqladmin.__file__).parent / "templates" / "sqladmin" / "list.html"
    ).read_text(encoding="utf-8")


def test_the_import_button_rewrite_still_finds_the_line_it_rewrites():
    """The drift guard for the second sqladmin line the panel rewrites.

    `admin/i18n.py::_ImportButton` relabels sqladmin's `_("Import CSV")`
    button, because this panel's import takes a `.csv` OR a `.json` file on
    one path and the button is all a staff member sees before opening the
    dialog. Same failure mode as the `<html>` element above: a rewrite that
    matches nothing looks installed and is not, and the button would quietly
    go back to naming one of the two formats.

    Anchored on the whole anchor rather than on the three words, so that this
    fails if sqladmin keeps the label and moves it - which would leave the
    rewrite matching nothing while the string was still findable.
    """
    assert i18n.SQLADMIN_IMPORT_BUTTON in _installed_sqladmin_list(), (
        f"sqladmin {_installed_version('sqladmin')} no longer writes "
        f"{i18n.SQLADMIN_IMPORT_BUTTON!r} in its own list.html, so the Import "
        f"button would go back to promising CSV only on a screen that takes "
        f"both formats. If the installed version does not match the pin "
        f"({_pinned_version('sqladmin')}), fix that first - this failure is a "
        f"symptom of it. If it does, update admin/i18n.py's literal by hand."
    )


def test_the_import_button_rewrite_is_applied_to_the_list_page_and_nowhere_else():
    """The extension is wired in, and it reaches only the template it names.

    The assertion above proves the literal is findable; an extension that was
    never added to the environment would pass it unchanged. This renders the
    substitution through a real Jinja environment instead.

    The second half matters more here than it did for the `<html>` element:
    five `brand/` templates descend from `sqladmin/list.html` and inherit the
    rewritten block, so a rewrite keyed on anything looser than the template
    name would run twice - and the second pass, finding no literal, would
    raise.
    """
    from jinja2 import Environment, DictLoader

    env = Environment(loader=DictLoader({}))
    i18n.install(env)

    source = _installed_sqladmin_list()
    rewritten = env.preprocess(source, i18n.SQLADMIN_LIST_TEMPLATE)
    assert '{{ _("Import") }}</a>' in rewritten
    assert i18n.SQLADMIN_IMPORT_BUTTON not in rewritten
    assert '_("Import CSV")' not in rewritten

    assert env.preprocess(source, "brand/model_list.html") == source


def test_a_list_template_without_the_import_button_is_refused_rather_than_ignored():
    """The mutation this guard exists to catch, exercised directly.

    A `preprocess` that returned the source unchanged when it could not find
    its literal would pass every other test in this file, and the button would
    simply go back to saying "Import CSV" with nothing in any log. So the
    not-found path is asserted to raise, and the message is asserted to name
    the version-skew cause and the consequence - a reader who is told only
    that "a rewrite failed" has to go and find out what breaks.
    """
    from jinja2 import Environment, DictLoader

    env = Environment(loader=DictLoader({}))
    i18n.install(env)

    with pytest.raises(RuntimeError) as raised:
        env.preprocess("<a>{{ _(\"Import CSV\") }}</a>", i18n.SQLADMIN_LIST_TEMPLATE)

    message = str(raised.value)
    assert "docker/constraints.txt" in message
    assert "VERSION SKEW" in message.upper()
    assert ".json" in message, (
        "the message does not say what the button would be getting wrong"
    )


@pytest.mark.parametrize("language", TRANSLATED)
def test_the_new_import_button_msgid_is_in_the_catalogue(language):
    """The rewrite introduces a msgid; a msgid with no entry renders English.

    `"Import"` was already a catalogue key before this rewrite existed, which
    is why no translation was added for it. Asserted rather than assumed,
    because the whole point of the rewrite is that the Chinese panel keeps a
    translated button - a rewrite that silently de-translated the control
    would be a worse outcome than the wrong word.
    """
    assert "Import" in i18n.catalogue(language).strings


@pytest.mark.parametrize("language", TRANSLATED)
def test_the_retired_import_csv_key_is_kept_because_the_package_still_has_it(
    language,
):
    """`"Import CSV"` renders nowhere now and stays in the catalogue anyway.

    A key nothing renders is normally litter. This one is not, and the reason
    is mechanical: `_sqladmin_msgids()` above reads its msgids out of the
    **installed package**, and `_ImportButton` rewrites the compiled template,
    not the file on disk - so `_("Import CSV")` is still written there and
    `test_sqladmin_s_own_chrome_is_translated` still demands an entry for it.

    Deleting the entry would fail that test with "sqladmin chrome untranslated
    in zh: ['Import CSV']", which is indistinguishable from a genuinely
    missing translation and would send the next reader to add it back. This
    test states the dependency in the place somebody would go to delete it.
    """
    assert "Import CSV" in i18n.catalogue(language).strings, (
        "'Import CSV' was removed from the catalogue, but sqladmin's own "
        "list.html still contains the string - admin/i18n.py rewrites the "
        "compiled template, not the installed file. Restore the entry; it "
        "becomes removable only when sqladmin itself stops writing it."
    )


def test_the_vendored_macros_copy_still_matches_its_original():
    """admin/templates/sqladmin/_macros.html is a copy; copies drift silently.

    Compared with the `_()` calls stripped back out, so the only difference
    this tolerates is the translation itself. A sqladmin upgrade that edits
    the original fails here - loudly, at the moment of the upgrade - instead
    of quietly reverting a translated menu or leaving the panel rendering an
    older version of sqladmin's own form markup.
    """
    complaint = _macros_complaint(
        _our_macros(),
        _installed_macros(),
        _installed_version("sqladmin"),
        _pinned_version("sqladmin"),
    )
    assert complaint is None, complaint


def test_a_faithful_copy_is_accepted():
    """The other half of the test above, which otherwise proves only refusal.

    A comparison that rejected everything would pass every "this must fail"
    assertion below. This is the one that says the check can also say yes.
    """
    original = _installed_macros()
    ours = "{# a header #}\n" + original.replace(
        "{{ menu.display_name }}", "{{ _(menu.display_name) }}"
    ).replace("{{ sub_menu.display_name }}", "{{ _(sub_menu.display_name) }}").replace(
        "{{ field.description }}", "{{ _(field.description) }}"
    ).replace(
        '"This is a required field"', '_("This is a required field")'
    )
    # Anchor: the `_()` edits must actually have been applied, or this test
    # would be comparing the original against itself and would pass even if
    # _undo_the_translation_edits did nothing at all.
    assert ours != "{# a header #}\n" + original, "anchor: the edits did not apply"
    assert "_(menu.display_name)" in ours
    assert '_("This is a required field")' in ours

    assert _macros_complaint(ours, original, "9.9.9", "9.9.9") is None


def test_a_drifted_copy_on_the_pinned_version_is_reported_as_drift():
    original = _installed_macros()
    drifted = "{# a header #}\n" + original.replace(
        'class="nav-item dropdown"', 'class="nav-item dropdown" data-something="1"'
    )
    assert 'data-something="1"' in drifted, "anchor: the mutation did not apply"

    complaint = _macros_complaint(drifted, original, "9.9.9", "9.9.9")
    assert complaint is not None
    assert "real drift" in complaint
    assert "Re-copy" in complaint


def test_a_version_skew_is_reported_as_a_version_skew_and_not_as_drift():
    """The whole point of the split.

    Same drifted copy as the test above; only the two versions differ. The
    message must change, because the thing the reader has to go and fix has
    changed.
    """
    original = _installed_macros()
    drifted = "{# a header #}\n" + original.replace(
        'class="nav-item dropdown"', 'class="nav-item dropdown" data-something="1"'
    )

    complaint = _macros_complaint(drifted, original, "0.30.0", "0.31.0")
    assert complaint is not None
    assert "0.30.0 is installed" in complaint
    assert "pins 0.31.0" in complaint
    assert "THE COPY IS PROBABLY FINE" in complaint
    # And it must NOT send the reader to re-copy the file, which is what the
    # message said before this split existed.
    assert "real drift" not in complaint


def test_the_pin_is_read_out_of_the_constraints_file_rather_than_guessed():
    """Anchors _pinned_version against the real file.

    A `_pinned_version` that returned None for everything would make the skew
    branch above unreachable in the real test, and every assertion about it
    would still pass.
    """
    assert _pinned_version("sqladmin") is not None
    # Normalisation is load-bearing: the file writes `SQLAlchemy`, not
    # `sqlalchemy`, and a case-sensitive match would silently find no pin.
    assert _pinned_version("sqlalchemy") == _pinned_version("SQLAlchemy") is not None
    assert _pinned_version("a-package-nobody-pins") is None


import admin  # noqa: E402  - imported for __file__ in the tests above


def _our_own_template_msgids() -> set[str]:
    """Every `_("...")` this repository wrote into its own templates.

    Read out of the templates rather than listed here, for the reason every
    other coverage set in this file is: a list stops covering the panel the
    moment somebody adds a string, and nothing fails. sqladmin's own msgids
    are covered separately, and the vendored `_macros.html` is excluded
    because its `_()` calls wrap variables rather than literals.
    """
    from pathlib import Path

    import admin

    root = Path(admin.__file__).parent / "templates"
    pattern = re.compile(r"_\(\s*[\"']([^\"']{4,})[\"']\s*\)")
    return {
        match
        for path in root.rglob("*.html")
        if path.name != "_macros.html"
        for match in pattern.findall(path.read_text(encoding="utf-8"))
    }


@pytest.mark.parametrize("language", TRANSLATED)
def test_our_own_templates_are_translated(language):
    """The strings this project wrote, as opposed to sqladmin's.

    Nothing covered these until the machine-translation notice moved into
    `brand/base.html` and `sqladmin/layout.html` - the two templates a
    reader of a machine-translated page cannot avoid - and a notice that
    renders in English on a page nobody can read in English says nothing.
    """
    strings = i18n.catalogue(language).strings
    missing = sorted(m for m in _our_own_template_msgids() if m not in strings)
    assert not missing, f"untranslated in {language}: {missing}"
