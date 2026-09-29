"""Bulk CSV import on the Sectors screen: audited, CSRF-checked, admin-only,
and all-or-nothing.

sqladmin ships an import and this project turned it on for exactly one table
first, as a pilot (admin/importing.py); the fourteen it is now on are pinned
below and their own particulars are in tests/admin/test_import_cleaning.py
and tests/admin/test_import_tables.py. Three things had to be added around
sqladmin's import and each one is a hole if it is missing, so each one is
driven here through real HTTP against a real database rather than by calling
a predicate:

* **audited** — auditing is a ``before_commit`` listener that writes only when
  ``_actor_var`` is set, and sqladmin's import path sets nothing. These tests
  read ``audit_log`` back after an import, never the code that writes it.
* **CSRF-checked** — ``csrf`` appears zero times in sqladmin's import modal,
  and this panel checks a token per form. A post without one must be refused.
* **administrator-only** — the Sectors screen is open to both roles (contract
  §8.3), so ``is_accessible`` says yes to a plain ``staff`` account and the
  import route would inherit that answer. The refusal is driven with a real
  signed-in non-administrator session.

And ``continue_on_error`` is pinned false, which is what makes an import
atomic. The last two tests hold it there: one file aborted in validation, one
aborted at the write, neither leaving a row or an audit entry behind. A later
change made in the name of friendliness has to break one of them.

**Why both halves of every refusal.** A test that only asserts a 403 passes
against a panel that refuses everybody — a typo in a URL, a view that failed
to register and a fixture that never logged in all read as a pass. So every
refusal here is paired with the same request made by an account that must
succeed.
"""

import json
import re

import pytest
from sqlalchemy import select, text

from admin.models import AuditLog
from admin.taxonomy_models import Sector

pytestmark = [pytest.mark.db, pytest.mark.asyncio]

#: Every `sector.code` this file creates starts with this, and the teardown
#: below deletes by the prefix rather than by a list of names. A fixed list
#: has to be extended by hand every time a test adds a row, which is exactly
#: what the next person forgets - the same reasoning as
#: tests/admin/conftest.py's "e6_" sweep.
_PREFIX = "wp1_"

#: `description` last, and every row below therefore ends in a trailing
#: comma that `_csv` appends for it.
#:
#: WP3 set `SectorAdmin.column_import_list = form_columns`, which added
#: `description` to the five columns this screen imports — `parse_csv` refuses
#: a file missing any import column outright, so every row here needs a cell
#: for it. Column *order* in the file is free (sqladmin reads it with a
#: `csv.DictReader`), so it goes at the end and the twelve row literals below
#: are left saying what they were written to say.
_HEADER = "code,name,sort_order,active,description"


def _csv(*rows: str) -> bytes:
    """A CSV whose header is the five columns sqladmin will import.

    Those five are `SectorAdmin.form_columns`, which is what
    `column_import_list` is set to; a file missing any of them is refused by
    `parse_csv` before a row is looked at, which is a different test from
    every one in this file.
    """
    padded = [f"{row}," for row in rows]
    return ("\r\n".join((_HEADER, *padded)) + "\r\n").encode("utf-8")


@pytest.fixture(autouse=True)
def _clean_sectors(admin_app):
    """Remove this file's own sector rows, before and after.

    Before as well as after: a run interrupted partway through leaves rows
    whose `code` is unique-constrained, and the next run's very first import
    would then fail for a reason that has nothing to do with what it asserts.

    The audit entries these tests produce are cleaned up by
    `_cleanup_staff` (tests/admin/conftest.py), which deletes by `actor` -
    every entry here carries the throwaway account's own username.
    """

    def sweep() -> None:
        factory = admin_app.state.session_factory
        with factory() as db:
            ids = db.execute(
                text("SELECT id FROM sector WHERE code LIKE :like"),
                {"like": _PREFIX + "%"},
            ).scalars().all()
            for sector_id in ids:
                db.execute(
                    text("DELETE FROM audit_log WHERE table_name = 'sector' "
                         "AND row_id = :id"),
                    {"id": sector_id},
                )
            db.execute(
                text("DELETE FROM sector WHERE code LIKE :like"),
                {"like": _PREFIX + "%"},
            )
            db.commit()

    sweep()
    yield
    sweep()


