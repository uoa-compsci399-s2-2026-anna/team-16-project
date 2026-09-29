"""What a spreadsheet does to a number, and what this panel does about it.

WP1 (tests/admin/test_import.py) settled who may import, that the import is
audited, and that a file either lands whole or not at all. It settled all of
that on `sector`, which was chosen for having nothing interesting in it - and
which therefore has **no `DECIMAL` column at all**. This file is the other
half: `unit_preset.kg_per_unit` is `DECIMAL(12, 4)`, and the workflow the
whole feature exists for is *export, open in Excel, correct a figure, save,
re-import*.

Excel is the adversary, and it is not a hypothetical one. On a machine whose
language is German or French it writes `1,5` where the file said `1.5`. It
groups digits with commas once a cell is formatted as a number. It folds a
long number into `1.2E+15` while showing fewer digits than it stores. It adds
a UTF-8 BOM and changes the line endings. None of that is asked for and none
of it is announced.

**These tests are about rejection, not correction.** `1,5` could be 1.5 or
15; `1.2E+15` may have lost its last digits before it was saved. A panel that
guesses stores a number nobody typed, in a column that is multiplied by a
count the visitor types (web/units.js) - so the wrong answer comes out of the
calculator with nothing on screen to say why. Every test below therefore
asserts two things together: that the file was refused, and that the refusal
**says what to change**. A message reading only "invalid row 7" sends
somebody back to a spreadsheet with nothing to look for, which is the failure
this work is against as much as the bad number itself.

**And every rejection is paired with an acceptance.** A cleaner that refused
everything would pass a file of refusal tests; so `1.23400` (a trailing zero,
exactly representable in decimal(12,4)) must import, `12.3400` must round-trip
digit for digit, and a BOM with CRLF must be a non-event.
"""

import json
import re
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from admin.factor_models import FactorUpstream
from admin.importing import numeric_rejections
from admin.models import AuditLog
from admin.taxonomy_models import Sector, UnitPreset

pytestmark = [pytest.mark.db, pytest.mark.asyncio]

#: Every row this file creates carries this in its `code`, and the teardown
#: sweeps on it rather than on a hand-kept list of names - the same reasoning
#: as tests/admin/test_import.py's `wp1_`.
_PREFIX = "wp2_"

#: The one code that cannot carry the prefix, because the thing it is testing
#: is a code made only of digits with a leading zero. Excel strips those; this
#: panel must not, and a code is the cross-layer identifier, so `0012` and
#: `12` are different rows. Swept by name below.
_DIGIT_CODE = "0012"

#: Both headers gained a column in WP3 and both put it last, so that the row
#: literals below are left saying what they were written to say and the two
#: helpers append the empty cell.
#:
#: WP3 set `column_import_list = form_columns` on every importable screen —
#: `food_category` on unit presets (the foreign key WP2 excluded, now written
#: as the referenced row's `code`) and `description` on sectors. `parse_csv`
#: refuses a file missing any import column outright, so a cell for each has
#: to be there. Column *order* in the file is free: sqladmin reads it with a
#: `csv.DictReader`.
#:
#: **Every row in this file leaves `food_category` blank**, which on this
#: table means the preset applies to every category — the common case, and the
#: one that keeps these tests about the decimal in `kg_per_unit` rather than
#: about a foreign key. The foreign key has its own file,
#: tests/admin/test_import_tables.py.
_PRESET_HEADER = "code,label,kg_per_unit,source_note,active,food_category"
_SECTOR_HEADER = "code,name,sort_order,active,description"


def _preset_csv(*rows: str, newline: str = "\r\n", bom: bool = False) -> bytes:
    """A unit-preset CSV, with the line ending and BOM under the test's control.

    Both are Excel's doing rather than anybody's choice, which is why they are
    parameters here and not constants.
    """
    padded = [f"{row}," for row in rows]
    body = (newline.join((_PRESET_HEADER, *padded)) + newline).encode("utf-8")
    return (b"\xef\xbb\xbf" + body) if bom else body


def _sector_csv(*rows: str) -> bytes:
    padded = [f"{row}," for row in rows]
    return ("\r\n".join((_SECTOR_HEADER, *padded)) + "\r\n").encode("utf-8")


