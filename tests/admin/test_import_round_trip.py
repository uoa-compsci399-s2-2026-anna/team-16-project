"""Export a table, upload exactly those bytes back, and nothing changes.

**This is the workflow the whole import feature exists for** — the client
exports, corrects a figure in a spreadsheet, and uploads the file again — and
until this file existed nobody had ever driven it. WP1 proved the import was
audited, WP2 proved what a spreadsheet does to a number, WP3 proved the
fourteen tables and the draft rule, WP3b proved the upsert and the preview.
All four drove files a *test* wrote. A file the *panel* wrote was refused, for
two reasons neither of them could see:

1. **The exported file was missing columns the import requires.** Export falls
   back to `column_list` and the import is `form_columns`; on eight of the
   fourteen screens those differ, so `parse_csv` refused the file outright
   with "missing required column(s)". On `equivalence` the missing column
   (`label_template`) is NOT NULL, so there was no correcting it by hand
   either without knowing the schema.
2. **Every foreign-key cell was written as `__str__`** — `"code — name"` —
   and the import wants the bare `code`. So a file off the Unit presets screen
   named a food category nothing answers to, on every row that had one.

Both are properties of the *file*, which is why they are tested by making the
panel produce one and handing it straight back. A test that built its own CSV
could not have caught either, and four files of them did not.

**The assertion is that the table is unchanged**, read out of the database
with SQL on both sides rather than through the code that wrote it. Not "the
response was 200", not "the row count is the same": every cell of every row of
the whole table, before and after.

Both formats, because **a file format has two ends and they must be defined in
one place.** `export_data` already took an `export_type` and sqladmin already
shipped `_export_json`, so JSON was never only an import format to be added —
it was an existing export whose round trip was broken in exactly the same way.
Fixing CSV and leaving JSON broken would have been two truths evolving apart
in a new costume.
"""

import csv
import io
import json
import re
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from admin.models import AuditLog
from admin.taxonomy_models import FoodCategory, UnitPreset

pytestmark = [pytest.mark.db, pytest.mark.asyncio]

#: Every row this file creates carries this in its `code`, and the teardown
#: sweeps on it — the same reasoning as `wp1_`, `wp2_` and `wp3_` in the four
#: files before it. A stray `unit_preset` or `food_category` row is not litter
#: in a test database: both appear in the public calculator.
_PREFIX = "wp4_"

#: The screen the round trip is driven on, and why this one.
#:
#: `unit_preset` carries, in five columns, every shape that was broken:
#: a foreign key that is **nullable** (`food_category` — blank means "every
#: food category"), a `DECIMAL`, a nullable free-text column that is in the
#: *form* and was not in the *export* (`source_note`), and a boolean. It also
#: carries no draft-only rule, so the file it produces is one this panel will
#: take back without a factor set having to be in any particular state.
_IDENTITY = "unit-preset"


@pytest.fixture
def session(_committed_session):
    """This file's own name for tests/admin/conftest.py's hard-committing
    session — see that module's `_committed_session` docstring."""
    return _committed_session


@pytest.fixture(autouse=True)
def _clean_rows(admin_app):
    """Remove this file's own rows and their audit entries, before and after.

    Before as well as after: `code` is unique, so a run interrupted partway
    through would leave the next run's first upload failing for a reason that
    has nothing to do with what it asserts.
    """

    def sweep() -> None:
        factory = admin_app.state.session_factory
        with factory() as db:
            for table in ("unit_preset", "food_category"):
                ids = db.execute(
                    text(f"SELECT id FROM {table} WHERE code LIKE :like"),
                    {"like": _PREFIX + "%"},
                ).scalars().all()
                for row_id in ids:
                    db.execute(
                        text("DELETE FROM audit_log WHERE table_name = :t "
                             "AND row_id = :id"),
                        {"t": table, "id": row_id},
                    )
                db.execute(
                    text(f"DELETE FROM {table} WHERE code LIKE :like"),
                    {"like": _PREFIX + "%"},
                )
            db.commit()

    sweep()
    yield
    sweep()