async def _import_token(client) -> str:
    """The CSRF token the Sectors list page renders for this session.

    Taken from the page the visitor would actually be on, not minted
    directly: the token only reaches the browser if the modal template
    renders it, and a test that built its own would pass with that template
    missing the field entirely - which is precisely the state this panel was
    in before admin/templates/sqladmin/modals/import.html existed.
    """
    page = await client.get("/admin/sector/list")
    assert page.status_code == 200, "the Sectors list page did not render"
    match = re.search(
        r'id="kaicalc-import-csrf" value="([^"]+)"', page.text
    )
    assert match, (
        "the Sectors list page rendered no CSRF token for the import modal, "
        "so nothing the browser sends could ever carry one"
    )
    return match.group(1)


async def _post_import(client, content: bytes, *, token, filename="sectors.csv",
                       continue_on_error=None):
    """POST a file at the import route, the way the modal does."""
    data = {} if token is None else {"csrf_token": token}
    if continue_on_error is not None:
        data["continue_on_error"] = continue_on_error
    return await client.post(
        "/admin/sector/import",
        files={"csvfile": (filename, content, "text/csv")},
        data=data,
    )


def _result(response) -> dict:
    """The final `result` event of sqladmin's newline-delimited JSON stream."""
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    finals = [event for event in events if event.get("type") == "result"]
    assert finals, f"the import stream carried no result event: {response.text!r}"
    return finals[-1]


def _entries(admin_app, actor: str) -> list[AuditLog]:
    """Every audit entry this actor wrote, oldest first."""
    factory = admin_app.state.session_factory
    with factory() as db:
        return list(
            db.scalars(
                select(AuditLog).where(AuditLog.actor == actor).order_by(AuditLog.id)
            ).all()
        )


def _sectors(admin_app) -> dict[str, Sector]:
    factory = admin_app.state.session_factory
    with factory() as db:
        return {
            row.code: row
            for row in db.scalars(
                select(Sector).where(Sector.code.like(_PREFIX + "%"))
            ).all()
        }


# --- 1. the import is audited ----------------------------------------------


async def test_an_import_writes_one_file_entry_and_one_entry_per_row(
    admin_client, admin_app
):
    """The whole of gap 1, read out of `audit_log` rather than out of the code.

    Both granularities are asserted together because each on its own is the
    trail people complain about: 500 indistinguishable `create` rows cannot
    say what was uploaded, and a single summary cannot say who changed one
    number.
    """
    token = await _import_token(admin_client)
    content = _csv(
        f"{_PREFIX}alpha,WP1 Alpha,901,1",
        f"{_PREFIX}beta,WP1 Beta,902,1",
    )

    response = await _post_import(admin_client, content, token=token,
                                  filename="sectors-corrected.csv")

    assert response.status_code == 200, response.text
    assert _result(response)["imported"] == 2, _result(response)["summary"]
    assert set(_sectors(admin_app)) == {f"{_PREFIX}alpha", f"{_PREFIX}beta"}

    entries = _entries(admin_app, admin_client.staff.username)
    actions = [entry.action for entry in entries]
    assert actions == ["import", "create", "create"], (
        "an import through the panel should write one file-level entry "
        f"followed by one entry per row it wrote; audit_log has {actions}"
    )

    header = entries[0]
    assert header.table_name == "sector"
    assert header.row_id is None, (
        "the file-level entry named a single row id; it describes a file, "
        "not one of the rows it wrote"
    )
    assert header.after_json["import_file"] == "sectors-corrected.csv", (
        "the file-level entry does not name the file that was uploaded, so "
        "nobody reading the trail can tell which file did this"
    )
    assert header.after_json["rows_created"] == 2
    assert header.after_json["bytes"] == len(content)
    assert re.fullmatch(r"[0-9a-f]{64}", header.after_json["sha256"]), (
        "the file-level entry carries no SHA-256 of the payload, so 'was "
        f"this the file I sent?' cannot be answered: {header.after_json}"
    )

    codes = sorted(entry.after_json["code"] for entry in entries[1:])
    assert codes == [f"{_PREFIX}alpha", f"{_PREFIX}beta"], (
        "the per-row entries do not carry the rows that were written, so "
        "'who changed this number' is unanswerable for an imported row"
    )


