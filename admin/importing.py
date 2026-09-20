"""Bulk CSV import, on sqladmin's own machinery, with the three things it
does not ship.

**sqladmin already has an import.** ``sqladmin/_import.py`` is 619 lines of
CSV parsing, per-row validation, foreign-key validation, a count check and a
streaming progress response; ``ModelView`` carries ``can_import``, there is a
``POST /admin/{identity}/import`` route, a ``check_can_import(request)`` hook
and a modal template. Nothing here duplicates any of that, and nothing here
should: a second import path is a second set of validation rules, and the one
that stops matching is the one nobody notices.

What it does not have, measured in the installed 0.31.0 rather than assumed:

1. **It is not audited.** Auditing on this panel is a ``before_commit``
   listener (``admin/modelviews.py``) that writes only when ``_actor_var`` is
   set, and that var is set exclusively by ``AuditedModelView``'s
   ``insert_model`` / ``update_model`` / ``delete_model``. ``insert_model``
   appears **zero** times in ``sqladmin/_import.py``: the import goes
   ``persist_import_row_sync`` -> ``session.add`` -> ``flush`` -> one
   ``session.commit()``. The session is already the right one — the import
   calls ``model_view.session_maker(...)``, which is the audited sessionmaker
   ``AuditedModelView.__init__`` installed — so the listener does fire, and
   returns early for want of an actor. **Setting the actor is the fix.** Every
   write produces an ``audit_log`` entry is an architecture invariant, and the
   single largest write a staff member can make is the one that must not be
   the exception.
2. **It has no CSRF token.** ``csrf`` appears **zero** times in sqladmin's
   shipped import modal, and this panel enforces CSRF per form —
   ``admin.csrf.check_token`` called explicitly in each view, never as
   middleware. Turning ``can_import`` on without this adds an unprotected
   write route to an authenticated panel.
3. **It has no role floor of its own.** ``is_accessible`` covers the nine
   routes sqladmin generates, ``/import`` among them — but the seven taxonomy
   screens are open to both roles by contract §8.3, so on those views
   ``is_accessible`` answers "yes" for a plain ``staff`` account and the
   import route inherits that answer.
4. **It coerces where this project rejects, and on one shape it does not
   cope at all.** ``coerce_column_value`` is ``column.type.python_type(value)``,
   so a ``DECIMAL`` column gets ``Decimal(value)`` — which accepts
   ``1.2E+15``, ``nan``, ``Infinity`` and, silently, ``1_000`` as one
   thousand. And ``Decimal("1,5")`` raises ``decimal.InvalidOperation``,
   which is an ``ArithmeticError`` and **not** a ``ValueError``, so
   ``merge_import_row_data``'s ``except (TypeError, ValueError)`` does not
   catch it: the comma decimal Excel writes in a German or French locale
   escapes as an unhandled exception out of the middle of a streaming
   response. ``_numeric_rejections`` below is the answer to both.
5. **It has no way to name a foreign key except by its number.** A foreign
   key on a scaffolded form is a ``QuerySelectField``, which matches the
   referenced row's primary key as text and refuses anything else with "Not a
   valid choice". Ids differ between deployments, so a file written with them
   can only ever be loaded back into the database it came from — which is the
   one thing a configuration meant to move between deployments must not be.
   ``resolve_foreign_keys`` below translates a ``code`` into the id on the way
   in, refuses a code nothing answers to, and is also where the draft-only
   rule is stated, because it is the one pass holding both the file's rows and
   the state of every factor set they name.

Four fixes, in two places, and which place each one is in is not arbitrary:

* ``AuditedImport`` (the view side) answers **who** may import, through
  ``check_can_import``. sqladmin calls that hook from two directions — the
  ``POST /import`` route consults it before doing anything, and the list page
  consults it to decide whether to draw the Import button at all — so one
  override both refuses the request and stops offering the control to
  somebody who would be refused.
* ``KaiAdmin.import_endpoint`` (the route side) answers **what happens to
  this upload**: the CSRF check, the cleaning pass, the ``continue_on_error``
  pin, and the contextvars the audit listener reads. None of the four can
  live on the view: ``check_can_import`` is also called on a GET of the list
  page, where there is no form and no token to check, and
  ``continue_on_error`` is read off the submitted form by sqladmin's own
  route with no hook in between.

WHY THE CLEANING PASS IS ON THE ROUTE TOO, AND NOT ON EITHER OF THE TWO
HOOKS THAT LOOK LIKE ITS PLACE.

* ``on_import_row(data, model, request)`` is the per-row hook, and it runs
  too late and sees the wrong thing. It is called from
  ``persist_import_row_sync``, i.e. after every row has been through
  ``validate_import_row``, and its ``data`` is ``merged_import_data`` — the
  values already coerced by ``coerce_column_value``. The raw cell string, the
  only thing that can say whether ``1,5`` or ``1.2E+15`` was what somebody
  typed, is gone by then. Worse, the corruption this pass exists to refuse
  can never reach that hook: ``Decimal("1,5")`` raises ``InvalidOperation``
  out of ``merge_import_row_data`` long before persistence begins.
* ``validate_import_row`` is the right *stage* and the wrong *shape*. It is a
  module-level function in ``sqladmin._import``, called directly by
  ``stream_import_response``; ``ModelView`` exposes no hook onto it. Extending
  it means either monkeypatching another package's module namespace or
  forking the 190-line generator that calls it — and WP1 already refused a
  fork of sqladmin's 290-line ``main.js`` for the same reason.

So the pass runs on the raw upload, in the route, before ``import_csv`` is
called at all. That is the one point where the raw cell strings, their line
numbers and the model's column metadata are all in hand and nothing has been
coerced or written. It is not a second import path: it parses and refuses,
it never persists, and a file it passes goes through sqladmin's own pipeline
untouched.


WHY THE ADMINISTRATOR FLOOR, ON SCREENS THAT ARE OTHERWISE BOTH ROLES.
Contract §8.3 puts taxonomy CRUD at both roles, and this does not change
that: a staff member keeps every row-at-a-time control on the screen. An
import is a different act from the ones the floor was weighed against. It is
the largest single write the panel can make, it is made from a file whose
contents nobody reviewed in the panel, and it has no undo — a wrong file
leaves the way back as "find and correct every row it wrote", which is the
same job by hand. That is the same shape as ``/admin/staff/action/delete`` and
``/admin/factor-set/action/import-published``, both administrator-only, and
``admin/factor_views.py``'s ``_require_admin_for_import`` reaches the same
answer about the same word on a screen that is otherwise both roles.

WHY ``continue_on_error`` IS PINNED TO FALSE AND MUST STAY THERE. With it
false, ``persist_import_models_with_count_check_sync`` is atomic: one
``session.commit()`` after the loop, ``session.rollback()`` on the first bad
row, and the visitor is told *"Import aborted on invalid row N. No rows were
imported."* With it true, a file half of whose rows are malformed lands half
its rows, and the taxonomy is left in a state no file describes — and, because
the audit entry for the import is written inside that same commit, a partial
import would be audited as though it were the file. The checkbox sqladmin
renders for it is ignored here rather than removed: a hand-built POST can send
the field whatever the modal does, so the refusal has to be on this side.
tests/admin/test_import.py pins it.

SIX FIXES NOW, NOT FOUR: THE TWO MODES AND THE DRY RUN.

sqladmin's persistence is insert-only — ``Query._get_model_object`` is
``return self.model_view.model(**data)``, a new instance per row, no lookup —
so *export, correct in a spreadsheet, re-import*, the workflow this whole
feature exists for, collided on the row's own key. The section headed "The two
import modes, the upsert, and the dry run" below replaces that behaviour
without replacing sqladmin's import: the match is made on the session, through
SQLAlchemy's own ``before_flush``, in the one window where the transient
object sqladmin built is still an object and not yet a row. Every one of
``sqladmin/_import.py``'s 619 lines still runs.

Three things live there and each has its own argument in place:

* **the natural key is read off the schema's UNIQUE constraints**, because it
  is ``code`` on only eight of the fourteen and three of them have no ``code``
  column at all;
* **the second mode deactivates rather than deletes** wherever there is an
  ``active`` column to set, because the taxonomy tables are pointed at by
  ``submission_entry`` and ``submission_line`` and a delete would be refused
  by the database on any deployment that has ever been used;
* **the dry run is the same code with the write left off**, so a preview and
  the import it previews cannot describe the file differently.
"""

import contextvars
import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from sqladmin import Admin
from sqladmin._import import (
    handle_import_upload,
    import_csv,
    import_error_response,
    validate_import_row,
)
from sqladmin.authentication import login_required
from sqladmin.helpers import parse_csv
from sqlalchemy import UniqueConstraint, event, select
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from admin.audit import row_to_dict
from admin.auth import SESSION_KEY
from admin.csrf import check_token
from admin.modelviews import (
    _IMPORT_DEACTIVATED,
    _IMPORTED_UPDATES,
    ImportedFile,
    _actor_var,
    _import_var,
    _view_var,
    account_is_admin,
)

