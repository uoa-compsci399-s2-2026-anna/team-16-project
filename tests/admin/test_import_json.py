"""JSON is another entry format, landing on the path CSV already had.

The decision this file holds to account, verbatim from the plan: *"JSON and
CSV land on one validation and persistence path. JSON is another entry format,
converted to the same row structure, never a second pipeline."*

So almost nothing here is about JSON. Each test uploads a `.json` file and
asserts that something WP2, WP3 or WP3b built for CSV happened to it — the
decimal rules, the foreign keys by `code`, the draft-only rule, the upsert, the
audit trail, the CSRF check. **A second pipeline would pass a test that only
checked that valid JSON lands**, and would quietly have none of those; these
check the opposite, which is the only thing worth checking.

The four tests that *are* about JSON are the four faults a CSV cannot have: a
file that is not an array, a row that is not an object, a cell holding a list,
and a number written as a JSON number rather than as a string. The last of
those is not cosmetic — `json.loads` parses a bare number into a **double**,
which on a `decimal(20,10)` column is exactly the FLOAT contract §1.2
prohibits, arriving through a door nothing was watching.
"""

import json
import re
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from admin.factor_models import Constant
from admin.models import AuditLog
from admin.taxonomy_models import Sector

pytestmark = [pytest.mark.db, pytest.mark.asyncio]

#: Swept by the fixture below rather than named row by row — the same
#: reasoning as `wp1_` through `wp4_` in the files before this one.
_PREFIX = "wp4j_"


@pytest.fixture
def session(_committed_session):
    """This file's own name for tests/admin/conftest.py's hard-committing
    session — see that module's `_committed_session` docstring."""
    return _committed_session


@pytest.fixture(autouse=True)
def _clean_rows(admin_app):
    def sweep() -> None:
        factory = admin_app.state.session_factory
        with factory() as db:
            # `unit_preset` as well, although every test here that names one
            # is a test of a refusal: a refusal that stopped refusing would
            # otherwise leave a row in a table the public calculator reads.
            db.execute(text("DELETE FROM `constant` WHERE code LIKE :like"),
                       {"like": _PREFIX + "%"})
            db.execute(text("DELETE FROM unit_preset WHERE code LIKE :like"),
                       {"like": _PREFIX + "%"})
            ids = db.execute(
                text("SELECT id FROM sector WHERE code LIKE :like"),
                {"like": _PREFIX + "%"},
            ).scalars().all()
            for row_id in ids:
                db.execute(
                    text("DELETE FROM audit_log WHERE table_name = 'sector' "
                         "AND row_id = :id"),
                    {"id": row_id},
                )
            db.execute(text("DELETE FROM sector WHERE code LIKE :like"),
                       {"like": _PREFIX + "%"})
            db.commit()

    sweep()
    yield
    sweep()


async def _token(client, identity: str) -> str:
    page = await client.get(f"/admin/{identity}/list")
    assert page.status_code == 200, f"the {identity} list page did not render"
    match = re.search(r'id="kaicalc-import-csrf" value="([^"]+)"', page.text)
    assert match, f"the {identity} list page rendered no CSRF token"
    return match.group(1)


async def _post_json(client, identity: str, payload, *, token,
                     filename="rows.json", dry_run=False):
    """Upload a JSON file the way the dialog does.

    `payload` is written out with `json.dumps` unless it is already text —
    which some tests need, because a bare JSON *number* and the string of the
    same digits are the whole difference between two of them.
    """
    body = payload if isinstance(payload, str) else json.dumps(payload)
    headers = {"X-Dry-Run": "true"} if dry_run else {}
    return await client.post(
        f"/admin/{identity}/import",
        files={"csvfile": (filename, body.encode("utf-8"), "application/json")},
        data={"csrf_token": token},
        headers=headers,
    )


def _result(response) -> dict:
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    finals = [event for event in events if event.get("type") == "result"]
    assert finals, f"the import stream carried no result event: {response.text!r}"
    return finals[-1]


def _sectors(admin_app) -> dict:
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
        return list(db.scalars(
            select(AuditLog).where(AuditLog.actor == actor).order_by(AuditLog.id)
        ).all())


def _sector_rows(*codes, **overrides):
    rows = []
    for index, code in enumerate(codes):
        row = {
            "code": f"{_PREFIX}{code}",
            "name": f"WP4 {code}",
            "description": None,
            "sort_order": str(940 + index),
            "active": True,
        }
        row.update(overrides)
        rows.append(row)
    return rows


# --- 1. a JSON file lands, and is audited exactly as a CSV one is ----------