async def test_the_file_entry_names_the_account_that_uploaded_it(
    admin_client, admin_app
):
    """The actor, which is the whole reason the contextvar has to be set.

    Without it the `before_commit` listener returns early and the import is
    silently unaudited - the failure mode is no rows at all, which is what
    the test above catches, so this one is about the *value* rather than the
    presence: an actor taken from anywhere a client can set is not an audit
    trail.
    """
    token = await _import_token(admin_client)

    await _post_import(admin_client, _csv(f"{_PREFIX}gamma,WP1 Gamma,903,1"),
                       token=token)

    entries = _entries(admin_app, admin_client.staff.username)
    assert entries, "the import wrote no audit entry at all"
    assert all(entry.actor == admin_client.staff.username for entry in entries), (
        "an imported row was filed against somebody other than the account "
        "that uploaded it"
    )


# --- 2. the import is CSRF-checked -----------------------------------------


async def test_an_import_with_no_token_is_refused(admin_client, admin_app):
    """Gap 2. A signed-in administrator, a valid file, and no token."""
    response = await _post_import(
        admin_client, _csv(f"{_PREFIX}delta,WP1 Delta,904,1"), token=None
    )

    assert response.status_code == 400, (
        "an import posted with no CSRF token was not refused; a page on "
        "another origin can make this exact request with a staff member's "
        "own cookie attached"
    )
    assert _sectors(admin_app) == {}, (
        "the refusal still wrote the row, so the check is in the wrong place"
    )
    assert _entries(admin_app, admin_client.staff.username) == []


async def test_an_import_with_the_wrong_token_is_refused(admin_client, admin_app):
    """The other half of the token check: present, and not this session's.

    Without this a check written as `if "csrf_token" in form` would pass the
    test above.
    """
    response = await _post_import(
        admin_client, _csv(f"{_PREFIX}epsilon,WP1 Epsilon,905,1"),
        token="not-this-session's-token",
    )

    assert response.status_code == 400, (
        "an import posted with a token this session never issued was accepted"
    )
    assert _sectors(admin_app) == {}


async def test_the_import_dialog_carries_the_token_and_the_script_that_sends_it(
    admin_client,
):
    """The browser half, which the server-side check cannot prove on its own.

    With the token rendered and nothing putting it into the request, every
    import a real staff member attempts is refused by the check above and the
    feature is dead on the panel while every server-side test still passes.

    **The script named here changed in WP4 and the reason it exists did not.**
    sqladmin's own `main.js` builds the upload's `FormData` by hand and never
    serialises the form, so a hidden field alone is in the DOM and in no
    request; WP1 carried the field past that by replacing `window.fetch` for
    one call and said the replacement dialog would delete that wrapper.
    /admin/static/import.js is the replacement and builds its own body, with
    the token an ordinary field in it. The test below holds the wrapper gone.
    """
    page = await admin_client.get("/admin/sector/list")

    assert 'id="kaicalc-import-csrf"' in page.text
    assert "/admin/static/import.js" in page.text, (
        "the import dialog renders a CSRF token but loads nothing that puts "
        "it into the upload, so no import from a browser can succeed"
    )
    script = await admin_client.get("/admin/static/import.js")
    assert script.status_code == 200, "the dialog loads a script that 404s"
    assert "csrf_token" in script.text, (
        "the import dialog's own script does not put the token into the "
        "request body it builds"
    )