#: What a visitor is told when the token is missing or stale. Plain words
#: about what to do, in the register the rest of this panel's refusals use:
#: the person reading it did nothing wrong and cannot act on the word "CSRF".
CSRF_REFUSAL = (
    "This import form is no longer valid — it was opened before you signed "
    "in, or in a window that has since been signed out. Reload the page, "
    "choose the file again and press Import."
)

#: What a visitor is told when the view they are importing into was never
#: wired for it. Not reachable from the panel's own screens; it exists
#: because the route is generic over every registered view.
NOT_IMPORTABLE_REFUSAL = (
    "This screen does not accept an imported file."
)


# --- Cleaning: the adversary is Excel --------------------------------------
#
# Staff will export a table, open it in a spreadsheet, correct a figure and
# save. That round trip is the whole point of the feature and it is also the
# only part of it nobody controls. Excel will, without being asked and
# without saying so:
#
#   * write the decimal separator of the machine's locale, so `1.5` comes
#     back as `1,5` on a German or French install;
#   * group digits with commas once a cell is formatted as a number;
#   * fold a long number into scientific notation, showing fewer digits than
#     it stores;
#   * add a UTF-8 BOM and change the line endings;
#   * strip the leading zeros off anything that looks like a number.
#
# REJECT, NEVER COERCE. `1,5` could mean 1.5 or 15; `1.2E+15` may already
# have lost its last digits before it was saved. Guessing produces a number
# nobody typed, stored as though somebody had, with nothing on screen to say
# so - and these columns are emissions factors and unit conversions, where
# the whole of a calculation rides on the figure. Contract §1.2 puts DECIMAL
# everywhere and prohibits FLOAT and DOUBLE for the same reason; a CSV round
# trip through a spreadsheet is exactly the seam a float artefact gets in by.
#
# THE TWO THINGS THAT ARE NOT HERE, because sqladmin already does them and a
# second copy is a second thing to keep in step: `parse_csv` strips a leading
# UTF-8 BOM (`if csv_content[:3] == b"\xef\xbb\xbf"`) and splits on
# `str.splitlines()`, which takes CRLF and LF alike. Both are covered by
# tests in tests/admin/test_import_cleaning.py so that an upgrade that drops
# either one is a red test here rather than a file nobody can import.
#
# AND THE ONE THING THIS DELIBERATELY DOES NOT TOUCH: a text column. `code`
# is the cross-layer identifier and it is a string - `0012` is a different
# code from `12`, and a cleaner that "helpfully" normalised it would break
# the identity the whole contract is keyed on. Excel may well have eaten the
# leading zeros before the file ever arrived, and that is a loss this code
# cannot detect or undo; what it can promise is that nothing on this side
# does it. Only columns whose `python_type` is `Decimal` or `int` are looked
# at at all.

#: What this panel will accept in a numeric column: digits, an optional
#: leading sign, and a full stop for the decimal point. Matched with
#: `fullmatch`, so a trailing "kg" or a stray quote is a refusal rather than
#: a half-read number.
#:
#: Deliberately much narrower than `decimal.Decimal` itself, which is the
#: whole reason this exists rather than a bare `try: Decimal(value)`.
#: `Decimal` accepts `1.2E+15`, `nan`, `Infinity`, and - silently - `1_000`,
#: which it reads as one thousand.
_PLAIN_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)")

#: The same, for a column that holds whole numbers. A decimal point is
#: refused here even when the fraction is zero: sqladmin would coerce the
#: cell with `int("901.0")`, which raises, and the message a staff member
#: would then get is sqladmin's generic one rather than the one below.
_WHOLE_NUMBER = re.compile(r"[+-]?\d+")

#: The three shapes worth naming in a refusal, because each has a different
#: thing for the reader to go and do.
_SCIENTIFIC = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)[eE][+-]?\d+")
_GROUPED = re.compile(r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d*)?")
_COMMA_DECIMAL = re.compile(r"[+-]?\d+,\d+")


def _column_shape(column: Any) -> str:
    """``decimal(12,4)`` — the column's real precision, never a constant.

    `unit_preset.kg_per_unit` is `DECIMAL(12, 4)`, the four factor value
    columns are `DECIMAL(20, 10)`, `submission_line.qty_kg` is
    `DECIMAL(16, 3)` and the money columns are `DECIMAL(14, 2)`. A hard-coded
    scale would be right for one table and wrong for the other three, and
    wrong in the direction that accepts a digit the column then rounds away.
    """
    precision = getattr(column.type, "precision", None)
    scale = getattr(column.type, "scale", None)
    if precision is None or scale is None:
        return "this column"
    return f"decimal({precision},{scale})"


def _reject_cell(line: int, name: str, column: Any, raw: str) -> str | None:
    """One numeric cell, from its raw string. A refusal, or ``None``.

    Every refusal names the line, the column, the value and what was
    expected. "Invalid row 7" sends somebody back to a spreadsheet with
    nothing to look for; these are written to be read at five o'clock by a
    person who did not make the mistake and cannot see this code.

    Surrounding whitespace is stripped before anything else. That is the one
    normalisation here and it cannot change a number: `" 1.5 "` and `"1.5"`
    are the same figure, and a spreadsheet that padded a cell is not a
    spreadsheet that got the value wrong.
    """
    value = raw.strip()
    if not value:
        # Blank is not this pass's business. Whether the column may be null,
        # and what a missing required value does, is `merge_import_row_data`'s
        # answer and it already gives it.
        return None

    where = f'Line {line}, column "{name}": the value "{raw}"'
    whole = column.type.python_type is int

    # THE COMMA, IN ITS THREE FORMS, AND WHY THEY ARE THREE MESSAGES.
    # `1,5` can only be a decimal comma - a thousands group is three digits.
    # `1,234.5` and `1,234,567` can only be grouping - the decimal point is
    # already there, or there are two commas. `1,234` on its own is both, and
    # 1.234 and 1234 are three orders of magnitude apart, so that one says so
    # instead of picking. Guessing is the thing this whole pass exists not to
    # do, and it would be least defensible here.
    if _COMMA_DECIMAL.fullmatch(value):
        if _GROUPED.fullmatch(value):
            return (
                f"{where} contains a comma, and there is no way to tell "
                "which comma it is. Read as a decimal separator it means "
                f"{value.replace(',', '.')}; read as a thousands separator "
                f"it means {value.replace(',', '')}. Write whichever you "
                "meant, with no comma in it — the panel will not choose "
                "between them for you."
            )
        return (
            f"{where} uses a comma where a decimal point belongs. Write it "
            f"as {value.replace(',', '.')}. A spreadsheet writes the comma "
            "by itself when the machine's language is set to German, French "
            "or another locale that uses one, so a file can carry it "
            "without anybody having typed it."
        )

    if _GROUPED.fullmatch(value):
        return (
            f"{where} groups its digits with commas. Write it as "
            f"{value.replace(',', '')}, with no grouping. The separator "
            "comes from the cell's number format in the spreadsheet, so "
            "turn that off before saving or the next export will carry it "
            "again."
        )

    if _SCIENTIFIC.fullmatch(value):
        return (
            f"{where} is in scientific notation. Write the figure out in "
            "full. No expansion is offered here on purpose: a spreadsheet "
            "shows fewer digits than it stores and converts a long number "
            "to this form by itself, so the end of the number may already "
            "be gone — check it against wherever it came from rather than "
            "against what the cell displays."
        )

    if whole and _PLAIN_NUMBER.fullmatch(value) and not _WHOLE_NUMBER.fullmatch(value):
        as_number = Decimal(value)
        advice = (
            f"Write it as {as_number.to_integral_value()}."
            if as_number == as_number.to_integral_value()
            else "Write it as a whole number."
        )
        return (
            f"{where} has a decimal point and this column holds whole "
            f"numbers. {advice}"
        )

    pattern = _WHOLE_NUMBER if whole else _PLAIN_NUMBER
    if not pattern.fullmatch(value):
        expected = (
            "a whole number — digits, with an optional leading minus sign"
            if whole
            else "digits, with an optional leading minus sign and a full "
                 "stop for the decimal point"
        )
        return (
            f"{where} is not a number. Write {expected}, and nothing else: "
            "no units, no currency symbol, no spaces, no thousands "
            "separators."
        )

    try:
        number = Decimal(value)
    except InvalidOperation:  # pragma: no cover - the patterns above exclude it
        return f"{where} is not a number."

    if whole:
        return None

    scale = getattr(column.type, "scale", None)
    precision = getattr(column.type, "precision", None)

    # `normalize()` first, so a trailing zero is not a refusal. `1.23400` in
    # a decimal(12,4) column is the same number as `1.2340`: nothing is lost
    # storing it and nobody needs to be sent back to the spreadsheet for a
    # keystroke that changed no value. `1.23456` is a different case, and the
    # one this check exists for.
    places = -number.normalize().as_tuple().exponent
    if scale is not None and places > scale:
        return (
            f"{where} does not fit {_column_shape(column)} — it needs "
            f"{places} decimal places and the column keeps {scale}. Decide "
            "the last figure yourself and write it out. The panel will not "
            "round for you: once a rounded number is in the table it is "
            "indistinguishable from one somebody typed, and this column is "
            "read straight into a calculation."
        )

    if scale is not None and precision is not None:
        digits = len(value.lstrip("+-").split(".")[0].lstrip("0"))
        if digits > precision - scale:
            return (
                f"{where} does not fit {_column_shape(column)} — it has "
                f"{digits} digits before the decimal point and the column "
                f"holds {precision - scale} ({precision} digits in all, "
                f"{scale} of them after the point)."
            )

    return None