async def test_a_json_file_lands_its_rows_and_writes_the_same_audit_trail(
    admin_client, admin_app
):
    """One file-level entry naming the file, then one entry per row.

    Read out of `audit_log` rather than out of the code, the way WP1's own
    test is: the audit trail is a `before_commit` listener that writes only
    when the actor contextvar is set, and a JSON upload that reached
    persistence by some other route would land its rows and write nothing.
    """
    token = await _token(admin_client, "sector")
    response = await _post_json(admin_client, "sector",
                                _sector_rows("alpha", "beta"), token=token,
                                filename="sectors-corrected.json")

    assert response.status_code == 200, response.text
    assert _result(response)["imported"] == 2, _result(response)["summary"]
    assert set(_sectors(admin_app)) == {f"{_PREFIX}alpha", f"{_PREFIX}beta"}

    entries = _entries(admin_app, admin_client.staff.username)
    assert [entry.action for entry in entries] == ["import", "create", "create"], (
        "a JSON import did not write the trail a CSV import writes; "
        f"audit_log has {[e.action for e in entries]}"
    )
    assert entries[0].after_json["import_file"] == "sectors-corrected.json"


async def test_the_audit_entry_digests_the_json_that_arrived_not_the_csv_it_became(
    admin_client, admin_app
):
    """The digest answers "is this the file I sent you", and nothing else.

    A JSON upload is converted into the row structure the rest of the route
    reads, so there are two candidate byte strings by the time the entry is
    written. Digesting the converted one would answer a question nobody asked
    and would leave the person who uploaded the file with a hash they cannot
    reproduce from anything they have.
    """
    import hashlib

    rows = _sector_rows("gamma")
    body = json.dumps(rows)
    token = await _token(admin_client, "sector")
    response = await _post_json(admin_client, "sector", body, token=token)
    assert response.status_code == 200, response.text

    header = _entries(admin_app, admin_client.staff.username)[0]
    assert header.after_json["bytes"] == len(body.encode("utf-8")), (
        "the audit entry counts bytes this panel produced, not the bytes that "
        f"arrived: {header.after_json}"
    )
    assert header.after_json["sha256"] == hashlib.sha256(
        body.encode("utf-8")
    ).hexdigest(), (
        "the audit entry digests something other than the uploaded file, so "
        "'was this the file I sent?' cannot be answered"
    )


async def test_a_json_import_without_a_token_is_refused(admin_client, admin_app):
    """A new entry format is a new way into a write route.

    The CSRF check is on the route, ahead of the branch that reads the file,
    and this drives that ordering rather than reading it: a JSON branch that
    read and converted before the token was looked at would still refuse the
    request, and would have done the work of an unauthenticated upload first.
    """
    response = await _post_json(admin_client, "sector", _sector_rows("delta"),
                                token="not-this-session's-token")

    assert response.status_code == 400, (
        "a JSON import posted with a token this session never issued was "
        "accepted"
    )
    assert _sectors(admin_app) == {}


# --- 2. every rule CSV has, JSON has, because it is the same code ---------


async def test_a_comma_decimal_in_json_is_refused_in_wp2s_own_words(
    admin_client, admin_app
):
    """The decimal rules are not re-implemented for JSON; they are re-run.

    `1,5` is the headline case: Excel writes it on a French or German install,
    `Decimal("1,5")` raises `InvalidOperation` — which is **not** a
    `ValueError`, so sqladmin's own `except (TypeError, ValueError)` does not
    catch it — and WP2's pass refuses it by name. If it reaches that pass
    through a JSON file too, the pass is shared; if JSON had a reader of its
    own, this row would have been coerced or would have crashed.
    """
    token = await _token(admin_client, "unit-preset")
    response = await _post_json(
        admin_client, "unit-preset",
        [{"code": f"{_PREFIX}comma", "label": "WP4 comma",
          "food_category": None, "kg_per_unit": "1,5",
          "source_note": None, "active": True}],
        token=token,
    )

    assert response.status_code == 400, response.text
    body = response.text
    assert "1,5" in body and "kg_per_unit" in body, (
        f"the refusal does not name the value or the column: {body}"
    )
    assert "Line 2" in body, (
        f"the refusal does not say which row is wrong: {body}"
    )
    assert "array" in body, (
        "a JSON file's refusal names lines that the file does not have, and "
        f"says nothing about what they count: {body}"
    )


