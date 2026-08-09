"""Transactional repository: the only Part B module that queries SQLAlchemy."""

from __future__ import annotations

import copy
import enum
import threading
import uuid
from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import distinct, func, select, update
from sqlalchemy.orm import Session

from db.errors import (
    FactorSetNotFoundError,
    FactorSetStateError,
    NoPublishedFactorSetError,
    TaxonomyInvariantError,
)
from db.models import (
    AuditLog,
    Constant,
    Destination,
    DestinationGroup,
    Equivalence,
    FactorDownstream,
    FactorSet,
    FactorSetStatus,
    FactorUpstream,
    FoodCategory,
    Formula,
    Metric,
    Scenario,
    Sector,
    Submission,
    SubmissionEntry,
    SubmissionLine,
    UnitPreset,
    utcnow,
)
from db.types import (
    DestinationGroupSpec,
    DestinationSpec,
    FoodCategorySpec,
    MetricSpec,
    PublicStats,
    SectorSpec,
    StatsBucket,
    TaxonomySnapshot,
    UnitPresetSpec,
)

BundleFactory = Callable[[dict[str, Any]], Any]
#: Contract §5.5. audit_log is readable by every staff member through
#: /admin/audit, so an unfiltered staff row would expose password hashes and
#: TOTP secrets to anyone holding an account.
#:
#: ``ip_hmac`` (``db/blocklist_models.py``'s ``IpBlock``) is here as defence in
#: depth, not because any current caller needs it — see ``admin/audit.py`` and
#: ``tests/admin/test_audit.py::test_an_ip_fingerprint_is_redacted_from_the_
#: audit_trail`` for the full argument. §2.3's reasoning for never displaying
#: the fingerprint applies to /admin/audit exactly as it does to the blocklist
#: screen.
REDACTED_FIELDS = {"password_hash", "mfa_secret_enc", "code_hash", "ip_hmac"}

_bundle_cache: dict[int, Any] = {}
_cache_lock = threading.RLock()


def _default_bundle_factory(data: dict[str, Any]) -> Any:
    try:
        from engine.bundle import FactorBundle
    except ImportError:
        try:
            from engine.types import FactorBundle
        except ImportError as exc:
            raise RuntimeError(
                "A's engine FactorBundle is not installed; inject bundle_factory "
                "until engine/ is merged"
            ) from exc
    return FactorBundle.from_json(data)


def invalidate_factor_bundle(factor_set_id: int | None = None) -> None:
    """Clear one cache slot, or every slot after a lifecycle transition."""
    with _cache_lock:
        if factor_set_id is None:
            _bundle_cache.clear()
        else:
            _bundle_cache.pop(factor_set_id, None)


def get_published_factor_set_id(session: Session) -> int:
    ids = session.scalars(
        select(FactorSet.id).where(FactorSet.status == FactorSetStatus.published)
    ).all()
    if not ids:
        raise NoPublishedFactorSetError("No factor set is published")
    if len(ids) != 1:
        raise FactorSetStateError("More than one factor set is published")
    return ids[0]


def get_factor_set_by_version(
    session: Session, version_label: str, *, public_only: bool = False
) -> FactorSet:
    stmt = select(FactorSet).where(FactorSet.version_label == version_label)
    if public_only:
        stmt = stmt.where(
            FactorSet.status.in_([FactorSetStatus.published, FactorSetStatus.archived])
        )
    row = session.scalar(stmt)
    if row is None:
        raise FactorSetNotFoundError(f"Unknown factor set: {version_label}")
    return row


def _published(session: Session) -> FactorSet:
    return session.get(FactorSet, get_published_factor_set_id(session))