def numeric_rejections(content: bytes, model_view: Any) -> list[str]:
    """Every numeric cell in the upload that this panel will not read.

    All of them, not the first. `continue_on_error` is pinned false, so
    either way nothing is written — but a refusal that names one bad cell at
    a time means one trip back to the spreadsheet per bad cell, and a file
    that came out of a locale with the wrong decimal separator has one per
    row.

    The column metadata comes from `model.__table__.columns`, which the panel
    already holds in memory: no connection is opened, nothing is queried, and
    `db/repository.py` remains the only module that talks to the database.
    A name in the import columns that is not a mapper column — a
    relationship, which is what `column_list` contributes on a view like
    `UnitPresetAdmin` — is skipped here; it is not a numeric cell and
    sqladmin's own path decides what to do with it.

    A file that cannot be parsed at all returns nothing, deliberately.
    `import_csv` parses it again a moment later and reports the missing
    header or the bad encoding itself, and one message for one fault is
    better than this function inventing a second wording for the same thing.
    """
    try:
        rows = parse_csv(content, model_view._import_prop_names)
    except Exception:
        return []

    columns = model_view.model.__table__.columns
    rejections: list[str] = []
    for line, row in enumerate(rows, start=2):
        # `start=2` matches `stream_import_response`'s own numbering: line 1
        # is the header. Two numbering schemes for one file is how a staff
        # member ends up editing the wrong row.
        for name in model_view._import_prop_names:
            column = columns.get(name)
            if column is None:
                continue
            try:
                python_type = column.type.python_type
            except NotImplementedError:  # pragma: no cover - not on these tables
                continue
            # `is`, not `issubclass`: `bool` is a subclass of `int` in Python,
            # and `active` is not a numeric cell. sqladmin's `_coerce_bool`
            # owns that column and takes true/1/yes/on/t and their negatives.
            if python_type is not Decimal and python_type is not int:
                continue
            refusal = _reject_cell(line, name, column, row.get(name) or "")
            if refusal is not None:
                rejections.append(refusal)
    return rejections


def rejection_response(rejections: list[str], limit: int) -> Response:
    """The whole file refused, in one message, in plain words.

    Says three things in this order, because that is the order a reader needs
    them: nothing happened, here is exactly what is wrong, here is what to do.
    The middle part is the only one that varies, and it is the reason the
    refusal is worth building at all.
    """
    shown = rejections[:limit]
    count = len(rejections)
    head = (
        "This file was not imported and nothing in the table has changed.\n"
        + (
            "One value could not be read:"
            if count == 1
            else f"{count} values could not be read:"
        )
    )
    body = "\n\n".join(f"  {refusal}" for refusal in shown)
    tail = ""
    if count > len(shown):
        tail = f"\n\n  … and {count - len(shown)} more."
    return import_error_response(
        f"{head}\n\n{body}{tail}\n\n"
        "Correct them in the spreadsheet and upload the file again."
    )


# --- Foreign keys are written as the referenced row's `code` ---------------
#
# `code` is the cross-layer identifier everywhere else in this system - the
# API speaks it, the front end never learns a primary key - and an imported
# file is another layer. Two reasons, and the second is the one that decides
# it:
#
#   * a staff member editing a spreadsheet cannot know an autoincrement id,
#     and nothing on the exported row tells them;
#   * **ids differ between deployments.** A file keyed on ids can only ever be
#     re-imported into the database it came from, which would make moving a
#     configuration from one deployment to another - the reason the
#     configuration export/import exists at all - impossible on arrival.
#
# HOW IT IS DONE, AND WHY IT REWRITES THE FILE RATHER THAN HOOKING A ROW.
# sqladmin's import validates every row through the view's own scaffolded
# create form, and a foreign key on that form is a `QuerySelectField` whose
# `process_formdata` takes **the referenced row's primary key as text** and
# matches it against the choices it loaded. A code handed to that field
# matches nothing, `data` comes back None, and `pre_validate` refuses the row
# with "Not a valid choice" - which is the unhelpful failure WP2 met on
# `unit_preset.food_category` and excluded the column over.
#
# `on_import_row` cannot fix it: it runs during persistence, long after the
# form has refused the row. So the translation happens here, on the raw
# upload, before `import_csv` is called - the same point and for the same
# reason as the numeric cleaning pass above. What sqladmin then receives is a
# file whose foreign-key cells hold primary keys, which is exactly what the
# create form receives when a person fills it in by hand. Nothing downstream
# is patched, forked or told about any of this.
#
# WHAT IS NOT CHECKED HERE, because sqladmin already checks it: nothing. Its
# own `validate_foreign_key_values` reads `merged_import_data` by the *column*
# key (`sector_id`), and this panel's import columns are the *relationship*
# names (`sector`), so that function finds nothing to check on every one of
# these views and returns an empty dict. The existence check below is the
# only one there is.

#: The tables whose natural key is not called `code`.
#:
#: `factor_set` is the only one and it is not an omission in the schema:
#: contract §2.2 gives a factor set a `version_label` - "2026-Q1 draft" - and
#: no `code` column at all. It is unique, it is what the factor-set screen
#: shows, what every factor screen's own filter offers
#: (`ForeignKeyFilter(..., FactorSet.version_label)`) and therefore what a
#: staff member writing a file has in front of them.
_NATURAL_KEY_COLUMNS = {"factor_set": "version_label"}

#: `factor_set.status` as it is written in the database, for the draft-only
#: rule below. A literal rather than `FactorSetStatus.draft` so that this
#: module - which is generic machinery every importable screen goes through -
#: does not depend on one feature's models. The literal is pinned against the
#: enum by a test (tests/admin/test_import_tables.py), which is what keeps a
#: rename of the enum member from quietly turning the rule off.
_DRAFT = "draft"


def natural_key_column(model: Any) -> Any:
    """The column by which a foreign key naming a row of ``model`` is written.

    Raises ``KeyError`` for a model with neither a ``code`` nor an entry
    above - a wiring mistake rather than a runtime condition, and one a test
    walks every importable view to catch, because the alternative is a screen
    whose import refuses every file for a reason nobody can act on.
    """
    table = model.__table__
    return table.c[_NATURAL_KEY_COLUMNS.get(table.name, "code")]


def foreign_key_import_columns(model_view: Any) -> dict[str, Any]:
    """``{import column name: referenced model}`` for this view's foreign keys.

    Read off the mapper rather than declared per view: a relationship added to
    a view's import columns is a foreign key whether or not anybody remembered
    to list it somewhere, and the failure of forgetting is silent.

    Only many-to-one. A one-to-many on an import column would be a row naming
    its own children, which no screen here does and which a single cell cannot
    express.
    """
    mapper = model_view._mapper
    columns: dict[str, Any] = {}
    for name in model_view._import_prop_names:
        relation = mapper.relationships.get(name)
        if relation is None:
            continue
        if relation.direction.name != "MANYTOONE":
            continue
        columns[name] = relation.mapper.class_
    return columns


def _lookup_by_code(model_view: Any, target: Any, values: set[str]) -> dict:
    """``{code: (primary key as text, status or None)}`` for those that exist.

    One query per column per file, never one per row: a 500-row file naming
    one sector asks about that sector once.

    Plain column values rather than ORM rows, taken inside the session. A
    detached instance whose attributes were never loaded raises on access, and
    the two values wanted here - the key to write into the file, and the
    status the draft rule reads - are both known at select time.

    ``status`` is selected wherever the referenced table has such a column,
    which is `factor_set` and nothing else. Reading it unconditionally rather
    than on a flag keeps the lookup ignorant of which feature needs it.
    """
    key_column = natural_key_column(target)
    # Single-column primary keys throughout: `id`, autoincrement, on all
    # fourteen importable tables and on everything they reference. A composite
    # key would need a composite cell, which this file format has no way to
    # write, so this would be the place to refuse rather than to guess.
    pk_column = list(target.__table__.primary_key.columns)[0]
    status_column = target.__table__.c.get("status")

    columns = [key_column, pk_column]
    if status_column is not None:
        columns.append(status_column)

    with model_view.session_maker() as session:
        rows = session.execute(select(*columns).where(key_column.in_(values))).all()

    return {
        str(row[0]): (str(row[1]), row[2] if status_column is not None else None)
        for row in rows
    }


def _unknown_code(line: int, name: str, target: Any, raw: str) -> str:
    """Nothing answers to this code. In WP2's register: line, column, value,
    and what was expected."""
    key = natural_key_column(target)
    return (
        f'Line {line}, column "{name}": the value "{raw}" names no row in '
        f'{target.__table__.name} — nothing there has "{key.key}" equal to '
        f'it. A foreign key is written as the referenced row\'s {key.key}, '
        "never its number: the numbers differ between one deployment and the "
        "next, so a file written with them could only ever be loaded back "
        "into the database it came from. Check the spelling against the "
        f"{target.__table__.name} screen, and add the row there first if it "
        "does not exist yet."
    )


