"""The fourteen tables, the four refusals, foreign keys by `code`, and the
draft-only rule.

WP1 (tests/admin/test_import.py) settled who may import, that an import is
audited and that a file lands whole or not at all, on `sector`. WP2
(tests/admin/test_import_cleaning.py) settled what a spreadsheet does to a
number, on `unit_preset`. Both were pilots on tables chosen for having nothing
else in them. This file is the rest of the surface:

* **Fourteen tables import and five do not**, and the five are not a leftover.
  Four are refused on principle — `audit_log`, `staff`, `submission`,
  `ip_block` — and each refusal is written as a comment on the view that does
  not have the attribute, because a refusal nobody can find gets "fixed" by
  the next person. `factor_set` is refused for a different reason again.
* **A foreign key in an imported file is the referenced row's `code`**, never
  its number. Ids differ between one deployment and the next, so a file keyed
  on them could only ever be loaded back into the database it came from.
* **The five factor children may be imported only into a draft**, checked
  against the file's own rows rather than against the page the visitor is
  standing on — because a visitor on a draft's page can upload a file whose
  rows name the published set, and that is the file these tests upload.
* **`DECIMAL` survives the journey.** WP2 measured that a `float()` in
  transit cannot be detected on a `decimal(12,4)` column — twelve significant
  digits against a double's fifteen to seventeen means no value the column can
  hold is changed by the round trip, 0 of 50,000 — and handed the real test to
  this file, where the columns are `decimal(20,10)` and 49,974 of 50,000 are.

**Every refusal is paired with an acceptance.** A panel that refused every
file would pass a file of refusal tests; so each rejection below is followed
by the same upload with the one thing corrected, which must land.
"""

import json
import re
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from admin.comparison_models import ComparisonScenario, ComparisonScenarioLine
from admin.factor_models import Constant, FactorSetStatus, FactorUpstream
from admin.importing import (
    _DRAFT, AuditedImport, foreign_key_import_columns, natural_key_column,
)
from admin.models import AuditLog
from admin.taxonomy_models import FoodItem, Metric, UnitPreset

pytestmark = [pytest.mark.db, pytest.mark.asyncio]

#: Every row this file creates carries this in its `code`, and the teardown
#: sweeps on it rather than on a hand-kept list of names - the same reasoning
#: as `wp1_` and `wp2_` in the two files before it. A stray `food_item` or
#: `unit_preset` row is not litter in a test database: both appear in the
#: public calculator.
_PREFIX = "wp3_"

#: The four tables that get no import, ever, with the reason each one is
#: refused for. The reasons are here as well as on the views because a test
#: that asserted only a boolean would be satisfied by somebody deleting the
#: comment.
_REFUSED = {
    "audit-log": "a log that can be written to is not a log",
    "staff": "a row carries a password hash and a TOTP secret; an import "
             "is privilege escalation with a file upload",
    # `submissions`, plural: SubmissionAdmin overrides sqladmin's identity.
    "submissions": "these rows are the public statistics; an import is a way "
                   "to manufacture them",
    "ip-block": "a security control, where a bulk overwrite is a bulk change "
                "to the panel's own defences",
}

#: The five tables whose rows belong to a factor set and may therefore only be
#: imported into a draft. Every `submission` stamps the `factor_set_id` it was
#: calculated against and has to keep reproducing years later.
_DRAFT_ONLY = sorted([
    "factor-upstream", "factor-downstream", "constant", "formula", "equivalence",
])


@pytest.fixture
def session(_committed_session):
    """This file's own name for tests/admin/conftest.py's hard-committing
    session - see that module's `_committed_session` docstring for why the
    shared fixture is not itself called `session`."""
    return _committed_session


