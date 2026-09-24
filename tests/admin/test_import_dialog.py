"""The file format's two ends held together, and the dialog that drives them.

Two things this file is for, and they are the same thing seen from either end:

* **Every importable screen exports what it imports.** Not the six that happen
  to be tested by name elsewhere — all fourteen, walked off the registered
  views, because the failure is silent and per screen: `get_export_columns`
  falls back to `column_list` and `get_import_columns` to `column_import_list`,
  and a screen where those differ serves a file `parse_csv` refuses to read.
* **The dialog can actually send one.** The markup and the script, asserted at
  the level a server-side test can reach; `tests/admin/
  test_import_dialog_browser.py` drives the rest in a real browser, because
  "the attribute is in the DOM" is not evidence that dropping a file does
  anything.
"""

import csv
import io

import pytest

from admin.importing import AuditedImport

#: `asyncio` is applied per test rather than to the file: two of the tests
#: below read the registered views straight off the app and are synchronous,
#: and marking a synchronous test with it is a warning on every run.
pytestmark = [pytest.mark.db]


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


def _importable(app):
    views = [view for view in _live_admin(app)._views
             if isinstance(view, AuditedImport)]
    assert len(views) == 14, (
        "the plan puts the import on fourteen screens and this panel "
        f"registers {len(views)}; the count is pinned so that a screen gained "
        "or lost is a decision somebody makes rather than a drift"
    )
    return views


# --- 1. what a screen exports is what it imports ---------------------------


def test_every_importable_screen_exports_the_columns_it_imports(admin_app):
    """The round trip's first precondition, on all fourteen at once.

    `parse_csv` refuses a file missing **any** import column, before a row is
    looked at — so a screen whose export leaves one out serves a file its own
    import cannot read, and says so in a message about a column the person
    never chose. Before WP4 that was eight of the fourteen.

    Asserted as equality rather than as a subset. A column in the export and
    not in the import is a column somebody edits in a spreadsheet and watches
    be ignored, which is the same defect with the cost moved.
    """
    for view in _importable(admin_app):
        assert view._export_prop_names == view._import_prop_names, (
            f"{type(view).__name__} exports {view._export_prop_names} and "
            f"imports {view._import_prop_names}. A file it exports cannot be "
            "read back by the screen that wrote it."
        )


def test_no_importable_column_is_a_shape_the_file_format_cannot_write(
    admin_app
):
    """`export_cell` writes a string, a boolean or an empty cell, and nothing
    else — which is a claim about the fourteen tables, not about Python.

    An ``Enum`` column would be the sharp case. `get_prop_value` converts a
    non-``StrEnum`` member to its **name**, while `coerce_column_value`
    reconstructs one from its **value**, so an enum column would export one
    spelling and import the other — a round trip that fails or, worse, one
    that silently lands a different member. A ``date`` or ``datetime`` would
    need an agreed format on both sides.

    None of the fourteen has one today. This is what makes that a fact rather
    than an assumption, so that a screen which gains such a column fails here
    — with this docstring next to the failure — rather than exporting
    something that cannot be read back.
    """
    import datetime
    import enum

    for view in _importable(admin_app):
        columns = view.model.__table__.columns
        for name in view._import_prop_names:
            column = columns.get(name)
            if column is None:
                continue  # a relationship; written as the referenced `code`
            try:
                python_type = column.type.python_type
            except NotImplementedError:  # pragma: no cover
                continue
            assert not issubclass(python_type, enum.Enum), (
                f"{view.model.__table__.name}.{name} is an enum column and is "
                "imported. Read `export_cell`'s docstring before adding one: "
                "the export and the import spell an enum member differently."
            )
            assert not issubclass(
                python_type, (datetime.date, datetime.datetime, datetime.time)
            ), (
                f"{view.model.__table__.name}.{name} is a date/time column and "
                "is imported. The file format has no agreed spelling for one."
            )


@pytest.mark.asyncio
async def test_every_importable_screen_serves_both_formats(admin_client, admin_app):
    """Both spellings, on every screen, over real HTTP.

    The CSV header is the assertion because it is the one thing a file with no
    rows in it still carries — and because it is exactly what `parse_csv`
    refuses a file for. JSON is asserted to answer at all: its shape with rows
    in it is driven by tests/admin/test_import_round_trip.py.
    """
    for view in _importable(admin_app):
        identity = view.identity
        exported = await admin_client.get(f"/admin/{identity}/export/csv")
        assert exported.status_code == 200, (
            f"the {identity} screen would not export: {exported.text[:200]}"
        )
        header = next(csv.reader(io.StringIO(exported.text)))
        assert header == list(view._import_prop_names), (
            f"the {identity} export's header is {header}; its import requires "
            f"{list(view._import_prop_names)}"
        )

        as_json = await admin_client.get(f"/admin/{identity}/export/json")
        assert as_json.status_code == 200, (
            f"the {identity} screen would not export JSON: {as_json.text[:200]}"
        )
        assert as_json.headers["content-type"].startswith("application/json")


# --- 2. the dialog ---------------------------------------------------------


