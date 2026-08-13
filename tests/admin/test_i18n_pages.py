"""What a rendered page actually contains, in the language that was asked for.

**test_i18n.py proves the catalogue is complete. This file proves it reaches
the browser**, which is a different claim and the one this project has been
burned on. Six defects reached the repository owner through green markup
tests: 87 field descriptions asserted present while four were invisible
behind a widget's CSS class, five guidance blocks asserted present while
absent from the built image. A catalogue can be complete and a page can still
render English, because there are four Jinja environments here and a
translation only arrives on a page whose environment had the callables
installed.

Every assertion below names a specific string AND the specific element it has
to appear in. "The page contains Chinese characters" would pass against a
page with one translated word in it and is not written anywhere in this file.
"""

import re

import pytest

from admin import i18n

pytestmark = [pytest.mark.asyncio, pytest.mark.db]

#: The description ConstantAdmin puts on `unit`. Chosen because it is short,
#: it is unambiguous, and it is rendered through sqladmin's `_macros.html` -
#: the vendored copy - so an assertion on it is an assertion that the copy is
#: in force and being found ahead of the installed package's own.
UNIT_HELP_ZH = "这个值的计量单位，供下一个看这个屏幕的人参考。系统不会用它做任何计算。"


async def test_the_login_page_renders_in_chinese_when_asked(client):
    """The first screen, and the one that has to work before any session does.

    A session cannot carry a language to a page that renders before the
    session exists, which is the whole argument for the cookie in
    admin/i18n.py. This is that argument, asserted.
    """
    response = await client.get("/admin/login", params={"lang": "zh"})

    assert response.status_code == 200
    body = response.text
    # The document language, not just the words. A screen reader reads a
    # Chinese page announced as en-NZ in English phonetics.
    assert '<html lang="zh">' in body
    assert "登录" in body


async def test_the_browser_s_own_language_is_honoured_without_any_query_string(
    client,
):
    """The whole of change 1: no picker, no cookie, no query string.

    A browser that says it prefers Chinese gets Chinese on the first request
    it makes, on a URL that carries nothing at all.
    """
    response = await client.get(
        "/admin/login", headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
    )

    assert response.status_code == 200
    assert '<html lang="zh">' in response.text
    assert "登录" in response.text


async def test_quality_values_decide_which_language_wins(client):
    """`zh;q=0.8, en;q=0.9` is a request for English, in that written order.

    A parser that reads the header left to right gets this backwards, which
    is why the header is ranked rather than split.
    """
    response = await client.get(
        "/admin/login", headers={"Accept-Language": "zh;q=0.8, en;q=0.9"}
    )
    assert '<html lang="en-NZ">' in response.text


async def test_an_unsupported_first_tag_renders_english_on_the_page(client):
    """The rule, on a rendered page rather than only in the negotiator.

    `fr` has no panel catalogue, and the `zh` behind it does not get a turn:
    a browser's second and third entries are frequently residue rather than a
    second language, and English is the floor everyone who reaches this panel
    can read.

    **The second half is what makes the first half evidence.** A panel that
    had lost the ability to render Chinese at all would satisfy the English
    assertion and fail here.
    """
    english = await client.get(
        "/admin/login", headers={"Accept-Language": "fr-CA,fr;q=0.9,zh;q=0.8"}
    )
    assert '<html lang="en-NZ">' in english.text
    assert "登录" not in english.text

    chinese = await client.get(
        "/admin/login", headers={"Accept-Language": "zh-CN,fr;q=0.9,en;q=0.8"}
    )
    assert '<html lang="zh">' in chinese.text
    assert "登录" in chinese.text


async def test_nothing_is_persisted_and_the_next_request_negotiates_again(client):
    """The cookie is gone. A language is a property of a request, not of a browser.

    `kaicalc_lang` was written by `?lang=` and read on every later request.
    Both halves are asserted absent: no Set-Cookie on the way out, and a
    second request with no header renders English even though the first one
    forced Chinese.
    """
    forced = await client.get("/admin/login", params={"lang": "zh"})
    assert '<html lang="zh">' in forced.text
    assert "kaicalc_lang" not in forced.headers.get("set-cookie", "")

    again = await client.get("/admin/login")
    assert '<html lang="en-NZ">' in again.text