@pytest.fixture(autouse=True)
def _clean_rows(admin_app, _committed_session):
    """Remove this file's own taxonomy rows and their audit entries.

    The factor rows are not swept here: every one of them belongs to an
    `e6-` factor set, and `_committed_session`'s own teardown deletes those
    sets, which cascades (`ondelete="CASCADE"` on every child's
    `factor_set_id`).

    Before as well as after: `code` is unique-constrained, so a run
    interrupted partway leaves a row that makes the next run's first import
    fail for a reason unrelated to what it asserts.

    **`_committed_session` is requested although nothing here uses it**, and
    that is what puts the two teardowns in the right order. A `wp3_` food item
    imported by a test below points at the `e6_dairy` category
    `taxonomy_for_factors` created, and `_committed_session`'s teardown
    deletes that category; run first, its `DELETE FROM food_category` fails on
    the foreign key and leaves every `e6_` row behind, so the *next* test's
    fixtures collide on a unique code and fail for a reason that has nothing
    to do with what they assert. Asking for it here makes it set up first and
    therefore torn down last, after the child rows are gone.
    """

    def sweep() -> None:
        factory = admin_app.state.session_factory
        with factory() as db:
            # Two tables have no `code` to sweep on, and both have to go
            # before the rows they point at. A scenario's lines are matched
            # through their parent scenario; an imported factor row is matched
            # by its `source_note`, and it holds a foreign key to the `wp3_`
            # metric below, so deleting the metric first fails on that key.
            db.execute(
                text("DELETE FROM comparison_scenario_line WHERE scenario_id IN "
                     "(SELECT id FROM comparison_scenario WHERE code LIKE :like)"),
                {"like": _PREFIX + "%"},
            )
            db.execute(
                text("DELETE FROM factor_upstream WHERE source_note LIKE :like"),
                {"like": _PREFIX + "%"},
            )
            db.execute(
                text("DELETE FROM constant WHERE code LIKE :like"),
                {"like": _PREFIX + "%"},
            )
            for table in ("food_item", "unit_preset", "comparison_scenario",
                          "metric"):
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
    """sqladmin's `Admin` instance, via a bound endpoint on its mounted app.

    The same reach tests/admin/test_import.py and test_role_matrix.py use, and
    for the same reason: a class that exists and was never registered would
    satisfy an import-based check while being unreachable.
    """
    for route in app.routes:
        for sub in getattr(getattr(route, "app", None), "routes", []):
            endpoint = getattr(sub, "endpoint", None)
            if endpoint is not None and hasattr(endpoint, "__self__"):
                return endpoint.__self__
    raise AssertionError("sqladmin's Admin instance was not reachable")


def _view(app, identity):
    for view in _live_admin(app)._views:
        if getattr(view, "identity", None) == identity:
            return view
    raise AssertionError(f"no registered view has the identity {identity!r}")


async def _token(client, identity: str, query: str = "") -> str:
    """The CSRF token this session's list page renders for the import modal.

    Taken from the page a visitor would be on rather than minted directly: the
    token only reaches a browser if the modal template renders it, and a test
    that built its own would pass with that template missing the field.
    """
    page = await client.get(f"/admin/{identity}/list{query}")
    assert page.status_code == 200, f"the {identity} list page did not render"
    match = re.search(r'id="kaicalc-import-csrf" value="([^"]+)"', page.text)
    assert match, (
        f"the {identity} list page rendered no CSRF token for the import "
        "modal, so nothing a browser sends could ever carry one"
    )
    return match.group(1)