async def test_the_fetch_wrapper_wp1_left_behind_is_gone(admin_client):
    """WP1's `window.fetch` wrapper, held deleted rather than described.

    It existed for one reason — sqladmin's hand-built `FormData` had nowhere
    to read a hidden field from — and it said so in its own header: "WP4 of
    the import plan replaces this modal outright, and the replacement will
    build its own request body with the token in it. Delete this file and the
    template that loads it in the same change."

    A monkeypatch of a global that nobody meant to keep is exactly the thing
    that survives by being harmless. This asserts the file is not served and
    that no page asks for it, so a later change that quietly restores either
    is a red test rather than a rediscovery.
    """
    gone = await admin_client.get("/admin/static/import-csrf.js")
    assert gone.status_code == 404, (
        "/admin/static/import-csrf.js is still served; WP1's `window.fetch` "
        "wrapper was to be deleted with the dialog that needed it"
    )
    page = await admin_client.get("/admin/sector/list")
    assert "import-csrf.js" not in page.text, (
        "the Sectors page still loads the fetch wrapper"
    )


# --- 3. the import is administrator-only -----------------------------------


async def test_a_plain_staff_member_cannot_import(staff_client, admin_app):
    """Gap 2's other half, driven with a real signed-in non-administrator.

    Not `is_accessible` in isolation: the Sectors screen is open to both
    roles by contract §8.3, so `is_accessible` returns True here and the
    refusal has to come from `check_can_import`. A unit test on the predicate
    would have passed with the hook never wired up.
    """
    response = await _post_import(
        staff_client, _csv(f"{_PREFIX}zeta,WP1 Zeta,906,1"), token="anything",
    )

    assert response.status_code == 403, (
        "a signed-in staff member was allowed to post an import; every "
        "@expose and every sqladmin hook is reachable by any signed-in "
        "account unless the view checks the role itself"
    )
    assert _sectors(admin_app) == {}


async def test_an_administrator_is_not_refused_the_same_request(
    admin_client, admin_app
):
    """The half that stops the refusal above being vacuous.

    Same route, same file shape, an administrator: anything other than 403.
    A panel that refused everybody - a mis-typed path, a view that never
    registered - would pass the test above and fail this one.
    """
    token = await _import_token(admin_client)

    response = await _post_import(
        admin_client, _csv(f"{_PREFIX}zeta,WP1 Zeta,906,1"), token=token,
    )

    assert response.status_code != 403, (
        "an administrator was refused the import route, so the staff refusal "
        "above proves nothing about roles"
    )
    assert set(_sectors(admin_app)) == {f"{_PREFIX}zeta"}


#: The two tests below are a pair and have to be two tests. `admin_client` and
#: `staff_client` are built on the same `client` fixture, so a test asking for
#: both gets one HTTP client whose session cookie belongs to whichever of them
#: logged in last - written as one test it silently compares a staff session
#: with itself, which is how it first passed the absence half and failed the
#: presence half.
async def test_the_import_button_is_offered_to_an_administrator(admin_client):
    """`check_can_import` is consulted by the list page as well as by the
    route, so one override both refuses a staff member and stops offering
    them the control. This is the presence half; the absence half is below.
    """
    page = await admin_client.get("/admin/sector/list")

    assert 'id="action-import"' in page.text, (
        "the Sectors screen offered an administrator no Import control, so "
        "the absence asserted in the next test proves nothing"
    )


async def test_the_import_button_is_withheld_from_a_plain_staff_member(
    staff_client,
):
    """A control that 403s when pressed is the panel telling somebody they
    have a capability they do not have, which on a five-person team becomes a
    message asking why it is broken."""
    page = await staff_client.get("/admin/sector/list")

    assert page.status_code == 200, (
        "a staff member could not open the Sectors screen at all, so the "
        "absence below is not about the Import control"
    )
    assert 'id="action-import"' not in page.text, (
        "the Sectors screen offered a plain staff member an Import control "
        "they would be refused on"
    )


