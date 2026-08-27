"""Transactional repository: the only Part B module that queries SQLAlchemy."""

from __future__ import annotations

import copy
import enum
import threading
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
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
#:
#: ``token`` (``submission.token``, §2.3) is here for the same defence-in-depth
#: reason as ``ip_hmac``, and ahead of the view that would leak it. §8.2
#: specifies ``/admin/submissions`` as a record-level moderation screen that
#: sets ``excluded_from_public``; the moment it is built on
#: ``AuditedModelView``, ``write_audit`` snapshots the whole row and copies the
#: **live** session token into ``audit_log`` -- readable by every staff member,
#: never expired, and out of reach of ``expire_tokens``, which nulls the column
#: on ``submission`` and knows nothing about copies. §2.3's guarantee is that
#: the linkage is severed after an hour; a copy in an audit row makes that
#: false for every moderated submission, permanently. It is not a credential,
#: but like ``ip_hmac`` it is the one field that ties a stored row back to a
#: person's browser session, which is precisely what this system promises not
#: to keep.
REDACTED_FIELDS = {
    "password_hash",
    "mfa_secret_enc",
    #: The column `mfa_secret_enc` became in contract v1.13, when TOTP
    #: secrets moved off `staff` onto `staff_totp_device` so that an account
    #: can enrol a second phone before losing the first. The old name is kept
    #: above rather than replaced: `audit_log` rows written before that
    #: migration still carry it, and this set is also what a future
    #: `before_json` reader would consult.
    "secret_enc",
    "code_hash",
    "ip_hmac",
    "token",
    #: The unclaimed password (contract v1.15 §8.3, renamed and widened in
    #: v1.16). Added here in the same change that added the column, not after
    #: it, because `row_to_dict` snapshots *every* mapped column of a `staff`
    #: row and `audit_log` is append-only — a value that reaches this table
    #: cannot be taken back out, and the whole point of the column is that its
    #: contents stop existing the moment the password is claimed. An audit copy
    #: would outlive that by the life of the deployment.
    #:
    #: It is the ciphertext that is redacted, and that is not belt-and-braces:
    #: the key is derived from SECRET_KEY, which is available to anything that
    #: can read `audit_log` in the first place, so the ciphertext in this
    #: table is the plaintext.
    "unclaimed_password_enc",
    #: What that column was called between v1.15 and v1.16 (migration `0012`
    #: renamed it). Kept for the same reason `mfa_secret_enc` is kept above,
    #: and it is the cheaper half of the two: a redaction that is one string
    #: out of date fails open and says nothing while it does.
    "initial_password_enc",
}

#: `factor_set_id -> (is_mock as it was when the bundle was built, bundle)`.
#: The flag rides alongside the bundle rather than being read off it because
#: `load_factor_bundle`'s `bundle_factory` is injectable and what it returns
#: is not this module's to introspect. See `load_factor_bundle` for why this
#: one field is re-checked on every hit when nothing else in the bundle is.
_bundle_cache: dict[int, tuple[bool, Any]] = {}
_cache_lock = threading.RLock()


def _default_bundle_factory(data: dict[str, Any]) -> Any:
    # v1.4 §4.1 names `engine/bundle.py`. This used to fall back to
    # `engine.types`, which is §3's module for the frozen dataclasses; a
    # `FactorBundle` found there would have satisfied this call and none of
    # the golden suite's, which loads it the documented way.
    try:
        from engine.bundle import FactorBundle
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