def _not_a_draft(line: int, name: str, raw: str, status: Any) -> str:
    """The file names a factor set that is not a draft."""
    state = getattr(status, "value", status)
    return (
        f'Line {line}, column "{name}": the value "{raw}" is a factor set '
        f"that is {state}, not {_DRAFT}. Its numbers must not change: every "
        "stored result carries the id of the set it was calculated against "
        "and has to go on reproducing years from now, which it cannot do if "
        "that set gains, loses or alters a row. Clone the set into a new "
        "draft and name the draft in this column instead."
    )


def _names_no_factor_set(line: int, name: str) -> str:
    """The draft column is empty. Refused here rather than left to the form,
    whose answer would be "Not a valid choice"."""
    return (
        f'Line {line}, column "{name}": no factor set is named. Every row in '
        "this file has to say which set of numbers it belongs to, and it has "
        "to be a draft. Write the draft's version label in this column."
    )


def resolve_foreign_keys(
    content: bytes, model_view: Any
) -> tuple[bytes | None, list[str]]:
    """Translate every foreign-key cell from a code into a primary key.

    Returns the rewritten file and no rejections, or ``None`` and every
    rejection in the file. All of them, not the first, for the reason
    `numeric_rejections` gives: one message per bad cell means one trip back
    to the spreadsheet per bad cell.

    **The draft-only rule is enforced here too**, and here is where it can be:
    this is the one pass that has both the file's own rows and the state of
    every factor set they name. Which page the visitor is standing on decides
    nothing - a visitor on a draft's screen can upload a file whose rows name
    the published set, and that is exactly the file this refuses.

    A file that cannot be parsed comes back unchanged and unrejected, the same
    way the numeric pass leaves it: `import_csv` parses it again a moment
    later and reports the missing header or the bad encoding in its own words.
    """
    try:
        rows = parse_csv(content, model_view._import_prop_names)
    except Exception:
        return content, []

    targets = foreign_key_import_columns(model_view)
    if not targets:
        return content, []
    draft_column = getattr(model_view, "import_draft_only_through", None)

    known: dict[str, dict] = {}
    for name, target in targets.items():
        wanted = {(row.get(name) or "").strip() for row in rows}
        wanted.discard("")
        known[name] = _lookup_by_code(model_view, target, wanted) if wanted else {}

    rejections: list[str] = []
    resolved: list[dict[str, str]] = []
    for line, row in enumerate(rows, start=2):
        # `start=2` for the same reason the numeric pass uses it: line 1 is
        # the header, and two numbering schemes for one file is how somebody
        # ends up editing the wrong row.
        out = {
            name: (row.get(name) or "")
            for name in model_view._import_prop_names
        }
        for name, target in targets.items():
            value = out[name].strip()
            if not value:
                if name == draft_column:
                    rejections.append(_names_no_factor_set(line, name))
                # Otherwise blank is not this pass's business: whether the
                # column may be null is the column's own answer, and
                # `merge_import_row_data` already gives it. A blank
                # `unit_preset.food_category` means "every food category",
                # which is the common case (see that model's docstring).
                continue
            found = known[name].get(value)
            if found is None:
                rejections.append(_unknown_code(line, name, target, out[name]))
                continue
            primary_key, status = found
            if name == draft_column and getattr(status, "value", status) != _DRAFT:
                rejections.append(_not_a_draft(line, name, out[name], status))
                continue
            out[name] = primary_key
        resolved.append(out)

    if rejections:
        return None, rejections
    return _rewritten(model_view._import_prop_names, resolved), []


def _rewritten(names: list[str], rows: list[dict[str, str]]) -> bytes:
    """The same file with its foreign-key cells translated.

    Re-emitted rather than patched in place, because the bytes that arrived
    may carry a BOM, either line ending and any quoting a spreadsheet felt
    like; `parse_csv` has already dealt with all three, and writing the parsed
    rows back out is what keeps this pass from having to deal with them again.
    Only the columns this view imports are written - the ones `parse_csv`
    itself keeps - so a file with extra columns loses them here exactly as it
    would have lost them there.

    **Not what gets audited.** The file-level audit entry digests the bytes as
    they were uploaded, which is the only form of them the person who sent the
    file can compare against.
    """
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(names)
    for row in rows:
        writer.writerow([row[name] for name in names])
    return buffer.getvalue().encode("utf-8")


# --- The two import modes, the upsert, and the dry run ---------------------
#
# WHY THIS EXISTS AT ALL. sqladmin's import is insert-only, and that is not a
# setting: `Query._get_model_object` is `return self.model_view.model(**data)`
# - a brand new instance per row, no lookup, no merge. So the workflow this
# whole feature was built for, *export, correct in a spreadsheet, re-import*,
# collides on the row's own `code` and the file is refused by the database.
#
# THE MATCH IS ON THE NATURAL KEY, NEVER ON `id`. The same rule the rest of
# the system follows and for the same two reasons: a staff member editing a
# spreadsheet cannot know an autoincrement id, and **ids differ between
# deployments**, so a file keyed on them could only ever be loaded back into
# the database it came from.
#
# BUT THE NATURAL KEY IS NOT ALWAYS `code`, AND ON HALF OF THE FOURTEEN IT IS
# NOT. Read off the schema's own UNIQUE constraints rather than assumed:
#
#   sector, food_category, food_item, destination_group, destination,
#   metric, unit_preset, comparison_scenario ......... (code)
#   constant, equivalence ............................ (factor_set_id, code)
#   formula .......................................... (factor_set_id, metric_id)
#   comparison_scenario_line ......................... (scenario_id, destination_id)
#   factor_upstream .. (factor_set_id, sector_id, food_category_id,
#                       food_item_id, destination_id, metric_id)
#   factor_downstream  (factor_set_id, destination_id, sector_id,
#                       food_category_id, metric_id)
#
# Three of the fourteen - `factor_upstream`, `factor_downstream`, `formula` -
# have **no `code` column at all**, and `comparison_scenario_line` has none
# either. An upsert keyed on `code` would have been a runtime `AttributeError`
# on four screens and a silent nothing on three more, so the key is derived
# from `__table__` and a view whose table carries no single UNIQUE constraint
# is refused by `natural_key_columns` rather than guessed at.
#
# THE NULLABLE KEY PARTS ARE REAL KEY VALUES. `factor_upstream.food_item_id`
# and `factor_downstream.sector_id` are nullable and a NULL means "every one
# of them" - which is why those two tables carry a second, COALESCE'd unique
# index (`uq_factor_upstream_generic`) on top of the plain constraint: MySQL
# treats two NULLs as distinct and the plain constraint alone stopped
# enforcing half of what it was written for. Matching here is done in Python
# on the resolved values, where `None` is an ordinary member of the tuple, so
# it has the COALESCE'd index's semantics and not the plain one's.


#: The default. A natural key in the file that exists updates that row; one
#: that does not creates it; a row in the table and not in the file is left
#: exactly as it was.
MODE_UPSERT = "update_and_add"

#: The same, and every row whose natural key the file does not carry is
#: **deactivated** - or, on the five tables that have no `active` column,
#: deleted. See `retirement_for` for which and why.
MODE_DEACTIVATE_MISSING = "deactivate_missing"

#: What the form field may say. An unrecognised value is refused rather than
#: defaulted: the two modes differ by whether rows the visitor is not looking
#: at disappear from the public calculator, and a typo that silently picked
#: one of them is the kind of thing nobody finds until a sector is missing.
IMPORT_MODES = (MODE_UPSERT, MODE_DEACTIVATE_MISSING)

#: The form field the modal posts. Not a header: the modal is a form and
#: `handle_import_upload` already reads `continue_on_error` from the same
#: place, so the mode travels the way the rest of the upload does.
MODE_FIELD = "import_mode"

#: What a visitor is told when the mode field says something this panel does
#: not recognise. Not reachable from the modal, which renders a closed list;
#: it exists because a hand-built POST can send anything.
UNKNOWN_MODE_REFUSAL = (
    "This import asked for a mode this panel does not have. Reload the page "
    "and choose either “Update and add” or “Update, add and "
    "deactivate rows that are not in the file”."
)

#: What a visitor is told when they ask for the second mode with a file that
#: has a header and no rows.
#:
#: **Refused rather than obeyed, and refused rather than ignored.** Obeyed, it
#: would deactivate the whole table from an empty file, which is the single
#: most destructive thing this panel could be asked to do by accident.
#: Ignored, it would silently do nothing - and sqladmin would take that
#: branch on its own, because `stream_import_response` never opens a database
#: session when there are no rows to persist, so the deactivation pass would
#: never run and the dry run would have promised something the real import
#: does not do. Saying so is the only answer that leaves the preview honest.
EMPTY_FILE_REFUSAL = (
    "This file has a header row and no data rows, and the mode chosen "
    "deactivates every row the file does not carry — so it would ask "
    "this panel to retire the whole table from an empty file. Nothing has "
    "been changed. Choose “Update and add” if the file is what you "
    "meant to send, or add the rows you want kept and upload it again."
)


