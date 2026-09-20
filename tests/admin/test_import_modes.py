"""The two import modes, and the dry run that says what either one would do.

WP1 (tests/admin/test_import.py) settled who may import and that an import is
audited; WP2 (test_import_cleaning.py) settled what a spreadsheet does to a
number; WP3 (test_import_tables.py) settled which fourteen tables import, how
their foreign keys are named and that a factor row may only reach a draft.
**All three left the import insert-only**, which is sqladmin's own behaviour:
``Query._get_model_object`` is ``return self.model_view.model(**data)``, a new
instance per row, no lookup. So *export, correct in a spreadsheet, re-import* —
the workflow the whole feature exists for — collided on the row's own key.

This file is the three things that close it.

* **Upsert on the natural key.** A key in the file that exists updates that
  row; one that does not creates it; a row the file does not name is left
  alone. Matched on the natural key and never on ``id``, because ids differ
  between deployments.
* **"Also deactivate the rest".** The same, and every row the file does not
  name is set ``active = false`` — or, on the five tables with no ``active``
  column, deleted. Both are driven here, on a table of each kind.
* **The dry run.** What an import *would* do, computed without writing. Proved
  by reading the table **and** ``audit_log`` back out afterwards, not by
  reading the code that decides.

**Every claim about what the panel would do is paired with the import that
does it.** A preview is worth nothing on its own: it is only true if the
import that follows it agrees, so the two are asserted against each other
rather than each against a number written in this file.
"""

import json
import re
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from admin.comparison_models import ComparisonScenario, ComparisonScenarioLine
from admin.factor_models import Constant
from admin.importing import (
    AuditedImport,
    MODE_DEACTIVATE_MISSING,
    MODE_UPSERT,
    _key_cells,
    natural_key_column,
    natural_key_columns,
    owning_scope_column,
    retirement_for,
)
from admin.models import AuditLog
from admin.taxonomy_models import Sector

pytestmark = [pytest.mark.db, pytest.mark.asyncio]

#: Every row this file creates carries this in its `code`, and the teardown
#: sweeps on it - the same reasoning as `wp1_`, `wp2_` and `wp3_` before it. A
#: stray `sector` row is not litter in a test database: it appears in the
#: public calculator.
_PREFIX = "wp3b_"

_SECTOR_HEADER = "code,name,sort_order,active,description"


@pytest.fixture
def session(_committed_session):
    """This file's own name for tests/admin/conftest.py's hard-committing
    session - see that module's `_committed_session` docstring for why the
    shared fixture is not itself called `session`."""
    return _committed_session


@pytest.fixture(autouse=True)
def _clean_rows(admin_app, _committed_session):
    """Remove this file's rows and their audit entries, before and after.

    `_committed_session` is requested although nothing here uses it directly,
    for the reason tests/admin/test_import_tables.py gives at length: asking
    for it makes it set up first and therefore torn down last, after the child
    rows that point at its `e6_` taxonomy are gone.
    """

    def sweep() -> None:
        factory = admin_app.state.session_factory
        with factory() as db:
            db.execute(
                text("DELETE FROM comparison_scenario_line WHERE scenario_id IN "
                     "(SELECT id FROM comparison_scenario WHERE code LIKE :like)"),
                {"like": _PREFIX + "%"},
            )
            db.execute(
                text("DELETE FROM constant WHERE code LIKE :like"),
                {"like": _PREFIX + "%"},
            )
            for table in ("comparison_scenario", "sector"):
                ids = db.execute(
                    text(f"SELECT id FROM {table} WHERE code LIKE :like"),
                    {"like": _PREFIX + "%"},
                ).scalars().all()
                for row_id in ids:
                    db.execute(
                        text("DELETE FROM audit_log WHERE table_name = :table "
                             "AND row_id = :id"),
                        {"table": table, "id": row_id},
                    )
                db.execute(
                    text(f"DELETE FROM {table} WHERE code LIKE :like"),
                    {"like": _PREFIX + "%"},
                )
            db.commit()

    sweep()
    yield
    sweep()


def _live_admin(app):
    """sqladmin's `Admin` instance, via a bound endpoint on its mounted app."""
    for route in app.routes:
        for sub in getattr(getattr(route, "app", None), "routes", []):
            endpoint = getattr(sub, "endpoint", None)
            if endpoint is not None and hasattr(endpoint, "__self__"):
                return endpoint.__self__
    raise AssertionError("sqladmin's Admin instance was not reachable")


def _importing_views(app):
    return [
        view for view in _live_admin(app)._views
        if hasattr(view, "model") and isinstance(view, AuditedImport)
    ]


async def _token(client, identity: str) -> str:
    page = await client.get(f"/admin/{identity}/list")
    assert page.status_code == 200, f"the {identity} list page did not render"
    match = re.search(r'id="kaicalc-import-csrf" value="([^"]+)"', page.text)
    assert match, f"the {identity} list page rendered no CSRF token"
    return match.group(1)