@pytest.fixture
def seeded(session):
    """Three unit presets covering every cell shape the export writes.

    Committed through the real models rather than raw inserts, because the
    thing under test is what the panel makes of a real row.
    """
    category = FoodCategory(code=f"{_PREFIX}cat", name="WP4 category")
    session.add(category)
    session.flush()
    session.add_all([
        # A null foreign key and a null free-text cell: the two places a
        # naive export writes the string "None" into the file, which comes
        # back as a food category nothing answers to and as the four
        # characters N-o-n-e stored in a note.
        UnitPreset(code=f"{_PREFIX}plain", label="WP4 plain",
                   food_category_id=None, kg_per_unit=Decimal("12.5000"),
                   source_note=None, active=True),
        # A foreign key that is set, a note carrying the two characters CSV
        # has to quote, and active false.
        UnitPreset(code=f"{_PREFIX}linked", label="WP4 linked",
                   food_category_id=category.id,
                   kg_per_unit=Decimal("0.0001"),
                   source_note='a note, with a comma and "quotes"',
                   active=False),
        # The far end of decimal(12,4): eight digits before the point and
        # four after, which is every digit the column can hold.
        #
        # `source_note` is NULL here and not `""` deliberately - see
        # `test_a_round_trip_turns_an_empty_string_into_null` below for the one
        # thing a round trip does change and why a text file cannot do
        # otherwise.
        UnitPreset(code=f"{_PREFIX}wide", label="WP4 wide",
                   food_category_id=category.id,
                   kg_per_unit=Decimal("87654321.4321"),
                   source_note=None, active=True),
    ])
    session.commit()
    return category


def _table(admin_app, table: str) -> dict:
    """Every row of a table, every column, straight out of MySQL.

    Read with SQL rather than through the ORM or through the view: the
    question this file asks is what is *in the database* after a file the
    panel wrote went back into it, and reading it through any of the code
    that put it there would make a matched pair of bugs invisible.
    """
    factory = admin_app.state.session_factory
    with factory() as db:
        rows = db.execute(text(f"SELECT * FROM {table} ORDER BY id")).mappings().all()
    return {row["id"]: dict(row) for row in rows}


async def _token(client, identity: str) -> str:
    """The CSRF token this session's list page renders for the import dialog.

    Read off the page a visitor would actually be on, never minted directly:
    the token only reaches a browser if the template renders it, and a test
    that built its own would pass with the field gone from the page.
    """
    page = await client.get(f"/admin/{identity}/list")
    assert page.status_code == 200, f"the {identity} list page did not render"
    match = re.search(r'id="kaicalc-import-csrf" value="([^"]+)"', page.text)
    assert match, (
        f"the {identity} list page rendered no CSRF token for the import "
        "dialog, so nothing a browser sends could ever carry one"
    )
    return match.group(1)


async def _post(client, identity: str, content: bytes, *, token, filename,
                content_type, mode=None, dry_run=False):
    data = {"csrf_token": token}
    if mode is not None:
        data["import_mode"] = mode
    headers = {"X-Dry-Run": "true"} if dry_run else {}
    return await client.post(
        f"/admin/{identity}/import",
        files={"csvfile": (filename, content, content_type)},
        data=data,
        headers=headers,
    )


def _result(response) -> dict:
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    finals = [event for event in events if event.get("type") == "result"]
    assert finals, f"the import stream carried no result event: {response.text!r}"
    return finals[-1]


def _entries(admin_app, actor: str) -> list[AuditLog]:
    factory = admin_app.state.session_factory
    with factory() as db:
        return list(db.scalars(
            select(AuditLog).where(AuditLog.actor == actor).order_by(AuditLog.id)
        ).all())


# --- 1. the round trip, both formats ---------------------------------------