def natural_key_columns(model: Any) -> tuple:
    """The columns that identify a row of ``model`` to an imported file.

    Read off the table's own UNIQUE constraint, never declared per view:
    a constraint is the database's answer to "which rows are the same row",
    and a second, hand-kept list of key columns is a second answer that is
    free to disagree with it. Each of the fourteen importable tables carries
    exactly one, which is what makes the derivation unambiguous; a table with
    none, or with two, raises here rather than being guessed at, and a test
    walks every importable view to catch it.

    Kept beside `natural_key_column` (singular) above, which answers a
    *different* question - how a foreign-key **cell** names a row of the table
    it points at - and is single-column by the nature of a cell. The two agree
    on every table that is both, which is pinned by a test rather than by a
    comment.
    """
    table = model.__table__
    uniques = [
        constraint for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    ]
    if len(uniques) != 1:
        raise KeyError(
            f"{table.name} carries {len(uniques)} unique constraints, so "
            "there is no single answer to which rows an imported file would "
            "be matching. Import cannot be turned on for it until there is."
        )
    return tuple(uniques[0].columns)


def owning_scope_column(model: Any):
    """The column naming the row this one is a child *of*, or ``None``.

    **This is what stops the second mode reaching outside the file's own
    subject.** "Deactivate every row the file does not carry" is the whole
    table for `sector`, and must not be the whole table for `constant`: a file
    of one draft's constants would otherwise retire every other factor set's,
    and a file of one scenario's lines would delete every other scenario's.

    Read off the schema rather than declared, and off the one thing in it that
    already means "this row belongs to that one": an outbound foreign key
    declared ``ON DELETE CASCADE``. Six of the fourteen have exactly one -
    the five factor children (`factor_set_id`) and `comparison_scenario_line`
    (`scenario_id`) - and the other eight have none, because they are nobody's
    children. Every one of those six also has that column as the first part of
    its natural key, which is asserted rather than assumed.
    """
    table = model.__table__
    owners = [
        foreign_key.parent for foreign_key in table.foreign_keys
        if (foreign_key.ondelete or "").upper() == "CASCADE"
    ]
    if not owners:
        return None
    if len(owners) > 1:
        raise KeyError(
            f"{table.name} is declared the child of {len(owners)} tables, so "
            "there is no single subject for an import that retires the rows a "
            "file leaves out."
        )
    owner = owners[0]
    if owner not in natural_key_columns(model):
        raise KeyError(
            f"{table.name}.{owner.name} is the column that makes this row a "
            "child, and it is not part of the table's natural key - so an "
            "import could not tell a row of one parent from the same row of "
            "another."
        )
    return owner


def retirement_for(model: Any) -> str:
    """``"deactivate"`` or ``"delete"``: what the second mode does here.

    **Deactivation is the schema talking, not caution.** All seven taxonomy
    tables are pointed at by foreign keys, four of them from `submission_entry`
    or `submission_line` - so once any calculation has been run the database
    refuses to delete those rows, and with `continue_on_error=False` that
    aborts the whole import. A literal "replace the table" option would be one
    that fails on every deployment that has ever been used. Deactivating
    instead keeps historical reproducibility (a `submission_entry` stamped
    years ago still points at a row that exists), is invisible to the public
    (the panel and the calculator both read active rows only), and is
    reversible.

    **FIVE OF THE FOURTEEN HAVE NO `active` COLUMN AND ARE THEREFORE A REAL
    DELETE**, and they are named here rather than left to hide inside a
    general rule:

    * ``comparison_scenario_line`` - a scenario's own child, nothing
      references it, and a line the file leaves out is a line that is not part
      of the scenario any more. There is nothing an inactive line could mean.
    * ``factor_upstream``, ``factor_downstream``, ``constant``, ``formula`` -
      the children of a factor set, and **only ever of a draft one**
      (`import_draft_only_through`). A published set's rows can never be
      reached by this path at all, so the reproducibility argument above does
      not apply: nothing points at these rows, a `submission` stamps the
      *set*, and a draft is by definition a set no stored result was ever
      calculated against. `equivalence` is the fifth child of a factor set and
      does carry `active`, so it deactivates.

    Verified against the schema rather than argued from it: every table this
    returns ``"delete"`` for has **no inbound foreign key at all**, which is
    pinned by a test. A table gaining one and keeping no `active` column would
    turn this into an import that the database refuses.
    """
    return "deactivate" if "active" in model.__table__.c else "delete"


def _coerce_key_part(column: Any, value: Any) -> Any:
    """One part of a natural key, normalised so that the three places it is
    read from compare equal.

    A key part arrives from a CSV cell (a string, already rewritten from a
    `code` into a primary key by `resolve_foreign_keys`), from a row selected
    out of the database (an ``int`` or a ``str``), and from the transient
    object sqladmin built for the row (whatever `coerce_column_value` made of
    it). ``"12"`` and ``12`` are the same row and have to hash the same, so
    all three go through this.

    Empty is ``None``, and ``None`` is a key value rather than the absence of
    one - `factor_downstream.sector_id` NULL means "every sector for this
    destination" and is exactly the row a levy is written as.

    **THE ``int`` BRANCH IS BELT-AND-BRACES TODAY, AND THAT WAS MEASURED.**
    Deleting it - so that every part goes through ``str`` alone - left every
    test in tests/admin/test_import_modes.py green, including the ones on
    `constant` and `comparison_scenario_line` whose keys are foreign keys. The
    mutation is equivalent rather than the tests weak: every natural-key column
    on the fourteen is either a ``String`` or an integer, and ``str`` is
    consistent across all three sources for both - the cell is already the
    digits `resolve_foreign_keys` wrote, the database returns the same integer
    and so does the object. What the branch buys is the case that does not
    exist yet: a key part with any other Python type - a ``Decimal``, say -
    would compare ``"1.50"`` against ``"1.5000"`` and match nothing, silently.
    It is kept as the place that failure would be fixed, not as something a
    test can currently tell apart.
    """
    if value is None or value == "":
        return None
    try:
        if column.type.python_type is int:
            return int(value)
    except (NotImplementedError, TypeError, ValueError):
        return value
    return str(value)


def _key_cells(model_view: Any) -> list:
    """``[(column, the import column that carries it)]`` for the natural key.

    A key part is written in the file either under its own name (`code`) or
    under the *relationship* that owns it: `factor_set_id` is written in the
    column called `factor_set`, because a foreign key in a file is named by
    the referenced row's `code` and sqladmin's form field for it is the
    relationship. Resolved off the mapper so that a view which renamed a
    field cannot drift from this.

    Raises if a key part is not importable at all, which would be a screen
    whose upsert could never match anything - it would insert a duplicate of
    every row and be refused by the very constraint it was reading.
    """
    mapper = model_view._mapper
    cells = []
    for column in natural_key_columns(model_view.model):
        name = None
        if column.key in model_view._import_prop_names:
            name = column.key
        else:
            for prop in model_view._import_prop_names:
                relation = mapper.relationships.get(prop)
                if relation is None:
                    continue
                if any(local is column
                       for local, _ in (relation.local_remote_pairs or [])):
                    name = prop
                    break
        if name is None:
            raise KeyError(
                f"{model_view.model.__table__.name}.{column.name} is part of "
                "this table's natural key and is not one of the columns an "
                "imported file carries, so no uploaded row could ever be "
                "matched to an existing one."
            )
        cells.append((column, name))
    return cells


def _attribute_for(mapper: Any, column: Any) -> str:
    """The mapped attribute name for a column. Usually the column's own name,
    and read through the mapper rather than assumed so that a model which
    named an attribute differently from its column still works."""
    return mapper.get_property_by_column(column).key


def _key_label(cells: list, key: tuple, raw: dict | None = None) -> str:
    """The natural key as a person would read it back on the screen.

    Written from the file's own cells where they are available - `factor_set`
    reads as the version label somebody typed, not as the primary key this
    pass translated it into - because the preview is read by the person who
    wrote the file.
    """
    parts = []
    for (column, name), value in zip(cells, key):
        shown = None if raw is None else raw.get(name)
        if shown in (None, ""):
            shown = "(none)" if value is None else value
        parts.append(f"{name}={shown}")
    return ", ".join(parts)


@dataclass(frozen=True)
class PlannedRow:
    """One row of the uploaded file, and what it would do."""

    #: The line in the file, counting the header as line 1 - the same
    #: numbering every rejection message uses.
    line: int
    #: The natural key, resolved and normalised.
    key: tuple
    #: That key as the file wrote it.
    label: str
    #: The row it would write over, or ``None`` if it would create one.
    row_id: int | None


@dataclass(frozen=True)
class RetiredRow:
    """One row already in the table whose natural key the file does not
    carry, and which the second mode would therefore retire."""

    row_id: int
    label: str
    #: ``"deactivate"`` or ``"delete"`` - see `retirement_for`.
    how: str


@dataclass
class ImportPlan:
    """What an uploaded file would do, computed without writing anything.

    Built on the route, from the file as it stands after the cleaning pass and
    the foreign-key translation, and used for two things that must not be able
    to disagree: it is the answer a dry run returns, and it is the map the
    upsert listener matches against while the real import runs. One object, so
    a preview that said "3 updated" cannot be followed by an import that
    creates three rows.
    """

    mode: str
    table: str
    cells: list
    scope_column: Any
    scope_values: set
    keys: set
    creates: list = field(default_factory=list)
    updates: list = field(default_factory=list)
    existing: dict = field(default_factory=dict)

    @property
    def row_count(self) -> int:
        return len(self.creates) + len(self.updates)