async def _post(client, identity: str, content: bytes, *, token, mode=None,
                dry_run=False, filename="rows.csv"):
    data = {"csrf_token": token}
    if mode is not None:
        data["import_mode"] = mode
    headers = {"X-Dry-Run": "true"} if dry_run else {}
    return await client.post(
        f"/admin/{identity}/import",
        files={"csvfile": (filename, content, "text/csv")},
        data=data,
        headers=headers,
    )


def _csv(header: str, *rows: str) -> bytes:
    return ("\r\n".join((header, *rows)) + "\r\n").encode("utf-8")


def _result(response) -> dict:
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    finals = [event for event in events if event.get("type") == "result"]
    assert finals, f"the import stream carried no result event: {response.text!r}"
    return finals[-1]


def _entries(admin_app, actor: str) -> list[AuditLog]:
    factory = admin_app.state.session_factory
    with factory() as db:
        return list(
            db.scalars(
                select(AuditLog).where(AuditLog.actor == actor).order_by(AuditLog.id)
            ).all()
        )


def _clear_entries(admin_app, actor: str) -> None:
    """Forget the trail so far, so that the next assertion is about the next
    import rather than about every import the test has made."""
    factory = admin_app.state.session_factory
    with factory() as db:
        db.execute(text("DELETE FROM audit_log WHERE actor = :actor"),
                   {"actor": actor})
        db.commit()


def _sectors(admin_app) -> dict:
    factory = admin_app.state.session_factory
    with factory() as db:
        return {
            row.code: {"name": row.name, "active": row.active,
                       "sort_order": row.sort_order, "id": row.id}
            for row in db.scalars(
                select(Sector).where(Sector.code.like(_PREFIX + "%"))
            ).all()
        }


# --- 1. What identifies a row, read off the schema -------------------------

#: The natural key of each of the fourteen, as the schema's own UNIQUE
#: constraints give it. **This is the finding, written down.** The plan
#: assumed `code` on thirteen of them; it is `code` alone on eight, three have
#: no `code` column at all, and `comparison_scenario_line` has none either.
_NATURAL_KEYS = {
    "sector": ("code",),
    "food_category": ("code",),
    "food_item": ("code",),
    "destination_group": ("code",),
    "destination": ("code",),
    "metric": ("code",),
    "unit_preset": ("code",),
    "comparison_scenario": ("code",),
    "constant": ("factor_set_id", "code"),
    "equivalence": ("factor_set_id", "code"),
    "formula": ("factor_set_id", "metric_id"),
    "comparison_scenario_line": ("scenario_id", "destination_id"),
    "factor_upstream": ("factor_set_id", "sector_id", "food_category_id",
                        "food_item_id", "destination_id", "metric_id"),
    "factor_downstream": ("factor_set_id", "destination_id", "sector_id",
                          "food_category_id", "metric_id"),
}


async def test_the_natural_key_of_each_importing_table_is_its_unique_constraint(
    admin_app
):
    """What an uploaded row is matched against, on every one of the fourteen.

    **Written out here rather than derived here.** `natural_key_columns` reads
    the table's UNIQUE constraint, which is the right place to read it from -
    but a test that re-derived it the same way would pass against any
    derivation at all, including one that returned the primary key. The
    literal above is the independent statement; if the two disagree, one of
    them has changed and a human has to say which.

    The consequence of getting this wrong is not an error. Match on too few
    columns and a file updates a row it was not talking about; match on too
    many - the primary key, say - and every row of a re-imported export is a
    duplicate.
    """
    seen = {}
    for view in _importing_views(admin_app):
        table = view.model.__table__.name
        seen[table] = tuple(
            column.name for column in natural_key_columns(view.model)
        )

    assert seen == _NATURAL_KEYS, (
        "the columns an imported row is matched on are not the ones the "
        "schema's UNIQUE constraints name. Differences: "
        + repr({k: (seen.get(k), _NATURAL_KEYS.get(k))
                for k in set(seen) | set(_NATURAL_KEYS)
                if seen.get(k) != _NATURAL_KEYS.get(k)})
    )


async def test_every_part_of_every_natural_key_is_a_column_the_file_carries(
    admin_app
):
    """A key part outside the import columns is a key that can never match.

    The failure is silent and total: every uploaded row would look new, the
    file would insert duplicates of rows that already exist, and the database
    would refuse the file on the very constraint the key was read from. The
    message a staff member would get is sqladmin's "Duplicate entry", naming
    the constraint and not the column.

    `_key_cells` is also what maps a key column onto the *name* it is written
    under - `factor_set_id` is written in a column called `factor_set` -
    which is the half that would break first if a view renamed a field.
    """
    for view in _importing_views(admin_app):
        cells = _key_cells(view)
        names = [name for _, name in cells]
        assert len(names) == len(natural_key_columns(view.model)), view.identity
        for name in names:
            assert name in view._import_prop_names, (
                f"{view.identity} matches uploaded rows on a column the file "
                f"does not carry ({name}), so no row could ever match"
            )


