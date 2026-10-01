"""Load `docker/mock-factors.json` into an empty database and publish it.

WHY THIS FILE EXISTS, AND WHY IT IS HERE RATHER THAN IN admin/
==============================================================

`kaicalc-admin seed-taxonomy` seeds the *taxonomy* - sectors, food categories,
destinations, destination groups, metrics, unit presets. It does not create a
factor set, and nothing else in the tree does either. Without one, a freshly
migrated database has **no published factor set**, and
`db/repository.get_published_factor_set_id` raises `NoPublishedFactorSetError`
on the first request. That is not a degraded calculator, it is a dead one:

    GET  /api/v1/taxonomy   -> fails, so the calculator page cannot even build
                               its dropdowns
    POST /api/v1/calculate  -> fails
    GET  /api/v1/factors    -> fails

Task 2's acceptance criterion is that `docker compose up` on a clean machine
produces a *working* calculator, so something has to put a published factor
set in the database. The task's other constraint is that it must not change
application behaviour, so this is deployment glue in `docker/` rather than a
new `kaicalc-admin seed-factors` subcommand in `admin/cli.py`. **That
subcommand is the right long-term home** - see the task 2 report.

The values are the ones from `tests/golden/case_01_canonical_two_entry/
bundle.json`, extracted verbatim, minus the taxonomy sections that
`admin/seed.py` already owns. They are placeholders (open item O-1: the client
has not supplied real New Zealand factors), and the set is written with
`is_mock = true`, which is what makes the mandatory non-dismissible
placeholder-data banner appear on every results view and export.

IDEMPOTENT, AND NON-DESTRUCTIVE IN THE STRONGEST SENSE
------------------------------------------------------

If **any** factor set already exists - draft, published or archived - this
script does nothing at all and says so. It does not compare, merge, or
"update the mock set to the current file". Once staff have touched the factor
tables, a redeploy that reasserted a placeholder set over their work would be
the single most destructive thing in this repository. The check is therefore
"is this database untouched?", not "does this particular set exist?".

Publishing goes through `db.repository.publish_factor_set`, not through a
direct `status = published` write, so the draft is subjected to the real
lifecycle: the one-published-set invariant and the O-7 prevention-completeness
check both run, and an `audit_log` row is written for the transition.

Run as:  python /app/seed_mock_factors.py     (see docker/init.sh)
"""

from __future__ import annotations

import json
import os
import sys
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from admin.factor_models import (
    Constant, Equivalence, FactorDownstream, FactorSet, FactorUpstream,
    FactorSetStatus, Formula,
)
from admin.taxonomy_models import Destination, FoodCategory, FoodItem, Metric, Sector
from db.repository import publish_factor_set
from db.session import create_session_factory

#: Beside this script inside the image (both are copied into /app).
DATA_FILE = Path(os.getenv("KAICALC_MOCK_FACTORS", Path(__file__).with_name("mock-factors.json")))

#: Written into `audit_log.actor` for the publish transition. Not a person and
#: not "cli" - an operator reading the audit log should be able to tell a
#: deployment's own first write from something a human typed.
ACTOR = "deploy-seed"


def _codes(session, model) -> dict[str, int]:
    return {row.code: row.id for row in session.scalars(select(model)).all()}


class MissingCodeError(RuntimeError):
    """A code this file names is not in the taxonomy."""


def _lookup(table: dict[str, int], code: str, kind: str, where: str) -> int:
    """Resolve one code, and say which one when it is not there.

    A bare ``table[code]`` raises ``KeyError: 'standard_mix'`` four frames
    down, and because ``init.sh`` runs under ``set -e`` that failure stops the
    migrate service, which stops api and admin - **the whole stack refuses to
    start over one renamed row**. Refusing is right: silently seeding a
    factor set with a row missing would publish a calculator that returns
    plausible numbers with a hole in them. But the operator has to be told
    what to fix, and a KeyError does not say which table the code belongs to,
    which row of this file wanted it, or that the cause is almost certainly a
    ``code`` renamed through the admin panel (a supported edit -
    ``FoodCategoryAdmin.form_columns`` includes ``code``) or a change to
    ``admin/seed.py`` that this file was not updated alongside.
    """
    try:
        return table[code]
    except KeyError:
        known = ", ".join(sorted(table)) or "(none)"
        raise MissingCodeError(
            f"{where} names {kind} {code!r}, which is not in the taxonomy. "
            f"Known {kind} codes: {known}. Either a code was renamed through "
            f"the admin panel, or admin/seed.py changed without "
            f"docker/mock-factors.json being updated to match."
        ) from None