#: The fourteen, by the identity sqladmin routes them under. Seven taxonomy,
#: five factor children, two comparison-scenario - the list the owner decided
#: and `docs/interfaces.md` §8.1 records.
_FOURTEEN = sorted([
    # Taxonomy (§8.1).
    "sector", "food-category", "food-item", "destination-group", "destination",
    "metric", "unit-preset",
    # The five children of a factor set. Draft-only, every one of them.
    "factor-upstream", "factor-downstream", "constant", "formula", "equivalence",
    # Comparison scenarios (§8.2).
    "comparison-scenario", "comparison-scenario-line",
])


async def test_exactly_the_fourteen_screens_accept_an_import(admin_app):
    """Fourteen tables import. This is what keeps it fourteen.

    **This test's real job is not the fourteen; it is everything else.**
    `can_import` is a one-word class attribute, and the thing that goes wrong
    quietly is a fifteenth view copying that word without `AuditedImport` and
    so without the administrator floor, the CSRF check, the cleaning pass and
    the foreign-key translation — every one of which lives on the mixin or on
    the route it guards, and none of which `can_import = True` brings with it.
    Four tables are refused outright and for reasons
    (tests/admin/test_import_tables.py); `factor_set` is refused for a
    different reason again; and the two that are neither would arrive here
    silently.

    Walks the live `Admin` object rather than a list of imports, because a
    class that exists and was never registered would satisfy an import-based
    check while being unreachable — and, the other way round, a view
    registered from somewhere this file never thought to look would be missed
    by one.
    """
    admin = _live_admin(admin_app)
    importable = sorted(
        view.identity for view in admin._views
        if hasattr(view, "model") and getattr(view, "can_import", False)
    )

    assert importable == _FOURTEEN, (
        "the set of screens accepting a bulk import is not the fourteen this "
        "was decided at. Every table added here needs its own answer to the "
        "draft-only rule, to how its foreign keys are named and to whether an "
        "uploaded file may write it at all — and `can_import = True` written "
        "without `AuditedImport` carries none of the floor. Added: "
        f"{sorted(set(importable) - set(_FOURTEEN))}; missing: "
        f"{sorted(set(_FOURTEEN) - set(importable))}"
    )


async def test_every_importing_screen_wears_the_mixin_that_guards_it(admin_app):
    """`can_import` is the switch; `AuditedImport` is everything else.

    The paired half of the test above, and the one that catches the specific
    accident it describes. A view that set `can_import = True` on its own
    would pass a list-of-fourteen check the moment somebody added its name to
    the list — and would have no administrator floor on its import route,
    because `is_accessible` on a taxonomy screen answers yes to a plain
    `staff` account (contract §8.3) and sqladmin's own `check_can_import` is
    `return self.can_import`.

    The route refuses such a view a second time
    (`test_a_view_without_the_mixin_is_refused_even_with_can_import_set`
    drives that over HTTP). This is the static half: the refusal should never
    be the thing standing between a staff member and a table.
    """
    from admin.importing import AuditedImport

    admin = _live_admin(admin_app)
    unguarded = sorted(
        view.identity for view in admin._views
        if hasattr(view, "model") and getattr(view, "can_import", False)
        and not isinstance(view, AuditedImport)
    )

    assert unguarded == [], (
        "these screens accept an import without wearing AuditedImport, so "
        "their import route has no administrator floor and their Import "
        f"button is offered to every signed-in account: {unguarded}"
    )