async def test_the_single_column_key_is_the_one_a_foreign_key_names_a_row_by(
    admin_app
):
    """The two answers this module gives have to be the same answer.

    `natural_key_column` (singular, WP3) says how a foreign-key **cell** names
    a row of the table it points at; `natural_key_columns` (plural) says which
    columns identify a row for the upsert. Where a table has a single-column
    natural key the two are talking about the same column, and a file that
    wrote `code` while the upsert matched on something else would update the
    wrong row without an error anywhere.
    """
    for view in _importing_views(admin_app):
        columns = natural_key_columns(view.model)
        if len(columns) != 1:
            continue
        assert natural_key_column(view.model) is columns[0], (
            f"{view.identity} names a row by "
            f"{natural_key_column(view.model).name} in a foreign-key cell and "
            f"matches it on {columns[0].name} in an import"
        )


# --- 2. Deactivate, and where it is a real delete instead -------------------

#: Which of the fourteen the second mode deactivates and which it deletes,
#: and what the "rest" is scoped to. **Also the finding.** The plan named
#: eight tables with `active` and one exception; there are nine and five.
_RETIREMENT = {
    "sector": ("deactivate", None),
    "food_category": ("deactivate", None),
    "food_item": ("deactivate", None),
    "destination_group": ("deactivate", None),
    "destination": ("deactivate", None),
    "metric": ("deactivate", None),
    "unit_preset": ("deactivate", None),
    "comparison_scenario": ("deactivate", None),
    "equivalence": ("deactivate", "factor_set_id"),
    "constant": ("delete", "factor_set_id"),
    "formula": ("delete", "factor_set_id"),
    "factor_upstream": ("delete", "factor_set_id"),
    "factor_downstream": ("delete", "factor_set_id"),
    "comparison_scenario_line": ("delete", "scenario_id"),
}


async def test_what_the_second_mode_does_to_each_table_and_what_it_is_scoped_to(
    admin_app
):
    """Deactivate or delete, and how far "the rest" reaches.

    Two decisions in one table because they are the same decision seen twice.
    A table with an `active` column deactivates, which keeps historical
    reproducibility, is invisible to the public and is reversible; a table
    without one deletes, and the five that do are named rather than left to
    hide inside a general rule.

    **The scope is the half that would go wrong quietly.** "Every row the file
    does not name" is the whole table for `sector` and must not be for
    `constant`: a file of one draft's constants would otherwise delete every
    other factor set's, published sets included. Read off the one `ON DELETE
    CASCADE` foreign key each child table carries.
    """
    seen = {}
    for view in _importing_views(admin_app):
        scope = owning_scope_column(view.model)
        seen[view.model.__table__.name] = (
            retirement_for(view.model), None if scope is None else scope.name,
        )

    assert seen == _RETIREMENT, (
        "what the second import mode would do is not what this file says. "
        "Differences: "
        + repr({k: (seen.get(k), _RETIREMENT.get(k))
                for k in set(seen) | set(_RETIREMENT)
                if seen.get(k) != _RETIREMENT.get(k)})
    )


async def test_a_table_that_would_be_deleted_from_is_referenced_by_nothing(
    admin_app
):
    """The condition under which deleting is safe, checked rather than argued.

    Deactivation is not caution: the seven taxonomy tables are pointed at by
    `submission_entry` and `submission_line`, so once any calculation has been
    run the database refuses to delete those rows - and with
    `continue_on_error=False` a refused delete aborts the whole import. A
    table gaining an inbound foreign key while keeping no `active` column
    would turn the second mode into an import that fails on every deployment
    that has ever been used, and there would be nothing on the screen to say
    so.
    """
    from db.base import Base

    for view in _importing_views(admin_app):
        if retirement_for(view.model) != "delete":
            continue
        table = view.model.__table__.name
        referrers = [
            f"{other.name}.{fk.parent.name}"
            for other in Base.metadata.tables.values()
            for fk in other.foreign_keys
            if fk.column.table.name == table
        ]
        assert referrers == [], (
            f"{table} has no `active` column, so the second import mode "
            f"deletes from it - and {referrers} point at it, so the database "
            "will refuse that delete and abort the whole file"
        )


# --- 3. Upsert, over HTTP ---------------------------------------------------