@pytest.fixture(autouse=True)
def _clean_rows(admin_app):
    """Remove this file's own rows and their audit entries, before and after.

    Before as well as after: `code` is unique-constrained, so a run
    interrupted partway leaves a row whose presence makes the *next* run's
    first import fail for a reason unrelated to anything it asserts.

    These rows would otherwise show up in the public calculator - a unit
    preset is a container a visitor picks from - so the sweep is not
    housekeeping, it is the test not leaking into the product.
    """

    def sweep() -> None:
        factory = admin_app.state.session_factory
        with factory() as db:
            for table in ("unit_preset", "sector"):
                ids = db.execute(
                    text(f"SELECT id FROM {table} WHERE code LIKE :like "
                         "OR code = :digits"),
                    {"like": _PREFIX + "%", "digits": _DIGIT_CODE},
                ).scalars().all()
                for row_id in ids:
                    db.execute(
                        text("DELETE FROM audit_log WHERE table_name = :table "
                             "AND row_id = :id"),
                        {"table": table, "id": row_id},
                    )
                db.execute(
                    text(f"DELETE FROM {table} WHERE code LIKE :like "
                         "OR code = :digits"),
                    {"like": _PREFIX + "%", "digits": _DIGIT_CODE},
                )
            db.commit()

    sweep()
    yield
    sweep()


async def _token(client, identity: str) -> str:
    """The CSRF token this session's list page renders for the import modal."""
    page = await client.get(f"/admin/{identity}/list")
    assert page.status_code == 200, f"the {identity} list page did not render"
    match = re.search(r'id="kaicalc-import-csrf" value="([^"]+)"', page.text)
    assert match, (
        f"the {identity} list page rendered no CSRF token for the import "
        "modal, so nothing a browser sends could ever carry one"
    )
    return match.group(1)


async def _post(client, identity, content, *, token, filename="rows.csv",
                continue_on_error=None):
    data = {} if token is None else {"csrf_token": token}
    if continue_on_error is not None:
        data["continue_on_error"] = continue_on_error
    return await client.post(
        f"/admin/{identity}/import",
        files={"csvfile": (filename, content, "text/csv")},
        data=data,
    )


def _result(response) -> dict:
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    finals = [event for event in events if event.get("type") == "result"]
    assert finals, f"the import stream carried no result event: {response.text!r}"
    return finals[-1]


def _presets(admin_app) -> dict[str, UnitPreset]:
    factory = admin_app.state.session_factory
    with factory() as db:
        return {
            row.code: row
            for row in db.scalars(
                select(UnitPreset).where(
                    UnitPreset.code.like(_PREFIX + "%")
                    | (UnitPreset.code == _DIGIT_CODE)
                )
            ).all()
        }


def _sectors(admin_app) -> dict[str, Sector]:
    factory = admin_app.state.session_factory
    with factory() as db:
        return {
            row.code: row
            for row in db.scalars(
                select(Sector).where(Sector.code.like(_PREFIX + "%"))
            ).all()
        }


def _entries(admin_app, actor: str) -> list[AuditLog]:
    factory = admin_app.state.session_factory
    with factory() as db:
        return list(
            db.scalars(
                select(AuditLog).where(AuditLog.actor == actor).order_by(AuditLog.id)
            ).all()
        )


async def _refusal(client, admin_app, *rows, **kwargs):
    """Post a unit-preset file that must be refused, and hand back the text.

    Asserts the three things every refusal in this file shares, so that each
    test below is left saying only what is particular to its own bad value:
    a 400, nothing written, and nothing audited. The last two are what make
    "reject rather than coerce" mean anything - a refusal that had already
    written the good rows above the bad one would be a coercion of the file.
    """
    token = await _token(client, "unit-preset")
    response = await _post(
        client, "unit-preset", _preset_csv(*rows), token=token, **kwargs
    )

    assert response.status_code == 400, (
        "the file was not refused; the panel answered "
        f"{response.status_code} for {rows!r}"
    )
    assert _presets(admin_app) == {}, (
        "a refused file still wrote rows, so the check runs too late to be "
        "a refusal at all"
    )
    assert _entries(admin_app, client.staff.username) == [], (
        "a refused file wrote an audit entry, which is a trail claiming a "
        "change that never happened"
    )
    return response.text