async def test_an_importable_screen_imports_exactly_its_create_form(admin_app):
    """`column_import_list = form_columns`, on every one of the fourteen.

    Not a style rule. sqladmin validates every imported row through the view's
    own scaffolded **create** form (`import_csv` ->
    `scaffold_form(self._form_create_rules)`), so the two lists cannot differ
    without something being wrong in one direction or the other:

    * an import column that is not a form field is a value nothing validates -
      it is coerced to the column's Python type and written, and every rule the
      form carries for that field (a length, a choice, a required-ness) is
      skipped for a file and applied to a person;
    * a **required** form field that is not an import column has no value in
      the formdata, so `pre_validate` refuses it - on every row of every file,
      with "Not a valid choice", which names neither the column nor the file.

    Both failures are silent in the sense that matters: the screen still lists,
    still edits, still exports, and only an upload behaves differently.

    Compares the resolved names rather than the two class attributes, because
    a view is free to write either list in any order and sqladmin resolves both
    through `_get_prop_name`.
    """
    admin = _live_admin(admin_app)
    wrong = {}
    for view in admin._views:
        if not (hasattr(view, "model") and getattr(view, "can_import", False)):
            continue
        form_names = [view._get_prop_name(item) for item in view.form_columns]
        if sorted(view._import_prop_names) != sorted(form_names):
            wrong[view.identity] = {
                "importable and not on the form":
                    sorted(set(view._import_prop_names) - set(form_names)),
                "on the form and not importable":
                    sorted(set(form_names) - set(view._import_prop_names)),
            }

    assert wrong == {}, (
        "these screens accept a set of import columns that is not their create "
        "form's fields. A column on the left of each entry is written without "
        "being validated; a required column on the right refuses every row of "
        f"every file: {wrong}"
    )


def _live_admin(app):
    """sqladmin's `Admin` instance, via a bound endpoint on its mounted app.

    The same reach tests/admin/test_role_matrix.py uses, and for the same
    reason: a class that exists and was never registered would satisfy an
    import-based check while being unreachable.
    """
    for route in app.routes:
        for sub in getattr(getattr(route, "app", None), "routes", []):
            endpoint = getattr(sub, "endpoint", None)
            if endpoint is not None and hasattr(endpoint, "__self__"):
                return endpoint.__self__
    raise AssertionError("sqladmin's Admin instance was not reachable")


# --- continue_on_error is pinned false -------------------------------------


async def test_a_file_with_one_bad_row_writes_nothing_even_when_asked_to_continue(
    admin_client, admin_app
):
    """`continue_on_error=False`, pinned against the form that can ask for
    the opposite.

    The flag is what makes `persist_import_models_with_count_check_sync`
    atomic - one commit after the loop, a rollback on the first bad row - and
    sqladmin reads it straight off the submitted form. The modal renders a
    checkbox for it, and a hand-built post can send the field whatever the
    modal does, so the post here sends "1" deliberately: the refusal has to be
    on the server's side of that field or it is not a refusal at all.

    A half-landed file is worse than a refused one. It leaves the taxonomy in
    a state no file describes, and - because the audit entry for the import is
    written inside the same commit - audited as though the whole file had
    landed.

    THE BAD CELL IS `active`, AND IT HAS TO BE SOMETHING WP2 DOES NOT CATCH.
    This test originally used a non-numeric `sort_order`, which WP2's cleaning
    pass (admin/importing.py) now refuses before sqladmin is called at all -
    and a refusal there, however correct, proves nothing about the flag this
    test exists to pin. `maybe` in a boolean column reaches sqladmin's own
    `_coerce_bool`, which is the path that has to abort.
    """
    token = await _import_token(admin_client)
    content = _csv(
        f"{_PREFIX}eta,WP1 Eta,907,1",
        f"{_PREFIX}theta,WP1 Theta,908,maybe",
    )

    response = await _post_import(admin_client, content, token=token,
                                  continue_on_error="1")

    result = _result(response)
    assert result["imported"] == 0, result["summary"]
    assert "aborted on invalid row 3" in result["summary"], (
        "the file was not aborted at its first bad row; "
        f"sqladmin reported {result['summary']!r}"
    )
    assert _sectors(admin_app) == {}, (
        "the valid row before the bad one was written anyway, so the file "
        "half-landed - continue_on_error is no longer pinned false"
    )