@pytest.mark.asyncio
async def test_the_dialog_takes_both_formats_in_one_control(admin_client):
    """One dialog, both files. The shipped input is `accept="text/csv"` and
    the shipped heading says "Import CSV"; this panel takes both on one path,
    so the control has to offer both or half the feature is unreachable."""
    page = (await admin_client.get("/admin/sector/list")).text

    assert 'id="kaicalc-import-file"' in page, "the dialog has no file input"
    accept = page.split('id="kaicalc-import-file"', 1)[1].split(">", 1)[0]
    assert ".csv" in accept and ".json" in accept, (
        f"the file control offers only some of the formats this screen takes: "
        f"{accept}"
    )


@pytest.mark.asyncio
async def test_the_list_page_s_import_button_does_not_promise_one_format(admin_client):
    """The button is the whole of what a staff member sees before the dialog.

    sqladmin labels it `_("Import CSV")`. This screen takes a `.csv` OR a
    `.json` file on one path, so that label tells somebody holding a JSON file
    that this is not the place for it - and the dialog behind it, which says
    so correctly, is never opened. `admin/i18n.py::_ImportButton` rewrites the
    label in sqladmin's own compiled template; this is the assertion that the
    rewrite reaches a real page rather than only a `preprocess` call.

    Anchored on the element, not on the words: "Import" appears in the modal's
    own heading and in its submit button too, and an assertion that only
    looked for the string would pass against a page whose button still said
    CSV.
    """
    page = (await admin_client.get("/admin/sector/list")).text

    assert 'data-bs-target="#modal-import">Import</a>' in page, (
        "the list page's Import control does not render the rewritten label; "
        "either admin/i18n.py::_ImportButton is not installed in sqladmin's "
        "Jinja environment, or sqladmin has changed the markup it is matched "
        "against"
    )
    assert "Import CSV" not in page, (
        "the page still promises CSV only somewhere, on a screen that takes "
        "both formats"
    )


@pytest.mark.asyncio
async def test_the_dialog_is_wired_for_drag_and_drop(admin_client):
    """`dragover`, `drop` and `dragenter` appear **zero** times in what
    sqladmin ships; it is a plain `<input type="file">`.

    All three, because dropping a file does nothing at all unless `dragover`
    is cancelled — the browser navigates to the file instead, replacing the
    panel with the contents of a CSV. A drop handler without it is the most
    common way a hand-written drop zone silently does nothing.
    """
    script = await admin_client.get("/admin/static/import.js")
    assert script.status_code == 200, "the dialog's script is not served"
    for event in ("dragenter", "dragover", "drop"):
        assert f'"{event}"' in script.text or f"'{event}'" in script.text, (
            f"the dialog binds no {event} handler, so a dropped file is "
            "either ignored or navigated to"
        )
    assert "preventDefault" in script.text, (
        "nothing cancels the browser's own handling of a dropped file"
    )
    assert 'id="kaicalc-import-drop"' in (
        await admin_client.get("/admin/sector/list")
    ).text, "the page renders no drop target"


@pytest.mark.asyncio
async def test_the_dialog_previews_before_it_writes(admin_client):
    """The preview is not optional and the confirmation is not pre-given.

    Upsert is what makes this necessary rather than pleasant: a mistyped
    `code` is silently a **new row** instead of a correction, and nothing in
    the result of a successful import distinguishes those two. The confirm
    control therefore starts hidden — a dialog that rendered it live would let
    a visitor write a file they had not looked at.
    """
    page = (await admin_client.get("/admin/sector/list")).text
    script = (await admin_client.get("/admin/static/import.js")).text

    confirm_cell = page.split('id="kaicalc-import-confirm-cell"', 1)
    assert len(confirm_cell) == 2, "the dialog renders no confirm control"
    before = confirm_cell[0].rsplit("<div", 1)[-1]
    assert "d-none" in before, (
        "the dialog's confirm button is visible before anything has been "
        f"previewed: {before!r}"
    )
    assert "X-Dry-Run" in script, (
        "the dialog never asks the panel what the file would do, so the "
        "preview it draws is either invented or absent"
    )


@pytest.mark.asyncio
async def test_the_dialog_sends_the_token_and_the_mode_as_ordinary_fields(
    admin_client
):
    """What WP1's `window.fetch` wrapper was standing in for.

    The wrapper existed because sqladmin's `main.js` builds its `FormData` by
    hand, so a hidden field was in the DOM and in no request. This dialog
    builds its own body, which means the token and the mode are ordinary
    fields in it — the same thing every other form in this panel sends, with
    nothing global patched to get them there.
    """
    script = (await admin_client.get("/admin/static/import.js")).text

    assert "new FormData()" in script, (
        "the dialog does not build its own request body, so the reason WP1's "
        "wrapper existed has not gone away"
    )
    for field in ('"csvfile"', '"csrf_token"', '"import_mode"'):
        assert f"append({field}" in script, (
            f"the dialog's request body carries no {field} field"
        )
    assert "window.fetch =" not in script and "window.fetch=" not in script, (
        "the dialog replaces `window.fetch`; that wrapper was WP1's stopgap "
        "and this dialog exists to make it unnecessary"
    )