async def test_a_json_number_in_a_decimal_column_keeps_every_digit(
    admin_client, admin_app, session, two_sets
):
    """**The float `json.loads` would have made, and does not.**

    This is the one failure mode JSON has and CSV cannot: a CSV cell is always
    text, and a JSON number is parsed by the standard library into a
    **double** before any of this project's code sees the file. On
    `constant.value`, which is `decimal(20,10)`, that loses the last three
    digits — silently, inside `json.loads`, on a factor a calculation rides
    on.

    `parse_float=Decimal` hands back the literal that was typed instead.
    Written here as a bare JSON number on purpose: quoting it would test
    nothing, because a quoted value never reaches the float path at all.

    The last assertion holds the test to account. WP2 measured that a
    `decimal(12,4)` column cannot detect a float in transit — 0 of 50,000,
    because twelve significant digits fit inside a double's fifteen to
    seventeen — so a test written on the wrong column would be green whatever
    happened. `decimal(20,10)` is 49,974 of 50,000.
    """
    live, draft = two_sets
    session.commit()

    exact = Decimal("1234567890.1234567891")
    body = (
        '[{"factor_set": "%s", "code": "%sEXACT", "value": %s, '
        '"unit": "x", "note": "twenty digits"}]'
        % (draft.version_label, _PREFIX, exact)
    )
    assert '"value": 1234567890.1234567891' in body, (
        "the value is quoted in this payload, so it never goes near the float "
        "path this test exists for"
    )

    token = await _token(admin_client, "constant")
    response = await _post_json(admin_client, "constant", body, token=token)
    assert response.status_code == 200, response.text
    assert _result(response)["imported"] == 1, _result(response)["summary"]

    factory = admin_app.state.session_factory
    with factory() as db:
        stored = db.scalar(
            select(Constant).where(Constant.code == f"{_PREFIX}EXACT")
        )
    assert stored is not None, "the row was not written at all"
    assert stored.value == exact, (
        f"the stored value is {stored.value}, and {exact} was uploaded as a "
        "JSON number. `json.loads` parses a bare number into a double unless "
        "it is told otherwise, and a double keeps about seventeen significant "
        "digits of this column's twenty — DECIMAL everywhere, and the "
        "prohibition on FLOAT and DOUBLE, is contract §1.2"
    )
    assert Decimal(str(float(exact))) != exact, (
        "this value survives a float round trip unchanged, so the assertion "
        "above cannot detect a float in transit and the test is not testing "
        "what it says. Pick a value that uses the column's full precision"
    )


async def test_a_json_file_naming_a_published_set_is_refused(
    admin_client, admin_app, session, two_sets
):
    """The draft-only rule is checked against the file's own rows, whatever
    the file is written in.

    The visitor here is on a screen they are entitled to import into; the file
    names the **published** set. Every `submission` stamps the
    `factor_set_id` it was calculated against and has to reproduce years
    later, which it cannot do if that set gains, loses or alters a row.
    """
    live, draft = two_sets
    session.commit()

    token = await _token(admin_client, "constant")
    response = await _post_json(
        admin_client, "constant",
        [{"factor_set": live.version_label, "code": f"{_PREFIX}NOPE",
          "value": "1.0", "unit": "x", "note": "into the published set"}],
        token=token,
    )

    assert response.status_code == 400, response.text
    assert "published" in response.text and live.version_label in response.text, (
        "a JSON file naming the published factor set was not refused by name: "
        f"{response.text}"
    )
    factory = admin_app.state.session_factory
    with factory() as db:
        assert db.scalar(
            select(Constant).where(Constant.code == f"{_PREFIX}NOPE")
        ) is None, "the row was written into a published set"


async def test_a_code_nothing_answers_to_is_refused_in_wp3s_own_words(
    admin_client
):
    """Foreign keys in a JSON file are the referenced row's `code`, resolved
    by the same pass that resolves a CSV file's — including the refusal, which
    says what a code is and why it is not a number."""
    token = await _token(admin_client, "unit-preset")
    response = await _post_json(
        admin_client, "unit-preset",
        [{"code": f"{_PREFIX}fk", "label": "WP4 fk",
          "food_category": "no_such_category", "kg_per_unit": "1.0000",
          "source_note": None, "active": True}],
        token=token,
    )

    assert response.status_code == 400, response.text
    assert "no_such_category" in response.text, response.text
    assert "food_category" in response.text, response.text


async def test_a_second_upload_of_the_same_json_updates_rather_than_duplicates(
    admin_client, admin_app
):
    """WP3b's upsert, reached through the JSON door.

    An entry format with a persistence path of its own would insert twice and
    be refused by the unique constraint — or, worse, not be.
    """
    token = await _token(admin_client, "sector")
    first = await _post_json(admin_client, "sector", _sector_rows("upsert"),
                             token=token)
    assert first.status_code == 200, first.text

    corrected = _sector_rows("upsert")
    corrected[0]["name"] = "WP4 upsert corrected"
    second = await _post_json(admin_client, "sector", corrected, token=token)
    assert second.status_code == 200, second.text

    rows = _sectors(admin_app)
    assert list(rows) == [f"{_PREFIX}upsert"], (
        f"the second upload added a row instead of correcting one: {list(rows)}"
    )
    assert rows[f"{_PREFIX}upsert"].name == "WP4 upsert corrected"
    actions = [entry.action for entry in _entries(admin_app,
                                                  admin_client.staff.username)]
    assert actions.count("update") == 1, (
        f"the second upload was not recorded as a correction: {actions}"
    )