async def test_a_second_file_with_the_same_code_updates_the_row_it_names(
    admin_client, admin_app
):
    """The whole point. Export, correct in a spreadsheet, re-import.

    Before this, sqladmin's insert-only persistence met the row's own unique
    `code` and the database refused the file - `Duplicate entry`, on the
    second upload of a file that differs from the first by one cell.

    Asserted on the row's `id` as well as its values: an upsert that deleted
    and re-inserted would satisfy every other assertion here and would break
    every `submission_entry` pointing at the row.
    """
    token = await _token(admin_client, "sector")
    first = await _post(admin_client, "sector",
                        _csv(_SECTOR_HEADER,
                             f"{_PREFIX}alpha,Alpha,901,1,first"),
                        token=token)
    assert first.status_code == 200, first.text
    original = _sectors(admin_app)[f"{_PREFIX}alpha"]

    second = await _post(admin_client, "sector",
                         _csv(_SECTOR_HEADER,
                              f"{_PREFIX}alpha,Alpha corrected,902,1,second"),
                         token=token)

    assert second.status_code == 200, second.text
    assert _result(second)["imported"] == 1, _result(second)["summary"]
    rows = _sectors(admin_app)
    assert list(rows) == [f"{_PREFIX}alpha"], (
        "re-importing a file whose code already exists made a second row "
        f"rather than updating the first: {sorted(rows)}"
    )
    assert rows[f"{_PREFIX}alpha"]["name"] == "Alpha corrected", (
        "the row was matched and not written; the correction was discarded"
    )
    assert rows[f"{_PREFIX}alpha"]["id"] == original["id"], (
        "the row was replaced rather than updated, so its id changed - every "
        "submission_entry pointing at it now points at nothing"
    )


async def test_one_file_can_update_one_row_and_create_another(
    admin_client, admin_app
):
    """"Update and add" is one mode, not two, and a real corrected export is
    both at once: a row whose figure changed beside a row that is new."""
    token = await _token(admin_client, "sector")
    await _post(admin_client, "sector",
                _csv(_SECTOR_HEADER, f"{_PREFIX}beta,Beta,903,1,x"),
                token=token)
    _clear_entries(admin_app, admin_client.staff.username)

    response = await _post(
        admin_client, "sector",
        _csv(_SECTOR_HEADER,
             f"{_PREFIX}beta,Beta corrected,903,1,x",
             f"{_PREFIX}gamma,Gamma,904,1,y"),
        token=token, mode=MODE_UPSERT,
    )

    assert response.status_code == 200, response.text
    rows = _sectors(admin_app)
    assert sorted(rows) == [f"{_PREFIX}beta", f"{_PREFIX}gamma"]
    assert rows[f"{_PREFIX}beta"]["name"] == "Beta corrected"

    header = _entries(admin_app, admin_client.staff.username)[0]
    assert header.action == "import"
    assert (header.after_json["rows_created"],
            header.after_json["rows_updated"]) == (1, 1), (
        "the file-level audit entry does not say what the file did: it "
        f"recorded {header.after_json}"
    )


async def test_a_row_the_file_does_not_name_is_left_alone_in_the_default_mode(
    admin_client, admin_app
):
    """The line between the two modes, driven from the default side.

    Without this, a mode that deactivated unconditionally would pass every
    test above: they only ever upload files naming every row they created.
    """
    token = await _token(admin_client, "sector")
    await _post(admin_client, "sector",
                _csv(_SECTOR_HEADER,
                     f"{_PREFIX}delta,Delta,905,1,x",
                     f"{_PREFIX}epsilon,Epsilon,906,1,y"),
                token=token)

    await _post(admin_client, "sector",
                _csv(_SECTOR_HEADER, f"{_PREFIX}delta,Delta again,905,1,x"),
                token=token, mode=MODE_UPSERT)

    rows = _sectors(admin_app)
    assert f"{_PREFIX}epsilon" in rows, (
        "a row the file did not mention was removed by the default mode"
    )
    assert rows[f"{_PREFIX}epsilon"]["active"] is True, (
        "a row the file did not mention was deactivated by the default mode, "
        "which is the other mode's job and disappears it from the public "
        "calculator"
    )


async def test_an_update_audit_entry_carries_both_the_before_and_the_after(
    admin_client, admin_app
):
    """The trail's answer to "what did this import change this number from".

    An import that wrote `update` entries with no `before` would satisfy
    "every write produces an audit entry" and be useless for the one question
    an import raises that a hand edit does not: a file changes many rows at
    once and nobody watched it happen.

    The before-snapshot is the hard half. The importer flushes every row
    inside its own SAVEPOINT, so by the commit the row is clean and
    SQLAlchemy's attribute history - which is where the ordinary CRUD path
    reads a "before" from - is empty. It has to be taken at `before_flush`,
    while the row is loaded and not yet touched.
    """
    token = await _token(admin_client, "sector")
    await _post(admin_client, "sector",
                _csv(_SECTOR_HEADER, f"{_PREFIX}zeta,Zeta,907,1,before text"),
                token=token)
    _clear_entries(admin_app, admin_client.staff.username)

    await _post(admin_client, "sector",
                _csv(_SECTOR_HEADER, f"{_PREFIX}zeta,Zeta II,908,1,after text"),
                token=token)

    entries = _entries(admin_app, admin_client.staff.username)
    actions = [entry.action for entry in entries]
    assert actions == ["import", "update"], (
        "re-importing an existing row did not write one file entry and one "
        f"update entry; audit_log has {actions}"
    )
    update = entries[1]
    assert update.before_json is not None, (
        "the update entry carries no before, so the trail cannot say what "
        "this import changed the row from"
    )
    assert update.before_json["name"] == "Zeta"
    assert update.before_json["sort_order"] == 907
    assert update.after_json["name"] == "Zeta II"
    assert update.after_json["sort_order"] == 908
    assert update.row_id == _sectors(admin_app)[f"{_PREFIX}zeta"]["id"]