def get_taxonomy(session: Session) -> TaxonomySnapshot:
    published = _published(session)
    groups = session.scalars(
        select(DestinationGroup)
        .where(DestinationGroup.active.is_(True))
        .order_by(DestinationGroup.sort_order, DestinationGroup.code)
    ).all()
    sectors = session.scalars(
        select(Sector)
        .where(Sector.active.is_(True))
        .order_by(Sector.sort_order, Sector.code)
    ).all()
    foods = session.scalars(
        select(FoodCategory)
        .where(FoodCategory.active.is_(True))
        .order_by(FoodCategory.sort_order, FoodCategory.code)
    ).all()
    if sum(1 for row in foods if row.is_standard_mix) != 1:
        raise TaxonomyInvariantError("Exactly one active food category must be standard_mix")
    metrics = session.scalars(
        select(Metric)
        .where(Metric.active.is_(True))
        .order_by(Metric.sort_order, Metric.code)
    ).all()
    destinations = session.execute(
        select(Destination, DestinationGroup.code)
        .join(DestinationGroup, Destination.group_id == DestinationGroup.id)
        .where(Destination.active.is_(True), DestinationGroup.active.is_(True))
        .order_by(Destination.sort_order, Destination.code)
    ).all()
    presets = session.execute(
        select(UnitPreset, FoodCategory.code)
        .outerjoin(FoodCategory, UnitPreset.food_category_id == FoodCategory.id)
        .where(UnitPreset.active.is_(True))
        .order_by(UnitPreset.code)
    ).all()
    return TaxonomySnapshot(
        sectors=tuple(SectorSpec(x.code, x.name, x.description, x.sort_order) for x in sectors),
        food_categories=tuple(
            FoodCategorySpec(x.code, x.name, x.is_standard_mix, x.sort_order)
            for x in foods
        ),
        destination_groups=tuple(
            DestinationGroupSpec(x.code, x.name, x.is_waste, x.sort_order)
            for x in groups
        ),
        destinations=tuple(
            DestinationSpec(x.code, x.name, group_code, x.description, x.sort_order)
            for x, group_code in destinations
        ),
        metrics=tuple(
            MetricSpec(
                x.code,
                x.name,
                x.unit,
                x.display_unit,
                x.display_precision,
                x.sort_order,
            )
            for x in metrics
        ),
        unit_presets=tuple(
            UnitPresetSpec(x.code, x.label, food_code, x.kg_per_unit)
            for x, food_code in presets
        ),
        factor_set_version=published.version_label,
        factor_set_is_mock=published.is_mock,
    )


def build_bundle_data(session: Session, factor_set_id: int) -> dict[str, Any]:
    factor_set = session.get(FactorSet, factor_set_id)
    if factor_set is None:
        raise FactorSetNotFoundError(f"Unknown factor set id: {factor_set_id}")
    taxonomy = get_taxonomy_for_bundle(session)
    upstream = session.execute(
        select(FactorUpstream, Sector.code, FoodCategory.code, Metric.code)
        .join(Sector, FactorUpstream.sector_id == Sector.id)
        .join(FoodCategory, FactorUpstream.food_category_id == FoodCategory.id)
        .join(Metric, FactorUpstream.metric_id == Metric.id)
        .where(FactorUpstream.factor_set_id == factor_set_id)
    ).all()
    downstream = session.execute(
        select(FactorDownstream, Destination.code, FoodCategory.code, Metric.code)
        .join(Destination, FactorDownstream.destination_id == Destination.id)
        .outerjoin(FoodCategory, FactorDownstream.food_category_id == FoodCategory.id)
        .join(Metric, FactorDownstream.metric_id == Metric.id)
        .where(FactorDownstream.factor_set_id == factor_set_id)
    ).all()
    constants = session.scalars(
        select(Constant).where(Constant.factor_set_id == factor_set_id)
    ).all()
    formulas = session.execute(
        select(Formula, Metric.code)
        .join(Metric, Formula.metric_id == Metric.id)
        .where(Formula.factor_set_id == factor_set_id)
    ).all()
    equivalences = session.execute(
        select(Equivalence, Metric.code)
        .join(Metric, Equivalence.source_metric_id == Metric.id)
        .where(
            Equivalence.factor_set_id == factor_set_id,
            Equivalence.active.is_(True),
        )
        .order_by(Equivalence.sort_order, Equivalence.code)
    ).all()
    return {
        "version_label": factor_set.version_label,
        "is_mock": factor_set.is_mock,
        **taxonomy,
        "constants": [
            {"code": x.code, "value": str(x.value), "unit": x.unit or "", "note": x.note or ""}
            for x in constants
        ],
        "formulas": [
            {"metric": code, "expression": x.expression, "notes": x.notes or ""}
            for x, code in formulas
        ],
        "upstream": [
            {
                "sector": sector,
                "food_category": food,
                "metric": metric,
                "value_per_kg": str(x.value_per_kg),
            }
            for x, sector, food, metric in upstream
        ],
        "downstream": [
            {
                "destination": destination,
                "food_category": food,
                "metric": metric,
                "value_per_kg": str(x.value_per_kg),
            }
            for x, destination, food, metric in downstream
        ],
        "equivalences": [
            {
                "code": x.code,
                "name": x.name,
                "source_metric": metric,
                "value_per_unit": str(x.value_per_unit),
                "label_template": x.label_template,
                "sort_order": x.sort_order,
            }
            for x, metric in equivalences
        ],
    }