# --- What Excel does to a decimal ------------------------------------------


async def test_a_comma_decimal_separator_is_refused_and_names_the_alternative(
    admin_client, admin_app
):
    """`1,5` in a DECIMAL column - the realistic corruption.

    Nobody types this. Excel writes it on a machine whose language is German,
    French or similar, and the staff member who exported, edited one cell and
    saved has no idea it happened. Two further things ride on this one test:

    * `Decimal("1,5")` raises `decimal.InvalidOperation`, which is an
      `ArithmeticError` and **not** a `ValueError`, so sqladmin's own
      `merge_import_row_data` - `except (TypeError, ValueError)` - does not
      catch it. Left to sqladmin this value escapes as an unhandled exception
      out of the middle of a streaming response.
    * `continue_on_error` is sent as "1" here deliberately. A hand-built post
      can set that field whatever the modal does, and a cleaning pass that
      honoured it would land the good rows above the bad one.
    """
    text_body = await _refusal(
        admin_client, admin_app,
        f"{_PREFIX}good,Good bucket,12.5,note,1",
        f'{_PREFIX}bad,Bad bucket,"1,5",note,1',
        continue_on_error="1",
    )

    assert "Line 3" in text_body, (
        "the refusal does not say which line is wrong, so a staff member has "
        f"to find it by eye: {text_body!r}"
    )
    assert 'column "kg_per_unit"' in text_body, (
        f"the refusal does not name the column: {text_body!r}"
    )
    assert '"1,5"' in text_body, (
        f"the refusal does not quote the value it refused: {text_body!r}"
    )
    assert "Write it as 1.5" in text_body, (
        "the refusal does not say what to write instead, which is the whole "
        f"difference between this and 'invalid row 3': {text_body!r}"
    )


async def test_a_thousands_separator_is_refused(admin_client, admin_app):
    """`1,234.5`. Unambiguous - the decimal point is already there - so the
    message may say exactly what the number is and where the comma came from.
    """
    text_body = await _refusal(
        admin_client, admin_app, f'{_PREFIX}g,Grouped,"1,234.5",note,1'
    )

    assert "Line 2" in text_body and 'column "kg_per_unit"' in text_body
    assert '"1,234.5"' in text_body
    assert "Write it as 1234.5" in text_body, (
        f"the refusal does not say what to write instead: {text_body!r}"
    )


async def test_a_comma_that_could_be_either_says_so_rather_than_choosing(
    admin_client, admin_app
):
    """`1,234` is genuinely ambiguous, and the two readings are a thousand
    times apart.

    A decimal comma gives 1.234; a thousands separator gives 1234. There is
    nothing in the file that decides between them, so the message names both
    and refuses. This is the case where guessing would be least defensible
    and most tempting, which is why it is its own test rather than folded in
    with the two above.
    """
    text_body = await _refusal(
        admin_client, admin_app, f'{_PREFIX}amb,Ambiguous,"1,234",note,1'
    )

    assert "1.234" in text_body and "1234" in text_body, (
        "the refusal does not offer the reader both readings of the comma, "
        f"so it has effectively chosen one: {text_body!r}"
    )


async def test_scientific_notation_is_refused_without_being_expanded(
    admin_client, admin_app
):
    """`1.2E+15`, which Excel produces from a long number by itself.

    Refused rather than expanded on purpose, and the message says so: the
    cell shows fewer digits than the sheet stores and the conversion may
    already have thrown the end of the number away, so an expansion here
    would be this panel inventing digits.
    """
    text_body = await _refusal(
        admin_client, admin_app, f"{_PREFIX}sci,Scientific,1.2E+15,note,1"
    )

    assert "scientific notation" in text_body, (
        f"the refusal does not name what is wrong with the value: {text_body!r}"
    )
    assert "1200000000000000" not in text_body, (
        "the refusal expanded the number for the reader, which is the panel "
        "supplying digits the spreadsheet may already have lost"
    )


# --- What the column keeps -------------------------------------------------