def build_import_plan(model_view: Any, content: bytes, mode: str) -> ImportPlan:
    """Read the file, ask the database what it already has, and decide.

    **Reads and does not write**, which is what lets the dry run be the same
    code path as the real import rather than a second description of it.

    A file that cannot be parsed comes back as an empty plan, the same way the
    two passes before this one leave it: `import_csv` parses it again a moment
    later and reports the missing header or the bad encoding in its own words.
    """
    cells = _key_cells(model_view)
    try:
        rows = parse_csv(content, model_view._import_prop_names)
    except Exception:
        rows = []

    scope = owning_scope_column(model_view.model)
    scope_index = None
    if scope is not None:
        scope_index = [column for column, _ in cells].index(scope)

    planned: list[tuple[int, tuple, str]] = []
    scope_values: set = set()
    for line, row in enumerate(rows, start=2):
        key = tuple(
            _coerce_key_part(column, row.get(name) or "")
            for column, name in cells
        )
        planned.append((line, key, _key_label(cells, key, row)))
        if scope_index is not None:
            scope_values.add(key[scope_index])

    plan = ImportPlan(
        mode=mode,
        table=model_view.model.__table__.name,
        cells=cells,
        scope_column=scope,
        scope_values=scope_values,
        keys={key for _, key, _ in planned},
    )

    with model_view.session_maker() as session:
        plan.existing = _existing_keys(session, model_view.model, cells,
                                       scope, scope_values)

    for line, key, label in planned:
        row_id = plan.existing.get(key)
        entry = PlannedRow(line=line, key=key, label=label, row_id=row_id)
        (plan.updates if row_id is not None else plan.creates).append(entry)
    return plan


def _existing_keys(session: Any, model: Any, cells: list, scope: Any,
                   scope_values: set) -> dict:
    """``{natural key: primary key}`` for the rows already in the table.

    Scoped to the parents the file names where the table has one (see
    `owning_scope_column`), so a file of one draft's constants asks about that
    draft and not about every factor set there has ever been.

    Column values rather than ORM rows, and one query rather than one per row.
    """
    key_columns = [column for column, _ in cells]
    primary_key = list(model.__table__.primary_key.columns)[0]
    statement = select(*key_columns, primary_key)
    if scope is not None:
        if not scope_values:
            return {}
        statement = statement.where(scope.in_(scope_values))
    found = {}
    for row in session.execute(statement).all():
        key = tuple(
            _coerce_key_part(column, value)
            for column, value in zip(key_columns, row[:-1])
        )
        found[key] = row[-1]
    return found


def retirements(session: Any, model: Any, plan: ImportPlan) -> list:
    """The rows the second mode would retire, in the order they are listed.

    Takes a session rather than opening one, because it is called from two
    places that must agree: the dry run, which reads through a session of its
    own and writes nothing, and the deactivation pass, which reads through the
    session the import is committing in - so that what it retires is decided
    inside the transaction that retires it, and a row created by this very
    file is never a row the same file retires.
    """
    if plan.mode != MODE_DEACTIVATE_MISSING:
        return []
    key_columns = [column for column, _ in plan.cells]
    primary_key = list(model.__table__.primary_key.columns)[0]
    active = model.__table__.c.get("active")
    how = retirement_for(model)

    columns = [*key_columns, primary_key]
    if active is not None:
        columns.append(active)
    statement = select(*columns)
    if plan.scope_column is not None:
        if not plan.scope_values:
            return []
        statement = statement.where(plan.scope_column.in_(plan.scope_values))

    out = []
    for row in session.execute(statement).all():
        key = tuple(
            _coerce_key_part(column, value)
            for column, value in zip(key_columns, row[:len(key_columns)])
        )
        if key in plan.keys:
            continue
        if active is not None and not row[len(key_columns) + 1]:
            # Already inactive. Left alone rather than written again, so that
            # re-uploading the same file twice does not fill the trail with
            # entries for a change that has already happened.
            continue
        out.append(RetiredRow(row_id=row[len(key_columns)],
                              label=_key_label(plan.cells, key), how=how))
    return out


#: The plan for the import in flight, or ``None``. Set by the route alongside
#: the three audit contextvars and read by the two session listeners below,
#: for the reason those three exist: the write happens deep inside sqladmin's
#: own machinery, while the streaming response body is produced, with no
#: reference back to the request that started it.
#:
#: **Never set for a dry run.** A dry run returns before this point, which is
#: what makes "it wrote nothing" a property of the control flow rather than a
#: promise.
_plan_var: contextvars.ContextVar["ImportPlan | None"] = contextvars.ContextVar(
    "kai_admin_import_plan", default=None
)


def install_import_modes(view: Any) -> None:
    """Teach one view's audited sessionmaker to upsert and to retire.

    **THE SEAM, AND WHY IT IS THIS ONE.** sqladmin's persistence is
    `persist_import_row_sync`: `_get_model_object` (a new instance, always),
    `on_import_row`, `_set_attributes_sync`, `session.add`, `session.flush`,
    each row inside its own SAVEPOINT. Not one of those five is replaceable
    from a `ModelView`: `Query` is constructed inside
    `persist_import_models_with_count_check_sync` and cannot be substituted,
    `on_import_row` is handed the new object and cannot return a different
    one, and `validate_import_row` is a module-level function with no hook on
    it. The three ways to change what gets written were to fork
    `sqladmin/_import.py` (619 lines), to monkey-patch `Query` for the whole
    process (which would change the *create form*'s behaviour too), or to work
    on the session - and the session is already ours: the importer calls
    `model_view.session_maker(...)`, which `AuditedModelView.__init__`
    replaced with the audited one. So the change is made where sqladmin hands
    us the object and before it becomes a row, through SQLAlchemy's own event
    API, and every one of those 619 lines - the validation, the streaming
    progress, the abort on the first bad row, the single commit - stays
    sqladmin's.

    Scoped to *this view's* sessionmaker instance, the same way the audit
    listener is: SQLAlchemy binds a listener registered on one `sessionmaker`
    object to the sessions that object produces, so nothing here can fire for
    another view, for the API layer or for the bootstrap step.
    """
    model = view.model
    mapper = view._mapper
    maker = view.session_maker

    @event.listens_for(maker, "before_flush")
    def _upsert_matching_rows(session, flush_context, instances) -> None:
        """Turn the INSERT sqladmin is about to make into an UPDATE.

        `before_flush` is where SQLAlchemy documents the session's contents as
        still being editable, and it is the only moment the pieces are all in
        hand at once: the transient object carries the file's values, the
        plan carries which existing row this natural key belongs to, and the
        existing row has not yet been touched, so `row_to_dict` on it is a
        genuine "before".

        `session.expunge` on the transient object rather than letting it be
        written and then deleted: `Session._flush` re-reads `session.new`
        *after* this event precisely so that a listener can do this, so the
        INSERT is never emitted at all. No row is created and destroyed, and
        nothing has to be undone if a later row of the same file is refused.

        Only the columns the object actually carries are copied over. A column
        outside the file is a column the file said nothing about, and an
        import that blanked it would be answering a question nobody asked.
        """
        plan = _plan_var.get()
        if plan is None:
            return
        for obj in list(session.new):
            if not isinstance(obj, model):
                continue
            key = tuple(
                _coerce_key_part(column,
                                 getattr(obj, _attribute_for(mapper, column),
                                         None))
                for column, _ in plan.cells
            )
            row_id = plan.existing.get(key)
            if row_id is None:
                continue
            existing = session.get(model, row_id)
            if existing is None:
                # The row went away between the plan and the flush. Left as an
                # insert, which is what the file asked for and what the
                # database will now accept.
                continue
            before = row_to_dict(existing)
            for attribute in mapper.column_attrs:
                column = attribute.columns[0]
                if column.primary_key:
                    continue
                if attribute.key in obj.__dict__:
                    setattr(existing, attribute.key, obj.__dict__[attribute.key])
            session.expunge(obj)
            session.info.setdefault(_IMPORTED_UPDATES, []).append(
                (existing, before)
            )

    @event.listens_for(maker, "before_commit", insert=True)
    def _retire_rows_absent_from_the_file(session) -> None:
        """"Also deactivate the rest", applied inside the import's own commit.

        **`insert=True`, and that is load-bearing.** This listener has to run
        before `_audited_session_maker`'s, because the rows it touches are
        audited by that one - a deactivation applied afterwards would be a
        change with no entry. Registered here, after the view's `__init__`
        has installed the audit listener, so prepending is the only way to be
        ahead of it.

        Skipped on a SAVEPOINT release for the same reason the audit listener
        skips one: SQLAlchemy raises `before_commit` when a nested transaction
        is released as well as when the real one commits, and the importer
        wraps every row in a nested transaction.

        **It deactivates; it does not delete** - except on the five tables
        that have no `active` column, where it does. `retirement_for` holds
        which and why.
        """
        plan = _plan_var.get()
        if plan is None or plan.mode != MODE_DEACTIVATE_MISSING:
            return
        if session.in_nested_transaction():
            return
        if session.info.get(_IMPORT_DEACTIVATED) is not None:
            return

        retired = retirements(session, model, plan)
        deactivated = 0
        accumulator = session.info.setdefault(_IMPORTED_UPDATES, [])
        for entry in retired:
            row = session.get(model, entry.row_id)
            if row is None:
                continue
            if entry.how == "delete":
                session.delete(row)
                continue
            before = row_to_dict(row)
            row.active = False
            accumulator.append((row, before))
            deactivated += 1
        session.info[_IMPORT_DEACTIVATED] = deactivated