@pytest.mark.parametrize(
    "export_type, filename, content_type",
    [
        ("csv", "unit_presets.csv", "text/csv"),
        ("json", "unit_presets.json", "application/json"),
    ],
)
async def test_a_file_this_panel_exported_re_imports_and_changes_nothing(
    admin_client, admin_app, seeded, export_type, filename, content_type
):
    """The whole point of the feature, driven end to end and asserted on the
    database.

    Exactly the bytes the export served are posted back at the import. Nothing
    is edited in between — not the header, not a key cell, not a quote — which
    is the promise: a staff member corrects one figure in a spreadsheet and
    everything they did not touch survives the journey.

    **The assertion is the whole table, cell by cell**, including the rows this
    file did not create. A round trip that quietly rewrote another row's
    `source_note` to "None" would pass any assertion narrowed to the rows under
    test.
    """
    before = _table(admin_app, "unit_preset")
    assert before, "the unit_preset table is empty, so this proves nothing"

    export = await admin_client.get(f"/admin/{_IDENTITY}/export/{export_type}")
    assert export.status_code == 200, export.text
    content = export.content
    assert content, "the export served an empty body"

    token = await _token(admin_client, _IDENTITY)
    response = await _post(admin_client, _IDENTITY, content, token=token,
                           filename=filename, content_type=content_type)

    assert response.status_code == 200, (
        f"a {export_type.upper()} file this panel exported was refused by its "
        f"own import: {response.text}"
    )
    result = _result(response)
    assert result["imported"] == len(before), (
        f"the export carried {len(before)} rows and the import took "
        f"{result['imported']}: {result.get('summary')}"
    )

    after = _table(admin_app, "unit_preset")
    assert after == before, (
        "a file this panel exported did not come back as the table it was "
        "exported from"
    )


@pytest.mark.parametrize("export_type", ["csv", "json"])
async def test_a_round_trip_updates_every_row_and_creates_none(
    admin_client, admin_app, seeded, export_type
):
    """Read out of `audit_log`, because the counts are the thing a mistyped
    `code` shows up in.

    sqladmin's persistence is insert-only and WP3b replaced it with an upsert
    on the natural key. If the export wrote a key cell in a form the upsert
    cannot match — which is exactly what `"code — name"` is — every row of the
    file is a *new* row rather than a correction, and the table still ends up
    with the right values in it because the originals are untouched. So
    "nothing changed" is necessary and not sufficient: the trail has to say
    `update`, never `create`.
    """
    before = _table(admin_app, "unit_preset")
    export = await admin_client.get(f"/admin/{_IDENTITY}/export/{export_type}")
    assert export.status_code == 200, export.text

    token = await _token(admin_client, _IDENTITY)
    response = await _post(
        admin_client, _IDENTITY, export.content, token=token,
        filename=f"unit_presets.{export_type}",
        content_type="text/csv" if export_type == "csv" else "application/json",
    )
    assert response.status_code == 200, response.text

    actions = [entry.action for entry in _entries(admin_app, admin_client.staff.username)]
    assert actions[0] == "import", (
        f"the file-level audit entry is missing; the trail reads {actions}"
    )
    assert "create" not in actions[1:], (
        "a file this panel exported was read as new rows rather than as "
        f"corrections to the ones it came from; the trail reads {actions}"
    )
    assert len(_table(admin_app, "unit_preset")) == len(before), (
        "the round trip added rows to the table it exported"
    )


async def test_a_round_trip_turns_an_empty_string_into_null(
    admin_client, admin_app, seeded, session
):
    """**The one thing a round trip changes, stated rather than discovered.**

    A file has one spelling for "this cell is blank". `merge_import_row_data`
    reads an empty cell in a nullable column as NULL — sqladmin's own rule,
    on the shipped path this panel deliberately does not fork — so a column
    holding the empty string comes back holding NULL.

    It is invisible everywhere it could be read: both render blank on the list
    page, on the details page and in every API response, and the difference
    survives no export. It is not invisible in `audit_log`, which records it as
    an update, and it is not free to pretend about — so it is asserted here, on
    the value the panel's own create form does produce for an empty text box.

    **This cannot be fixed on either end.** Writing NULL and `""` differently
    in the file would mean inventing a spelling that only this panel
    understands, and it would mean the CSV and the JSON of one table no longer
    saying the same thing — which is the failure this whole package exists to
    close. The place to fix it, if it is ever worth fixing, is the create form
    that stores `""` in a nullable column in the first place.
    """
    session.add(UnitPreset(code=f"{_PREFIX}blankish", label="WP4 blankish",
                           food_category_id=None,
                           kg_per_unit=Decimal("1.0000"),
                           source_note="", active=True))
    session.commit()

    export = await admin_client.get(f"/admin/{_IDENTITY}/export/csv")
    assert export.status_code == 200, export.text
    token = await _token(admin_client, _IDENTITY)
    response = await _post(admin_client, _IDENTITY, export.content, token=token,
                           filename="unit_presets.csv", content_type="text/csv")
    assert response.status_code == 200, response.text

    factory = admin_app.state.session_factory
    with factory() as db:
        stored = db.execute(
            text("SELECT source_note FROM unit_preset WHERE code = :c"),
            {"c": f"{_PREFIX}blankish"},
        ).scalar_one()
    assert stored is None, (
        "an empty string in a nullable column survived a round trip as an "
        f"empty string; this test records that it does not: {stored!r}"
    )


