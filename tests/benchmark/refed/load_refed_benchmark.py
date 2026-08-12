"""Load the ReFED comparison taxonomy and factor set into a database.

Run it against a database that already has the New Zealand taxonomy and the
New Zealand factor set. It adds; it never edits or replaces.

    python tests/benchmark/refed/load_refed_benchmark.py

Reads ``DATABASE_URL`` from the environment, exactly as
``docker/seed_mock_factors.py`` does.

WHY THIS EXISTS RATHER THAN REUSING seed_mock_factors.py
--------------------------------------------------------
``docker/seed_mock_factors.py`` honours ``KAICALC_MOCK_FACTORS`` and so can be
pointed at a different JSON file -- but it refuses outright if *any* factor set
already exists (``docker/seed_mock_factors.py``, the ``if existing:`` guard),
because its job is a first-run seed. It also only ever looks taxonomy codes up;
it cannot create them. Both are correct for a deploy seed and both make it
unusable as a second-set loader.

WHAT IT GUARANTEES
------------------
One session, one commit. The taxonomy rows and the factor set land together or
neither lands: a half-applied load would leave food categories with no factors
behind them, which computes a plausible zero rather than failing.

It leaves the new set as a **draft**. Publishing it would archive whatever is
currently live, and this is United States data -- it must never be the live
factor set. Reach it through the admin dry-run page instead; see
``docs/refed-comparison.md``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from admin.factor_models import (  # noqa: E402
    Constant,
    FactorDownstream,
    FactorSet,
    FactorSetStatus,
    FactorUpstream,
    Formula,
)
from admin.taxonomy_models import (  # noqa: E402
    Destination,
    DestinationGroup,
    FoodCategory,
    Metric,
    Sector,
)
from admin.taxonomy_rules import (  # noqa: E402
    TaxonomyInvariantError,
    check_prevention_intact,
    check_single_standard_mix,
)
from db.session import create_session_factory  # noqa: E402

HERE = Path(__file__).resolve().parent


class LoadError(RuntimeError):
    """Anything that should stop the load with a message and no traceback."""


def _codes(session: Session, model) -> dict[str, int]:
    return {row.code: row.id for row in session.scalars(select(model)).all()}


def _lookup(table: dict[str, int], code: str, kind: str, where: str) -> int:
    if code not in table:
        known = ", ".join(sorted(table)[:12])
        raise LoadError(
            f"Unknown {kind} code {code!r} in {where}. "
            f"Known {kind} codes begin: {known}. "
            "Has `kaicalc-admin seed-taxonomy` been run?"
        )
    return table[code]


def _ensure(session: Session, model, code: str, **fields) -> bool:
    """Create one row if its code is absent. Returns True if it created it.

    The same shape as ``admin/seed.py``'s helper: matches on ``code``, never
    updates an existing row. Re-running the loader after a partial failure is
    therefore safe, and an operator's edit to a row is never overwritten.
    """
    if session.scalar(select(model).where(model.code == code)) is not None:
        return False
    session.add(model(code=code, **fields))
    return True


def load_taxonomy(session: Session, data: dict) -> dict[str, int]:
    created = {"sector": 0, "food_category": 0, "destination": 0}

    for row in data["sectors"]:
        created["sector"] += _ensure(
            session, Sector,
            row["code"], name=row["name"], description=row.get("description"),
            sort_order=row["sort_order"], active=row.get("active", True),
        )
    for row in data["food_categories"]:
        created["food_category"] += _ensure(
            session, FoodCategory,
            row["code"], name=row["name"],
            is_standard_mix=row["is_standard_mix"],
            sort_order=row["sort_order"], active=row.get("active", True),
        )

    session.flush()
    groups = _codes(session, DestinationGroup)
    for row in data["destinations"]:
        group_id = _lookup(
            groups, row["group_code"], "destination group",
            f"destination {row['code']!r}")
        created["destination"] += _ensure(
            session, Destination,
            row["code"], group_id=group_id, name=row["name"],
            description=row.get("description"),
            sort_order=row["sort_order"], active=row.get("active", True),
        )

    session.flush()
    # The same two invariants admin/seed.py checks after inserting. Neither
    # should be able to trip here -- nothing in the fixture is a standard mix
    # and nothing touches `prevention` -- which is exactly why they are worth
    # asserting rather than assuming.
    check_single_standard_mix(session)
    check_prevention_intact(session)
    return created


def load_factor_set(session: Session, data: dict) -> tuple[int, dict[str, int]]:
    label = data["version_label"]
    if session.scalar(select(FactorSet).where(FactorSet.version_label == label)):
        raise LoadError(
            f"A factor set labelled {label!r} is already in this database. "
            "Nothing has been changed. Delete it from the admin panel first if "
            "you mean to reload it."
        )

    factor_set = FactorSet(
        version_label=label,
        status=FactorSetStatus.draft,
        is_mock=bool(data.get("is_mock", True)),
        notes=data.get("notes"),
    )
    session.add(factor_set)
    session.flush()
    set_id = factor_set.id

    sectors = _codes(session, Sector)
    foods = _codes(session, FoodCategory)
    destinations = _codes(session, Destination)
    metrics = _codes(session, Metric)

    counts = {"constant": 0, "formula": 0, "upstream": 0, "downstream": 0}

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

    for row in data.get("upstream", []):
        destination = row["destination"]
        session.add(FactorUpstream(
            factor_set_id=set_id,
            sector_id=_lookup(sectors, row["sector"], "sector", "upstream"),
            food_category_id=_lookup(
                foods, row["food_category"], "food category", "upstream"),
            destination_id=(
                None if destination is None
                else _lookup(destinations, destination, "destination", "upstream")
            ),
            metric_id=_lookup(metrics, row["metric"], "metric", "upstream"),
            value_per_kg=Decimal(str(row["value_per_kg"])),
            source_note=row.get("source_note"),
            data_quality=row.get("data_quality"),
        ))
        counts["upstream"] += 1

    for row in data.get("downstream", []):
        food_category = row["food_category"]
        session.add(FactorDownstream(
            factor_set_id=set_id,
            destination_id=_lookup(
                destinations, row["destination"], "destination", "downstream"),
            food_category_id=(
                None if food_category is None
                else _lookup(foods, food_category, "food category", "downstream")
            ),
            metric_id=_lookup(metrics, row["metric"], "metric", "downstream"),
            value_per_kg=Decimal(str(row["value_per_kg"])),
            source_note=row.get("source_note"),
            data_quality=row.get("data_quality"),
        ))
        counts["downstream"] += 1

    return set_id, counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--taxonomy", type=Path,
                        default=HERE / "refed-benchmark-taxonomy.json")
    parser.add_argument("--factors", type=Path,
                        default=HERE / "refed-benchmark-factors.json")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    args = parser.parse_args()

    if not args.database_url:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 1
    for path in (args.taxonomy, args.factors):
        if not path.is_file():
            print(f"No such file: {path}", file=sys.stderr)
            return 1

    taxonomy = json.loads(args.taxonomy.read_text(encoding="utf-8"))
    factors = json.loads(args.factors.read_text(encoding="utf-8"))

    factory = create_session_factory(args.database_url)
    with factory() as session:
        try:
            created = load_taxonomy(session, taxonomy)
            set_id, counts = load_factor_set(session, factors)
        except (LoadError, TaxonomyInvariantError) as exc:
            session.rollback()
            print(f"Refused to load: {exc}", file=sys.stderr)
            return 1
        session.commit()

    print(
        "Taxonomy added: "
        f"{created['sector']} sector(s), "
        f"{created['food_category']} food categories, "
        f"{created['destination']} destinations "
        "(rows whose code already existed were left alone)."
    )
    print(
        f"Factor set {factors['version_label']!r} created as id {set_id}, "
        f"status DRAFT, is_mock=true: "
        f"{counts['constant']} constants, {counts['formula']} formulas, "
        f"{counts['upstream']} upstream rows, "
        f"{counts['downstream']} downstream rows."
    )
    print()
    print("It is a DRAFT and nothing that is live has changed. This is United")
    print("States data and must never be published as the live factor set.")
    print("Run it from the admin dry-run page: /admin/try, choosing this set.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except LoadError as exc:  # pragma: no cover - belt and braces
        print(f"Refused to load: {exc}", file=sys.stderr)
        raise SystemExit(1)