def get_taxonomy_for_bundle(session: Session) -> dict[str, list[dict[str, Any]]]:
    groups = session.scalars(
        select(DestinationGroup)
        .where(DestinationGroup.active.is_(True))
        .order_by(DestinationGroup.sort_order, DestinationGroup.code)
    ).all()
    sectors = session.scalars(
        select(Sector).where(Sector.active.is_(True)).order_by(Sector.sort_order, Sector.code)
    ).all()
    foods = session.scalars(
        select(FoodCategory)
        .where(FoodCategory.active.is_(True))
        .order_by(FoodCategory.sort_order, FoodCategory.code)
    ).all()
    metrics = session.scalars(
        select(Metric).where(Metric.active.is_(True)).order_by(Metric.sort_order, Metric.code)
    ).all()
    destinations = session.execute(
        select(Destination, DestinationGroup.code)
        .join(DestinationGroup, Destination.group_id == DestinationGroup.id)
        .where(Destination.active.is_(True), DestinationGroup.active.is_(True))
        .order_by(Destination.sort_order, Destination.code)
    ).all()
    return {
        "sectors": [
            {"code": x.code, "name": x.name, "sort_order": x.sort_order}
            for x in sectors
        ],
        "food_categories": [
            {
                "code": x.code,
                "name": x.name,
                "is_standard_mix": x.is_standard_mix,
                "sort_order": x.sort_order,
            }
            for x in foods
        ],
        "destination_groups": [
            {"code": x.code, "name": x.name, "is_waste": x.is_waste, "sort_order": x.sort_order}
            for x in groups
        ],
        "destinations": [
            {"code": x.code, "name": x.name, "group": group, "sort_order": x.sort_order}
            for x, group in destinations
        ],
        "metrics": [
            {
                "code": x.code,
                "name": x.name,
                "unit": x.unit,
                "display_unit": x.display_unit,
                "display_precision": x.display_precision,
                "sort_order": x.sort_order,
            }
            for x in metrics
        ],
    }


def load_factor_bundle(
    session: Session,
    factor_set_id: int | None = None,
    *,
    bundle_factory: BundleFactory | None = None,
) -> Any:
    factor_set_id = factor_set_id or get_published_factor_set_id(session)
    factory = bundle_factory or _default_bundle_factory
    with _cache_lock:
        if factor_set_id in _bundle_cache:
            return _bundle_cache[factor_set_id]
    bundle = factory(build_bundle_data(session, factor_set_id))
    with _cache_lock:
        return _bundle_cache.setdefault(factor_set_id, bundle)


def _row_dict(row: Any) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def publish_factor_set(session: Session, factor_set_id: int, actor: str) -> None:
    _transition_factor_set(
        session,
        factor_set_id,
        actor,
        "publish",
        allowed_statuses={FactorSetStatus.draft},
    )


def rollback_to(session: Session, factor_set_id: int, actor: str) -> None:
    _transition_factor_set(
        session,
        factor_set_id,
        actor,
        "rollback",
        allowed_statuses={FactorSetStatus.archived},
    )


def _transition_factor_set(
    session: Session,
    factor_set_id: int,
    actor: str,
    action: str,
    *,
    allowed_statuses: set[FactorSetStatus],
) -> None:
    rows = session.scalars(select(FactorSet).order_by(FactorSet.id).with_for_update()).all()
    target = next((x for x in rows if x.id == factor_set_id), None)
    if target is None:
        raise FactorSetNotFoundError(f"Unknown factor set id: {factor_set_id}")
    if sum(row.status == FactorSetStatus.published for row in rows) > 1:
        raise FactorSetStateError("More than one factor set is published")
    if target.status not in allowed_statuses:
        expected = ", ".join(sorted(status.value for status in allowed_statuses))
        raise FactorSetStateError(f"Target factor set must be {expected}")
    now = utcnow()
    for current in rows:
        if current.status == FactorSetStatus.published:
            before = _row_dict(current)
            current.status = FactorSetStatus.archived
            write_audit(session, actor, "update", "factor_set", current.id, before, _row_dict(current))
    before = _row_dict(target)
    target.status = FactorSetStatus.published
    target.published_at = now
    target.published_by = actor
    write_audit(session, actor, action, "factor_set", target.id, before, _row_dict(target))
    session.flush()
    invalidate_factor_bundle()