# --- 1b. the same round trip on `equivalence`, for the column v1.80 added --
#
# `unit_preset` is this file's main subject because it carries every cell shape
# that was broken, and it carries no draft-only rule. `equivalence` is the
# screen a NEW column landed on, and it is the one screen in the tree where a
# missing export column was NOT NULL and therefore uncorrectable by hand (see
# the module docstring, item 1). So the new column is driven here rather than
# argued about: `description` is nullable, free text, and the only prose a
# visitor now reads behind a tangible-equivalence card.
#
# `one_draft` rather than `two_sets`: the five factor children refuse a file
# naming a published set, and the export route serves the whole table, so a
# round trip is only reachable on a deployment with nothing published. That
# limitation has its own test further down this file.


@pytest.mark.parametrize(
    "export_type, filename, content_type",
    [
        ("csv", "equivalences.csv", "text/csv"),
        ("json", "equivalences.json", "application/json"),
    ],
)
async def test_an_equivalence_file_this_panel_exported_re_imports_unchanged(
    admin_client, admin_app, one_draft, session, export_type, filename, content_type
):
    """v1.64's promise, on v1.80's column, in both formats.

    The assertion is the whole `equivalence` table, cell by cell, read with SQL
    on both sides -- so a `description` the export dropped, truncated, wrote as
    the word `None`, or quietly replaced with `source_note` fails here. A test
    narrowed to "the response was 200" would pass against all four.
    """
    session.commit()
    before = _table(admin_app, "equivalence")
    assert before, "the equivalence table is empty, so this proves nothing"
    assert any(row["description"] for row in before.values()), (
        "no row carries a description, so this round trip would not exercise "
        "the column it was written for"
    )
    assert any(
        row["description"] != row["source_note"] for row in before.values()
    ), "description and source_note are the same text; the two cannot be told apart"

    export = await admin_client.get(f"/admin/equivalence/export/{export_type}")
    assert export.status_code == 200, export.text
    assert "description" in export.text.splitlines()[0] or export_type == "json", (
        f"the exported CSV header carries no `description` column: "
        f"{export.text.splitlines()[0]}"
    )

    token = await _token(admin_client, "equivalence")
    response = await _post(admin_client, "equivalence", export.content, token=token,
                           filename=filename, content_type=content_type)
    assert response.status_code == 200, (
        f"a {export_type.upper()} file this panel exported was refused by its "
        f"own import: {response.text}"
    )
    result = _result(response)
    assert result["imported"] == len(before), (
        f"the export carried {len(before)} rows and the import took "
        f"{result['imported']}: {result.get('summary')}"
    )

    assert _table(admin_app, "equivalence") == before, (
        "a file this panel exported did not come back as the table it was "
        "exported from"
    )


@pytest.mark.parametrize("export_type", ["csv", "json"])
async def test_the_exported_equivalence_carries_the_sentence_and_the_note_apart(
    admin_client, one_draft, session, export_type
):
    """Both columns, distinguishable, in both formats.

    `description` and `source_note` are different claims -- what the comparison
    means, and where the factor came from -- and v1.80 took the second off
    every visitor-facing surface while keeping it here and in `GET /factors`.
    An export that carried one in place of the other, or carried one and not
    the other, would make the panel the place the distinction stops being
    true.
    """
    session.commit()
    export = await admin_client.get(f"/admin/equivalence/export/{export_type}")
    assert export.status_code == 200, export.text

    if export_type == "csv":
        rows = {row["code"]: row for row in csv.DictReader(io.StringIO(export.text))}
    else:
        rows = {row["code"]: row for row in json.loads(export.text)}

    row = rows["km_driven"]
    assert "one sentence about the comparison" in row["description"], row
    assert "equivalence source note" in row["source_note"], row
    assert row["description"] != row["source_note"]