def _covered_by(session: Session, factor_set_id: int) -> dict[str, set[int]]:
    """The taxonomy row ids one factor set can actually price. §5.1.

    "Covered" is defined from the factor tables and from nothing else:

    * a **destination** has at least one ``factor_downstream`` row -- including
      the ``food_category_id IS NULL`` row, which §2.2 defines as "every food
      category" and which is how a per-tonne charge like the waste levy is
      held -- **or** appears as a non-NULL ``factor_upstream.destination_id``;
    * a **sector** appears as ``factor_upstream.sector_id`` **or** as a
      non-NULL ``factor_downstream.sector_id``;
    * a **food category** appears as ``factor_upstream.food_category_id`` or as
      a non-NULL ``factor_downstream.food_category_id``.

    ``factor_upstream.destination_id`` is read as well as
    ``factor_downstream``'s because O-7 (v1.8) made it nullable and NULL means
    "every destination". The two published shapes need both halves: a New
    Zealand set carries one general row per ``(sector, food_category, metric)``
    plus a ``prevention`` override, so the column is where its only
    per-destination information lives; the ReFED fixture carries an explicit
    row per destination, so the column is where nearly all of its information
    lives. Reading only one of the two tables loses one of the two shapes, and
    both are in the deployed database today.

    ``factor_downstream.sector_id`` joins the sector half for the mirror-image
    reason, since v1.31. It is nullable and NULL means "every sector", so a
    NULL row is no evidence about any particular sector and is skipped exactly
    as a NULL ``food_category_id`` already was. It is not redundant with the
    upstream read: a factor set may legitimately price a stage of the supply
    chain downstream only -- a per-tonne disposal charge that differs by
    collection contract, with no upstream footprint of its own -- and reading
    only ``factor_upstream`` would drop that sector from the form while the
    rows that price it sat in the database. The New Zealand set is unaffected,
    because all of its downstream rows are NULL here and its sectors come from
    ``factor_upstream`` as before.
    """
    upstream = session.execute(
        select(
            FactorUpstream.sector_id,
            FactorUpstream.food_category_id,
            FactorUpstream.destination_id,
        )
        .where(FactorUpstream.factor_set_id == factor_set_id)
        .distinct()
    ).all()
    downstream = session.execute(
        select(
            FactorDownstream.destination_id,
            FactorDownstream.sector_id,
            FactorDownstream.food_category_id,
        )
        .where(FactorDownstream.factor_set_id == factor_set_id)
        .distinct()
    ).all()
    sectors = {sector_id for sector_id, _, _ in upstream}
    foods = {food_id for _, food_id, _ in upstream}
    destinations = {dest_id for _, _, dest_id in upstream if dest_id is not None}
    for dest_id, sector_id, food_id in downstream:
        destinations.add(dest_id)
        if sector_id is not None:
            sectors.add(sector_id)
        if food_id is not None:
            foods.add(food_id)
    return {"sectors": sectors, "food_categories": foods, "destinations": destinations}