# --- 4. "Also deactivate the rest" -----------------------------------------


async def test_the_second_mode_deactivates_a_row_the_file_does_not_name(
    admin_client, admin_app
):
    """The second mode, and the thing it must not do.

    **It deactivates; it does not delete**, and both halves are asserted: the
    row is still there, and it is inactive. A delete would be refused by the
    database the moment any calculation had been run against the row, and with
    `continue_on_error=False` that refusal aborts the whole file - so a
    "replace the table" option would be one that fails on every deployment
    that has ever been used.
    """
    token = await _token(admin_client, "sector")
    await _post(admin_client, "sector",
                _csv(_SECTOR_HEADER,
                     f"{_PREFIX}eta,Eta,909,1,x",
                     f"{_PREFIX}theta,Theta,910,1,y"),
                token=token)
    kept_id = _sectors(admin_app)[f"{_PREFIX}theta"]["id"]
    _clear_entries(admin_app, admin_client.staff.username)

    response = await _post(
        admin_client, "sector",
        _csv(_SECTOR_HEADER, f"{_PREFIX}eta,Eta,909,1,x"),
        token=token, mode=MODE_DEACTIVATE_MISSING,
    )

    assert response.status_code == 200, response.text
    rows = _sectors(admin_app)
    assert f"{_PREFIX}theta" in rows, (
        "the second mode deleted the row instead of deactivating it. Once a "
        "calculation has been run the database refuses that delete, and the "
        "whole file aborts with it"
    )
    assert rows[f"{_PREFIX}theta"]["id"] == kept_id
    assert rows[f"{_PREFIX}theta"]["active"] is False, (
        "a row the file did not name is still active, so the mode did nothing"
    )
    assert rows[f"{_PREFIX}eta"]["active"] is True, (
        "the mode deactivated a row the file *did* name"
    )

    entries = _entries(admin_app, admin_client.staff.username)
    header = entries[0]
    assert header.after_json["rows_deactivated"] == 1, (
        "the file-level entry does not count the deactivation apart from the "
        f"rows the file wrote: {header.after_json}"
    )
    assert header.after_json["rows_updated"] == 1, (
        "the deactivation was counted as a row the file wrote; the two are "
        f"different acts: {header.after_json}"
    )
    deactivation = [
        entry for entry in entries[1:]
        if entry.after_json and entry.after_json.get("code") == f"{_PREFIX}theta"
    ]
    assert len(deactivation) == 1, "the deactivated row was not audited"
    assert deactivation[0].before_json["active"] is True
    assert deactivation[0].after_json["active"] is False


async def test_the_default_mode_is_what_an_upload_with_no_mode_field_gets(
    admin_client, admin_app
):
    """An upload that names no mode must not deactivate anything.

    Every caller written before this existed - and WP1's, WP2's and WP3's
    own tests - post no mode field at all. A default of "deactivate the rest"
    would turn each of them into a file that retires the whole table, and
    would do the same to any bookmarked form.
    """
    token = await _token(admin_client, "sector")
    await _post(admin_client, "sector",
                _csv(_SECTOR_HEADER,
                     f"{_PREFIX}iota,Iota,911,1,x",
                     f"{_PREFIX}kappa,Kappa,912,1,y"),
                token=token)

    await _post(admin_client, "sector",
                _csv(_SECTOR_HEADER, f"{_PREFIX}iota,Iota,911,1,x"),
                token=token, mode=None)

    assert _sectors(admin_app)[f"{_PREFIX}kappa"]["active"] is True, (
        "an upload that named no mode deactivated a row it did not mention"
    )