async def test_a_dry_run_of_a_json_file_previews_it_and_writes_nothing(
    admin_client, admin_app
):
    """The preview the dialog shows, on a JSON upload, with its line note.

    The note is the one thing the preview has to say about the format: every
    rejection in this panel names a *line*, and a JSON array has none of its
    own, so the entries are numbered as the rows they become and the reader is
    told what the numbers mean.
    """
    token = await _token(admin_client, "sector")
    response = await _post_json(admin_client, "sector",
                                _sector_rows("dry"), token=token, dry_run=True)

    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["dry_run"] is True and plan["ok"] is True, plan
    assert plan["counts"]["created"] == 1 and plan["counts"]["updated"] == 0
    assert "array" in plan["line_note"], (
        f"the preview of a JSON file says nothing about its line numbers: "
        f"{plan['line_note']!r}"
    )
    assert _sectors(admin_app) == {}, "the dry run wrote the row"
    assert _entries(admin_app, admin_client.staff.username) == [], (
        "the dry run wrote an audit entry"
    )


# --- 3. the four faults only a JSON file can have -------------------------


async def test_a_file_that_is_not_an_array_is_refused_and_says_what_is_wanted(
    admin_client, admin_app
):
    token = await _token(admin_client, "sector")
    response = await _post_json(admin_client, "sector",
                                {"code": f"{_PREFIX}single"}, token=token)

    assert response.status_code == 400, response.text
    assert "array of objects" in response.text, response.text
    assert "a single object" in response.text, (
        f"the refusal does not say what did arrive: {response.text}"
    )
    assert _sectors(admin_app) == {}


async def test_a_file_that_is_not_json_at_all_names_the_real_line(
    admin_client, admin_app
):
    """A syntax error is found before there are any rows to number, so the
    line it names is a real line of the file and the message says so — the one
    place in this module where "line" means something different, and the
    difference is stated rather than left to be inferred."""
    token = await _token(admin_client, "sector")
    response = await _post_json(admin_client, "sector",
                                '[{"code": "a",\n  "name": oops}]', token=token)

    assert response.status_code == 400, response.text
    assert "not valid JSON" in response.text, response.text
    assert "line 2" in response.text, (
        f"the refusal does not point at the syntax error: {response.text}"
    )
    assert "real lines of this file" in response.text, response.text


async def test_a_row_missing_a_column_is_refused_naming_it(
    admin_client, admin_app
):
    """The same rule `parse_csv` applies to a header, applied per row.

    A JSON object can leave a key out where a CSV row cannot leave a cell out,
    so the refusal has to name the row as well as the column — and has to say
    that blank is written, not omitted, because that is the correction.
    """
    rows = _sector_rows("short")
    del rows[0]["description"]
    token = await _token(admin_client, "sector")
    response = await _post_json(admin_client, "sector", rows, token=token)

    assert response.status_code == 400, response.text
    assert '"description"' in response.text, response.text
    assert "Line 2" in response.text, response.text
    assert "null" in response.text, (
        f"the refusal does not say how to write a blank cell: {response.text}"
    )
    assert _sectors(admin_app) == {}


async def test_a_cell_holding_a_list_is_refused_naming_the_column(
    admin_client, admin_app
):
    """One cell holds one value. A list or a nested object is the one shape a
    cell cannot express, and the only shape this reader refuses on its own."""
    rows = _sector_rows("nested")
    rows[0]["name"] = ["WP4", "nested"]
    token = await _token(admin_client, "sector")
    response = await _post_json(admin_client, "sector", rows, token=token)

    assert response.status_code == 400, response.text
    assert '"name"' in response.text and "list" in response.text, response.text
    assert _sectors(admin_app) == {}


async def test_a_json_file_may_write_true_and_a_number_as_themselves(
    admin_client, admin_app
):
    """The reader is deliberately more liberal than the writer.

    The export writes every cell as a string, because that is one rule and it
    makes the CSV and the JSON of a table carry identical text. A person
    hand-writing a file, or a script producing one, will type `true` and
    `941` — and refusing those would be refusing a file that says exactly what
    it means.
    """
    token = await _token(admin_client, "sector")
    response = await _post_json(
        admin_client, "sector",
        [{"code": f"{_PREFIX}liberal", "name": "WP4 liberal",
          "description": None, "sort_order": 941, "active": False}],
        token=token,
    )

    assert response.status_code == 200, response.text
    row = _sectors(admin_app)[f"{_PREFIX}liberal"]
    assert row.sort_order == 941
    assert row.active is False, (
        "a JSON `false` was not read as false, so every row written that way "
        "would be active"
    )
    assert row.description is None