async def test_a_row_that_fails_at_the_write_rolls_its_audit_entries_back_too(
    admin_client, admin_app
):
    """Contract §5.5 in the direction that is easy to get wrong: a change
    that is rolled back must leave no audit record claiming it happened.

    The bad row here passes every validation sqladmin can do and fails at the
    database - two rows with one `code`, which is unique. That is the only
    path that reaches `persist_import_models_with_count_check_sync`'s own
    rollback, and it is the path where the audit entries are already in the
    session when the failure happens: the listener adds them inside the
    transaction that is about to commit, so they go back with it or they
    are a lie.
    """
    token = await _import_token(admin_client)
    content = _csv(
        f"{_PREFIX}iota,WP1 Iota,908,1",
        f"{_PREFIX}iota,WP1 Iota Again,909,1",
    )

    response = await _post_import(admin_client, content, token=token)

    result = _result(response)
    assert result["imported"] == 0, result["summary"]
    assert _sectors(admin_app) == {}, (
        "the first row survived a file whose second row the database "
        "refused; the import is not atomic"
    )
    assert _entries(admin_app, admin_client.staff.username) == [], (
        "audit_log kept an entry for an import that wrote nothing, which is "
        "a trail claiming a change that never happened"
    )


async def test_a_view_without_the_mixin_is_refused_even_with_can_import_set(
    admin_client, admin_app, monkeypatch
):
    """The floor under the floor.

    `can_import = True` is one word, and sqladmin's own `check_can_import`
    returns it unchanged - so writing that word on a view that does not wear
    `AuditedImport` opens the upload route to any signed-in account and
    leaves it unaudited, with nothing on the screen saying so. The route
    refuses a view it does not recognise instead.

    Driven by setting that word on a live view rather than by declaring a
    fake one: the check is `isinstance(model_view, AuditedImport)` against
    the instance sqladmin registered, and a stand-in class would be testing
    the stand-in.

    **`factor-set`, because it is a screen that will never wear the mixin.**
    WP1 used `metric`, which WP3 then turned the import on for - and the
    failure was the wrong colour: the route let the request past this check
    and refused the file for its header instead, a 400 where this asserts a
    403. `factor_set` is not one of the fourteen and the reason is on the view
    itself: a set is created by cloning and its `status` is moved by four
    lifecycle actions that lock, revalidate and stamp.
    """
    view = next(
        v for v in _live_admin(admin_app)._views
        if getattr(v, "identity", None) == "factor-set"
    )
    monkeypatch.setattr(view, "can_import", True)
    token = await _import_token(admin_client)

    response = await admin_client.post(
        "/admin/factor-set/import",
        files={"csvfile": ("sets.csv", _csv(), "text/csv")},
        data={"csrf_token": token},
    )

    assert response.status_code == 403, (
        "a screen that was never wired for import accepted an upload as soon "
        "as somebody wrote can_import on it; that upload would be unaudited "
        f"and open to any signed-in account. Answered {response.status_code}"
    )


async def test_an_import_runs_the_view_s_own_invariant_guard(
    admin_client, admin_app, monkeypatch
):
    """`validate_before_commit` is the only enforcement point a form cannot
    walk past, and an import must not be the way past it.

    `sector` carries no cross-row invariant (tests/admin/test_taxonomy_views.py
    says so), so this asserts the *call*, not a refusal: it is the thing that
    has to be true before WP3 turns import on for `food_category`, whose
    "exactly one standard mix" rule no column constraint can express and
    whose guard lives on exactly this hook. The hook only runs if the route
    set `_view_var` - the listener has no other reference to the view.
    """
    view = next(
        v for v in _live_admin(admin_app)._views
        if getattr(v, "identity", None) == "sector"
    )
    called: list[object] = []
    monkeypatch.setattr(
        view, "validate_before_commit", lambda session: called.append(session)
    )
    token = await _import_token(admin_client)

    await _post_import(admin_client, _csv(f"{_PREFIX}kappa,WP1 Kappa,910,1"),
                       token=token)

    assert called, (
        "an imported row was committed without the view's own "
        "validate_before_commit ever being asked about it, so an import is a "
        "way round every cross-row rule this panel has"
    )