# --- 2. the cells the round trip turns on ----------------------------------


@pytest.mark.parametrize("export_type", ["csv", "json"])
async def test_a_foreign_key_is_exported_as_the_referenced_code(
    admin_client, seeded, export_type
):
    """Not `"code — name"`, and not a number.

    `_export_csv` writes `str(await get_prop_value(row, name))` and
    `get_prop_value` returns the related *object*, so the cell was this
    project's `__str__` — `"wp4_cat — WP4 category"`. The import wants the bare
    code, for the reason `resolve_foreign_keys` states: ids differ between
    deployments, so a file keyed on them can only ever be loaded back into the
    database it came from.
    """
    export = await admin_client.get(f"/admin/{_IDENTITY}/export/{export_type}")
    assert export.status_code == 200, export.text
    body = export.text

    assert f"{_PREFIX}cat" in body, (
        "the export names no food category at all, so the row that has one "
        f"was not written: {body[:400]}"
    )
    assert "WP4 category" not in body, (
        "the exported foreign-key cell carries the referenced row's display "
        "text as well as its code — that is `__str__`, which is what the "
        f"import refuses: {body[:400]}"
    )


@pytest.mark.parametrize("export_type", ["csv", "json"])
async def test_an_empty_cell_is_exported_empty_and_never_as_the_word_none(
    admin_client, seeded, export_type
):
    """`str(None)` is `"None"`, which is four characters that get stored.

    A null `source_note` written that way comes back as a note reading "None",
    and a null `food_category` written that way is refused as a code nothing
    answers to. Neither is visible in a 200 and both survive a row count.
    """
    export = await admin_client.get(f"/admin/{_IDENTITY}/export/{export_type}")
    assert export.status_code == 200, export.text

    if export_type == "csv":
        assert not re.search(r'(^|,)"?None"?($|,)', export.text), (
            "the exported CSV carries a cell whose whole value is the word "
            f"None: {export.text[:400]}"
        )
    else:
        rows = json.loads(export.text)
        assert any(row["code"] == f"{_PREFIX}plain" for row in rows)
        for row in rows:
            for name, value in row.items():
                assert value != "None", (
                    f"the exported JSON writes {name!r} as the string "
                    f'"None": {row}'
                )


@pytest.mark.parametrize("export_type", ["csv", "json"])
async def test_the_two_formats_carry_identical_text_for_identical_data(
    admin_client, seeded, export_type
):
    """**One rule for a cell: text, or empty.** Contract §8.1.1's own table.

    A `DECIMAL` has to be a string in JSON because JavaScript's `Number` is a
    double and §1.2 transmits decimals as strings everywhere else in this
    system. Writing the integers and the booleans as text too costs nothing
    and means the CSV and the JSON of one table carry **the same characters**
    — so the difference between the two files is syntax and nothing else, and
    there is one thing to check rather than two.

    `12.5000`, not `12.5`: the digits as the column stores them. A file that
    dropped the trailing zeros would still re-import to the same number, and
    would still be a file that does not say what is in the database.
    """
    export = await admin_client.get(f"/admin/{_IDENTITY}/export/{export_type}")
    assert export.status_code == 200, export.text

    if export_type == "csv":
        rows = {row["code"]: row
                for row in csv.DictReader(io.StringIO(export.text))}
    else:
        rows = {row["code"]: row for row in json.loads(export.text)}

    plain = rows[f"{_PREFIX}plain"]
    linked = rows[f"{_PREFIX}linked"]

    assert plain["kg_per_unit"] == "12.5000", (
        "the exported decimal is not the digits the column stores: "
        f"{plain['kg_per_unit']!r}"
    )
    assert plain["active"] == "true" and linked["active"] == "false", (
        "a boolean is not written as true/false: "
        f"{plain['active']!r} / {linked['active']!r}"
    )
    assert linked["food_category"] == f"{_PREFIX}cat", (
        f"the foreign key is not the referenced code: {linked['food_category']!r}"
    )
    assert linked["source_note"] == 'a note, with a comma and "quotes"', (
        "a value carrying the characters the format has to escape did not "
        f"survive being written: {linked['source_note']!r}"
    )