async def test_the_second_mode_on_a_table_with_no_active_column_deletes(
    admin_client, admin_app, session, taxonomy_for_factors
):
    """`comparison_scenario_line`, which is the exception written out loud.

    It has no `active` column, it is a scenario's own child and nothing
    references it, so "the file is the scenario's lines" is a real delete -
    there is nothing an inactive line could mean. Said here rather than left
    to a general rule, because a reader meeting the rule alone would assume
    every table deactivates.

    **And the delete is scoped to the scenarios the file names.** A second
    scenario's lines are in the table and not in the file, and they have to
    survive: a file about one scenario is not a statement about another.
    """
    taxonomy = taxonomy_for_factors
    subject = ComparisonScenario(code=f"{_PREFIX}subject", name="Subject",
                                 sector_id=taxonomy.sector.id, gwp_horizon=100)
    bystander = ComparisonScenario(code=f"{_PREFIX}bystander", name="Bystander",
                                   sector_id=taxonomy.sector.id, gwp_horizon=100)
    session.add_all([subject, bystander])
    session.flush()
    session.add_all([
        ComparisonScenarioLine(scenario_id=subject.id,
                              destination_id=taxonomy.destination.id,
                              qty_kg=Decimal("100.000")),
        ComparisonScenarioLine(scenario_id=bystander.id,
                              destination_id=taxonomy.destination.id,
                              qty_kg=Decimal("200.000")),
    ])
    session.commit()

    # A file naming the subject scenario and a *different* destination, so
    # that the line already there is one the file does not carry.
    second_destination = _second_destination(admin_app, session, taxonomy)
    token = await _token(admin_client, "comparison-scenario-line")
    response = await _post(
        admin_client, "comparison-scenario-line",
        _csv("scenario,destination,qty_kg",
             f"{subject.code},{second_destination.code},300.000"),
        token=token, mode=MODE_DEACTIVATE_MISSING,
    )

    assert response.status_code == 200, response.text
    factory = admin_app.state.session_factory
    with factory() as db:
        subject_lines = db.scalars(
            select(ComparisonScenarioLine).where(
                ComparisonScenarioLine.scenario_id == subject.id)
        ).all()
        bystander_lines = db.scalars(
            select(ComparisonScenarioLine).where(
                ComparisonScenarioLine.scenario_id == bystander.id)
        ).all()

    assert [line.destination_id for line in subject_lines] == \
        [second_destination.id], (
        "the line the file did not carry survived, so the second mode did "
        "nothing on a table that has no `active` column to set"
    )
    assert len(bystander_lines) == 1, (
        "another scenario's line was deleted by a file that never mentioned "
        "that scenario - the retirement is not scoped to the rows the file is "
        "about"
    )


def _second_destination(admin_app, session, taxonomy):
    """A second destination, so a scenario can have a line the file replaces.

    Reuses the `e6_` fixture prefix so `_committed_session`'s own teardown
    sweeps it - a stray `destination` row appears in the public calculator.
    """
    from admin.taxonomy_models import Destination

    existing = session.scalars(
        select(Destination).where(Destination.code == "e6_compost")
    ).first()
    if existing is not None:
        return existing
    destination = Destination(group_id=taxonomy.destination.group_id,
                              code="e6_compost", name="Compost")
    session.add(destination)
    session.commit()
    return destination


async def test_the_second_mode_does_not_reach_another_factor_set(
    admin_client, admin_app, session, two_sets
):
    """The scope, on the table where getting it wrong is unrecoverable.

    `constant` has no `active` column, so the second mode deletes - and it is
    a child of a factor set. A file carrying one draft's constants that
    retired "every row not in the file" across the whole table would delete
    the **published** set's constants, which every stored submission depends
    on to go on reproducing. There is no undo for that and no error message
    either: the calculator would simply start answering differently.

    The draft's second constant is the one the file leaves out, so the test
    also proves the scoped delete happens rather than being skipped wholesale.
    """
    live, draft = two_sets
    session.commit()
    _clear_entries(admin_app, admin_client.staff.username)

    token = await _token(admin_client, "constant")
    response = await _post(
        admin_client, "constant",
        _csv("factor_set,code,value,unit,note",
             f"{draft.version_label},GWP_CH4_100,29.8,kg CO2e / kg CH4,kept"),
        token=token, mode=MODE_DEACTIVATE_MISSING,
    )

    assert response.status_code == 200, response.text
    factory = admin_app.state.session_factory
    with factory() as db:
        live_codes = sorted(db.scalars(
            select(Constant.code).where(Constant.factor_set_id == live.id)).all())
        draft_codes = sorted(db.scalars(
            select(Constant.code).where(Constant.factor_set_id == draft.id)).all())

    assert live_codes == ["GWP_CH4_100"], (
        "the published set's constants were touched by a file that named only "
        f"the draft: {live_codes}. Every stored submission calculated against "
        "that set has just stopped reproducing"
    )
    assert draft_codes == ["GWP_CH4_100"], (
        "the draft's own constant that the file left out survived, so the "
        f"second mode did nothing at all: {draft_codes}"
    )


async def test_a_mode_this_panel_does_not_have_is_refused(
    admin_client, admin_app
):
    """An unrecognised mode is a refusal, never a default.

    The two modes differ by whether rows the visitor is not looking at vanish
    from the public calculator. Silently picking one is the kind of thing
    nobody finds until a sector is missing from the form.
    """
    token = await _token(admin_client, "sector")
    response = await _post(admin_client, "sector",
                           _csv(_SECTOR_HEADER, f"{_PREFIX}mu,Mu,913,1,x"),
                           token=token, mode="replace_everything")

    assert response.status_code == 400, (
        f"an unknown import mode was accepted; the panel answered "
        f"{response.status_code}"
    )
    assert _sectors(admin_app) == {}, "the refused upload wrote a row anyway"
    assert _entries(admin_app, admin_client.staff.username) == []