async def test_every_response_says_it_varies_by_accept_language(client):
    """Without this, a shared cache serves one visitor's Chinese page to the next.

    Asserted on a plain English response as well as a translated one: a cache
    keys on what it was told varies, and the English entry stored without it
    is the one that gets handed back to a Chinese-speaking browser.
    """
    for params in ({}, {"lang": "zh"}):
        response = await client.get("/admin/login", params=params)
        vary = response.headers.get("vary", "")
        assert "accept-language" in vary.lower(), (
            f"no Vary: Accept-Language on /admin/login with params={params!r}; "
            f"got {vary!r}"
        )


async def test_a_request_with_no_choice_and_no_header_is_english(client):
    response = await client.get("/admin/login")
    assert '<html lang="en-NZ">' in response.text
    assert "Sign in" in response.text or "Continue" in response.text


async def test_an_unrecognised_lang_is_ignored_and_the_header_still_decides(client):
    """`?lang=qq` is not an error and not a veto - it simply is not there.

    The request then negotiates as though the parameter had been absent,
    which is the only behaviour that does not make a typo in a support email
    look like a broken panel.
    """
    response = await client.get(
        "/admin/login",
        params={"lang": "qq"},
        headers={"Accept-Language": "zh-CN,zh;q=0.9"},
    )

    assert '<html lang="zh">' in response.text
    assert "kaicalc_lang" not in response.headers.get("set-cookie", "")


async def test_a_field_description_renders_translated_inside_its_own_element(
    admin_client,
):
    """The 82 field descriptions - the reason this work was asked for.

    Asserted INSIDE `<small class="text-muted">`, which is the element
    sqladmin's macro puts a description in. Asserting only that the string is
    somewhere on the page would pass if it rendered into a title attribute,
    into a comment, or into a hidden element - which is exactly the shape of
    the defect where four descriptions were present in the markup and
    invisible on the screen.
    """
    response = await admin_client.get("/admin/constant/create", params={"lang": "zh"})
    assert response.status_code == 200

    descriptions = re.findall(
        r'<small class="text-muted">(.*?)</small>', response.text, re.S
    )
    rendered = [d.strip() for d in descriptions]
    assert UNIT_HELP_ZH in rendered, (
        "the Chinese help text for `unit` is not inside a <small "
        "class='text-muted'> on the constant create form. Either the vendored "
        "_macros.html is not being found ahead of sqladmin's own, or the "
        f"catalogue entry has drifted. Found: {rendered}"
    )


async def test_the_english_form_is_unchanged_when_no_language_is_chosen(admin_client):
    """Translation must not be something a panel gets whether it asked or not."""
    response = await admin_client.get("/admin/constant/create")
    assert "What the value is measured in" in response.text
    assert UNIT_HELP_ZH not in response.text


async def test_the_navigation_menu_is_translated(admin_client):
    """Menu labels come from sqladmin's Menu, built once at start-up.

    They have no per-request hook other than the vendored macro, so this is
    the assertion that the macro's menu edits are in force - a panel whose
    forms are Chinese and whose navigation is English is half-translated in
    the most visible way there is.
    """
    response = await admin_client.get("/admin/constant/list", params={"lang": "zh"})
    body = response.text

    for english, chinese in (
        ("Factors", "因子"),
        ("Taxonomy", "分类体系"),
        ("Constants", "常量"),
    ):
        assert (
            f'<span class="nav-link-title">{chinese}</span>' in body
        ), f"menu entry {english!r} did not render as {chinese!r}"