async def test_the_exported_columns_are_the_columns_the_import_requires(
    admin_client, seeded
):
    """`parse_csv` refuses a file missing any import column outright.

    Export fell back to `column_list` and the import is `form_columns`; on
    `unit_preset` that difference is `source_note`, and on `equivalence` it is
    `label_template`, which is NOT NULL. So the exported file was not a file
    with a wrong cell in it — it was a file the import could not read at all.
    """
    export = await admin_client.get(f"/admin/{_IDENTITY}/export/csv")
    assert export.status_code == 200, export.text
    header = export.text.splitlines()[0].split(",")
    assert "source_note" in header, (
        "the exported file does not carry `source_note`, which this screen's "
        f"import requires; its header is {header}"
    )


# --- 3. the preview the modal shows before anything is written -------------


async def test_a_dry_run_of_an_exported_file_promises_no_change(
    admin_client, admin_app, seeded
):
    """The preview and the import are the same code path with the write left
    off, so the preview of a round trip must say what the round trip does.

    Driven with the same bytes the test above imports for real. A preview that
    said "3 rows would be added" for a file that updates three is the failure
    mode upsert makes possible and the preview exists to catch.
    """
    before = _table(admin_app, "unit_preset")
    export = await admin_client.get(f"/admin/{_IDENTITY}/export/csv")
    assert export.status_code == 200, export.text

    token = await _token(admin_client, _IDENTITY)
    response = await _post(admin_client, _IDENTITY, export.content, token=token,
                           filename="unit_presets.csv", content_type="text/csv",
                           dry_run=True)

    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["dry_run"] is True
    assert plan["ok"] is True, plan["rejected"]
    assert plan["counts"]["created"] == 0, (
        "the preview of a file this panel exported says rows would be added; "
        f"every one of them already exists: {plan['created']}"
    )
    assert plan["counts"]["updated"] == len(before)
    assert _table(admin_app, "unit_preset") == before, (
        "the dry run wrote to the table"
    )


# --- 4. what the export does not do, measured rather than assumed ---------


async def test_a_factor_table_exports_every_set_at_once_and_says_so_on_the_way_back(
    admin_client, admin_app, session, two_sets
):
    """**The limitation contract §8.1.1 states, driven rather than reasoned
    about.**

    `GET /admin/{identity}/export/{type}` serves the whole table — the export
    route never applies the list page's own filters, which on the five factor
    children means the rows of *every* factor set in one file. A deployment
    that has published anything therefore exports a file whose rows name the
    published set, and the draft-only rule refuses exactly that file.

    The point of the test is which of the two ways it fails. It is refused,
    at the first published row, naming the line, the column, the version label
    and the state — not accepted, not half-written. That is the behaviour the
    contract records; if somebody later makes the export follow the page's
    filter, this test is where they find out what the current answer was and
    what it cost.
    """
    live, draft = two_sets
    session.commit()

    export = await admin_client.get("/admin/constant/export/csv")
    assert export.status_code == 200, export.text
    assert live.version_label in export.text, (
        "the export of a factor child does not carry the published set's "
        "rows, so this test is measuring something other than what it says"
    )
    assert draft.version_label in export.text

    token = await _token(admin_client, "constant")
    response = await _post(admin_client, "constant", export.content, token=token,
                           filename="constants.csv", content_type="text/csv")

    assert response.status_code == 400, (
        "a whole-table export of a factor child was accepted back although it "
        f"names the published set: {response.text[:400]}"
    )
    assert live.version_label in response.text and "published" in response.text, (
        f"the refusal does not name the set or its state: {response.text}"
    )
    assert "Line 2" in response.text or "Line 3" in response.text, (
        f"the refusal does not say which row is the problem: {response.text}"
    )