async def test_a_fifth_decimal_place_is_refused_because_the_column_keeps_four(
    admin_client, admin_app
):
    """`kg_per_unit` is `DECIMAL(12, 4)`, and a fifth place is rounded away on
    the way in.

    That is the quietest failure in this file: the import succeeds, the row
    looks right, and the stored figure is one nobody typed. Nothing on the
    screen afterwards distinguishes a number the panel rounded from a number
    a person chose.
    """
    text_body = await _refusal(
        admin_client, admin_app, f"{_PREFIX}long,Too precise,1.23456,note,1"
    )

    assert "decimal(12,4)" in text_body, (
        "the refusal does not tell the reader what the column actually "
        f"holds: {text_body!r}"
    )
    assert "5 decimal places" in text_body and "keeps 4" in text_body, (
        f"the refusal does not say by how much the value misses: {text_body!r}"
    )


async def test_a_trailing_zero_past_the_scale_is_not_refused(
    admin_client, admin_app
):
    """`1.23400` in a decimal(12,4) column is the same number as `1.2340`.

    The half that stops the test above being "refuse anything long". Nothing
    is lost storing this, so nobody is sent back to the spreadsheet for a
    keystroke that changed no value - and a cleaner written as
    `len(after_the_point) > scale` fails here while passing every rejection
    test in the file.
    """
    token = await _token(admin_client, "unit-preset")

    response = await _post(
        admin_client, "unit-preset",
        _preset_csv(f"{_PREFIX}zeros,Trailing zeros,1.23400,note,1"),
        token=token,
    )

    assert response.status_code == 200, response.text
    assert _result(response)["imported"] == 1, _result(response)["summary"]
    stored = _presets(admin_app)[f"{_PREFIX}zeros"].kg_per_unit
    assert stored == Decimal("1.2340"), (
        f"a value that fits decimal(12,4) exactly was not stored: {stored!r}"
    )


async def test_too_many_digits_before_the_point_are_refused(
    admin_client, admin_app
):
    """The other end of `decimal(12,4)`: twelve digits in all, four after the
    point, so eight before it.

    Left to the database this is an out-of-range error raised from inside the
    import's own commit, and what the staff member is shown is a driver
    message about a column they did not name.
    """
    text_body = await _refusal(
        admin_client, admin_app, f"{_PREFIX}big,Too large,123456789.5,note,1"
    )

    assert "9 digits before the decimal point" in text_body, (
        f"the refusal does not say what is oversized: {text_body!r}"
    )
    assert "holds 8" in text_body, (
        f"the refusal does not say what would fit: {text_body!r}"
    )


async def test_the_scale_comes_from_the_column_and_not_from_a_constant():
    """The same value, two columns, two answers - which a hard-coded 4 cannot
    give.

    `async` with nothing awaited, only because this module's `pytestmark`
    applies `asyncio` to every test in it and pytest-asyncio warns on a sync
    function carrying that mark. The alternative is marking each of the
    eighteen HTTP tests individually so that this one can opt out, which
    trades a warning for eighteen chances to forget.

    `unit_preset.kg_per_unit` is `DECIMAL(12, 4)` and
    `factor_upstream.value_per_kg` is `DECIMAL(20, 10)`; elsewhere in this
    schema there are `DECIMAL(16, 3)` and `DECIMAL(14, 2)` columns as well. A
    constant would be right for one table and wrong for the others, and wrong
    in the direction that accepts a digit the column then rounds away.

    A pure test rather than an HTTP one because `factor_upstream` has no
    import of its own yet - that is WP3, and it carries the draft-only rule
    with it. What is being proved here is that the pass reads the metadata,
    and the metadata is the same object either way.
    """

    class _View:
        def __init__(self, model, columns):
            self.model = model
            self._import_prop_names = columns

    five_places = b"value_per_kg\r\n1.23456\r\n"
    factors = numeric_rejections(five_places, _View(FactorUpstream, ["value_per_kg"]))
    assert factors == [], (
        "five decimal places were refused for a decimal(20,10) column, which "
        f"keeps ten: {factors}"
    )

    presets = numeric_rejections(
        b"kg_per_unit\r\n1.23456\r\n", _View(UnitPreset, ["kg_per_unit"])
    )
    assert len(presets) == 1 and "decimal(12,4)" in presets[0], (
        "the same value was not refused for a decimal(12,4) column, so the "
        f"scale is not being read from the column at all: {presets}"
    )

    eleven_places = numeric_rejections(
        b"value_per_kg\r\n1.23456789012\r\n", _View(FactorUpstream, ["value_per_kg"])
    )
    assert len(eleven_places) == 1 and "decimal(20,10)" in eleven_places[0], (
        "eleven decimal places were accepted for a decimal(20,10) column, so "
        f"the scale is being read as something else entirely: {eleven_places}"
    )


