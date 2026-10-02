"""Load the client-derived draft factor set into a database, as a DRAFT.

    python data/upstream-factors-draft/load_upstream_factors_draft.py

Reads ``DATABASE_URL`` from the environment, exactly as
``docker/seed_mock_factors.py`` and ``tests/benchmark/refed/
load_refed_benchmark.py`` do. Modelled directly on the latter -- see its own
docstring for why this shape exists rather than reusing
``docker/seed_mock_factors.py`` (which refuses outright if any factor set
already exists, and can only look taxonomy codes up, not create them).

Unlike the ReFED loader, this one adds **no taxonomy**: every sector, food
category, food item and destination this file's data names is already seeded by
``admin/seed.py`` (this is New Zealand data, keyed to the New Zealand
taxonomy, not a parallel one). If any code this file expects is missing, that
is treated as a hard stop -- see ``_lookup`` below -- rather than created on
the fly, because creating taxonomy from inside a factor loader is exactly the
kind of code path ``admin/seed.py``'s own docstring warns is fragile once
staff have started renaming rows through the panel.

WHAT IT GUARANTEES
-------------------
One session, one commit. The factor set lands whole or not at all.

It leaves the new set as a **draft** and never calls
``publish_factor_set``. Publishing would archive whatever is currently live,
and this data has not been confirmed by the client -- ``is_mock`` stays
``true`` for the same reason. Reach it through the admin panel's dry-run view
once it exists in the database.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from admin.factor_models import (  # noqa: E402
    Constant,
    Equivalence,
    FactorDownstream,
    FactorSet,
    FactorSetStatus,
    FactorUpstream,
    Formula,
)
from admin.taxonomy_models import (  # noqa: E402
    Destination,
    FoodCategory,
    FoodItem,
    Metric,
    Sector,
)
from db.session import create_session_factory  # noqa: E402

HERE = Path(__file__).resolve().parent


class LoadError(RuntimeError):
    """Anything that should stop the load with a message and no traceback."""


def _codes(session: Session, model) -> dict[str, int]:
    return {row.code: row.id for row in session.scalars(select(model)).all()}


def _lookup(table: dict[str, int], code: str, kind: str, where: str) -> int:
    if code not in table:
        known = ", ".join(sorted(table)[:20])
        raise LoadError(
            f"Unknown {kind} code {code!r} in {where}. Known {kind} codes "
            f"begin: {known}. Has `kaicalc-admin seed-taxonomy` been run, or "
            "has admin/seed.py changed since this draft was built?"
        )
    return table[code]


def load_factor_set(session: Session, data: dict) -> tuple[int, dict[str, int]]:
    label = data["version_label"]
    if session.scalar(select(FactorSet).where(FactorSet.version_label == label)):
        raise LoadError(
            f"A factor set labelled {label!r} is already in this database. "
            "Nothing has been changed. Delete it from the admin panel first "
            "if you mean to reload it."
        )

    factor_set = FactorSet(
        version_label=label,
        status=FactorSetStatus.draft,
        is_mock=bool(data.get("is_mock", True)),
        #: v1.58's release switch for step 2.5, read the same way
        #: ``docker/seed_mock_factors.py`` reads it and defaulting the same
        #: way. **FALSE is the answer for every file written before the field
        #: existed** and the safe answer either way: a set that released step
        #: 2.5 because nobody said otherwise would ask a finer question than
        #: its factors can answer. A set that says ``true`` and carries no
        #: item-level row is refused at publish by
        #: ``refuse_item_level_without_item_rows``, not here -- this loader
        #: lands what the JSON says and leaves the set a draft.
        item_level_enabled=bool(data.get("item_level_enabled", False)),
        notes=data.get("notes"),
    )
    session.add(factor_set)
    session.flush()
    set_id = factor_set.id

    sectors = _codes(session, Sector)
    foods = _codes(session, FoodCategory)
    #: v1.54's second nullable dimension on ``factor_upstream``. Looked up the
    #: same hard-stopping way as every other code (see ``_lookup``): a
    #: ``food_item`` this database does not have is a drifted
    #: ``ITEM_LEVEL_CLIENT_FOODS`` in the build script, and creating the row
    #: here would put a factor-loader's own vocabulary into a global taxonomy
    #: table -- which is how forty-eight ``refed_*`` rows came to sit in
    #: ``food_category``.
    items = _codes(session, FoodItem)
    destinations = _codes(session, Destination)
    metrics = _codes(session, Metric)

    counts = {
        "constant": 0, "formula": 0, "upstream": 0, "downstream": 0,
        "equivalence": 0,
    }

    for row in data.get("constants", []):
        session.add(Constant(
            factor_set_id=set_id, code=row["code"],
            value=Decimal(str(row["value"])),
            unit=row.get("unit"), note=row.get("note"),
        ))
        counts["constant"] += 1

    for row in data.get("formulas", []):
        session.add(Formula(
            factor_set_id=set_id,
            metric_id=_lookup(metrics, row["metric"], "metric", "formulas"),
            expression=row["expression"], notes=row.get("notes"),
        ))
        counts["formula"] += 1

    for index, row in enumerate(data.get("upstream", [])):
        where = f"upstream[{index}]"
        destination = row.get("destination")
        session.add(FactorUpstream(
            factor_set_id=set_id,
            sector_id=_lookup(sectors, row["sector"], "sector", where),
            food_category_id=_lookup(
                foods, row["food_category"], "food category", where),
            #: ``None`` means the row prices every food in the category
            #: (section 2.2's candidate 4), which is what every row of this
            #: draft meant before the item level landed.
            food_item_id=(
                None if row.get("food_item") is None
                else _lookup(items, row["food_item"], "food item", where)
            ),
            destination_id=(
                None if destination is None
                else _lookup(destinations, destination, "destination", where)
            ),
            metric_id=_lookup(metrics, row["metric"], "metric", where),
            value_per_kg=Decimal(str(row["value_per_kg"])),
            source_note=row.get("source_note"),
            data_quality=row.get("data_quality"),
        ))
        counts["upstream"] += 1

    for index, row in enumerate(data.get("downstream", [])):
        where = f"downstream[{index}]"
        sector = row.get("sector")
        food_category = row.get("food_category")
        session.add(FactorDownstream(
            factor_set_id=set_id,
            destination_id=_lookup(
                destinations, row["destination"], "destination", where),
            sector_id=(
                None if sector is None
                else _lookup(sectors, sector, "sector", where)
            ),
            food_category_id=(
                None if food_category is None
                else _lookup(foods, food_category, "food category", where)
            ),
            metric_id=_lookup(metrics, row["metric"], "metric", where),
            value_per_kg=Decimal(str(row["value_per_kg"])),
            source_note=row.get("source_note"),
            data_quality=row.get("data_quality"),
        ))
        counts["downstream"] += 1

    for index, row in enumerate(data.get("equivalences", [])):
        where = f"equivalences[{index}]"
        session.add(Equivalence(
            factor_set_id=set_id, code=row["code"], name=row["name"],
            source_metric_id=_lookup(
                metrics, row["source_metric"], "metric", where),
            value_per_unit=Decimal(str(row["value_per_unit"])),
            label_template=row["label_template"],
            #: v1.80. The one sentence the results page prints beside the
            #: figures. Named individually for the reason the four below are:
            #: a key this loader does not read is a key the JSON carries and
            #: the database never sees -- and the failure here is quiet, an
            #: empty disclosure on every card rather than an error.
            description=row.get("description"),
            #: v1.71's ladder columns. Named individually rather than
            #: splatted, for the reason the section check below exists: a key
            #: this loader does not read is a key the JSON carries and the
            #: database never sees, and the `equivalences` section itself was
            #: silently dropped that way once already. Dropping these four
            #: would land a set whose rungs are all shown at once -- three
            #: sentences saying the same thing at three sizes -- with nothing
            #: saying so.
            label_template_one=row.get("label_template_one"),
            family=row.get("family"),
            min_value=(
                None if row.get("min_value") is None
                else Decimal(str(row["min_value"]))
            ),
            max_value=(
                None if row.get("max_value") is None
                else Decimal(str(row["max_value"]))
            ),
            source_note=row.get("source_note"),
            sort_order=int(row.get("sort_order", 0)),
        ))
        counts["equivalence"] += 1

    #: The defect this guards against: a whole section of the JSON silently
    #: dropped because nothing here reads its key. That happened once
    #: already -- ``equivalences`` was in every draft file this loader read
    #: and nothing below counted it, so the set landed with an empty
    #: "Tangible equivalents" block and nothing said so. Every key in `data`
    #: that isn't one of the known scalar fields must be a section this
    #: function knows how to load, and every row that section's JSON list
    #: carries must have produced exactly one inserted row -- not "at least
    #: one", not "some" -- or the load is refused rather than summarised as
    #: though it were complete.
    known_scalars = {"version_label", "is_mock", "notes", "item_level_enabled"}
    sections = {
        "constants": "constant",
        "formulas": "formula",
        "upstream": "upstream",
        "downstream": "downstream",
        "equivalences": "equivalence",
    }
    unrecognised = set(data) - known_scalars - set(sections)
    if unrecognised:
        raise LoadError(
            f"Unrecognised section(s) {sorted(unrecognised)!r} in the "
            "factor-set JSON. This loader has no code path for them, which "
            "is exactly how the `equivalences` section was silently dropped "
            "before -- add a loop for the new section rather than loading "
            "everything else and leaving it out."
        )
    for json_key, counts_key in sections.items():
        present = len(data.get(json_key, []))
        inserted = counts[counts_key]
        if present != inserted:
            raise LoadError(
                f"Section {json_key!r} carried {present} row(s) in the JSON "
                f"but only {inserted} were inserted into the database. "
                "Refusing to load a set that silently drops part of what it "
                "was given."
            )

    return set_id, counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--factors", type=Path,
                        default=HERE / "upstream_factors_draft.json")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    args = parser.parse_args()

    if not args.database_url:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 1
    if not args.factors.is_file():
        print(f"No such file: {args.factors}", file=sys.stderr)
        return 1

    factors = json.loads(args.factors.read_text(encoding="utf-8"))

    factory = create_session_factory(args.database_url)
    with factory() as session:
        try:
            set_id, counts = load_factor_set(session, factors)
        except LoadError as exc:
            session.rollback()
            print(f"Refused to load: {exc}", file=sys.stderr)
            return 1
        session.commit()

    item_rows = sum(1 for row in factors.get("upstream", []) if row.get("food_item"))
    print(
        f"Factor set {factors['version_label']!r} created as id {set_id}, "
        f"status DRAFT, is_mock=true, item_level_enabled="
        f"{str(bool(factors.get('item_level_enabled', False))).lower()} "
        f"({item_rows} of the upstream rows name a food item): "
        f"{counts['constant']} constants, {counts['formula']} formulas, "
        f"{counts['upstream']} upstream rows, "
        f"{counts['downstream']} downstream rows, "
        f"{counts['equivalence']} equivalences."
    )
    print()
    print("It is a DRAFT and nothing that is live has changed. Do not")
    print("publish it without the owner's decision -- see")
    print("docs/upstream-factors-draft.md. Reach it from the admin dry-run")
    print("page, /admin/try, choosing this set.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except LoadError as exc:  # pragma: no cover - belt and braces
        print(f"Refused to load: {exc}", file=sys.stderr)
        raise SystemExit(1)