async def test_sqladmin_s_own_chrome_is_translated_on_a_real_page(admin_client):
    """"New", "Search", the pagination line - strings inside the installed package.

    This is the assertion that `install_gettext_callables` actually replaced
    sqladmin's null translations. Nothing in this repository can be edited to
    translate these; if the seam stops working, they silently revert to
    English and only this test notices.
    """
    response = await admin_client.get("/admin/constant/list", params={"lang": "zh"})
    body = response.text

    assert "搜索" in body, "sqladmin's own Search control did not translate"
    assert "新建常量" in body, (
        "sqladmin's `New %(name)s` did not translate, or its %(name)s "
        "interpolation broke - both would show here"
    )


async def test_the_page_heading_composed_from_a_view_name_is_translated(admin_client):
    """`New Constant` and `Constants` - the biggest text on each screen.

    sqladmin builds the create heading as `_("New %(name)s",
    name=model_view.name)`: it translates the sentence and interpolates the
    model name into it *untranslated*, so a complete catalogue still renders
    "新建Constant" without admin/i18n.py's `_TranslatedAttribute`. The list
    heading is `{{ model_view.name_plural }}` with no `_()` around it at all.

    Anchored on the `card-title` element and on both headings, because
    removing `translate_view_names` from create_app leaves the navigation
    menu correct - that is translated by the vendored macro instead - and a
    test that only looked at the menu would pass against a panel whose every
    page heading had reverted to English.
    """
    create = await admin_client.get("/admin/constant/create", params={"lang": "zh"})
    assert '<h3 class="card-title">新建常量</h3>' in create.text

    listing = await admin_client.get("/admin/constant/list", params={"lang": "zh"})
    assert '<h3 class="card-title">常量</h3>' in listing.text


async def test_no_language_picker_is_rendered_anywhere(admin_client):
    """The owner asked for no control, and a control is what this removes.

    Anchored on the element the switcher had and on the query string it
    emitted, so re-adding either fails here rather than being noticed in a
    screenshot.
    """
    response = await admin_client.get("/admin/constant/list", params={"lang": "zh"})
    body = response.text

    assert 'id="language-switcher"' not in body
    assert "hreflang=" not in body


async def test_a_reviewed_language_carries_no_machine_translation_notice(admin_client):
    """Chinese is reviewed by the people using the panel; it must not be labelled.

    The counterpart - that a machine-translated language DOES carry the
    notice - is asserted below against a catalogue built for the test,
    because no language the panel ships is machine translated and a test that
    cannot run until one arrives would prove nothing today.
    """
    response = await admin_client.get("/admin/constant/list", params={"lang": "zh"})
    assert 'id="machine-translation-notice"' not in response.text


def _machine_translated_german(monkeypatch):
    fake = i18n.Catalogue(
        language="de",
        endonym="Deutsch",
        machine_translated=True,
        strings={},
        tags=("de",),
    )
    monkeypatch.setitem(i18n._CATALOGUES, "de", fake)
    monkeypatch.setitem(i18n._TAG_INDEX, "de", "de")


async def test_the_notice_renders_for_a_machine_translated_language(
    admin_client, monkeypatch
):
    """The promise the interface has to make, and where it now makes it.

    It used to sit on the switcher's option, where somebody was choosing.
    With no switcher there is nothing to choose and nowhere to hang it, so it
    is a strip on the page itself - the only surface a reader of a
    machine-translated page is guaranteed to be looking at.
    """
    _machine_translated_german(monkeypatch)

    response = await admin_client.get("/admin/constant/list", params={"lang": "de"})
    body = response.text

    assert 'id="machine-translation-notice"' in body
    assert "machine translated and has not been reviewed" in body


async def test_the_notice_reaches_the_pages_that_render_before_a_session(
    client, monkeypatch
):
    """The login gauntlet uses a different base template and a different Jinja
    environment - which is precisely how a block reaches every screen the
    tests look at and none of the ones they do not.
    """
    _machine_translated_german(monkeypatch)

    response = await client.get("/admin/login", params={"lang": "de"})
    assert 'id="machine-translation-notice"' in response.text