async def test_the_second_mode_refuses_a_file_with_no_rows(
    admin_client, admin_app
):
    """A header and nothing else, asking for every row to be retired.

    **Refused rather than obeyed and refused rather than ignored.** Obeyed it
    would retire the whole table from an empty file. Ignored it would do
    nothing at all - and sqladmin would take that branch by itself, because
    `stream_import_response` never opens a database session when there are no
    rows to persist, so the deactivation pass would never run and a dry run
    would have promised something the import does not do. The refusal is what
    keeps the preview honest.
    """
    token = await _token(admin_client, "sector")
    await _post(admin_client, "sector",
                _csv(_SECTOR_HEADER, f"{_PREFIX}nu,Nu,914,1,x"),
                token=token)

    response = await _post(admin_client, "sector", _csv(_SECTOR_HEADER),
                           token=token, mode=MODE_DEACTIVATE_MISSING)

    assert response.status_code == 400, (
        "a header-only file was accepted in the mode that retires every row "
        f"the file does not name; the panel answered {response.status_code}"
    )
    assert _sectors(admin_app)[f"{_PREFIX}nu"]["active"] is True, (
        "an empty file deactivated the table"
    )


# --- 5. The dry run ---------------------------------------------------------


async def test_a_dry_run_writes_nothing_to_the_table_or_to_the_audit_log(
    admin_client, admin_app
):
    """The whole of the promise, read out of both tables rather than inferred.

    Both, because each alone is passable by a wrong implementation: an import
    that wrote the rows and skipped the audit would satisfy an audit-only
    check, and one that audited a file it never wrote would satisfy a
    rows-only check. They are read back after the request, not counted before
    it and trusted.
    """
    token = await _token(admin_client, "sector")
    await _post(admin_client, "sector",
                _csv(_SECTOR_HEADER, f"{_PREFIX}xi,Xi,915,1,before"),
                token=token)
    before_rows = _sectors(admin_app)
    _clear_entries(admin_app, admin_client.staff.username)

    response = await _post(
        admin_client, "sector",
        _csv(_SECTOR_HEADER,
             f"{_PREFIX}xi,Xi rewritten,916,1,after",
             f"{_PREFIX}omicron,Omicron,917,1,new"),
        token=token, mode=MODE_DEACTIVATE_MISSING, dry_run=True,
    )

    assert response.status_code == 200, response.text
    assert _sectors(admin_app) == before_rows, (
        "a dry run changed the table. It wrote "
        f"{_sectors(admin_app)} where {before_rows} was there before"
    )
    assert _entries(admin_app, admin_client.staff.username) == [], (
        "a dry run wrote an audit entry, which is a trail claiming a change "
        "that never happened"
    )


async def test_a_dry_run_says_what_the_import_then_actually_does(
    admin_client, admin_app
):
    """The preview and the import it previews, asserted against each other.

    **Not against numbers written in this file.** A preview is only worth
    something if the import agrees with it, and two descriptions of the same
    file are two things that can drift apart - which is this project's
    recurring failure. So the counts come out of the dry run and are compared
    with what the table holds after the same file is uploaded for real.

    The file is deliberately all three things at once: one row that exists,
    one that does not, and one row in the table that the file leaves out.
    """
    token = await _token(admin_client, "sector")
    await _post(admin_client, "sector",
                _csv(_SECTOR_HEADER,
                     f"{_PREFIX}pi,Pi,918,1,x",
                     f"{_PREFIX}rho,Rho,919,1,y"),
                token=token)

    content = _csv(_SECTOR_HEADER,
                   f"{_PREFIX}pi,Pi corrected,918,1,x",
                   f"{_PREFIX}sigma,Sigma,920,1,z")

    preview = await _post(admin_client, "sector", content, token=token,
                          mode=MODE_DEACTIVATE_MISSING, dry_run=True)
    assert preview.status_code == 200, preview.text
    counts = preview.json()["counts"]
    assert counts == {"created": 1, "updated": 1, "deactivated": 1,
                      "deleted": 0, "rejected": 0}, (
        f"the preview does not describe the file it was given: {counts}"
    )
    assert preview.json()["ok"] is True

    real = await _post(admin_client, "sector", content, token=token,
                       mode=MODE_DEACTIVATE_MISSING)
    assert real.status_code == 200, real.text

    rows = _sectors(admin_app)
    created = [code for code in rows if code == f"{_PREFIX}sigma"]
    deactivated = [code for code, row in rows.items() if not row["active"]]
    assert len(created) == counts["created"], (
        f"the preview promised {counts['created']} new row(s) and the import "
        f"made {len(created)}"
    )
    assert len(deactivated) == counts["deactivated"], (
        f"the preview promised {counts['deactivated']} deactivation(s) and "
        f"the import made {len(deactivated)}: {deactivated}"
    )
    assert rows[f"{_PREFIX}pi"]["name"] == "Pi corrected"
    assert deactivated == [f"{_PREFIX}rho"]