def read_import_mode(form: Any) -> str | None:
    """The mode the upload asked for, or ``None`` if it named one that does
    not exist. An absent field is the default, because that is what every
    caller written before this existed sends."""
    raw = form.get(MODE_FIELD)
    if raw in (None, ""):
        return MODE_UPSERT
    value = str(raw).strip()
    return value if value in IMPORT_MODES else None


def read_dry_run_header(request: Request) -> bool | None:
    """§6.2's `X-Dry-Run`, read the way `api/router.py` reads it.

    The same word, deliberately: this panel already has a dry-run vocabulary -
    the header on `POST /api/v1/calculate` and the `/admin/try` screen that
    sends it - and a second one would be a second thing to learn. `false` is
    not an error, because it is what a client sends when it templates the
    header unconditionally; anything else is ``None``, which the route turns
    into a refusal, so that a typo does not silently write the file.
    """
    header = request.headers.get("X-Dry-Run")
    if header is None:
        return False
    value = header.strip().lower()
    if value in {"true", "false"}:
        return value == "true"
    return None


async def dry_run_rejections(request: Request, model_view: Any, content: bytes,
                             denormalize: Any) -> tuple[list[str], int | None]:
    """Every row sqladmin's own validation would refuse, and where it would
    stop.

    **`validate_import_row` itself**, imported from `sqladmin._import` and
    called with exactly the arguments the real import calls it with - the
    view's scaffolded *create* form, its import columns, its denormaliser. A
    preview with rules of its own is a preview that tells you the file is fine
    and then watches it be refused, which is the failure this whole feature
    exists to stop happening in a spreadsheet.

    Returns the messages and the line the import would abort at, because
    `continue_on_error` is pinned false: a file with a bad row at line 7
    writes nothing at all, whatever lines 2 to 6 say.
    """
    try:
        rows = parse_csv(content, model_view._import_prop_names)
    except Exception:
        return [], None

    form_class = await model_view.scaffold_form(model_view._form_create_rules)
    messages: list[str] = []
    aborts_at: int | None = None
    for line, row in enumerate(rows, start=2):
        _, errors, _ = validate_import_row(
            row, model_view._import_prop_names, model_view.model, form_class,
            denormalize,
        )
        if not errors:
            continue
        if aborts_at is None:
            aborts_at = line
        for field_name, field_errors in errors.items():
            for message in field_errors:
                messages.append(f'Line {line}, column "{field_name}": {message}')
    return messages, aborts_at


def dry_run_response(plan: ImportPlan, retired: list, rejections: list[str],
                     aborts_at: int | None, limit: int) -> Response:
    """What a dry run answers with: counts, and the rows behind each count.

    **JSON rather than the newline-delimited stream a real import answers
    with**, because there is no progress to report - nothing is being written,
    so there is nothing to be part-way through. WP4 renders this; the shape is
    the contract between the two.

    `ok` is false when anything would be refused, and the summary says so in
    the same words the real import would: with `continue_on_error` pinned
    false, one bad row means no rows.
    """
    ok = not rejections
    if not ok:
        summary = (
            f"This file would be refused at line {aborts_at} and nothing "
            "would be written. Every row that could not be read is listed "
            "below."
        )
    else:
        pieces = [f"{len(plan.creates)} row(s) would be added",
                  f"{len(plan.updates)} would be updated"]
        if plan.mode == MODE_DEACTIVATE_MISSING:
            deactivating = [entry for entry in retired if entry.how == "deactivate"]
            deleting = [entry for entry in retired if entry.how == "delete"]
            if deactivating:
                pieces.append(f"{len(deactivating)} would be deactivated")
            if deleting:
                pieces.append(f"{len(deleting)} would be deleted")
        summary = ", ".join(pieces) + ". Nothing has been written."

    return JSONResponse(
        {
            "dry_run": True,
            "ok": ok,
            "table": plan.table,
            "mode": plan.mode,
            "total": plan.row_count,
            "aborts_at_line": aborts_at,
            "created": [
                {"line": entry.line, "key": entry.label}
                for entry in plan.creates[:limit]
            ],
            "updated": [
                {"line": entry.line, "key": entry.label, "row_id": entry.row_id}
                for entry in plan.updates[:limit]
            ],
            "deactivated": [
                {"row_id": entry.row_id, "key": entry.label}
                for entry in retired[:limit] if entry.how == "deactivate"
            ],
            "deleted": [
                {"row_id": entry.row_id, "key": entry.label}
                for entry in retired[:limit] if entry.how == "delete"
            ],
            "counts": {
                "created": len(plan.creates),
                "updated": len(plan.updates),
                "deactivated": sum(1 for e in retired if e.how == "deactivate"),
                "deleted": sum(1 for e in retired if e.how == "delete"),
                "rejected": len(rejections),
            },
            "rejected": rejections[:limit],
            "summary": summary,
        }
    )


class AuditedImport:
    """Mixed into a ``ModelView`` to turn sqladmin's import on, behind the
    administrator floor.

    Mixed in **ahead** of the view's own base, the way ``AdministratorOnly``
    is, so that ``check_can_import`` resolves here rather than to sqladmin's
    "return ``self.can_import``".

    Deliberately not a subclass of ``AuditedModelView``. The auditing a
    subclass would inherit is not what makes an import audited — the actor
    contextvar is, and that is set by the route below. Keeping this a plain
    mixin is what lets it be worn by a view that already has a base class,
    which is every view on this panel.

    **Every view wearing this sets ``column_import_list = form_columns``**,
    and that is a property rather than a habit. sqladmin validates each
    imported row through the view's own scaffolded *create* form
    (``import_csv`` → ``scaffold_form(self._form_create_rules)``), so the two
    lists cannot differ without something being wrong in one direction or the
    other: an import column that is not a form field is a value nothing
    validates, and a **required** form field that is not an import column
    fails ``pre_validate`` on every row of every file. Writing it as the
    assignment rather than as a second copy of the list is what stops the two
    drifting the next time a column is added to a screen.
    """

    #: sqladmin's own switch. ``False`` on ``ModelView``; the whole of what
    #: this mixin turns on, everything else here being the conditions.
    can_import = True

    #: The import column naming the factor set every row of an uploaded file
    #: must belong to, or ``None`` where no such rule applies.
    #:
    #: Set to ``"factor_set"`` on the five factor children - `factor_upstream`,
    #: `factor_downstream`, `constant`, `formula`, `equivalence` - and on
    #: nothing else. Those five are the tables whose rows a stored submission
    #: depends on: every `submission` stamps the `factor_set_id` it was
    #: calculated against and has to keep reproducing years later, which it
    #: cannot do if that set gains, loses or alters a row. The same rule
    #: `admin/factor_views.py`'s `_refuse_if_factor_set_not_draft` states for
    #: the edit and delete paths, and `admin/factor_lifecycle.py`'s
    #: `import_published_into` states for its target.
    #:
    #: Checked against **the file's own contents, every row** - see
    #: `resolve_foreign_keys`. The page the visitor is standing on decides
    #: nothing, because a visitor on a draft's screen can upload a file whose
    #: rows name the published set.
    import_draft_only_through: str | None = None

    def __init__(self) -> None:
        """Wire this view's own sessionmaker for the two import modes.

        **After ``super().__init__()``, which is not incidental.**
        ``AuditedModelView.__init__`` is what replaces ``session_maker`` with
        the audited one and registers the audit listener on it; there is no
        sessionmaker to attach to before that call, and the ``before_commit``
        listener installed below has to be *ahead* of the audit one, which it
        can only be by being registered after it with ``insert=True``.

        A mixin with an ``__init__`` works here because this class is mixed in
        **ahead** of the view's own base, the way ``AdministratorOnly`` is, so
        ``super()`` reaches that base and the chain is unbroken.
        """
        super().__init__()
        install_import_modes(self)

    @property
    def import_retirement_word(self) -> str:
        """"deactivated" or "deleted", for the modal's own label.

        **The control has to say what will happen on the screen it is on.**
        "Deactivate rows not in the file" is true on nine of the fourteen and
        false on the other five, which have no `active` column and where the
        second mode is a real delete (`retirement_for` holds which and why). A
        staff member choosing an option must not be told something the panel
        will not do.
        """
        if retirement_for(self.model) == "deactivate":
            return "deactivated"
        return "deleted"

    async def check_can_import(self, request: Request) -> bool:
        """``role = admin``, re-read from the database on every request.

        Called by sqladmin from two places and the difference matters: the
        ``POST /{identity}/import`` route consults it (via ``Admin._import``)
        before it touches the upload, and the list page consults it to decide
        whether to render the Import button and its modal. So one override
        both refuses a plain ``staff`` account and stops offering them a
        control they would be refused on — an entry that 403s when pressed is
        the panel telling somebody they have a capability they do not have.

        A boolean rather than a raise: that is the hook's contract, and
        ``Admin._import`` turns ``False`` into 403 itself.
        """
        if not self.can_import:
            return False
        return account_is_admin(
            self.session_maker, request.session.get(SESSION_KEY)
        )


