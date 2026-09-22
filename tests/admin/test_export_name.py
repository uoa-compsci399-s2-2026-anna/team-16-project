"""The name of the file a staff member downloads, in both languages.

**The defect this file exists for was invisible in English.** sqladmin builds
the download name as ``f"{self.name}_{timestamp}.{ext}"`` and then runs it
through ``sqladmin.helpers.secure_filename``, a Werkzeug port whose second
line is ``filename.encode("ascii", "ignore").decode("ascii")``. By the time an
export runs, ``admin/i18n.py::translate_view_names`` has replaced every view's
``name`` with a descriptor returning the **translated** string - which is the
whole point of it, and what puts 「供应链环节」 at the top of the page. On the
Chinese panel the entire title is therefore non-ASCII and is deleted:

    'Sector'     -> 'Sector_2026-09-22_16-54-02.csv'
    '供应链环节'  -> '2026-09-22_16-54-02.csv'      <- the title is gone

A staff member exporting three tables to look at side by side got three files
distinguishable only by the second they were downloaded in. So every assertion
here is made in Chinese as well as English; an English-only suite passes
against the defect and proves nothing.
"""

import re
from contextlib import contextmanager

import pytest

from admin import i18n

pytestmark = [pytest.mark.db]

#: `{identity}_{YYYY-MM-DD}_{HH-MM-SS}.{ext}`, anchored at both ends.
#:
#: The timestamp is part of the contract, not decoration: a staff member who
#: exports the same table twice in one session must get two files rather than
#: one silently overwritten by their browser.
_EXPECTED = re.compile(r"^(?P<stem>[a-z0-9-]+)_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$")


@contextmanager
def _rendering_in(language: str):
    """Render as the panel would for `language`, and put it back afterwards.

    The language is a ContextVar the whole session shares, and
    `i18n.set_language` returns the code it SET rather than the one it
    replaced - so the active value is read first and restored by hand. A test
    that left Chinese in force would translate the next test's fixtures and
    fail it somewhere else entirely.
    """
    previous = i18n.active_language()
    i18n.set_language(language)
    try:
        yield
    finally:
        i18n.set_language(previous)


def _live_admin(app):
    """sqladmin's own `Admin` instance, off the running app's route table.

    Reached through the routes rather than by importing the class, for the
    reason tests/admin/test_import_tables.py gives: a view that exists and was
    never registered would satisfy an import-based check while being
    unreachable from a browser.
    """
    for route in app.routes:
        for sub in getattr(getattr(route, "app", None), "routes", []):
            endpoint = getattr(sub, "endpoint", None)
            if endpoint is not None and hasattr(endpoint, "__self__"):
                return endpoint.__self__
    raise AssertionError("sqladmin's Admin instance was not reachable")


def _exporting(app):
    """Every registered ModelView whose Export control is on.

    `_views` also holds the panel's own pages - everything registered with
    `add_base_view` rather than `add_view`: Getting started, the dry run, the
    comparison, the deployment view and the login gauntlet's screens. They
    have no model and no export. Filtered by `isinstance` rather than by
    `getattr(view, "can_export", False)`, so that a ModelView which somehow
    lost the attribute fails here rather than being silently excluded from
    every assertion below.
    """
    from sqladmin import ModelView

    views = [
        view
        for view in _live_admin(app)._views
        if isinstance(view, ModelView) and view.can_export
    ]
    assert len(views) == 19, (
        "the panel registers a different number of exporting screens than "
        f"this file was written against ({len(views)} now); the count is "
        "pinned so that a screen gained or lost is a decision somebody makes "
        "rather than a drift"
    )
    return views


@pytest.mark.parametrize("language", ["en", "zh"])
def test_every_exporting_screen_names_its_file_after_the_table(admin_app, language):
    """Walked off the registered views, not a list of the ones known to break.

    Every one of them, in both languages, because the defect is a property of
    the *name* being translated rather than of any one screen - a view added
    later inherits it without anybody touching this file.
    """
    with _rendering_in(language):
        for view in _exporting(admin_app):
            for export_type in view.export_types:
                name = view.get_export_name(export_type=export_type)
                stem, _, extension = name.rpartition(".")
                assert extension == export_type, f"{view.identity}: {name!r}"
                match = _EXPECTED.match(stem)
                assert match, (
                    f"{view.identity} exports as {name!r} in {language}, which "
                    f"is not '<table>_<date>_<time>.{export_type}'"
                )
                assert match.group("stem") == view.identity, (
                    f"{view.identity} exports as {name!r} in {language}; the "
                    f"file does not say which table it came from"
                )


@pytest.mark.parametrize("language", ["en", "zh"])
def test_the_name_survives_the_ascii_filter_it_is_about_to_be_passed_through(
    admin_app, language
):
    """`secure_filename` is the thing that deleted the title, and it stays.

    A filename travels in `Content-Disposition` and then onto a staff member's
    filesystem; a non-ASCII one there is a bet on how the client decodes it.
    The fix was never to defeat the filter - it was to stop feeding a
    translated string into it. So this asserts the filter is a no-op on what
    the panel now produces, in the language where it used to be destructive.
    """
    from sqladmin.helpers import secure_filename

    with _rendering_in(language):
        for view in _exporting(admin_app):
            name = view.get_export_name(export_type="csv")
            assert name.isascii(), f"{view.identity} in {language}: {name!r}"
            assert secure_filename(name) == name, (
                f"{view.identity}'s export name is altered by the ASCII filter "
                f"in {language}: {name!r} -> {secure_filename(name)!r}"
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("identity", ["sector", "audit-log"])
async def test_the_downloaded_file_is_named_on_a_real_response(
    admin_client, identity, language
):
    """The header a browser actually reads, on the Chinese panel too.

    The tests above call `get_export_name` directly. This one goes through the
    route, because the translated `name` is installed on the view *class* at
    application build time and only a real request proves that the language in
    force while a response is built is the one the filename was built from.

    **Both export paths.** `sector` wears `AuditedImport`, whose own
    `export_data` (admin/importing.py) replaces sqladmin's pair so that the
    file it writes is the file the import reads; `audit-log` does not, and
    goes through sqladmin's `_export_csv`. They call `secure_filename` in
    different modules, so a repair that reached only one of them would still
    lose the title on the five screens that do not wear it.
    """
    response = await admin_client.get(
        f"/admin/{identity}/export/csv", params={"lang": language}
    )
    assert response.status_code == 200

    disposition = response.headers["content-disposition"]
    filename = disposition.split("filename=", 1)[1].strip('"')
    assert filename.startswith(f"{identity}_"), (
        f"the downloaded file is called {filename!r} in {language}; it does "
        f"not say which table it came from"
    )
    assert _EXPECTED.match(filename.removesuffix(".csv")), filename


@pytest.mark.parametrize("language", ["en", "zh"])
def test_the_table_name_on_the_page_is_still_translated(admin_app, language):
    """The repair must not have been made by un-translating the view.

    Reverting `translate_view_names` would make every filename ASCII and pass
    every assertion above, at the cost of an English page heading and an
    English navigation menu on the Chinese panel. So the property that caused
    the defect is asserted to still hold.
    """
    with _rendering_in(language):
        sector = next(v for v in _exporting(admin_app) if v.identity == "sector")
        expected = "供应链环节" if language == "zh" else "Sector"
        assert sector.name == expected