# --- Not a number at all ---------------------------------------------------


async def test_a_value_that_is_not_a_number_says_what_one_looks_like(
    admin_client, admin_app
):
    """`12 kg`. The unit typed into the cell alongside the figure, which is
    what a person does when the column header does not say the unit.

    The message has to describe the accepted shape, because "not a number" on
    its own does not tell somebody that the space is the problem.
    """
    text_body = await _refusal(
        admin_client, admin_app, f'{_PREFIX}unit,With a unit,"12 kg",note,1'
    )

    assert "is not a number" in text_body
    assert "no units" in text_body and "no spaces" in text_body, (
        "the refusal does not describe what an acceptable value looks like: "
        f"{text_body!r}"
    )


async def test_a_value_decimal_would_quietly_accept_is_refused(
    admin_client, admin_app
):
    """`1_000`, which `decimal.Decimal` reads as one thousand without a word.

    This is why the pass is a pattern and not `try: Decimal(value)`. So are
    `nan` and `Infinity`, which `Decimal` also accepts and which reach a
    DECIMAL column as a database error rather than as a refusal.
    """
    text_body = await _refusal(
        admin_client, admin_app, f"{_PREFIX}under,Underscored,1_000,note,1"
    )
    assert "is not a number" in text_body

    other = await _refusal(
        admin_client, admin_app, f"{_PREFIX}nan,Not a number,nan,note,1"
    )
    assert "is not a number" in other


async def test_a_decimal_point_in_a_whole_number_column_is_refused(
    admin_client, admin_app
):
    """`sector.sort_order` is an INTEGER, and `901.0` is what a spreadsheet
    makes of `901` once the column has been formatted as a number.

    Refused even though the fraction is zero: sqladmin would coerce it with
    `int("901.0")`, which raises, and the message a staff member then gets is
    the generic "Invalid value" rather than the one below. The whole-number
    columns are checked on the same pass as the decimal ones, which is what
    makes "every numeric cell" true rather than "every DECIMAL cell".
    """
    token = await _token(admin_client, "sector")

    response = await _post(
        admin_client, "sector",
        _sector_csv(f"{_PREFIX}sect,WP2 Sector,901.0,1"),
        token=token,
    )

    assert response.status_code == 400, (
        f"a decimal in an integer column was accepted: {response.text!r}"
    )
    assert 'column "sort_order"' in response.text
    assert "whole numbers" in response.text and "Write it as 901" in response.text, (
        "the refusal does not say what to write instead: "
        f"{response.text!r}"
    )
    assert _sectors(admin_app) == {}


# --- Every bad value, not the first ----------------------------------------


async def test_every_bad_value_in_the_file_is_reported_not_just_the_first(
    admin_client, admin_app
):
    """One trip back to the spreadsheet, not one per bad cell.

    A file that came out of a German locale has the wrong decimal separator
    in *every* row, and a refusal naming only the first would be answered by
    a corrected file that is refused again, forty times. Nothing is written
    either way - `continue_on_error` is pinned false - so reporting all of
    them costs nothing and saves the afternoon.
    """
    text_body = await _refusal(
        admin_client, admin_app,
        f'{_PREFIX}one,First,"1,5",note,1',
        f'{_PREFIX}two,Second,"2,5",note,1',
        f'{_PREFIX}three,Third,"3,5",note,1',
    )

    for line in ("Line 2", "Line 3", "Line 4"):
        assert line in text_body, (
            f"{line} is missing from the refusal, so a staff member would "
            f"correct one row and be refused again: {text_body!r}"
        )
    assert "3 values could not be read" in text_body, (
        f"the refusal does not count what is wrong: {text_body!r}"
    )


# --- What must still import ------------------------------------------------