def clone_factor_set(
    session: Session, source_id: int, new_label: str, actor: str
) -> int:
    source = session.get(FactorSet, source_id)
    if source is None:
        raise FactorSetNotFoundError(f"Unknown factor set id: {source_id}")
    clone = FactorSet(
        version_label=new_label,
        status=FactorSetStatus.draft,
        is_mock=source.is_mock,
        effective_from=source.effective_from,
        notes=source.notes,
    )
    session.add(clone)
    session.flush()
    copy_specs = (
        (FactorUpstream, ("sector_id", "food_category_id", "metric_id", "value_per_kg")),
        (FactorDownstream, ("destination_id", "food_category_id", "metric_id", "value_per_kg")),
        (Constant, ("code", "value", "unit", "note")),
        (Formula, ("metric_id", "expression", "notes")),
        (Equivalence, ("code", "name", "source_metric_id", "value_per_unit", "label_template", "sort_order", "active")),
    )
    for model, fields in copy_specs:
        for row in session.scalars(select(model).where(model.factor_set_id == source_id)):
            session.add(model(factor_set_id=clone.id, **{name: getattr(row, name) for name in fields}))
    write_audit(session, actor, "create", "factor_set", clone.id, None, _row_dict(clone))
    session.flush()
    return clone.id


def _resolve_id(session: Session, model: Any, code: str) -> int:
    value = session.scalar(select(model.id).where(model.code == code))
    if value is None:
        raise FactorSetNotFoundError(f"Unknown {model.__tablename__} code: {code}")
    return value


def upsert_submission(
    session: Session, token: str | None, req: Any, factor_set_id: int
) -> tuple[int, str]:
    """Contract §5.3. One call, one submission, N entries.

    `req` is a §3 `CalculationRequest` and carries `req.entries`, each one an
    `EntryInput` with its own `sector_code`, `food_category_code` and its own
    `current` / `alternative` tuples of `ScenarioLine`. There is no
    `req.current`: `ScenarioInput` was deleted in v1.2 precisely because an
    integration that keeps reading `req.current.sector_code` persists one row
    for a five-entry calculation and nothing raises.
    """
    now = utcnow()
    submission = None
    if token:
        submission = session.scalar(
            select(Submission).where(Submission.token == token).with_for_update()
        )
        if submission is not None and (
            submission.token_expires_at is None or submission.token_expires_at <= now
        ):
            submission.token = None
            submission = None
    if submission is None:
        token = str(uuid.uuid4())
        submission = Submission(
            token=token,
            token_expires_at=now + timedelta(hours=1),
            created_at=now,
            updated_at=now,
            factor_set_id=factor_set_id,
            gwp_horizon=req.gwp_horizon,
        )
        session.add(submission)
        session.flush()
    else:
        submission.updated_at = now
        submission.factor_set_id = factor_set_id
        submission.gwp_horizon = req.gwp_horizon
        # §5.3: the entry set is rebuilt, not patched -- the entries carry no
        # client-supplied identity to reconcile a removed one against an added
        # one. Cleared through the ORM relationship rather than by a bulk
        # DELETE so that the lines go with the entries on SQLite too, where
        # ON DELETE CASCADE is inert unless PRAGMA foreign_keys is on.
        submission.entries.clear()
        # Flushed before the inserts below, or a rebuild that re-uses the same
        # (sector, food_category) pair -- a user changing one number and
        # recalculating, the commonest case there is -- collides with
        # uq_submission_entry. The unit of work emits deletes after inserts.
        session.flush()
    for sort_order, entry in enumerate(req.entries):
        # Every code is resolved before the row is constructed. _resolve_id
        # issues a SELECT, which triggers autoflush; a half-built entry that
        # is attached to `submission` but not yet in the session is skipped by
        # that flush with only a warning.
        sector_id = _resolve_id(session, Sector, entry.sector_code)
        food_category_id = (
            _resolve_id(session, FoodCategory, entry.food_category_code)
            if entry.food_category_code
            else None
        )
        lines = [
            SubmissionLine(
                scenario=scenario_name,
                destination_id=_resolve_id(session, Destination, line.destination_code),
                qty_kg=line.qty_kg,
            )
            for scenario_name, scenario_lines in (
                (Scenario.current, entry.current),
                (Scenario.alternative, entry.alternative),
            )
            if scenario_lines is not None
            for line in scenario_lines
        ]
        session.add(
            SubmissionEntry(
                submission=submission,
                sector_id=sector_id,
                #: NULL means the user did not break their waste down by type.
                #: Stored as NULL: resolving it to standard_mix here would
                #: report a composition the user never claimed (§5.4).
                food_category_id=food_category_id,
                #: §2.3. Request order, so §6.2's entries[] can be paired with
                #: the rows on the user's screen; id order cannot be relied on
                #: because the rebuild above reassigns ids.
                sort_order=sort_order,
                lines=lines,
            )
        )
    session.flush()
    return submission.id, token