async def _post(client, identity: str, content: bytes, *, token,
                filename="rows.csv"):
    data = {} if token is None else {"csrf_token": token}
    return await client.post(
        f"/admin/{identity}/import",
        files={"csvfile": (filename, content, "text/csv")},
        data=data,
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


def _rows(admin_app, model, like=_PREFIX + "%"):
    factory = admin_app.state.session_factory
    with factory() as db:
        return {
            row.code: row
            for row in db.scalars(select(model).where(model.code.like(like))).all()
        }


async def _refused(client, admin_app, identity, content, *, query=""):
    """Post a file that must be refused, and hand back its text.

    Asserts the two things every refusal here shares whatever table it is
    against: a 400, and **nothing audited**. The second is what makes "reject
    rather than coerce" mean anything - a trail entry for a file that wrote
    nothing is a trail claiming a change that never happened.

    "Nothing written" is asserted by each caller rather than here, because the
    table differs and a helper that guessed which one to look in would be one
    `select` away from asserting nothing at all.
    """
    token = await _token(client, identity, query)
    response = await _post(client, identity, content, token=token)

    assert response.status_code == 400, (
        f"the file was not refused; the panel answered {response.status_code}"
    )
    assert _entries(admin_app, client.staff.username) == [], (
        "a refused file wrote an audit entry, which is a trail claiming a "
        "change that never happened"
    )
    return response.text


# --- 1. Fourteen import; four are refused, and each refusal has a reason ----


async def test_the_four_refused_tables_carry_no_import(admin_app):
    """`audit_log`, `staff`, `submission` and `ip_block`, by the attribute.

    The comment on each of those four views carries the argument; this is what
    makes the argument load-bearing. A reader who disagrees with one of them
    has to change a test that says why, rather than add a word to a class.

    Both halves: `can_import` false **and** the mixin absent. Either one alone
    would let the other be added quietly - `AuditedImport` sets `can_import`
    itself, so wearing it *is* turning the import on.
    """
    for identity, reason in _REFUSED.items():
        view = _view(admin_app, identity)
        assert getattr(view, "can_import", False) is False, (
            f"{identity} accepts a bulk import. It must not: {reason}. If "
            "this is a deliberate reversal, the comment on the view is where "
            "the reasoning is and where it has to be answered"
        )
        assert not isinstance(view, AuditedImport), (
            f"{identity} wears AuditedImport, which sets can_import itself. "
            f"It must not: {reason}"
        )


async def test_the_import_route_refuses_each_of_the_four(admin_client, admin_app):
    """The same four, driven over HTTP by an administrator.

    The attribute test above proves the declaration; this proves the route
    honours it, which is the thing an attacker meets. An administrator
    deliberately, because it is the strongest account there is: if the request
    is refused for this one it is refused for everybody, and a test run as a
    `staff` account would pass against a panel that merely refuses `staff`.

    **Paired with an acceptance in the same session**, so that a 403 caused by
    a broken fixture, an unauthenticated client or a typo in the URL shape
    cannot read as a pass.
    """
    for identity in _REFUSED:
        response = await admin_client.post(
            f"/admin/{identity}/import",
            files={"csvfile": ("rows.csv", b"code\r\nx\r\n", "text/csv")},
            data={"csrf_token": "whatever"},
        )
        assert response.status_code == 403, (
            f"POST /admin/{identity}/import was not refused; it answered "
            f"{response.status_code}. {_REFUSED[identity]}"
        )

    permitted = await admin_client.post(
        "/admin/destination-group/import",
        files={"csvfile": ("rows.csv", b"code\r\nx\r\n", "text/csv")},
        data={"csrf_token": "whatever"},
    )
    assert permitted.status_code != 403, (
        "the same session was refused a screen that does accept an import, so "
        "the four refusals above prove nothing about those four tables"
    )


async def test_factor_set_itself_is_not_one_of_the_fourteen(admin_app):
    """The nineteenth view, refused for a reason of its own.

    `factor_set` is not on the four-table list and is not importable either. A
    set is created by cloning, and its `status` is moved by four lifecycle
    actions that take a lock, revalidate the formulas and stamp
    `published_at`/`published_by`; a file able to write those columns would
    walk past every step, and could leave two rows claiming to be published at
    once. Its five *children* import, which is the distinction worth pinning -
    they are next to each other in the sidebar.
    """
    parent = _view(admin_app, "factor-set")
    assert getattr(parent, "can_import", False) is False, (
        "the factor_set screen accepts a bulk import, so a file can now set "
        "`status` without the lock, the revalidation or the stamp that the "
        "publish action takes"
    )
    for child in _DRAFT_ONLY:
        assert getattr(_view(admin_app, child), "can_import", False) is True, (
            f"{child} is one of the fourteen and does not accept an import"
        )


# --- 2. A foreign key is the referenced row's `code` -----------------------


async def test_every_foreign_key_on_every_importing_screen_resolves_by_code(
    admin_app
):
    """Each importable relationship column has a natural key to be written as.

    Walks all fourteen rather than listing the columns, because the failure
    this catches is a column *added* to a screen later: `natural_key_column`
    raises `KeyError` for a model with neither a `code` nor an entry in its
    exception table, and the place that would otherwise surface is a staff
    member's upload refusing with a traceback.

    `factor_set` is the one exception and it is asserted by name. That table
    has no `code` column at all - contract §2.2 gives it a `version_label` -
    so a rule reading "always `code`" would be wrong about five of the
    fourteen screens.
    """
    importable = [
        view for view in _live_admin(admin_app)._views
        if hasattr(view, "model") and getattr(view, "can_import", False)
    ]
    assert importable, "no screen accepts an import, so this proves nothing"

    seen = {}
    for view in importable:
        for column, target in foreign_key_import_columns(view).items():
            key = natural_key_column(target)
            seen[f"{view.identity}.{column}"] = key.key
            assert key.type.python_type is str, (
                f"{view.identity}.{column} would be written as "
                f"{target.__table__.name}.{key.key}, which is not text. A "
                "foreign key is named by a code, and a number in that column "
                "is exactly what this rule exists to keep out of a file"
            )

    assert seen.get("constant.factor_set") == "version_label", (
        "a factor set is named in a file by its version_label; it has no "
        f"`code` column at all. Resolved instead by: {seen.get('constant.factor_set')}"
    )
    assert seen.get("food-item.food_category") == "code", seen


async def test_a_foreign_key_is_written_as_the_referenced_row_s_code(
    admin_client, admin_app, session, taxonomy_for_factors
):
    """The plain case, end to end: a food item naming its category.

    `food_category` on this screen is a *relationship*, not a mapper column,
    so sqladmin hands the raw cell to a `QuerySelectField` that matches
    primary keys - the shape WP2 met on `unit_preset.food_category` and
    excluded the column over. What lands here is the id, resolved from the
    code before sqladmin ever saw the file.
    """
    session.commit()
    category = taxonomy_for_factors.category

    token = await _token(admin_client, "food-item")
    response = await _post(
        admin_client, "food-item",
        _csv("code,name,food_category,sort_order,active",
             f"{_PREFIX}cheese,Cheese,{category.code},7,1"),
        token=token,
    )

    assert response.status_code == 200, response.text
    assert _result(response)["imported"] == 1, _result(response)["summary"]

    rows = _rows(admin_app, FoodItem)
    assert set(rows) == {f"{_PREFIX}cheese"}
    assert rows[f"{_PREFIX}cheese"].food_category_id == category.id, (
        "the row landed under a different food category than the code named"
    )


async def test_a_code_nothing_answers_to_refuses_the_whole_file(
    admin_client, admin_app, session, taxonomy_for_factors
):
    """The rejection, in WP2's register: line, column, value, and that nothing
    answers to it.

    The second row is the bad one, and the first is good - so a refusal that
    wrote what it could before stopping would leave a row behind, and the
    shared helper's "nothing written" assertion catches that rather than this
    test having to.
    """
    session.commit()
    category = taxonomy_for_factors.category

    text_ = await _refused(
        admin_client, admin_app, "food-item",
        _csv("code,name,food_category,sort_order,active",
             f"{_PREFIX}good,Good,{category.code},1,1",
             f"{_PREFIX}bad,Bad,e6_dairyy,2,1"),
    )

    assert _rows(admin_app, FoodItem) == {}, (
        "a refused file still wrote the good row above the bad one, so the "
        "check runs too late to be a refusal at all"
    )
    assert "Line 3" in text_, f"the refusal does not name the line: {text_}"
    assert '"food_category"' in text_, (
        f"the refusal does not name the column: {text_}"
    )
    assert '"e6_dairyy"' in text_, f"the refusal does not quote the value: {text_}"
    assert "names no row in food_category" in text_, (
        f"the refusal does not say that nothing answers to the code: {text_}"
    )


async def test_the_referenced_row_s_number_is_refused_like_any_other_wrong_code(
    admin_client, admin_app, session, taxonomy_for_factors
):
    """A primary key in a foreign-key column is not a shortcut. It is wrong.

    This is the decision, not a side effect of it: **ids differ between
    deployments**, so a file written with them can only ever be loaded back
    into the database it came from - which is the one thing a configuration
    meant to move between deployments must not be. Accepting an id "as well,
    for convenience" would produce files that work on the machine they were
    made on and fail, or silently point somewhere else, on any other.

    The number used is the real id of the real category the test could have
    named correctly, so this is the exact file a person who read the ids off a
    details page would write.
    """
    session.commit()
    category = taxonomy_for_factors.category

    text_ = await _refused(
        admin_client, admin_app, "food-item",
        _csv("code,name,food_category,sort_order,active",
             f"{_PREFIX}byid,By id,{category.id},1,1"),
    )

    assert _rows(admin_app, FoodItem) == {}, (
        "a file naming its category by number wrote a row anyway, which on "
        "another deployment would be a row pointing at whatever happens to "
        "carry that id there"
    )
    assert f'"{category.id}"' in text_ and "names no row in food_category" in text_, (
        "a primary key in a foreign-key column was not refused the way any "
        f"other unresolvable value is: {text_}"
    )
    assert "differ between one deployment and the next" in text_, (
        "the refusal does not say why a number is not accepted here, which is "
        f"the one thing the reader needs in order not to try it again: {text_}"
    )


async def test_a_blank_optional_foreign_key_stays_blank(
    admin_client, admin_app, session, taxonomy_for_factors
):
    """The acceptance that keeps the rule from being "every cell must resolve".

    `unit_preset.food_category` is nullable and blank is its common case: the
    preset applies to every food category. A pass that refused an empty cell
    would refuse most of this table, and WP2's whole file leaves it blank.
    """
    session.commit()

    token = await _token(admin_client, "unit-preset")
    response = await _post(
        admin_client, "unit-preset",
        _csv("code,label,food_category,kg_per_unit,source_note,active",
             f"{_PREFIX}bucket,20 L bucket,,12.5000,,1"),
        token=token,
    )

    assert response.status_code == 200, response.text
    rows = _rows(admin_app, UnitPreset)
    assert set(rows) == {f"{_PREFIX}bucket"}, _result(response)["summary"]
    assert rows[f"{_PREFIX}bucket"].food_category_id is None, (
        "a blank food category became a row pointing at one"
    )
    assert rows[f"{_PREFIX}bucket"].kg_per_unit == Decimal("12.5000")


async def test_a_row_with_six_foreign_keys_resolves_all_of_them(
    admin_client, admin_app, session, two_sets, taxonomy_for_factors
):
    """`factor_upstream` is the widest row in the schema: six foreign keys,
    two of them optional, and one of the six is a factor set named by a
    `version_label` rather than a `code`.

    Worth its own test rather than being left to the single-key cases above,
    because the failure a per-column resolver has is per column: five
    resolved and one not is a row that lands under the wrong sector, or under
    a food nobody named, with nothing on the screen to say so. `food_item` and
    `destination` are left blank here, which is the universal case for the
    first (no deployment yet names a food per factor) and the normal one for
    the second.
    """
    live, draft = two_sets
    taxonomy = taxonomy_for_factors

    # A metric of this file's own, and the reason is the import being
    # insert-only. `two_sets` already files an upstream row against
    # (draft, sector, category, -, -, metric), and `factor_upstream` carries
    # UNIQUE on exactly that tuple - so a file naming the fixture's own metric
    # collides on the constraint rather than testing anything about foreign
    # keys. Naming a second metric is the one field that has to differ.
    metric = Metric(code=f"{_PREFIX}water", name="Water", unit="L")
    session.add(metric)
    session.commit()

    token = await _token(admin_client, "factor-upstream")
    response = await _post(
        admin_client, "factor-upstream",
        _csv(
            "factor_set,sector,food_category,food_item,destination,metric,"
            "value_per_kg,data_quality,source_note",
            f"{draft.version_label},{taxonomy.sector.code},"
            f"{taxonomy.category.code},,,{metric.code},"
            f"3.1400000000,measured,{_PREFIX}six keys",
        ),
        token=token,
    )

    assert response.status_code == 200, response.text
    assert _result(response)["imported"] == 1, response.text

    factory = admin_app.state.session_factory
    with factory() as db:
        row = db.scalar(
            select(FactorUpstream).where(
                FactorUpstream.source_note == f"{_PREFIX}six keys"
            )
        )
    assert row is not None, "the row was not written at all"
    assert (row.factor_set_id, row.sector_id, row.food_category_id,
            row.metric_id) == (draft.id, taxonomy.sector.id,
                               taxonomy.category.id, metric.id), (
        "at least one of the four named foreign keys resolved to the wrong "
        "row, which is a factor filed against something nobody named"
    )
    assert row.food_item_id is None and row.destination_id is None, (
        "a blank optional foreign key became a row pointing at something"
    )
    assert row.value_per_kg == Decimal("3.1400000000")


async def test_a_child_row_names_its_parent_by_the_parent_s_code(
    admin_client, admin_app, session, taxonomy_for_factors
):
    """`comparison_scenario_line` is the one importable table with no `code`
    of its own.

    A line is identified by nothing but the scenario it belongs to and the
    destination it names, both of which are foreign keys - so if the
    translation did not work on this screen there would be no way to write a
    line at all, and the screen would be one of the fourteen in name only. It
    is also the one table where "deactivate the rest" will have to be a real
    delete, because it carries no `active` column, which is a reason to know
    that its import works before that question is asked.
    """
    taxonomy = taxonomy_for_factors
    scenario = ComparisonScenario(
        code=f"{_PREFIX}scenario", name="WP3 scenario",
        sector_id=taxonomy.sector.id, gwp_horizon=100,
    )
    session.add(scenario)
    session.commit()

    token = await _token(admin_client, "comparison-scenario-line")
    response = await _post(
        admin_client, "comparison-scenario-line",
        _csv("scenario,destination,qty_kg",
             f"{scenario.code},{taxonomy.destination.code},1200.500"),
        token=token,
    )

    assert response.status_code == 200, response.text
    assert _result(response)["imported"] == 1, _result(response)["summary"]

    factory = admin_app.state.session_factory
    with factory() as db:
        lines = db.scalars(
            select(ComparisonScenarioLine).where(
                ComparisonScenarioLine.scenario_id == scenario.id
            )
        ).all()
    assert len(lines) == 1, "the line did not land under the scenario it named"
    assert lines[0].destination_id == taxonomy.destination.id
    assert lines[0].qty_kg == Decimal("1200.500")


# --- 3. The draft-only rule, against the file's own contents ---------------


async def test_the_five_factor_children_are_the_ones_that_carry_the_rule(
    admin_app
):
    """Declared on five screens and on no others.

    The rule exists because every `submission` stamps the `factor_set_id` it
    was calculated against, and these five tables are the ones whose rows that
    id resolves to. A sixth screen carrying it would be a screen refusing
    files for no reason; one of these five losing it would be a published set
    editable by upload, which is the defect `_refuse_if_factor_set_not_draft`
    exists to prevent on the two other write paths.
    """
    declared = sorted(
        view.identity for view in _live_admin(admin_app)._views
        if getattr(view, "import_draft_only_through", None) is not None
    )
    assert declared == _DRAFT_ONLY, (
        "the set of screens enforcing the draft-only rule on an uploaded file "
        f"is not the five children of a factor set: {declared}"
    )


async def test_the_state_the_rule_compares_against_is_the_enum_s_own_value():
    """`_DRAFT` is a string, and this is what stops it drifting.

    admin/importing.py compares the status it read out of `factor_set`
    against a literal rather than against `FactorSetStatus.draft`, so that the
    generic import machinery does not depend on one feature's models. The cost
    of that is exactly this: a rename of the enum member would leave the
    literal matching nothing, the comparison would be true for every set, and
    **every upload into a published set would be accepted** - a rule that
    fails open, silently, with all its tests still green except this one.
    """
    assert _DRAFT == FactorSetStatus.draft.value, (
        "admin/importing.py compares a factor set's status against "
        f"{_DRAFT!r}, and the enum's own value is "
        f"{FactorSetStatus.draft.value!r}. Nothing would ever match, so every "
        "file would be treated as naming a non-draft - or, if the mismatch "
        "runs the other way, none would"
    )


async def test_a_file_naming_the_published_set_is_refused_from_the_draft_s_page(
    admin_client, admin_app, session, two_sets
):
    """The file decides, not the page. This is the whole of the rule.

    The visitor is standing on the Constants list filtered to the **draft**
    (`?factor_set_id=<draft>`), and takes the import modal's token from that
    page, which is what a person doing this by hand would do. The file they
    upload names the **published** set on every row.

    A guard that read "which factor set is this screen filtered to" would pass
    this file. A guard that read the row would not, and the difference is a
    published set gaining a constant - which every historical submission
    stamped with that set's id then reproduces differently for ever.

    **What this test actually catches, measured rather than assumed.** Deleting
    the route-level check and re-running this file does *not* write the row:
    `ConstantAdmin.validate_before_commit` -> `_require_draft_factor_set` still
    fires on the commit and rolls the whole thing back. So the rule fails safe
    either way, and somebody could reasonably conclude the route check is
    redundant. It is not, and the difference is the whole of what a staff
    member sees. Without it the panel answers **200** with
    *"Import failed during database commit. No rows were imported (rolled
    back)."* - a database-level message, on a success status, naming neither
    the row nor the reason. With it the panel answers **400** and names the
    line, the column, the version label, the state it is in and what to do
    instead. That is why the assertions below are on the status and on the
    words, not only on the absence of the row.
    """
    live, draft = two_sets
    session.commit()

    text_ = await _refused(
        admin_client, admin_app, "constant",
        _csv("factor_set,code,value,unit,note",
             f"{live.version_label},{_PREFIX}SNUCK_IN,1.0,x,via the draft's page"),
        query=f"?factor_set_id={draft.id}",
    )

    assert "Line 2" in text_ and f'"{live.version_label}"' in text_, (
        f"the refusal does not name the line and the set: {text_}"
    )
    assert "published, not draft" in text_, (
        f"the refusal does not say what state the named set is in: {text_}"
    )

    factory = admin_app.state.session_factory
    with factory() as db:
        landed = db.scalars(
            select(Constant).where(Constant.code.like(_PREFIX + "%"))
        ).all()
    assert list(landed) == [], (
        "a row was written into the published set by a file uploaded from the "
        "draft's own page"
    )


async def test_the_same_file_naming_the_draft_is_imported(
    admin_client, admin_app, session, two_sets
):
    """The paired acceptance, byte-for-byte the file above with one label
    changed.

    Without it the test above passes against a screen whose import is broken
    outright, or against one that refuses every factor file for a reason that
    has nothing to do with the draft rule.
    """
    live, draft = two_sets
    session.commit()

    token = await _token(admin_client, "constant")
    response = await _post(
        admin_client, "constant",
        _csv("factor_set,code,value,unit,note",
             f"{draft.version_label},{_PREFIX}SNUCK_IN,1.0,x,via the draft's page"),
        token=token,
    )

    assert response.status_code == 200, response.text
    assert _result(response)["imported"] == 1, _result(response)["summary"]

    factory = admin_app.state.session_factory
    with factory() as db:
        landed = db.scalars(
            select(Constant).where(Constant.code.like(_PREFIX + "%"))
        ).all()
    assert [row.factor_set_id for row in landed] == [draft.id], (
        "the row did not land in the draft the file named"
    )


async def test_a_row_naming_no_factor_set_is_refused_in_words(
    admin_client, admin_app, session, two_sets
):
    """An empty `factor_set` cell is refused here rather than by the form.

    The form's answer to it is "Not a valid choice", which names neither the
    column nor what to write in it. It is also the one blank this pass does
    not leave to the column's own nullability: `factor_set_id` is NOT NULL on
    all five tables, so an empty cell can only ever be a mistake.
    """
    session.commit()

    text_ = await _refused(
        admin_client, admin_app, "constant",
        _csv("factor_set,code,value,unit,note",
             f",{_PREFIX}NOWHERE,1.0,x,no set named"),
    )

    factory = admin_app.state.session_factory
    with factory() as db:
        landed = db.scalars(
            select(Constant).where(Constant.code.like(_PREFIX + "%"))
        ).all()
    assert list(landed) == [], "a row with no factor set named was written anyway"

    assert "no factor set is named" in text_, (
        f"the refusal does not say what is missing: {text_}"
    )
    assert "Not a valid choice" not in text_, (
        "the row reached the scaffolded form, whose message names neither the "
        f"column nor what to write in it: {text_}"
    )


# --- 4. DECIMAL survives the journey ---------------------------------------


async def test_a_twenty_digit_factor_value_arrives_with_every_digit(
    admin_client, admin_app, session, two_sets
):
    """The float-in-transit test WP2 could not write, on a column that can
    hold the difference.

    **Why this is not the same test WP2 already has.** Routing a `DECIMAL`
    column through `float()` in sqladmin's `coerce_column_value` left
    `test_a_clean_decimal_arrives_as_a_decimal_with_the_digits_typed` green,
    and the reason was arithmetic rather than a defect: `unit_preset.
    kg_per_unit` is `decimal(12,4)`, twelve significant digits, and an IEEE
    double carries fifteen to seventeen - so **no value that column can hold
    is changed by a float round trip.** Re-measured independently: 0 of
    50,000 for `decimal(12,4)`, 49,974 of 50,000 for `decimal(20,10)`.

    `constant.value` is `decimal(20,10)`, and so are
    `factor_upstream.value_per_kg`, `factor_downstream.value_per_kg` and
    `equivalence.value_per_unit`. The value below uses all twenty digits: ten
    before the point, which is `precision - scale`, and ten after, which is
    `scale`. `float()` of it keeps seventeen significant digits and drops the
    last three, so the row that lands differs from the row that was uploaded
    in a place nothing on any screen would show.

    `DECIMAL` everywhere, `FLOAT` and `DOUBLE` prohibited, is an architecture
    invariant and contract §1.2. This is the test that defends it at the
    import boundary - the one seam where a number leaves this system's types,
    spends a while as text in a spreadsheet, and comes back.
    """
    live, draft = two_sets
    session.commit()

    # Twenty digits, every one of them significant, and all twenty inside
    # decimal(20,10). A double keeps about seventeen. The last assertion in
    # this test is what holds that claim to account.
    exact = Decimal("1234567890.1234567891")

    token = await _token(admin_client, "constant")
    response = await _post(
        admin_client, "constant",
        _csv("factor_set,code,value,unit,note",
             f"{draft.version_label},{_PREFIX}EXACT,{exact},x,twenty digits"),
        token=token,
    )
    assert response.status_code == 200, response.text
    assert _result(response)["imported"] == 1, _result(response)["summary"]

    factory = admin_app.state.session_factory
    with factory() as db:
        stored = db.scalar(
            select(Constant).where(Constant.code == f"{_PREFIX}EXACT")
        )
    assert stored is not None, "the row was not written at all"

    assert stored.value == exact, (
        f"the stored value is {stored.value}, and {exact} was uploaded. Every "
        "digit of a factor has to survive the file, and this column keeps "
        "twenty of them. A value that comes back changed has been through a "
        "binary float somewhere between the upload and the INSERT - which is "
        "what DECIMAL everywhere, and the prohibition on FLOAT and DOUBLE, "
        "exists to prevent"
    )
    assert Decimal(str(float(exact))) != exact, (
        "this value survives a float round trip unchanged, so the assertion "
        "above cannot detect a float in transit and the test is not testing "
        "what it says. Pick a value that uses the column's full precision"
    )
