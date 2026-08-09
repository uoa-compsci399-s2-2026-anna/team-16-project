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

from sqlalchemy import delete, distinct, func, select, update
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
    sector_id = _resolve_id(session, Sector, req.current.sector_code)
    food_category_id = (
        _resolve_id(session, FoodCategory, req.current.food_category_code)
        if req.current.food_category_code
        else None
    )
    if submission is None:
        token = str(uuid.uuid4())
        submission = Submission(
            token=token,
            token_expires_at=now + timedelta(hours=1),
            created_at=now,
            updated_at=now,
            factor_set_id=factor_set_id,
            sector_id=sector_id,
            food_category_id=food_category_id,
            gwp_horizon=req.gwp_horizon,
        )
        session.add(submission)
        session.flush()
    else:
        submission.updated_at = now
        submission.factor_set_id = factor_set_id
        submission.sector_id = sector_id
        submission.food_category_id = food_category_id
        submission.gwp_horizon = req.gwp_horizon
        session.execute(delete(SubmissionLine).where(SubmissionLine.submission_id == submission.id))
    for scenario_name, scenario in (
        (Scenario.current, req.current),
        (Scenario.alternative, req.alternative),
    ):
        if scenario is None:
            continue
        for line in scenario.lines:
            session.add(
                SubmissionLine(
                    submission_id=submission.id,
                    scenario=scenario_name,
                    destination_id=_resolve_id(session, Destination, line.destination_code),
                    qty_kg=line.qty_kg,
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


def _bucketise(
    rows: list[tuple[str, str, int, Decimal]], total: int, threshold: int
) -> tuple[StatsBucket, ...]:
    visible: list[StatsBucket] = []
    other_count = 0
    other_kg = Decimal("0")
    for code, label, count, total_kg in rows:
        if count < threshold:
            other_count += count
            other_kg += total_kg or Decimal("0")
            continue
        share = (Decimal(count) / Decimal(total)).quantize(Decimal("0.0001")) if total else Decimal("0")
        visible.append(StatsBucket(code, label, count, share, total_kg or Decimal("0")))
    if other_count:
        share = (Decimal(other_count) / Decimal(total)).quantize(Decimal("0.0001")) if total else Decimal("0")
        visible.append(StatsBucket("other", "Other (sample too small)", other_count, share, other_kg))
    return tuple(visible)


def get_public_stats(session: Session, threshold: int = 5) -> PublicStats:
    eligible = select(Submission.id).where(Submission.excluded_from_public.is_(False)).subquery()
    total = session.scalar(select(func.count()).select_from(eligible)) or 0
    destination_rows = session.execute(
        select(
            Destination.code,
            Destination.name,
            func.count(distinct(SubmissionLine.submission_id)),
            func.sum(SubmissionLine.qty_kg),
        )
        .join(Destination, SubmissionLine.destination_id == Destination.id)
        .where(
            SubmissionLine.submission_id.in_(select(eligible.c.id)),
            SubmissionLine.scenario == Scenario.current,
        )
        .group_by(Destination.code, Destination.name)
    ).all()
    current_totals = (
        select(
            SubmissionLine.submission_id,
            func.sum(SubmissionLine.qty_kg).label("total_kg"),
        )
        .where(SubmissionLine.scenario == Scenario.current)
        .group_by(SubmissionLine.submission_id)
        .subquery()
    )
    sector_rows = session.execute(
        select(Sector.code, Sector.name, func.count(Submission.id), func.sum(current_totals.c.total_kg))
        .join(Submission, Submission.sector_id == Sector.id)
        .join(current_totals, current_totals.c.submission_id == Submission.id)
        .where(Submission.id.in_(select(eligible.c.id)))
        .group_by(Sector.code, Sector.name)
    ).all()
    standard_mixes = session.scalars(
        select(FoodCategory).where(
            FoodCategory.is_standard_mix.is_(True),
            FoodCategory.active.is_(True),
        )
    ).all()
    if len(standard_mixes) != 1:
        raise TaxonomyInvariantError(
            "Exactly one active food category must be standard_mix"
        )
    standard_mix = standard_mixes[0]
    food_code = func.coalesce(FoodCategory.code, standard_mix.code if standard_mix else "standard_mix")
    food_name = func.coalesce(FoodCategory.name, standard_mix.name if standard_mix else "Standard mix")
    food_rows = session.execute(
        select(food_code, food_name, func.count(Submission.id), func.sum(current_totals.c.total_kg))
        .outerjoin(FoodCategory, Submission.food_category_id == FoodCategory.id)
        .join(current_totals, current_totals.c.submission_id == Submission.id)
        .where(Submission.id.in_(select(eligible.c.id)))
        .group_by(food_code, food_name)
    ).all()
    return PublicStats(
        generated_at=utcnow(),
        total_calculations=total,
        suppression_threshold=threshold,
        by_destination=_bucketise(list(destination_rows), total, threshold),
        by_sector=_bucketise(list(sector_rows), total, threshold),
        by_food_category=_bucketise(list(food_rows), total, threshold),
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