def expire_tokens(session: Session, now: datetime) -> int:
    result = session.execute(
        update(Submission)
        .where(Submission.token.is_not(None), Submission.token_expires_at < now)
        .values(token=None)
    )
    return result.rowcount or 0


#: §5.4/§6.4. The label of the bucket a NULL `submission_entry.food_category_id`
#: falls into: the user did not break their waste down by type. Deliberately
#: not `standard_mix`, which is what a user selects on purpose.
UNSPECIFIED_FOOD_CATEGORY = ("unspecified", "Not broken down by type")
OTHER_BUCKET = ("other", "Other (sample too small)")


def _decimal(value: Any) -> Decimal:
    #: SQLite hands a summed DECIMAL back through float; MySQL does not.
    #: §1.2 puts these on the wire as strings, so a float must not survive
    #: this far.
    if isinstance(value, Decimal):
        return value
    return Decimal("0") if value is None else Decimal(str(value))


def _bucketise(
    rows: list[tuple[str, str, int, Any]], threshold: int
) -> tuple[StatsBucket, ...]:
    """Suppress, then share.

    The denominator is this breakdown's **own** total entry count, not
    `total_calculations` (§6.4: "share is computed within its own breakdown,
    against that breakdown's own total, and does sum to 1"). A submission
    contributes one observation per entry to `by_sector`, and one per entry
    *per destination used* to `by_destination`, so no breakdown is a
    breakdown of the submission count.

    A suppressed bucket keeps its count inside that denominator by being
    merged into `other` rather than dropped: dropping it would not remove a
    number from the page, it would inflate every share left on it.
    """
    total = sum(count for _, _, count, _ in rows)
    ordered = sorted(rows, key=lambda row: (-row[2], row[0]))

    def share(count: int) -> Decimal:
        if not total:
            return Decimal("0")
        return (Decimal(count) / Decimal(total)).quantize(Decimal("0.0001"))

    visible: list[StatsBucket] = []
    other_count = 0
    other_kg = Decimal("0")
    for code, label, count, total_kg in ordered:
        if count < threshold:
            other_count += count
            other_kg += _decimal(total_kg)
            continue
        visible.append(StatsBucket(code, label, count, share(count), _decimal(total_kg)))
    if other_count:
        visible.append(
            StatsBucket(*OTHER_BUCKET, other_count, share(other_count), other_kg)
        )
    return tuple(visible)