def _decimal(row: dict, key: str) -> Decimal:
    """Values are transported as strings, per the DECIMAL-everywhere rule.

    `Decimal(str)` and never `Decimal(float)`: the whole point of the rule is
    that no value in this system passes through a binary float, and a seed
    that quietly did so would put 0.7000000000000001 in a factor table.
    """
    return Decimal(str(row[key]))


def main() -> int:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("seed_mock_factors: DATABASE_URL is required.", file=sys.stderr)
        return 1

    if not DATA_FILE.is_file():
        print(f"seed_mock_factors: {DATA_FILE} not found.", file=sys.stderr)
        return 1

    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    factory = create_session_factory(database_url)

    with factory() as session:
        existing = session.scalars(select(FactorSet)).all()
        if existing:
            labels = ", ".join(f"{row.version_label} ({row.status.value})" for row in existing)
            print(
                f"Factor sets already exist ({labels}). Nothing to do - this "
                "seed never overwrites a set that is already in the database."
            )
            return 0

        sectors = _codes(session, Sector)
        foods = _codes(session, FoodCategory)
        destinations = _codes(session, Destination)
        metrics = _codes(session, Metric)
        #: v1.54. Empty in a database seeded before the vocabulary existed,
        #: which is why a row naming a food is looked up rather than assumed:
        #: the lookup raises with the row index instead of writing a NULL,
        #: which would silently price every food in the category.
        items = _codes(session, FoodItem)
        if not (sectors and foods and destinations and metrics):
            print(
                "seed_mock_factors: the taxonomy is empty. Run "
                "`kaicalc-admin seed-taxonomy` first - the factor rows below "
                "are keyed on sector, food_category, destination and metric "
                "codes that have to exist already.",
                file=sys.stderr,
            )
            return 1

        factor_set = FactorSet(
            version_label=data["version_label"],
            status=FactorSetStatus.draft,
            is_mock=bool(data.get("is_mock", True)),
            #: v1.58's switch. Defaults FALSE, which is the answer for every
            #: file written before the field existed -- and the safe default
            #: either way: a set that released step 2.5 because nobody said
            #: otherwise would ask a finer question than its factors answer.
            item_level_enabled=bool(data.get("item_level_enabled", False)),
            notes=data.get("notes"),
        )
        session.add(factor_set)
        session.flush()
        set_id = factor_set.id

        counts = {
            "constant": 0, "formula": 0, "factor_upstream": 0,
            "factor_downstream": 0, "equivalence": 0,
        }

        for row in data.get("constants", []):
            session.add(Constant(
                factor_set_id=set_id, code=row["code"], value=_decimal(row, "value"),
                unit=row.get("unit"), note=row.get("note"),
            ))
            counts["constant"] += 1

        for index, row in enumerate(data.get("formulas", [])):
            where = f"formulas[{index}]"
            session.add(Formula(
                factor_set_id=set_id,
                metric_id=_lookup(metrics, row["metric"], "metric", where),
                expression=row["expression"], notes=row.get("notes"),
            ))
            counts["formula"] += 1

        for index, row in enumerate(data.get("upstream", [])):
            where = f"upstream[{index}]"
            #: `destination` is nullable and means "every destination that has
            #: none of its own". The `prevention` rows at 0 are the ones that
            #: make prevention a whole offset (O-7); without them
            #: publish_factor_set below refuses the set outright, which is the
            #: check doing exactly its job.
            dest = row.get("destination")
            session.add(FactorUpstream(
                factor_set_id=set_id,
                sector_id=_lookup(sectors, row["sector"], "sector", where),
                food_category_id=_lookup(
                    foods, row["food_category"], "food_category", where
                ),
                #: v1.54's second nullable dimension. `None` means the row
                #: prices every food in the category, which is what all twelve
                #: of `case_01`'s rows mean and what every file written before
                #: the dimension existed means.
                food_item_id=(
                    None if row.get("food_item") is None
                    else _lookup(items, row["food_item"], "food_item", where)
                ),
                destination_id=(
                    None if dest is None
                    else _lookup(destinations, dest, "destination", where)
                ),
                metric_id=_lookup(metrics, row["metric"], "metric", where),
                value_per_kg=_decimal(row, "value_per_kg"),
                source_note=row.get("source_note"),
                data_quality=row.get("data_quality"),
            ))
            counts["factor_upstream"] += 1

        for index, row in enumerate(data.get("downstream", [])):
            where = f"downstream[{index}]"
            #: Both `sector` and `food_category` are nullable and mean "every
            #: value of that dimension for this destination" - a null category
            #: is how a per-tonne charge like the waste levy is represented,
            #: and a null sector is what a set whose disposal routes cost the
            #: same wherever the waste arose takes on every row (v1.31).
            #: `.get` rather than `[...]`, matching `destination` above: this
            #: seed is the first-run path for a hand-written file, and a set
            #: written before v1.31 has no `sector` key at all - reading it as
            #: absent means "every sector", which is what those rows meant.
            sector = row.get("sector")
            food = row.get("food_category")
            session.add(FactorDownstream(
                factor_set_id=set_id,
                destination_id=_lookup(
                    destinations, row["destination"], "destination", where
                ),
                sector_id=(
                    None if sector is None
                    else _lookup(sectors, sector, "sector", where)
                ),
                food_category_id=(
                    None if food is None
                    else _lookup(foods, food, "food_category", where)
                ),
                metric_id=_lookup(metrics, row["metric"], "metric", where),
                value_per_kg=_decimal(row, "value_per_kg"),
                source_note=row.get("source_note"),
                data_quality=row.get("data_quality"),
            ))
            counts["factor_downstream"] += 1

        for index, row in enumerate(data.get("equivalences", [])):
            where = f"equivalences[{index}]"
            session.add(Equivalence(
                factor_set_id=set_id, code=row["code"], name=row["name"],
                source_metric_id=_lookup(
                    metrics, row["source_metric"], "metric", where
                ),
                value_per_unit=_decimal(row, "value_per_unit"),
                label_template=row["label_template"],
                #: v1.80. Named rather than splatted, like every other key
                #: this loader reads: a key the JSON carries and this file
                #: does not is a key the database never sees, and an
                #: equivalence with no `description` shows an empty
                #: disclosure on every card with nothing failing.
                description=row.get("description"),
                source_note=row.get("source_note"),
                sort_order=int(row.get("sort_order", 0)),
            ))
            counts["equivalence"] += 1

        session.flush()
        publish_factor_set(session, set_id, actor=ACTOR)
        session.commit()

    print(f"Seeded and published factor set {data['version_label']}.")
    for table, count in counts.items():
        print(f"  {table:<18}{count:>3} created")
    if data.get("is_mock", True):
        print()
        print(
            "This is a MOCK factor set. Every value in it is a placeholder "
            "(open item O-1). The calculator will show the non-dismissible "
            "placeholder-data warning on every result and export until a "
            "real factor set is published through the admin panel."
        )
    return 0


if __name__ == "__main__":
    # MissingCodeError is caught here rather than left to propagate. It is a
    # configuration mismatch an operator can fix, not a bug, and a traceback
    # would bury the one sentence that says which code is missing under six
    # frames of SQLAlchemy. The exit status is still non-zero, so init.sh's
    # `set -e` still stops the deployment - the message changes, the refusal
    # does not.
    try:
        sys.exit(main())
    except MissingCodeError as exc:
        print(f"seed_mock_factors: refused to seed. {exc}", file=sys.stderr)
        sys.exit(1)