async def test_a_byte_order_mark_and_crlf_do_not_stop_a_file_importing(
    admin_client, admin_app
):
    """Saved from Excel as "CSV UTF-8": a BOM in front of the header and CRLF
    endings.

    sqladmin's own `parse_csv` strips the BOM and splits on `splitlines()`,
    so this needs no code here - and that is exactly why it needs a test
    here. With the BOM left on, the first header name is `\\ufeffcode`, the
    required-column check fails, and every file a staff member exports from
    Excel is refused with a message about a missing column that is plainly
    present.
    """
    token = await _token(admin_client, "unit-preset")

    response = await _post(
        admin_client, "unit-preset",
        _preset_csv(f"{_PREFIX}bom,From Excel,2.5,note,1", bom=True),
        token=token,
    )

    assert response.status_code == 200, response.text
    assert _result(response)["imported"] == 1, _result(response)["summary"]
    assert f"{_PREFIX}bom" in _presets(admin_app)


async def test_lf_line_endings_import_the_same_as_crlf(admin_client, admin_app):
    """The same file saved on a Mac or by a script, which is the other half of
    "either line ending"."""
    token = await _token(admin_client, "unit-preset")

    response = await _post(
        admin_client, "unit-preset",
        _preset_csv(f"{_PREFIX}lf,Unix endings,2.5,note,1", newline="\n"),
        token=token,
    )

    assert response.status_code == 200, response.text
    assert _result(response)["imported"] == 1, _result(response)["summary"]
    assert f"{_PREFIX}lf" in _presets(admin_app)


async def test_a_code_made_of_digits_keeps_its_leading_zeros(
    admin_client, admin_app
):
    """`code` is text, and `0012` is not the same row as `12`.

    `code` is the cross-layer identifier - the API and the front end use it
    and never the primary key - so a cleaner that treated every cell that
    looks like a number as one would silently re-point a row at a different
    identity. Excel may well have eaten the zeros before the file arrived and
    nothing here can undo that; what this pins is that nothing on *this* side
    does it, and that a code made only of digits is not even offered to the
    numeric checks.
    """
    token = await _token(admin_client, "unit-preset")

    response = await _post(
        admin_client, "unit-preset",
        _preset_csv(f"{_DIGIT_CODE},Numeric code,2.5,note,1"),
        token=token,
    )

    assert response.status_code == 200, response.text
    assert _result(response)["imported"] == 1, _result(response)["summary"]
    assert _DIGIT_CODE in _presets(admin_app), (
        "a code made only of digits did not survive the import as written; "
        f"what landed was {sorted(_presets(admin_app))}"
    )


async def test_a_clean_decimal_arrives_as_a_decimal_with_the_digits_typed(
    admin_client, admin_app
):
    """The invariant this whole pass is in service of, read back off the row.

    Contract §1.2: DECIMAL everywhere, FLOAT and DOUBLE prohibited, and a CSV
    round trip through a spreadsheet is exactly the seam a float artefact
    gets in by. Two things are pinned here: the column hands back a `Decimal`
    rather than a `float`, and the digits that were typed are the digits
    stored - `12.3400`, not `12.34` and not `12.3`.

    WHAT THIS TEST CANNOT PROVE, SAID PLAINLY, BECAUSE THE NUMBERS DECIDE IT
    AND NOT THE TEST. It cannot catch a `float()` *in transit* - a value
    converted to a double somewhere between the CSV and the INSERT and back
    to a Decimal by the driver. `decimal(12,4)` is twelve significant digits
    and an IEEE double carries fifteen to seventeen, so **every** value this
    column can hold survives that round trip unchanged; measured over 200,000
    random `decimal(12,4)` values, zero came back different. The same
    measurement over `decimal(20,10)` - the scale of all four factor value
    columns - changed 49,960 values out of 50,000. So the test that can
    actually catch a float in transit belongs to `factor_upstream` and its
    siblings, and it is WP3's to write when their import is turned on. What
    is asserted here is what this table is able to answer.
    """
    token = await _token(admin_client, "unit-preset")

    response = await _post(
        admin_client, "unit-preset",
        _preset_csv(f"{_PREFIX}exact,Exactly four places,12.3400,note,1"),
        token=token,
    )

    assert response.status_code == 200, response.text
    stored = _presets(admin_app)[f"{_PREFIX}exact"].kg_per_unit
    assert isinstance(stored, Decimal), (
        f"kg_per_unit came back as {type(stored).__name__}, not Decimal"
    )
    assert str(stored) == "12.3400", (
        f"the digits that were typed are not the digits stored: {stored!r}"
    )