def _digest_upload(filename: str | None, content: bytes) -> ImportedFile:
    """What goes in the file-level audit entry.

    The bytes as uploaded, never a decoded or re-encoded form of them: a
    digest that survives a round trip through this process's idea of an
    encoding is a digest of what we understood, not of what was sent, and the
    question it exists to answer is "is this the file I sent you".
    """
    return ImportedFile(
        filename=filename or "(unnamed)",
        byte_count=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )


class KaiAdmin(Admin):
    """sqladmin's ``Admin``, with the import route replaced.

    One method is overridden. The route table, the templates, the menu and
    the other eight generated routes are sqladmin's, untouched — this is not
    a fork.

    **Why the route and not a hook.** Three of the things this method does
    have no hook on ``ModelView``:

    * the CSRF token has to be read off the submitted form, and the only
      per-view hook that runs on this route (``check_can_import``) also runs
      on a GET of the list page, where there is no form;
    * ``continue_on_error`` is read straight off the form by
      ``handle_import_upload`` and handed to ``import_csv`` by the route, with
      nothing in between;
    * the audit contextvars have to be set around work that has not started
      when any per-view hook returns — the database writing happens while the
      streaming response body is being produced, after this method has
      returned its response object.
    """

    @login_required
    async def import_endpoint(self, request: Request) -> Response:
        """``POST /admin/{identity}/import``.

        The order of the checks below is the order of what they cost. The
        role floor and the token are decided before a byte of the upload is
        parsed; the upload is read before anything is written; nothing is
        written until sqladmin's own validation has passed every row.
        """
        # sqladmin's own gate: `is_accessible` for the view, then its
        # `check_can_import`, which `AuditedImport` above overrides with the
        # administrator floor. Raises 403 itself.
        await self._import(request)

        identity = request.path_params["identity"]
        model_view = self._find_model_view(identity)

        # A view that was never wired for import cannot reach this line
        # today - `_import` above would have refused it, because
        # `ModelView.can_import` is False and its `check_can_import` returns
        # it. This is the floor under that: `can_import = True` written on a
        # plain `ModelView` would otherwise open this route to any signed-in
        # account, because nothing else on that view carries the role check.
        # The import is available through `AuditedImport` or not at all.
        if not isinstance(model_view, AuditedImport):
            raise HTTPException(status_code=403, detail=NOT_IMPORTABLE_REFUSAL)

        # `await request.form(...)`, NOT `async with`. Starlette caches the
        # parsed form on the request (`Request._form`), so the
        # `async with request.form(max_files=1)` inside
        # `handle_import_upload` below gets this same object back rather than
        # re-reading a stream that has already been consumed - but its
        # `__aexit__` closes the form, which closes the uploaded file. Using
        # the context-manager form here would close it before that function
        # ever read it.
        form = await request.form(max_files=1)
        if not check_token(request.session, form.get("csrf_token")):
            return import_error_response(CSRF_REFUSAL)

        upload = await handle_import_upload(request, model_view)
        if upload.error:
            return import_error_response(upload.error, upload.status_code)
        if not upload.content:
            return import_error_response(
                "No CSV file uploaded or file does not have a .csv extension."
            )

        # THE CLEANING PASS. Before the contextvars, because a file refused
        # here writes nothing and audits nothing - the same as a file refused
        # for its token. See the module docstring for why it is here rather
        # than on `on_import_row` or on `validate_import_row`.
        #
        # ONE REFUSAL, TWO FUNCTIONS, AND THAT IS THE DIVISION.
        #
        # To the person who uploaded the file this is a single pass: one
        # message, one 400, one list of everything wrong, in one place. Both
        # run before either can refuse, for the same reason each reports every
        # bad cell rather than the first - a file out of a spreadsheet with
        # the wrong decimal separator and a misspelled sector has both faults,
        # and answering them one round trip at a time is the behaviour these
        # messages exist to replace.
        #
        # They are two functions because one of them must not touch the
        # database and the other cannot avoid it: `numeric_rejections` reads
        # only column metadata already in memory, which is what lets
        # tests/admin/test_import_cleaning.py drive the decimal rules against
        # a bare object with no session at all, while resolving a code means
        # asking whether a row exists. Splitting them there rather than
        # anywhere else keeps the untestable half small.
        #
        # The numeric rejections come first in the list because they are about
        # the cell a person typed; the foreign-key ones are about a row
        # somewhere else.
        rejections = numeric_rejections(upload.content, model_view)
        content, unresolved = resolve_foreign_keys(upload.content, model_view)
        rejections.extend(unresolved)
        if rejections:
            return rejection_response(
                rejections, model_view.max_reported_missed_rows
            )

        # THE MODE, AND THE PLAN IT PRODUCES.
        #
        # Read here, on the route, for the third time and the same reason WP2
        # put the cleaning pass here and WP3 put the foreign keys here: this
        # is the only place where the file's raw rows, the view's column
        # metadata and a database session are all in hand *before* anything
        # has been written. `on_import_row` is handed data that is already
        # coerced and is called one row at a time, with no view of the file as
        # a whole - and a mode that says "deactivate every row the file does
        # not carry" is a statement about the whole file by definition.
        #
        # The plan is the one object the dry run and the real import share.
        # Two descriptions of what an upload would do are two things that can
        # disagree, and the only place a reader would ever find out is after
        # the write.
        mode = read_import_mode(form)
        if mode is None:
            return import_error_response(UNKNOWN_MODE_REFUSAL)

        dry_run = read_dry_run_header(request)
        if dry_run is None:
            return import_error_response(
                "X-Dry-Run must be true or false."
            )

        plan = build_import_plan(model_view, content, mode)

        if mode == MODE_DEACTIVATE_MISSING and plan.row_count == 0:
            return import_error_response(EMPTY_FILE_REFUSAL)

        if dry_run:
            # NOTHING BELOW THIS LINE RUNS, AND THAT IS THE WHOLE PROOF.
            #
            # The audit contextvars are not set, the plan contextvar is not
            # set, and `import_csv` - the only thing on this route that opens
            # a write session - is never called. "A dry run writes nothing" is
            # therefore a property of the control flow rather than a promise
            # made by code that also writes; the test for it reads the table
            # and `audit_log` back out afterwards rather than taking this
            # comment's word for it.
            with model_view.session_maker() as session:
                retired = retirements(session, model_view.model, plan)
            refusals, aborts_at = await dry_run_rejections(
                request, model_view, content, self._denormalize_wtform_data,
            )
            return dry_run_response(plan, retired, refusals, aborts_at,
                                    model_view.max_reported_missed_rows)

        uploaded = form.get("csvfile")
        _import_var.set(_digest_upload(getattr(uploaded, "filename", None),
                                       upload.content))
        _actor_var.set(request.session.get(SESSION_KEY) or "unknown")
        _view_var.set(model_view)
        # Set alongside the other three and never reset, for the reason spelled
        # out below: the write has not started when this method returns.
        _plan_var.set(plan)

        # SET AND NOT RESET, AND THAT IS NOT AN OVERSIGHT.
        #
        # `insert_model` and friends can use a try/finally because the write
        # has finished by the time they return. This one has not started: the
        # response below is a StreamingResponse, and every row is validated
        # and committed while its body is being produced, which happens after
        # this method returns. A `finally` here would clear the actor before
        # the commit the listener fires on, and the import would be
        # unaudited - the exact defect this module exists to close.
        #
        # There is nothing to reset. A ContextVar set inside a request is set
        # in the context of the asyncio task serving that request, which is a
        # copy taken when the task was created; the streaming body is
        # produced by that same task or by a child of it, which inherits
        # another copy. Neither can be seen by another request, and both die
        # with this one. Verified against the installed Starlette: for
        # `spec_version >= 2.4` `StreamingResponse.__call__` awaits
        # `stream_response` inline, and below it starts it on a task group
        # child - a copy either way.
        #
        # The one thing this does leave set is the actor for the remainder of
        # *this* request, which is the request that actor made. The listener's
        # own filter (`if not (created or updated or deleted)`) means a
        # read-only session that happens to commit still writes nothing.

        # `content`, not `upload.content`: the file with its foreign-key cells
        # translated from codes into the primary keys sqladmin's own create
        # form expects (see `resolve_foreign_keys`). Identical to the upload
        # for a view with no foreign key among its import columns.
        #
        # `False`, never `upload.continue_on_error`. See the module docstring:
        # the flag is what makes the import atomic, the modal offers a
        # checkbox for it, and a hand-built POST can set the field whatever
        # the modal does - so it is refused here rather than hidden there.
        return await import_csv(
            request,
            model_view,
            content,
            False,
            self._denormalize_wtform_data,
        )