def get_taxonomy(session: Session) -> TaxonomySnapshot:
    """The active taxonomy **narrowed to what the published set covers**. §5.1, §6.1.

    Until v1.21 this returned every active row. §2.1's three vocabulary tables
    carry no ``factor_set_id`` -- a factor set brings factors, not a vocabulary
    -- so publishing one could not narrow the form, and the calculator offered
    destinations the published set prices at nothing. **A user who types a
    quantity against one of those gets a silent zero and the form gives no
    sign.** That was invisible while exactly one set of taxonomy rows existed
    and became impossible to miss when §10.3's ReFED fixture added a second,
    disjoint vocabulary to the same tables: 26 destinations offered, 12 of them
    priced. It was never only ReFED's -- ``MOCK-v0`` prices 6 destinations of
    14 and 3 sectors of 6, so most of the New Zealand form is already a silent
    zero, and this is what makes the deliverable stop claiming otherwise. The
    rows come back the moment real factors are loaded, with no code change,
    which is Decision 2 doing its job.

    **Two rows are kept whatever the factor tables say, and neither is an
    exception to the rule so much as a row the rule cannot speak about.**

    A **prevention** destination's factors are zero *by construction* -- that is
    the whole of what makes it a 100% offset and what keeps the two scenarios
    mass-conserving (§6.2). An absence of factor rows is therefore not evidence
    that a set does not support it, which is the inference this function draws
    for every other row, so every row flagged ``is_prevention`` is held out of
    the inference. **By the flag, not by a code**: this read
    ``x.code == PREVENTION_CODE`` until the flag existed, which meant §10.3's
    ``refed_prevention`` -- a prevention destination by every property that
    matters -- was subject to an inference that cannot be true of it.

    In the deployed database ``prevention`` is covered anyway, because
    ``publish_factor_set`` refuses a set whose general upstream rows have no
    matching zero override -- but that is a coincidence of two other rules
    rather than a guarantee, and the improvement panel is unusable the day it
    stops holding. Both prevention rows may appear at once, which is what
    happens under a set that prices only one vocabulary. That crossing is
    accepted rather than hidden: §6.2 refuses every flagged destination in a
    *current* scenario outright, and a line to a foreign prevention destination
    in the *alternative* falls back to the generic upstream row and is charged
    for it -- which understates the benefit of wasting less rather than
    overstating it.

    The **standard mix** is kept for the structural half of the same reason:
    §2.1 requires exactly one active row to carry ``is_standard_mix`` and §6.2
    resolves a null ``food_category`` to it, so filtering it out would leave a
    consumer with no legal way to say "composition unknown" while the server
    went on resolving null to a code it was never offered.

    **``metric`` is not filtered.** Metrics are the output vocabulary; nothing
    a user types is a metric, so an uncovered one cannot become the silent zero
    this function exists to remove -- it would be a zero column, visible on its
    own terms. §10.3 already rules metric rows global.

    **``get_taxonomy_for_bundle`` is not filtered either, and must not be.** It
    is a different function feeding the engine's dictionary of legal codes and
    §6.3's factor export. Narrowing it would turn every code this endpoint no
    longer offers from a zero into an ``UNKNOWN_CODE`` 400 -- including for a
    browser tab holding a taxonomy fetched before the last publish. The bundle
    stays a superset deliberately.

    Reading a historical submission is unaffected: ``get_public_stats`` selects
    ``destination.code`` and ``.name`` from the tables it joins and never
    consults this snapshot, so a submission recorded under a set that is now
    archived still reads back under its own names.
    """
    published = _published(session)
    covered = _covered_by(session, published.id)
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
    #: Still counted over every **active** row, not over the narrowed list. It
    #: is §2.1's invariant about the table -- "exactly one active row must be
    #: standard_mix" -- and a published set that happens to price none of the
    #: food categories does not make the schema wrong. Counting the narrowed
    #: list would turn this into a 500 on the calculator's first request the
    #: day someone publishes a set the standard mix is not in, which is the
    #: state the deployed ReFED set is in right now.
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
    #: **Smallest container first, and that is why the order is `kg_per_unit`
    #: rather than `code`.** This is the one taxonomy table with no
    #: `sort_order`, and the list is a `<select>` a visitor scans for their own
    #: bin. Alphabetical on the code put the 1100 L front-loader above the 660 L
    #: one and the 140 L kerbside bin above the 80 L one, which is a list nobody
    #: can scan. Size is the only meaningful order and the column already
    #: carries it, so no schema change buys it. `code` breaks the tie so the
    #: order is total and a fixture can assert it; two rows may legitimately
    #: share a mass once one of them is category-specific.
    presets = session.execute(
        select(UnitPreset, FoodCategory.code)
        .outerjoin(FoodCategory, UnitPreset.food_category_id == FoodCategory.id)
        .where(UnitPreset.active.is_(True))
        .order_by(UnitPreset.kg_per_unit, UnitPreset.code)
    ).all()

    # ---- the narrowing, applied once the active rows are in hand ----------
    sectors = [x for x in sectors if x.id in covered["sectors"]]
    foods = [
        x for x in foods
        if x.id in covered["food_categories"] or x.is_standard_mix
    ]
    destinations = [
        (x, group_code) for x, group_code in destinations
        if x.id in covered["destinations"] or x.is_prevention
    ]
    #: A group appears iff a destination that survived belongs to it. Derived
    #: from the surviving rows rather than computed a second time, so
    #: `destinations[].group` can never name a group this response omits --
    #: that dangling reference is the one way this filter could break a
    #: consumer that was reading both lists correctly.
    visible_groups = {group_code for _, group_code in destinations}
    groups = [x for x in groups if x.code in visible_groups]
    #: A preset naming a food category the caller can no longer choose is a
    #: unit conversion for a row that is not on the form. `food_code` is NULL
    #: for a preset that applies to every category, and those always stay.
    visible_foods = {x.code for x in foods}
    presets = [
        (x, food_code) for x, food_code in presets
        if food_code is None or food_code in visible_foods
    ]
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
            DestinationSpec(
                x.code, x.name, group_code, x.description, x.sort_order,
                x.is_prevention,
            )
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
    """One projection, two consumers: §10.2's `bundle.json` and §6.3's export.

    `source_note` and `data_quality` are selected here rather than only in
    `get_factor_export` because the export is built from this dictionary and
    there is no second projection to add them to. §6.3 **requires** both on
    every upstream and downstream row and `source_note` on every equivalence,
    present-and-null rather than omitted, so a consumer can tell "no
    provenance recorded" from "this endpoint does not report provenance";
    §10.2 makes the same keys **optional and ignored** in a bundle, so
    carrying them costs the engine nothing. Publishing the values and dropping
    their provenance is the one combination v1.1 added the columns to prevent:
    it removes the defence and keeps the exposure.
    """
    factor_set = session.get(FactorSet, factor_set_id)
    if factor_set is None:
        raise FactorSetNotFoundError(f"Unknown factor set id: {factor_set_id}")
    taxonomy = get_taxonomy_for_bundle(session)
    #: The outer join on Destination is the O-7 half of §2.2 (v1.8):
    #: `destination_id` is nullable and NULL means "every destination", so an
    #: inner join here would publish only the `prevention` overrides and drop
    #: every general row — the exact inverse of the bug O-7 closed, and just as
    #: silent. `factor_downstream` outer-joins FoodCategory for the same reason.
    upstream = session.execute(
        select(FactorUpstream, Sector.code, FoodCategory.code, Destination.code,
               Metric.code)
        .join(Sector, FactorUpstream.sector_id == Sector.id)
        .join(FoodCategory, FactorUpstream.food_category_id == FoodCategory.id)
        .outerjoin(Destination, FactorUpstream.destination_id == Destination.id)
        .join(Metric, FactorUpstream.metric_id == Metric.id)
        .where(FactorUpstream.factor_set_id == factor_set_id)
    ).all()
    #: `factor_downstream` outer-joins **both** Sector and FoodCategory, for
    #: the reason the note above gives about Destination: each is nullable and
    #: NULL means "every value of that dimension", so an inner join would
    #: publish only the rows that name one and silently drop every general row.
    downstream = session.execute(
        select(FactorDownstream, Destination.code, Sector.code, FoodCategory.code,
               Metric.code)
        .join(Destination, FactorDownstream.destination_id == Destination.id)
        .outerjoin(Sector, FactorDownstream.sector_id == Sector.id)
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
                #: §2.2/§10.2 (v1.8): `null` is a legal value meaning "every
                #: destination", not a missing field, and must survive both
                #: directions of the round trip — §4.1's lookup order is exact
                #: destination, then this row, then zero.
                "destination": destination,
                "metric": metric,
                "value_per_kg": str(x.value_per_kg),
                "source_note": x.source_note,
                "data_quality": x.data_quality,
            }
            for x, sector, food, destination, metric in upstream
        ],
        "downstream": [
            {
                "destination": destination,
                #: §2.2/§10.2 (v1.31): `null` is a legal value meaning "every
                #: sector", not a missing field, and must survive both
                #: directions of the round trip — §4.1's lookup order runs
                #: (sector, food_category), then (sector, NULL), then
                #: (NULL, food_category), then (NULL, NULL), then zero.
                "sector": sector,
                "food_category": food,
                "metric": metric,
                "value_per_kg": str(x.value_per_kg),
                "source_note": x.source_note,
                "data_quality": x.data_quality,
            }
            for x, destination, sector, food, metric in downstream
        ],
        "equivalences": [
            {
                "code": x.code,
                "name": x.name,
                "source_metric": metric,
                "value_per_unit": str(x.value_per_unit),
                "label_template": x.label_template,
                "source_note": x.source_note,
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
            {
                "code": x.code,
                "name": x.name,
                "group": group,
                "sort_order": x.sort_order,
                "is_prevention": x.is_prevention,
            }
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


def _live_is_mock(session: Session, factor_set_id: int) -> bool | None:
    """This set's placeholder flag as the database has it right now.

    A `SELECT` rather than `session.get`, which would answer out of the
    identity map when the caller's session has already loaded the row —
    the one case this check exists to catch.
    """
    return session.scalar(
        select(FactorSet.is_mock).where(FactorSet.id == factor_set_id)
    )


def load_factor_bundle(
    session: Session,
    factor_set_id: int | None = None,
    *,
    bundle_factory: BundleFactory | None = None,
) -> Any:
    """§5.2's per-set cache. **Only a published set is cached** (v1.4).

    §5.2 requires a slot per `factor_set_id` so that staff dry-running a
    draft can never evict the published bundle or, worse, serve draft factors
    to a public request. Caching the draft as well satisfies that literally
    and breaks the thing the draft slot exists to support: the panel's CRUD
    screens write `factor_upstream`, `constant` and `formula` rows directly
    and have no reason to call `invalidate_factor_bundle` — only the
    lifecycle transitions do — so the first dry run of a draft pins that
    draft's numbers for the lifetime of the process. A staff member then
    edits a factor, re-runs the dry run, sees the old figure, and has no way
    to tell that from a formula that ignores their column. Not caching it is
    the cheaper of the two fixes and the only one that does not put a
    repository hook into all eleven admin views; a draft is dry-run by one
    person at a time, so there is no load argument on the other side.

    **`is_mock` is re-read on every cache hit, and nothing else is.** The
    flag is the only field of a published bundle that legitimately moves
    while that set stays published (§2.2: staff publish the real factors,
    verify them live for a day or two, then clear the flag through
    FactorSetAdmin's confirmed action). Every other field is immutable in
    place, which is what makes caching the rest of the bundle safe at all.

    Nothing else can carry that change across: `invalidate_factor_bundle` is
    a module-level dict in one process, and docker/compose.yaml runs the
    panel and the API as **two services**, so the panel clearing its own slot
    leaves the API's warm one untouched — for the lifetime of that process,
    since this cache has no expiry. Without this check, clearing the flag
    would take the placeholder banner off `/factors` (read live by
    `get_factor_export`) and leave it on every `/calculate` result, and
    *setting* the flag — the direction §2.2 requires to be instant and
    frictionless, so that anyone can mark a set as placeholder data the
    moment they doubt it — would not reach the public at all.

    The cost is one primary-key `SELECT` of one boolean per calculation,
    against a bundle of some 900 factor rows. What the cache is for is not
    loading those.
    """
    factor_set_id = factor_set_id or get_published_factor_set_id(session)
    factory = bundle_factory or _default_bundle_factory
    with _cache_lock:
        cached = _bundle_cache.get(factor_set_id)
    if cached is not None:
        cached_is_mock, bundle = cached
        if _live_is_mock(session, factor_set_id) == cached_is_mock:
            return bundle
        # The flag moved under a warm slot. Drop it and rebuild below rather
        # than patch the cached bundle: `bundle_factory` is injectable and
        # what it returns is not this module's to reach into.
        invalidate_factor_bundle(factor_set_id)
    bundle = factory(build_bundle_data(session, factor_set_id))
    # `build_bundle_data` has already loaded this row into the identity map,
    # so the status check costs no second round trip.
    factor_set = session.get(FactorSet, factor_set_id)
    if factor_set is None or factor_set.status is not FactorSetStatus.published:
        return bundle
    with _cache_lock:
        return _bundle_cache.setdefault(
            factor_set_id, (factor_set.is_mock, bundle)
        )[1]


def _row_dict(row: Any) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def find_missing_prevention_upstream(
    session: Session, factor_set_id: int
) -> list[tuple[str, str, str]]:
    """Which `(sector, food_category, metric)` tuples of this set would still
    charge a prevented line an upstream factor. Contract §2.2, O-7.

    A tuple qualifies when it has a general upstream row (`destination_id IS
    NULL`) and **no prevention destination has a row at zero for it** — whether
    because every such row is absent or because each carries a non-zero value.
    For those tuples a prevented line keeps some or all of the entry's upstream
    burden and the calculator reverts towards its pre-v1.8 behaviour,
    understating the benefit of wasting less.

    **"No prevention destination", not "every prevention destination."** The
    role is a flag now (§2.1) and more than one row may carry it, because two
    vocabularies share these global tables (§10.3). Requiring a zero override
    per flagged row would refuse `MOCK-v0` the moment §10.3's fixture is
    loaded, since `MOCK-v0` has no `refed_prevention` rows and never will —
    including on a rollback to it. What this guard exists to protect is that an
    alternative scenario **can** express wasting less without an upstream
    charge, and one working offset per tuple is what that takes.

    **Existence is not the rule; the value is.** An earlier revision of this
    function checked only that a `prevention` row was present, while both
    callers' messages told the staff member to add one "at 0". A row at 1.9
    satisfied an existence check completely and reopened O-7 for that tuple
    silently — the same failure this guard was written for, with one extra
    step, and against a `source_note` and an `architecture.md` §4.1 that now
    state the whole offset as fact. Zero here is a modelling decision, not a
    default: prevented food was never produced, so there is no upstream burden
    to attribute, and any other value is a claim nothing in the system
    supports.

    **This is worse than the original O-7, not better, which is why it is
    checked rather than filed.** O-7 was wrong everywhere and therefore
    discoverable; this is wrong for one sector while every other sector on the
    same results page is right, and it arrives with no error, no warning and
    nothing in the log. A staff member adds a sector to a draft, publishes,
    and sees exactly what they expected to see.

    Returns **codes, not ids** (§1.1) and sorted, so a caller can put them
    straight into a message a human has to act on. An empty list is the
    healthy state.

    Returns empty when the taxonomy has no prevention destination at all.
    That is an unseeded database rather than an incomplete factor set, and it
    is `admin/taxonomy_rules.check_prevention_destination`'s to refuse — a
    second rule stated in terms of the same row would be a second thing to keep
    in step, and this one would report every tuple in the set.
    """
    prevention_ids = list(
        session.scalars(
            select(Destination.id).where(Destination.is_prevention.is_(True))
        )
    )
    if not prevention_ids:
        return []

    covered = (
        select(FactorUpstream.sector_id, FactorUpstream.food_category_id,
               FactorUpstream.metric_id)
        .where(
            FactorUpstream.factor_set_id == factor_set_id,
            FactorUpstream.destination_id.in_(prevention_ids),
            #: The whole of the difference between "a row exists" and "the
            #: offset is whole". Compared against `Decimal` rather than `0` so
            #: that no float is bound into the statement (§1.2); DECIMAL(20,10)
            #: compares exactly on both MySQL and SQLite.
            FactorUpstream.value_per_kg == Decimal("0"),
        )
        .subquery()
    )
    rows = session.execute(
        select(Sector.code, FoodCategory.code, Metric.code)
        #: Explicit left side: every selected column belongs to a *joined*
        #: table, so without this SQLAlchemy cannot infer which FROM the
        #: joins hang off and raises InvalidRequestError.
        .select_from(FactorUpstream)
        .join(Sector, FactorUpstream.sector_id == Sector.id)
        .join(FoodCategory, FactorUpstream.food_category_id == FoodCategory.id)
        .join(Metric, FactorUpstream.metric_id == Metric.id)
        .outerjoin(
            covered,
            (FactorUpstream.sector_id == covered.c.sector_id)
            & (FactorUpstream.food_category_id == covered.c.food_category_id)
            & (FactorUpstream.metric_id == covered.c.metric_id),
        )
        .where(
            FactorUpstream.factor_set_id == factor_set_id,
            FactorUpstream.destination_id.is_(None),
            covered.c.sector_id.is_(None),
        )
    ).all()
    return sorted((sector, food, metric) for sector, food, metric in rows)


def prevention_destination_codes(session: Session) -> frozenset[str]:
    """The codes of every destination flagged `is_prevention` (§2.1).

    `db/` is the only layer that touches the database, and `api/` may not
    import `admin/`, so this is how §6.2's validator learns which destinations
    are the offset. It reads the **taxonomy**, not a factor set: `destination`
    carries no `factor_set_id`, so the answer does not depend on what is
    published and is the same for a dry run as for a public request.

    Not restricted to `active` rows on purpose. A deactivated prevention row is
    still a destination for which "this waste did not happen" is true, and a
    caller who names one in a *current* scenario must be refused rather than
    told the code is merely unknown — `check_prevention_destination` is what
    refuses the deactivation, and this must not depend on having won that race.
    """
    return frozenset(
        session.scalars(
            select(Destination.code).where(Destination.is_prevention.is_(True))
        )
    )


def _refuse_incomplete_prevention(session: Session, factor_set_id: int) -> None:
    missing = find_missing_prevention_upstream(session, factor_set_id)
    if not missing:
        return
    listed = ", ".join(f"{sector}/{food}/{metric}" for sector, food, metric in missing)
    #: Never empty where this message is built: with no flagged destination
    #: `find_missing_prevention_upstream` returns [] and there is nothing to
    #: report. A fallback literal here would be the magic string coming back
    #: in the one place a staff member reads.
    codes = ", ".join(sorted(prevention_destination_codes(session)))
    raise FactorSetStateError(
        f"{len(missing)} factor combinations have an upstream factor but no "
        f"prevention upstream row at 0 — either it is missing or it carries a "
        f"non-zero value — so a prevented line would still be charged upstream "
        f"impact: {listed}. Add or correct an upstream row at 0 against a "
        f"prevention destination ({codes}) for each before publishing."
    )


def refuse_nonzero_prevention_factors(session: Session, factor_set_id: int) -> None:
    """A prevention destination priced at anything but zero. §2.1, §2.2.

    Until this existed the zero was **assumed on the downstream side and
    unenforced on the upstream side of any set with no generic rows.**
    `_refuse_incomplete_prevention` above catches a non-zero upstream value
    only where a generic row exists to compare it against, so a set built the
    ReFED way — an explicit row per destination and no generic rows at all —
    could carry a prevention row at 1.9 and publish. Nothing anywhere looked at
    `factor_downstream`.

    A destination flagged `is_prevention` whose factors are not zero is not a
    100% offset, so the alternative scenario stops being the same mass at no
    cost and `net_benefit` silently reports a smaller improvement than the
    scenario describes. That is the same class of failure as O-7 and it is
    invisible in exactly the same way.

    **An absent row stays legal.** §4.1's lookup returns `Decimal('0')` for a
    missing factor, so absence already *is* zero; this refuses only a row that
    exists and disagrees. That is why it cannot regress a set that simply
    prices a prevention destination nowhere — which is the case
    `get_taxonomy`'s hold-out is built around.
    """
    problems: list[str] = []
    for table, label in ((FactorUpstream, "upstream"), (FactorDownstream, "downstream")):
        rows = session.execute(
            select(Destination.code, table.value_per_kg)
            .select_from(table)
            .join(Destination, table.destination_id == Destination.id)
            .where(
                table.factor_set_id == factor_set_id,
                Destination.is_prevention.is_(True),
                table.value_per_kg != Decimal("0"),
            )
        ).all()
        problems.extend(f"{label} {code} = {value}" for code, value in rows)
    if not problems:
        return
    raise FactorSetStateError(
        f"{len(problems)} factor rows price a prevention destination at "
        f"something other than 0: {', '.join(sorted(problems))}. A prevention "
        "destination is a 100% offset — that zero is what lets an alternative "
        "scenario describe the same mass at no impact — so set each of these "
        "to 0, or delete the row, before publishing."
    )


def publish_factor_set(session: Session, factor_set_id: int, actor: str) -> None:
    """Contract §5.2, plus the O-7 completeness check of §2.2.

    The check belongs here rather than on the upstream-factor form: this is
    the single transactional choke point, it is where the one-published-row
    invariant is already settled, and a form-level guard cannot see a row that
    has not been written yet — a staff member adds the general row, saves, and
    is refused for a `prevention` row they were about to add next.

    **Publish only, deliberately, not `rollback_to`.** Rollback is the "put the
    calculator back to a state that worked" operation, and a set archived
    before v1.8 will legitimately fail this check. Refusing an emergency
    rollback over a completeness rule would be a worse failure than the one the
    rule prevents. `refuse_nonzero_prevention_factors` is scoped the same way
    and for the same reason.
    """
    _refuse_incomplete_prevention(session, factor_set_id)
    refuse_nonzero_prevention_factors(session, factor_set_id)
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
        #: `destination_id` is in this tuple because leaving it out is how O-7
        #: comes back. Clone-edit-publish is the recommended staff workflow
        #: (§5.2), so a clone that dropped the column would collapse every
        #: `prevention` zero onto its general row on the first real factor set
        #: — and the clone would still have the right row *count*, which is all
        #: the older half of test_clone_is_deep asserted.
        (FactorUpstream, ("sector_id", "food_category_id", "destination_id",
                          "metric_id", "value_per_kg")),
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
    session: Session,
    token: str | None,
    req: Any,
    factor_set_id: int,
    *,
    time_frame: str | None = None,
) -> tuple[int, str]:
    """Contract §5.3. One call, one submission, N entries.

    `req` is a §3 `CalculationRequest` and carries `req.entries`, each one an
    `EntryInput` with its own `sector_code`, `food_category_code`, its own
    `current` / `alternative` tuples of `ScenarioLine`, and (v1.48) its own
    `total_input_kg`, `total_value_nzd` and `wasted_value_nzd`. There is no
    `req.current`: `ScenarioInput` was deleted in v1.2 precisely because an
    integration that keeps reading `req.current.sector_code` persists one row
    for a five-entry calculation and nothing raises.

    `time_frame` is kept off `req` deliberately: `req` is §3's
    `CalculationRequest`, which the engine also consumes, and the period is
    not an engine input -- the engine must never be handed a value it is
    required not to use. The three per-entry numbers, by contrast, are read
    straight off `req.entries[i]`: `EntryInput` carries them (v1.48), so there
    is no second, wire-shaped object to pair up by position any more.

    Does **not** set `is_public_contributed`: it defaults false at the schema
    and only the opt-in route Task 6 owns may change it.
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
            time_frame=time_frame,
        )
        session.add(submission)
        session.flush()
    else:
        submission.updated_at = now
        submission.factor_set_id = factor_set_id
        submission.gwp_horizon = req.gwp_horizon
        # Written on the update path too: a second calculation reusing the
        # same token would otherwise keep the first one's period forever.
        submission.time_frame = time_frame
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
                #: v1.48. `None` is not zero (see `EntryInput`): a caller that
                #: builds `req` by hand and leaves these unset gets `None` for
                #: all three, which is the same "not stated" the schema
                #: already means.
                total_input_kg=entry.total_input_kg,
                total_value_nzd=entry.total_value_nzd,
                wasted_value_nzd=entry.wasted_value_nzd,
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

    **`other` is itself subject to the threshold, and clears it by absorbing
    more.** A single sub-threshold bucket used to be republished verbatim
    under a new name -- `count: 1` with its exact `total_kg` -- which hid the
    label and nothing else. Worse in combination than alone: `by_sector.other`
    and `by_food_category.other` are drawn from the same entries, so an
    `other` of one entry publishes that user's exact tonnage three times over
    and the three rows join on sight. So: while `other` exists and is below
    the threshold, the **smallest visible** bucket is merged into it as well,
    which raises `other`'s count fastest per bucket surrendered and costs the
    page the least informative row first.

    **If merging everything still cannot clear the threshold, the breakdown
    is published empty** -- no buckets at all, `total_calculations` on its
    own. That is the case of a data set so small that every row in it is
    identifying, and there is no arrangement of it that is both useful and
    safe.

    The privacy ruling here overrides the shares property below where the two
    conflict, but in practice they do not: `other` remains an ordinary bucket
    carrying every suppressed entry, so the denominator is untouched and the
    shares still sum to 1. An empty breakdown has no shares to sum.

    **The shares sum to exactly 1, and that takes one extra step.** §6.4
    states it as a property a consumer may rely on, but quantizing each share
    independently to four places does not deliver it: three buckets of one
    entry each are three thirds, and 3 x 0.3333 is 0.9999. The residue is
    assigned to the largest bucket, where it is the smallest relative
    distortion, after `other` has been formed -- so every suppressed entry is
    inside the sum, which is the whole reason suppression merges rather than
    drops. Without this a statistics page renders a legend adding to 99.99%,
    and the fixture D builds against (`tests/fixtures/stats.json`, which is
    levelled) and the API disagree about whether the chart is complete.
    """
    total = sum(count for _, _, count, _ in rows)
    ordered = sorted(rows, key=lambda row: (-row[2], row[0]))

    def share(count: int) -> Decimal:
        if not total:
            return Decimal("0")
        return (Decimal(count) / Decimal(total)).quantize(Decimal("0.0001"))

    kept: list[tuple[str, str, int, Decimal]] = []
    other_count = 0
    other_kg = Decimal("0")
    for code, label, count, total_kg in ordered:
        if count < threshold:
            other_count += count
            other_kg += _decimal(total_kg)
            continue
        kept.append((code, label, count, _decimal(total_kg)))

    # `kept` is ordered by descending count, so the smallest visible bucket is
    # the last one. Pop from the end until `other` clears the threshold.
    while other_count and other_count < threshold and kept:
        _, _, count, total_kg = kept.pop()
        other_count += count
        other_kg += total_kg

    if other_count and other_count < threshold:
        # Everything merged and it still does not clear. Publishing `other`
        # alone would be publishing the whole breakdown under one label.
        return ()

    visible = [
        StatsBucket(code, label, count, share(count), total_kg)
        for code, label, count, total_kg in kept
    ]
    if other_count:
        visible.append(
            StatsBucket(*OTHER_BUCKET, other_count, share(other_count), other_kg)
        )
    if total and visible:
        residue = Decimal("1") - sum(bucket.share for bucket in visible)
        if residue:
            largest = max(range(len(visible)), key=lambda i: visible[i].share)
            visible[largest] = replace(
                visible[largest], share=visible[largest].share + residue
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
        # §1.3: always UTC, always the `Z` designator. This used to append
        # "Z" to a naive value and leave an aware one alone, so the same
        # column serialised two ways — `2026-08-09T03:00:00Z` when it came
        # back naive off MySQL and `2026-08-09T03:00:00+00:00` when it was
        # still the aware object `utcnow()` produced. Both are the same
        # instant and neither is malformed, which is why nothing failed;
        # `audit_log` is simply not a table anyone should have to normalise
        # timestamps out of after the fact. A non-UTC aware value was worse
        # than inconsistent — it kept its own offset and claimed §1.3
        # compliance. `api/serialization.wire()` has always done this
        # correctly, and cannot be reused: `db/` may not import `api/`.
        aware = (
            value.replace(tzinfo=timezone.utc)
            if value.tzinfo is None
            else value.astimezone(timezone.utc)
        )
        return aware.isoformat().replace("+00:00", "Z")
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