def get_public_stats(session: Session, threshold: int = 5) -> PublicStats:
    """Contract §5.4.

    Three properties of this function are each easy to lose and none of them
    fails loudly:

    1. **Every breakdown reads the current scenario only.** Both scenarios
       share `submission_line`, and the alternative is a user's what-if, not
       an observation. Without the predicate, `prevention` — the destination
       for waste that by construction did *not* happen — becomes a bucket in
       the public chart and every `total_kg` roughly doubles.
    2. **Every breakdown joins up to `submission`,** one table further than
       its own grouping needs, because `excluded_from_public` lives there
       (§2.3). Stopping at `submission_entry` applies staff moderation to
       nothing.
    3. **The unit of aggregation is the entry, not the submission.** One
       submission with three entries is three sector observations; counting
       it once, as whichever stage it happened to enter first, is exactly
       wrong for the multi-stage businesses this calculator is most useful to.
       `total_calculations` alone still counts submissions, so it and the
       bucket counts deliberately do not sum (§6.4).
    """
    total_calculations = session.scalar(
        select(func.count())
        .select_from(Submission)
        .where(Submission.excluded_from_public.is_(False))
    ) or 0

    # One row per entry: that entry's current-scenario mass. Grouped on the
    # entry, not the submission -- an entry's mass belongs in its own sector
    # and food-category bucket, not spread across its siblings'.
    entry_current_kg = (
        select(
            SubmissionLine.submission_entry_id.label("submission_entry_id"),
            func.sum(SubmissionLine.qty_kg).label("total_kg"),
        )
        .where(SubmissionLine.scenario == Scenario.current)
        .group_by(SubmissionLine.submission_entry_id)
        .subquery()
    )
    # The inner join to it is what "restricted to entries having
    # current-scenario lines" means: an entry with no current line has no
    # mass to report and is not an observation of anything.
    entries = (
        select(
            SubmissionEntry.id,
            SubmissionEntry.sector_id,
            SubmissionEntry.food_category_id,
            entry_current_kg.c.total_kg,
        )
        .join(Submission, SubmissionEntry.submission_id == Submission.id)
        .join(
            entry_current_kg,
            entry_current_kg.c.submission_entry_id == SubmissionEntry.id,
        )
        .where(Submission.excluded_from_public.is_(False))
        .subquery()
    )

    sector_rows = session.execute(
        select(
            Sector.code,
            Sector.name,
            func.count(entries.c.id),
            func.sum(entries.c.total_kg),
        )
        .join(entries, entries.c.sector_id == Sector.id)
        .group_by(Sector.code, Sector.name)
    ).all()

    # A NULL food category is a bucket, not a gap, and it is not resolved to
    # standard_mix: the engine does that to pick a factor, the statistics must
    # report what the user actually told us (§5.4).
    food_code = func.coalesce(FoodCategory.code, UNSPECIFIED_FOOD_CATEGORY[0])
    food_name = func.coalesce(FoodCategory.name, UNSPECIFIED_FOOD_CATEGORY[1])
    food_rows = session.execute(
        select(
            food_code,
            food_name,
            func.count(entries.c.id),
            func.sum(entries.c.total_kg),
        )
        .select_from(entries)
        .outerjoin(FoodCategory, entries.c.food_category_id == FoodCategory.id)
        .group_by(food_code, food_name)
    ).all()

    destination_rows = session.execute(
        select(
            Destination.code,
            Destination.name,
            func.count(distinct(SubmissionLine.submission_entry_id)),
            func.sum(SubmissionLine.qty_kg),
        )
        .select_from(SubmissionLine)
        .join(SubmissionEntry, SubmissionLine.submission_entry_id == SubmissionEntry.id)
        .join(Submission, SubmissionEntry.submission_id == Submission.id)
        .join(Destination, SubmissionLine.destination_id == Destination.id)
        .where(
            Submission.excluded_from_public.is_(False),
            SubmissionLine.scenario == Scenario.current,
        )
        .group_by(Destination.code, Destination.name)
    ).all()

    return PublicStats(
        generated_at=utcnow(),
        total_calculations=total_calculations,
        suppression_threshold=threshold,
        by_destination=_bucketise(list(destination_rows), threshold),
        by_sector=_bucketise(list(sector_rows), threshold),
        by_food_category=_bucketise(list(food_rows), threshold),
    )


def get_factor_export(
    session: Session, version_label: str | None = None, *, public_only: bool = True
) -> dict[str, Any]:
    factor_set = (
        get_factor_set_by_version(session, version_label, public_only=public_only)
        if version_label
        else _published(session)
    )
    data = build_bundle_data(session, factor_set.id)
    return {
        "factor_set": {
            "version_label": factor_set.version_label,
            "is_mock": factor_set.is_mock,
            "published_at": factor_set.published_at,
            "notes": factor_set.notes or "",
        },
        "constants": data["constants"],
        "formulas": data["formulas"],
        "upstream": data["upstream"],
        "downstream": data["downstream"],
        "equivalences": data["equivalences"],
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[redacted]" if key in REDACTED_FIELDS else _json_safe(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat() + ("Z" if value.tzinfo is None else "")
    # `date` is checked after `datetime` because datetime subclasses it. This
    # branch came from admin/audit.py's `_encode`, which handled a plain date
    # where this function would otherwise have fallen through to deepcopy and
    # left a non-JSON-serialisable object in the payload.
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, bytes):
        return "[binary]"
    return copy.deepcopy(value)


def write_audit(
    session: Session,
    actor: str,
    action: str,
    table_name: str,
    row_id: int | None,
    before: dict | None,
    after: dict | None,
) -> None:
    session.add(
        AuditLog(
            at=utcnow(),
            actor=actor,
            action=action,
            table_name=table_name,
            row_id=row_id,
            before_json=_json_safe(before) if before is not None else None,
            after_json=_json_safe(after) if after is not None else None,
        )
    )