async def test_a_dry_run_reports_the_rows_that_would_be_refused(
    admin_client, admin_app
):
    """The reason the preview has to exist at all.

    Upsert makes a mistyped key silently a new row instead of a correction,
    and the preview is the only place a reader catches it - so a preview that
    said "2 to add" for a file whose second row the import would refuse would
    be worse than none. It runs sqladmin's own `validate_import_row`, not a
    second set of rules, and it names the line the import would abort at,
    because `continue_on_error` is pinned false and one bad row means no rows
    at all.
    """
    token = await _token(admin_client, "sector")
    response = await _post(
        admin_client, "sector",
        _csv(_SECTOR_HEADER,
             f"{_PREFIX}tau,Tau,921,1,fine",
             f"{_PREFIX}upsilon,Upsilon,922,maybe,not a boolean"),
        token=token, dry_run=True,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is False, (
        "a file with a row the import would refuse was previewed as fine"
    )
    assert body["aborts_at_line"] == 3, (
        "the preview does not say where the import would stop; with "
        "continue_on_error pinned false the rows before that line are not "
        f"written either. It said {body['aborts_at_line']}"
    )
    assert body["counts"]["rejected"] >= 1
    assert any("Line 3" in message for message in body["rejected"]), (
        f"no rejection names the line it is about: {body['rejected']}"
    )
    assert _sectors(admin_app) == {}, "a refused preview wrote a row"


async def test_a_dry_run_header_that_is_neither_true_nor_false_is_refused(
    admin_client, admin_app
):
    """`X-Dry-Run`, read the way `api/router.py` reads it.

    The same word deliberately - this panel already has a dry-run vocabulary
    and a second one is a second thing to learn. A typo must not silently
    write the file, which is what an `if header == "true"` would do.
    """
    token = await _token(admin_client, "sector")
    response = await admin_client.post(
        "/admin/sector/import",
        files={"csvfile": ("rows.csv",
                           _csv(_SECTOR_HEADER, f"{_PREFIX}phi,Phi,923,1,x"),
                           "text/csv")},
        data={"csrf_token": token},
        headers={"X-Dry-Run": "yes-please"},
    )

    assert response.status_code == 400, (
        "a misspelt X-Dry-Run header was treated as 'not a dry run' and the "
        f"file was written; the panel answered {response.status_code}"
    )
    assert _sectors(admin_app) == {}


async def test_the_modal_offers_the_mode_and_names_what_it_will_do(
    admin_client
):
    """The control has to say what happens on the screen it is on.

    "Deactivate rows not in the file" is true on nine of the fourteen and
    false on the five that have no `active` column, where the second mode is a
    real delete. A staff member choosing an option must not be told something
    the panel will not do - and the server-side default means a screen that
    rendered no control at all would look like it worked.
    """
    sector_page = await admin_client.get("/admin/sector/list")
    line_page = await admin_client.get("/admin/comparison-scenario-line/list")

    assert 'id="kaicalc-import-mode"' in sector_page.text, (
        "the Sectors screen offers no way to choose the import mode, so the "
        "second mode is unreachable from a browser"
    )

    # SCOPED TO THE OPTION'S OWN TEXT, AND THAT WAS MEASURED RATHER THAN
    # ASSUMED. Written first as `"deactivated" in sector_page.text`, this
    # assertion survived a mutation that made every table delete - because the
    # Sectors list page already carries the word twice: once in this option and
    # once in the bulk-deactivate action's confirmation dialog ("This
    # deactivates every selected row"). Counted directly on the rendered page:
    # two occurrences with the word correct, one with it wrong, and `in` cannot
    # tell those apart. The same trap waits on the other side, where "deleted"
    # would match a row-delete control.
    sector_option = _second_mode_option(sector_page.text)
    line_option = _second_mode_option(line_page.text)

    assert "deactivated" in sector_option and "deleted" not in sector_option, (
        "the Sectors screen's second-mode option does not say the rows are "
        f"deactivated: {sector_option!r}"
    )
    assert "deleted" in line_option and "deactivated" not in line_option, (
        "the scenario-lines screen offers to 'deactivate' rows on a table "
        "with no `active` column, where the rows are deleted outright: "
        f"{line_option!r}"
    )
    assert "import_mode" in (await admin_client.get(
        "/admin/static/import-csrf.js")).text, (
        "nothing puts the chosen mode into sqladmin's hand-built upload, so "
        "every import from a browser is the default whatever is selected"
    )


def _second_mode_option(page: str) -> str:
    """The words inside the second mode's own `<option>`, and nothing else on
    the page."""
    match = re.search(r'<option value="deactivate_missing">(.*?)</option>',
                      page, re.S)
    assert match, "the page renders no option for the second import mode"
    return " ".join(match.group(1).split())
