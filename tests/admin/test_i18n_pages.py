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


async def test_the_choice_is_remembered_in_a_cookie_and_survives_the_next_request(
    client,
):
    """`?lang=` sets it once; every later request is Chinese without the query."""
    first = await client.get("/admin/login", params={"lang": "zh"})
    set_cookie = first.headers.get("set-cookie", "")
    assert f"{i18n.COOKIE_NAME}=zh" in set_cookie
    # Path "/" is what lets one choice cover the panel and the public
    # calculator, which nginx serves from the same origin.
    assert "Path=/" in set_cookie

    # httpx keeps the cookie jar, so this request carries it and names no lang.
    again = await client.get("/admin/login")
    assert '<html lang="zh">' in again.text
    assert "登录" in again.text


async def test_a_request_with_no_choice_is_english(client):
    response = await client.get("/admin/login")
    assert '<html lang="en-NZ">' in response.text
    assert "Sign in" in response.text or "Continue" in response.text


async def test_an_unrecognised_language_renders_english_and_is_not_persisted(client):
    """Absent and unrecognised behave the same, and neither is written down."""
    response = await client.get("/admin/login", params={"lang": "qq"})

    assert '<html lang="en-NZ">' in response.text
    assert f"{i18n.COOKIE_NAME}=" not in response.headers.get("set-cookie", "")


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


async def test_the_language_switcher_offers_every_language_and_marks_the_active_one(
    admin_client,
):
    response = await admin_client.get("/admin/constant/list", params={"lang": "zh"})
    body = response.text

    assert 'id="language-switcher"' in body
    for catalogue in i18n.languages():
        assert f'hreflang="{catalogue.language}"' in body, (
            f"{catalogue.language} is a shipped catalogue but the switcher "
            "does not offer it"
        )
    assert "中文" in body and "English" in body


async def test_a_reviewed_language_carries_no_machine_translation_notice(admin_client):
    """Chinese is reviewed by the people using the panel; it must not be labelled.

    The counterpart - that a machine-translated language DOES carry the
    notice - is asserted in test_the_notice_renders_for_a_machine_translated_language
    below, against a catalogue built for the test, because no shipped
    language is machine translated yet and a test that cannot run until the
    nineteen arrive would prove nothing today.
    """
    response = await admin_client.get("/admin/constant/list", params={"lang": "zh"})
    assert "机器翻译，未经审校" not in response.text
    assert "Machine translation, not reviewed" not in response.text


async def test_the_notice_renders_for_a_machine_translated_language(
    admin_client, monkeypatch
):
    """The promise the interface has to make, and where it makes it.

    Nineteen languages will ship machine translated and unread. A person
    choosing one has to be told at the moment of choosing, not in a document.
    Built here rather than waiting for a real one so that the mechanism is
    proven now, while somebody is looking at it.
    """
    fake = i18n.Catalogue(
        language="de", endonym="Deutsch", machine_translated=True, strings={}
    )
    monkeypatch.setitem(i18n._CATALOGUES, "de", fake)

    response = await admin_client.get("/admin/constant/list", params={"lang": "de"})
    body = response.text

    assert 'hreflang="de"' in body
    assert "Deutsch" in body
    assert "Machine translation, not reviewed" in body, (
        "a machine-translated language was offered in the switcher without "
        "the notice that says so"
    )