async def test_the_modal_renders_a_multi_line_refusal_as_multiple_lines(
    admin_client,
):
    """The browser half of the message quality, which no server-side test can
    see.

    A refusal naming four bad values is four lines of text, and the dialog
    puts a non-200 body into the page as `textContent` — so the newlines reach
    the DOM and then collapse under the browser's default
    `white-space: normal`. Every assertion in this file would still pass while
    what a staff member actually sees is the single run-on paragraph the
    messages were written to avoid.

    The element named here changed with WP4's dialog and the property did not:
    it is the box a refusal is written into that has to keep the line breaks.
    """
    page = await admin_client.get("/admin/unit-preset/list")

    assert "#kaicalc-import-message" in page.text and "pre-wrap" in page.text, (
        "the import dialog does not preserve the line breaks in a refusal, so "
        "a file with four bad values is shown as one paragraph"
    )
    script = await admin_client.get("/admin/static/import.js")
    assert "textContent" in script.text, (
        "the dialog does not write a refusal as text, so a message quoting a "
        "value somebody typed would be rendered as markup"
    )


# --- The new screen carries the same floor as the first one ----------------


async def test_a_plain_staff_member_cannot_import_unit_presets(
    staff_client, admin_app
):
    """`AuditedImport` is a mixin and a second view is where it gets left off.

    The role floor lives on `check_can_import`, which resolves through the
    mixin only if the mixin is ahead of the view's own base - a one-line
    mistake on this class would open the route with nothing on screen saying
    so. Driven with a real signed-in non-administrator, because the Unit
    presets screen is open to both roles by contract §8.3 and `is_accessible`
    therefore answers yes here.
    """
    response = await _post(
        staff_client, "unit-preset",
        _preset_csv(f"{_PREFIX}staff,Refused,2.5,note,1"), token="anything",
    )

    assert response.status_code == 403, (
        "a signed-in staff member was allowed to import unit presets; the "
        f"panel answered {response.status_code}"
    )
    assert _presets(admin_app) == {}


async def test_an_administrator_is_not_refused_the_same_request(
    admin_client, admin_app
):
    """The half that stops the refusal above being vacuous - a screen that
    refused everybody would pass it."""
    token = await _token(admin_client, "unit-preset")

    response = await _post(
        admin_client, "unit-preset",
        _preset_csv(f"{_PREFIX}ok,Accepted,2.5,note,1"), token=token,
    )

    assert response.status_code != 403, (
        "an administrator was refused the unit-preset import route, so the "
        "staff refusal above proves nothing about roles"
    )
    assert f"{_PREFIX}ok" in _presets(admin_app)


async def test_the_import_of_a_unit_preset_is_audited(admin_client, admin_app):
    """WP1's audit machinery, on the second table to wear the mixin.

    Not a re-test of the listener: what could be wrong here and nowhere else
    is the pairing of `AuditedImport` with a view whose `session_maker` is a
    different audited sessionmaker, one built for `UnitPreset`. The listener
    filters on `isinstance(obj, model)`, so a mis-paired view writes a header
    entry counting zero rows.
    """
    token = await _token(admin_client, "unit-preset")

    await _post(
        admin_client, "unit-preset",
        _preset_csv(f"{_PREFIX}aud,Audited,2.5,note,1"),
        token=token, filename="presets-corrected.csv",
    )

    entries = _entries(admin_app, admin_client.staff.username)
    actions = [entry.action for entry in entries]
    assert actions == ["import", "create"], (
        "a unit-preset import did not write one file entry and one row "
        f"entry; audit_log has {actions}"
    )
    assert entries[0].table_name == "unit_preset"
    assert entries[0].after_json["import_file"] == "presets-corrected.csv"
    assert entries[0].after_json["rows_created"] == 1, (
        "the file-level entry counted no rows, which is what a view paired "
        f"with the wrong model's listener writes: {entries[0].after_json}"
    )
