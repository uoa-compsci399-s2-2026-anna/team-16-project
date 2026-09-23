"""Transactional repository: the only Part B module that queries SQLAlchemy."""

from __future__ import annotations

import logging
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
    FoodItem,
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
    FoodItemSpec,
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

#: `factor_set_id -> (is_mock, item vocabulary, bundle)` -- the first two as
#: they were when the bundle was built. Both ride alongside the bundle rather
#: than being read off it because `load_factor_bundle`'s `bundle_factory` is
#: injectable and what it returns is not this module's to introspect. See
#: `load_factor_bundle` for why these two are re-checked on every hit when
#: nothing else in the bundle is.
_bundle_cache: dict[int, tuple[bool, tuple, Any]] = {}
_cache_lock = threading.RLock()


#: `db/repository.py` had no logger until v1.57. It has one now for exactly one
#: purpose - see `load_factor_bundle` - and not as a general facility: this
#: module's other failures are refusals a caller handles, and a log line is what
#: you write when there is no caller left to tell.
_LOGGER = logging.getLogger(__name__)


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

    ``food_items`` (v1.54) is the **one dimension whose rule is not that**, and
    the difference is deliberate rather than an oversight. An item is covered
    when its own rows exist **or when its parent category is covered** — call
    it parent-covered — because §2.2's upstream chain falls a food with no row
    of its own through to the category average, which is a defined, meaningful
    number: the nine category factors *are* the averages of these same foods.
    The other four dimensions have no such fallback. A destination nothing
    prices is priced at ``Decimal('0')``, and offering it is the silent zero
    this whole function exists to stop offering; an item nothing prices is
    offered its category's average and the figure is as good as the one the
    visitor would have got by naming the category instead. Requiring an item to
    carry rows of its own would therefore hide almost the whole vocabulary from
    the moment step 2.5 is released — full coverage is 19 items x 6 sectors x
    every metric the set prices, which was 570 rows at the five metrics of the
    day and is 684 since v1.70 added a sixth, and no data that will exist comes
    close — while the thing it would be protecting against cannot happen.

    The union is not redundant in one case, which is why it is a union. An item
    row carries a NOT NULL ``food_category_id``, so an item priced under its own
    parent is parent-covered anyway; what the first clause catches on its own is
    a row filed under a category the item does not belong to, which the schema
    permits (``submission_entry``'s CHECK says only that an item may not arrive
    without a category) and which the engine reports rather than prices.

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
            FactorUpstream.food_item_id,
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
    sectors = {sector_id for sector_id, _, _, _ in upstream}
    foods = {food_id for _, food_id, _, _ in upstream}
    items = {item_id for _, _, item_id, _ in upstream if item_id is not None}
    destinations = {dest_id for _, _, _, dest_id in upstream if dest_id is not None}
    for dest_id, sector_id, food_id in downstream:
        destinations.add(dest_id)
        if sector_id is not None:
            sectors.add(sector_id)
        if food_id is not None:
            foods.add(food_id)
    #: The parent-covered half, and the only query in this function that reads
    #: a taxonomy table rather than a factor one — because the rule itself is
    #: about the taxonomy's shape (which category a food belongs to), not about
    #: what any factor set priced. See this function's docstring.
    if foods:
        items |= set(
            session.scalars(
                select(FoodItem.id).where(FoodItem.food_category_id.in_(foods))
            )
        )
    return {
        "sectors": sectors,
        "food_categories": foods,
        "food_items": items,
        "destinations": destinations,
    }


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
    #: v1.58. The item vocabulary, inner-joined to its parent because
    #: `food_item.food_category_id` is NOT NULL and the parent code is what the
    #: front end groups step 2.5 by. Same order as every other section,
    #: `sort_order` then `code`.
    items = session.execute(
        select(FoodItem, FoodCategory.code)
        .join(FoodCategory, FoodItem.food_category_id == FoodCategory.id)
        .where(FoodItem.active.is_(True), FoodCategory.active.is_(True))
        .order_by(FoodItem.sort_order, FoodItem.code)
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
    #: v1.58, and the one line in this block whose rule is **not** "the
    #: published set has a factor row for it". `_covered_by` has computed the
    #: parent-covered union since v1.57 and this is its first reader: a food is
    #: offered when it has rows of its own **or when its parent category is
    #: covered**, because an unpriced food falls through to the category
    #: average -- a defined number, and §2.1's categories *are* the averages
    #: of these same foods. Every other dimension falls through to
    #: `Decimal("0")`, which is the silent zero this whole block exists to
    #: stop offering.
    #:
    #: Filtered a second time against the food categories that actually
    #: survived, rather than by `covered["food_items"]` alone: an item under a
    #: category the caller cannot choose is a food step 2.5 would offer under
    #: a heading step 2 does not have.
    surviving_foods = {x.code for x in foods}
    items = [
        (x, food_code) for x, food_code in items
        if x.id in covered["food_items"] and food_code in surviving_foods
    ]
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
        food_items=tuple(
            FoodItemSpec(x.code, x.name, food_code, x.sort_order)
            for x, food_code in items
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
        factor_set_item_level_enabled=published.item_level_enabled,
    )


def get_taxonomy_for_naming(session: Session) -> TaxonomySnapshot:
    """Every active taxonomy row, **not** narrowed to what the published set
    covers — `get_taxonomy`'s superset, in `get_taxonomy`'s own shape.

    Exists for exactly one caller: `POST /export/pdf` (`api/router.py`),
    which uses a taxonomy snapshot only to turn a `code` a computed result
    already carries into the name a reader can recognise
    (`api/pdf_render.py::_Taxonomy`). `get_taxonomy`'s narrowing is a rule
    about a *form* — do not offer, as a choice, a destination the published
    set prices at nothing — and a rendered document is not a form: a
    submission is free to name a destination the currently published set
    does not price (its downstream factor was a silent zero, not a refusal),
    and `entry_rule_problems` validates the request against the wider
    vocabulary `get_taxonomy_for_bundle` speaks for, not against this
    function's narrowed sibling. Handing that same narrowed snapshot to the
    renderer meant a perfectly valid destination code fell through
    `_Taxonomy`'s "tolerant of a code the snapshot does not carry" fallback
    and printed as itself — `anaerobic_digestion` rather than "Anaerobic
    digestion" — the moment the published set stopped pricing it, which nothing
    about the request or the calculation did anything wrong to cause.

    Same active-row queries as `get_taxonomy_for_bundle` (§10.3's engine-facing
    superset), returned as a `TaxonomySnapshot` instead of that function's
    plain dicts because `_Taxonomy` reads dataclass attributes. `unit_presets`
    is left empty and `factor_set_version` / `factor_set_is_mock` are read off
    the published row only to satisfy the dataclass's own required fields —
    `_Taxonomy` reads neither; the renderer takes both from the engine
    `CalculationResult` it already has.
    """
    published = _published(session)
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
    #: Unnarrowed, like every other list this function returns: its one caller
    #: turns a `code` a computed result already carries into a name, so a food
    #: the published set has stopped pricing must still be nameable.
    items = session.execute(
        select(FoodItem, FoodCategory.code)
        .join(FoodCategory, FoodItem.food_category_id == FoodCategory.id)
        .where(FoodItem.active.is_(True), FoodCategory.active.is_(True))
        .order_by(FoodItem.sort_order, FoodItem.code)
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
    return TaxonomySnapshot(
        sectors=tuple(SectorSpec(x.code, x.name, x.description, x.sort_order) for x in sectors),
        food_categories=tuple(
            FoodCategorySpec(x.code, x.name, x.is_standard_mix, x.sort_order)
            for x in foods
        ),
        food_items=tuple(
            FoodItemSpec(x.code, x.name, food_code, x.sort_order)
            for x, food_code in items
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
        unit_presets=(),
        factor_set_version=published.version_label,
        factor_set_is_mock=published.is_mock,
        factor_set_item_level_enabled=published.item_level_enabled,
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
    #: `FoodItem` is outer-joined for the same reason as `Destination` one line
    #: below it, and the consequence of getting it wrong is the same shape: an
    #: inner join would publish only the item-level rows and silently drop
    #: every category row, which is every row in every database today.
    upstream = session.execute(
        select(FactorUpstream, Sector.code, FoodCategory.code, FoodItem.code,
               Destination.code, Metric.code)
        .join(Sector, FactorUpstream.sector_id == Sector.id)
        .join(FoodCategory, FactorUpstream.food_category_id == FoodCategory.id)
        .outerjoin(FoodItem, FactorUpstream.food_item_id == FoodItem.id)
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
                #: §2.2/§10.2 (v1.54): `null` is a legal value meaning "every
                #: food item in this category" — the category average, which is
                #: the normal row and what this table held until the column
                #: existed. **The key has to be here even though it is null on
                #: every row in every database today**, because the failure it
                #: prevents is silent in a way none of the others are: an item
                #: factor a staff member has authored, written and audited
                #: reaches an engine that never sees it, the line is priced at
                #: the category average, and the screen says the food was
                #: named. `food_items` right below is optional in
                #: `FactorBundle.from_json` so that pre-v1.54 bundles load,
                #: which is precisely what would let the omission pass for a
                #: healthy bundle.
                "food_item": item,
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
            for x, sector, food, item, destination, metric in upstream
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
    #: v1.54's vocabulary section. Inner-joined to `FoodCategory` because
    #: `food_item.food_category_id` is NOT NULL and the parent is not
    #: decoration: it is both the row a food with no factor of its own falls
    #: back to and what the engine checks a named food against, so an item
    #: whose category could not be resolved is an item that can be neither
    #: priced nor refused. Empty in every database today, which is what makes
    #: this landing inert.
    items = session.execute(
        select(FoodItem, FoodCategory.code)
        .join(FoodCategory, FoodItem.food_category_id == FoodCategory.id)
        .where(FoodItem.active.is_(True))
        .order_by(FoodItem.sort_order, FoodItem.code)
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
        #: §10.2's optional thirteenth key (v1.54). `food_category` is the
        #: parent code, not an id: `code` is the cross-layer identifier and no
        #: primary key reaches the engine.
        "food_items": [
            {
                "code": x.code,
                "name": x.name,
                "food_category": food_code,
                "sort_order": x.sort_order,
            }
            for x, food_code in items
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


def _live_item_vocabulary(session: Session) -> tuple:
    """The `food_item` rows as they reach a bundle, right now. v1.58.

    **The second thing that can move under a warm cache slot, and until v1.58
    there was only one.** `food_item` is *global taxonomy* (§2.1) -- a factor
    set brings factors, not a vocabulary -- so unlike every factor row in a
    published set it is not immutable in place: a staff member can add a food
    at any moment, to a set nobody republishes. `invalidate_factor_bundle` is a
    module-level dict in one process and `docker/compose.yaml` runs the panel
    and the API as two services, so the panel clearing its own slot reaches
    nothing; that is the same reasoning `is_mock` is re-read for, and it
    applies here for the same reason.

    **What goes wrong without it is visible rather than subtle, which is why it
    is worth a query.** §6.1 offers the vocabulary from the database and
    §6.2 resolves a named food against the *bundle*: add a food and the form
    offers it immediately while every calculation naming it answers
    `VALIDATION_ERROR: unknown food_item` until the API process is restarted.
    Measured on the running stack before this function existed.

    Every column `build_bundle_data` publishes is in the fingerprint, not just
    the primary keys: a rename changes what the results page prints, and
    re-parenting a food changes which pairs `resolve_food_item` accepts. The
    row count is bounded by design -- the client's list is about twenty foods
    -- so this is one small `SELECT` beside the one-boolean `SELECT`
    `_live_is_mock` already costs.
    """
    return tuple(
        session.execute(
            select(
                FoodItem.code,
                FoodItem.name,
                FoodItem.food_category_id,
                FoodItem.sort_order,
            )
            .where(FoodItem.active.is_(True))
            .order_by(FoodItem.sort_order, FoodItem.code)
        ).all()
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

    **`is_mock` and the item vocabulary are re-read on every cache hit, and
    nothing else is.** The flag is one of two things in a published bundle
    that legitimately move while that set stays published (§2.2: staff
    publish the real factors, verify them live for a day or two, then clear
    the flag through FactorSetAdmin's confirmed action). The other, since
    v1.58, is `food_item` -- **global taxonomy, not a child of the set**, so
    nothing about publishing pins it. Every other field is immutable in place,
    which is what makes caching the rest of the bundle safe at all. See
    `_live_item_vocabulary` for what goes wrong without the second check, and
    for why the two are checked here rather than hooked into the panel.

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
        cached_is_mock, cached_vocabulary, bundle = cached
        if (
            _live_is_mock(session, factor_set_id) == cached_is_mock
            and _live_item_vocabulary(session) == cached_vocabulary
        ):
            return bundle
        # The flag or the vocabulary moved under a warm slot. Drop it and
        # rebuild below rather than patch the cached bundle: `bundle_factory`
        # is injectable and what it returns is not this module's to reach into.
        invalidate_factor_bundle(factor_set_id)
    #: **Read before the bundle is composed, and stamped on the slot
    #: afterwards.** The two orderings are not equivalent and only this one is
    #: safe. Read *after* the compose and a write that lands in between -- a
    #: staff member adding a food, or clearing `is_mock`, while this request
    #: is assembling some 900 factor rows -- is stamped onto a bundle that
    #: predates it, so every later hit compares the new state against a
    #: fingerprint that already says "new" and returns the stale bundle. With
    #: no expiry on this cache that is for the life of the process, which is
    #: the failure this fingerprint exists to prevent, reintroduced by the
    #: order in which it was taken.
    #:
    #: Read *before*, a write in the same window makes the fingerprint stale
    #: instead, and the next hit rebuilds once for nothing. That is the
    #: direction to be wrong in: a needless rebuild costs one request the
    #: work this cache saves, and a wrongly-validated slot costs every
    #: request a wrong answer until somebody restarts the process.
    #:
    #: Taken on the draft path too, where it is two small queries spent on a
    #: slot that will not be cached (only a published set is). Deciding first
    #: would cost a query of its own to learn the status, and the draft path
    #: is one staff member dry-running one set -- the side to be wasteful on.
    fingerprint = (_live_is_mock(session, factor_set_id),
                   _live_item_vocabulary(session))
    bundle = factory(build_bundle_data(session, factor_set_id))
    #: **Reported here, never raised here.** `validate()` returns problems and
    #: does not raise; `publish_factor_set` refuses a set that has any, which is
    #: where a staff member can still do something about it. By the time a
    #: bundle is being loaded the set is already published and a visitor is
    #: waiting, and a data defect that still computes *a* number is not worth
    #: answering with a maintenance page for everybody.
    #:
    #: But it was worth nothing at all before: the only caller of `validate()`
    #: was the inline dry-run branch in `api/router.py`, so the one bundle
    #: nobody checked was the one every public request uses. A duplicate
    #: upstream row - two rows differing only in a column the key does not carry
    #: - silently collapses to whichever came last, and served a wrong number
    #: with nothing in the logs.
    #: Asked of the object, not assumed of it. `bundle_factory` is an
    #: injection point whose contract has always been "returns whatever the
    #: engine's bundle is", and three tests hand it a plain dict to prove the
    #: cache partitions by set without dragging the engine in. Requiring
    #: `validate()` here would have been a new demand on that seam, made
    #: silently, to serve a check that is about the real bundle.
    validate = getattr(bundle, "validate", None)
    problems = validate() if callable(validate) else []
    if problems:
        _LOGGER.error(
            "Factor set %s composes into a bundle with %d problem(s); it is being "
            "served anyway because a published set is what visitors are already "
            "using. %s",
            factor_set_id, len(problems), "; ".join(problems[:5]),
        )
    # `build_bundle_data` has already loaded this row into the identity map,
    # so the status check costs no second round trip.
    factor_set = session.get(FactorSet, factor_set_id)
    if factor_set is None or factor_set.status is not FactorSetStatus.published:
        return bundle
    with _cache_lock:
        #: `fingerprint`, not `factor_set.is_mock` and a second vocabulary
        #: query: both would be read *after* the compose above. See the note
        #: at the read. `setdefault` keeps whichever bundle won the race
        #: between two threads composing at once -- they are equivalent, and
        #: the loser's is simply dropped.
        return _bundle_cache.setdefault(
            factor_set_id, (*fingerprint, bundle)
        )[2]


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


def count_item_level_upstream(session: Session, factor_set_id: int) -> int:
    """How many of this set's upstream rows name a food item. §2.2 (v1.54)."""
    return session.scalar(
        select(func.count())
        .select_from(FactorUpstream)
        .where(
            FactorUpstream.factor_set_id == factor_set_id,
            FactorUpstream.food_item_id.is_not(None),
        )
    ) or 0


def item_level_coverage(session: Session, factor_set_id: int) -> tuple[int, int]:
    """`(foods this set prices individually, foods in the vocabulary)`.

    What the factor-set screen shows beside `item_level_enabled`, because
    releasing step 2.5 is a judgement about how much of the vocabulary actually
    carries numbers and a boolean cannot carry that. "3 of 19" and "19 of 19"
    are both legal (the guard is soft, §3.5) and they are not the same decision.

    Counts **distinct foods**, not rows: one food priced for every metric
    across six sectors is six rows per metric and one food, and the number a
    staff member is weighing is how many of the foods on the new screen will be
    answered with something better than their category's average.

    The denominator is the **active** vocabulary, matching what the calculator
    would offer; a retired item is not a gap in coverage. Both halves are zero
    in every database today.
    """
    priced = session.scalar(
        select(func.count(distinct(FactorUpstream.food_item_id))).where(
            FactorUpstream.factor_set_id == factor_set_id,
            FactorUpstream.food_item_id.is_not(None),
        )
    ) or 0
    total = session.scalar(
        select(func.count()).select_from(FoodItem).where(FoodItem.active.is_(True))
    ) or 0
    return priced, total


def refuse_item_level_without_item_rows(session: Session, factor_set_id: int) -> None:
    """`item_level_enabled` on a set that prices no food individually. §3.5.

    The flag releases step 2.5 — the screen that asks *which food*, not only
    which category. On a set with no item-level rows every answer to that
    question is priced at the food's category average, so the calculator asks a
    more specific question than its numbers can answer and returns a figure
    that looks more authoritative for it. Under a mock factor set, which is the
    only kind that exists today, that is a reputational risk for the client
    rather than a modelling one, and it is the exact thing §2.2's mandatory
    placeholder banner is already struggling to say.

    **Soft, and deliberately so.** One item row is enough. Full coverage is 19
    items x 6 sectors x every metric the set prices (570 rows at five, 684
    since v1.70 added a sixth) and is unreachable from any data
    that will exist, and a partly-covered set is coherent because everything
    else falls back to the category average — see `_covered_by`. A guard
    demanding more would make the flag unusable and would be arguing with
    §2.2's own fallback.

    Run at publish and after an import, where `revalidate_formulas` already
    runs. After an import it is doing a second job worth naming: the target's
    rows were just deleted and re-copied from the published set, so a flag that
    survives with no item rows under it means **the copy lost the item
    dimension** — the silent hybrid this landing exists to prevent.
    """
    factor_set = session.get(FactorSet, factor_set_id)
    if factor_set is None or not factor_set.item_level_enabled:
        return
    if count_item_level_upstream(session, factor_set_id) > 0:
        return
    raise FactorSetStateError(
        f"'{factor_set.version_label}' has item-level detail switched on but "
        "carries no upstream factor that names a food item, so every food on "
        "that screen would be priced at its category's average while the "
        "screen asked a more specific question. Add at least one upstream "
        "factor with a food item, or switch item-level detail off."
    )


def find_item_rows_without_category_fallback(
    session: Session, factor_set_id: int
) -> list[tuple[str, str, str]]:
    """Which `(sector, food_category, metric)` tuples price a food but not the
    category it belongs to. §2.2 (v1.54).

    **This is the one silent zero the item dimension can still produce**, and
    the engine cannot refuse it: §2.2's chain has no silent-zero trap *as long
    as the data has a category row to fall back on*, and that is a property of
    the data, not of the chain. A tuple whose only upstream rows name a food
    answers candidates 1 and 3 for that food and nothing at all for any other
    food in the same category, or for a visitor who named the category and no
    food — those fall past candidates 2 and 4 to `Decimal('0')`.

    That is the O-7 failure shape exactly, and worse than the original for the
    same reason O-7's own guard records: wrong for one food while every other
    figure on the same results page is right, arriving with no error, no
    warning and nothing in the log.

    **The fallback is the `(NULL item, NULL destination)` row specifically**,
    not any category-level row. A category row that names a destination is
    §2.2's candidate 2 and answers only *at that destination* — the `prevention`
    override is exactly that shape — so a set whose only category row for the
    tuple is the zero override still drops every other destination to zero.

    Returns **codes, not ids** (§1.1) and sorted, so a caller can put them
    straight into a message a staff member has to act on. Empty is the healthy
    state, and is what every set with no item rows returns — which is all of
    them today.
    """
    fallback = (
        select(
            FactorUpstream.sector_id,
            FactorUpstream.food_category_id,
            FactorUpstream.metric_id,
        )
        .where(
            FactorUpstream.factor_set_id == factor_set_id,
            FactorUpstream.food_item_id.is_(None),
            FactorUpstream.destination_id.is_(None),
        )
        .subquery()
    )
    rows = session.execute(
        select(Sector.code, FoodCategory.code, Metric.code)
        #: Explicit left side, for the reason `find_missing_prevention_upstream`
        #: gives above: every selected column belongs to a joined table.
        .select_from(FactorUpstream)
        .join(Sector, FactorUpstream.sector_id == Sector.id)
        .join(FoodCategory, FactorUpstream.food_category_id == FoodCategory.id)
        .join(Metric, FactorUpstream.metric_id == Metric.id)
        .outerjoin(
            fallback,
            (FactorUpstream.sector_id == fallback.c.sector_id)
            & (FactorUpstream.food_category_id == fallback.c.food_category_id)
            & (FactorUpstream.metric_id == fallback.c.metric_id),
        )
        .where(
            FactorUpstream.factor_set_id == factor_set_id,
            FactorUpstream.food_item_id.is_not(None),
            fallback.c.sector_id.is_(None),
        )
        .distinct()
    ).all()
    return sorted((sector, food, metric) for sector, food, metric in rows)


def refuse_item_rows_without_category_fallback(
    session: Session, factor_set_id: int
) -> None:
    missing = find_item_rows_without_category_fallback(session, factor_set_id)
    if not missing:
        return
    listed = ", ".join(f"{sector}/{food}/{metric}" for sector, food, metric in missing)
    raise FactorSetStateError(
        f"{len(missing)} combinations carry a factor for a food item but no "
        "factor for the food category it belongs to, so every other food in "
        "that category — and anyone who named the category and no food — would "
        f"be priced at 0 rather than at an average: {listed}. Add an upstream "
        "factor for each of those, leaving both the food item and the "
        "destination blank, then publish."
    )


def refuse_a_bundle_that_does_not_validate(
    session: Session, factor_set_id: int, *, bundle_factory: BundleFactory | None = None
) -> None:
    """Refuse publishing a set whose composed bundle reports problems.

    `FactorBundle.validate()` has existed since v1.4 and **never raises** - it
    returns a list of human-readable problems and leaves the decision to its
    caller. Until now the only caller was `api/router.py`, and only inside the
    branch that handles an inline dry-run bundle a staff member pasted in. So
    the one bundle nobody checked was the one every public request uses.

    What that hides is specific rather than theoretical. `from_json` records
    duplicate rows rather than raising on them, because a bundle is a re-keying
    and the later row silently wins; two upstream rows differing only in a
    column the key does not carry collapse into one, and the calculator serves
    whichever survived. That is a wrong number with no error anywhere - the
    shape of the three this project has already shipped.

    **Publish is the right place and load is not.** Raising here refuses an
    operation a staff member is performing, with the problems in front of them
    and the draft still editable. Raising at load would take the calculator
    down for every visitor over a data defect that still computes *a* number,
    which is the worse failure - so `load_factor_bundle` logs instead.

    Scoped to publish, never `rollback_to`, for the reason written above the
    prevention guards: rollback is the "put it back to a state that worked"
    operation, and refusing an emergency rollback over a completeness rule is
    itself the emergency.
    """
    factory = bundle_factory or _default_bundle_factory
    problems = factory(build_bundle_data(session, factor_set_id)).validate()
    if not problems:
        return
    raise FactorSetStateError(
        "This set does not compose into a usable bundle, so publishing it would "
        "serve numbers the calculator cannot vouch for. "
        + "; ".join(problems[:5])
        + (f" (and {len(problems) - 5} more)" if len(problems) > 5 else "")
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
    #: v1.54's two, scoped exactly as the two above and for the same reason:
    #: publish only, never `rollback_to` below, because rollback is the "put
    #: the calculator back to a state that worked" operation and refusing an
    #: emergency rollback over a completeness rule is the worse failure.
    refuse_item_level_without_item_rows(session, factor_set_id)
    refuse_item_rows_without_category_fallback(session, factor_set_id)
    #: Last, because it is the only one that composes the whole bundle - the
    #: cheap row-level rules should say the specific thing first, so a staff
    #: member gets "add a category factor for primary_production/dairy/co2e"
    #: rather than a generic "this does not compose".
    refuse_a_bundle_that_does_not_validate(session, factor_set_id)
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
    #: The second of the two hand-written `FactorSet(...)` constructors - see
    #: the same note in `admin/factor_lifecycle.clone_factor_set`, which is the
    #: one the panel calls. A setting carried by one and dropped by the other
    #: is invisible until a staff member clones, and then the clone comes back
    #: with the switch off. `item_level_enabled` (v1.54) is carried here for
    #: that reason; both are covered by one parametrised test.
    clone = FactorSet(
        version_label=new_label,
        status=FactorSetStatus.draft,
        is_mock=source.is_mock,
        item_level_enabled=source.item_level_enabled,
        effective_from=source.effective_from,
        notes=source.notes,
    )
    session.add(clone)
    session.flush()
    #: Every column except the two that identify the row, read off the mapper
    #: rather than written out here.
    #:
    #: **This was a hand-written field list per model and it had already
    #: drifted.** It named `destination_id` on purpose - the comment said, in
    #: as many words, that leaving it out is how O-7 comes back - and then
    #: silently missed `factor_downstream.sector_id`, which is the same class
    #: of defect in the sector dimension, plus `source_note` and
    #: `data_quality` on three tables. Six columns across three tables, added
    #: to the models after the list was written and never added to the list.
    #:
    #: A list that has to be edited every time a column is added will be
    #: missed again, and the thing that misses it is not visible: the clone
    #: comes out with the right row COUNT, which is what
    #: `test_clone_is_deep_and_publish_rollback_preserve_single_published`
    #: asserted. `admin/factor_lifecycle.py::_clone_children` - the copy the
    #: admin panel actually calls - has always done it by reflection, which is
    #: why it never drifted.
    for model in (FactorUpstream, FactorDownstream, Constant, Formula, Equivalence):
        for row in session.scalars(select(model).where(model.factor_set_id == source_id)):
            fields = {
                column.name: getattr(row, column.name)
                for column in row.__table__.columns
                if column.name not in ("id", "factor_set_id")
            }
            session.add(model(factor_set_id=clone.id, **fields))
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
    period_start: datetime | None = None,
    period_end: datetime | None = None,
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

    `period_start` and `period_end` (v1.67) sit beside `time_frame` and are
    kept off `req` for exactly the same reason, which is the more important
    half of why they are written out here rather than folded into the request
    object: the period is now a pair of *instants*, and two instants are what
    it takes to write `(end - start)` against a metric total. The contract
    forbids that outright (§2.3), and the cheapest way to keep forbidding it
    is to make sure the engine never sees either value.

    **What these two instants are.** The visitor's **local wall-clock time,
    with no zone** -- what a person read off the clock on their own wall,
    stored verbatim. Adequate as a label, printed back to the visitor who
    typed it; **inadequate for comparison across submissions**, because two
    rows both saying `08:10` may be two hours apart or twenty-two and nothing
    on either row can say which (§2.3 stores no address and no user agent, so
    no zone can be inferred, by design). `db/models.py` says this at the
    column and `alembic/versions/0018_submission_period.py` says it at the
    migration; it is repeated here because this is the function that writes
    them.

    They are validated on the wire and again by `ck_submission_period`
    (§2.3): both or neither, running forwards, not before the epoch, `custom`
    only with an interval and an interval only with a `time_frame`. This
    function does not re-check them -- the CHECK is what makes that safe, and
    a third copy of the rule would be a third thing to keep in step.

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
            period_start=period_start,
            period_end=period_end,
        )
        session.add(submission)
        session.flush()
    else:
        submission.updated_at = now
        submission.factor_set_id = factor_set_id
        submission.gwp_horizon = req.gwp_horizon
        # Written on the update path too: a second calculation reusing the
        # same token would otherwise keep the first one's period forever.
        # All three together, and never one without the others: leaving the
        # interval behind while `time_frame` moved would leave the row in
        # exactly the state `ck_submission_period` exists to forbid --
        # `one_month` against last week's shift, or `custom` against nothing.
        submission.time_frame = time_frame
        submission.period_start = period_start
        submission.period_end = period_end
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
        #: v1.58. The named food, stored **beside** its category and never
        #: instead of it (design §3.4). `food_category_id IS NULL` already
        #: means *the visitor did not break their waste down by type*, and
        #: §5.4 forbids conflating that with a finer answer -- which is also
        #: what `ck_submission_entry_item_has_category` says at the schema, and
        #: what keeps `by_food_category` rolling items up into their parents
        #: with no change to the aggregation at all.
        #:
        #: `getattr` rather than an attribute access, for the reason the three
        #: money fields above are defaulted on `EntryInput`: `req` is a §3
        #: `CalculationRequest` and this function is called with hand-built
        #: ones in the suite. A request object that predates the slot means
        #: "named no food", which is exactly what `None` here stores.
        food_item_id = (
            _resolve_id(session, FoodItem, entry.food_item_code)
            if getattr(entry, "food_item_code", None)
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
                #: v1.58. NULL means the visitor named a category and no food,
                #: which is every submission written before this revision.
                food_item_id=food_item_id,
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


def set_public_contribution(session: Session, token: str) -> bool:
    """The visitor's own opt-in (§5.3, v1.48). Returns whether a row moved.

    Keyed on `token` because that is the only handle the browser has - and
    the reason this has a deadline nobody should have to discover: §2.3's
    `expire_tokens` nulls the column an hour on, which is what severs the
    link between a stored row and a session. After that the row cannot be
    found and the visitor cannot opt in. That is correct rather than
    unfortunate: the mechanism that makes the offer possible is the same one
    the privacy design deliberately destroys.

    Idempotent, and silent on a miss. A token that resolves to nothing is
    treated as absent, the same as everywhere else it appears.
    """
    result = session.execute(
        update(Submission)
        .where(Submission.token == token)
        .values(is_public_contributed=True)
    )
    return bool(result.rowcount)


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
       its own grouping needs, because `excluded_from_public` and (v1.48)
       `is_public_contributed` both live there (§2.3, §5.3). Stopping at
       `submission_entry` applies neither staff moderation nor visitor
       consent to anything. The two predicates are independent and both
       required: staff exclusion withdraws a row the visitor offered, and
       consent is not staff's to grant on a visitor's behalf, so a row must
       clear both to be counted anywhere below, including
       `total_calculations`.
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
        .where(
            Submission.excluded_from_public.is_(False),
            Submission.is_public_contributed.is_(True),
        )
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
        .where(
            Submission.excluded_from_public.is_(False),
            Submission.is_public_contributed.is_(True),
        )
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
            Submission.is_public_contributed.is_(True),
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
        #: **`food_item` is carried only on the rows that have one, and the
        #: `food_items` section is still not carried at all.**
        #:
        #: Stripping it unconditionally was the first answer here, deferred to
        #: the API landing on the grounds that §6.3 is a *public* document -
        #: `tests/api/test_api.py` compares its key set against
        #: `tests/fixtures/factors.json` exactly, and `web/js/methodology.js`
        #: renders these rows to visitors - so publishing a new dimension is a
        #: contract change that belongs with the landing that gives the item a
        #: public name to print.
        #:
        #: The cost of deferring it is what changed the answer. **This** landing
        #: is the one that lets a staff member author an item-level row, so from
        #: the first such row a stripped export publishes two rows that are
        #: identical in every key it prints and price differently - a
        #: transparency page actively misleading about the numbers it exists to
        #: disclose. A contract bump is the smaller harm.
        #:
        #: Emitting the key only when it has a value is what lets both be true:
        #: no item rows exist, so every byte of today's export and of
        #: `tests/fixtures/factors.json` is unchanged and no visitor-facing
        #: response moves, which is this landing's whole claim - and the day one
        #: exists, the export tells the two rows apart without waiting for §6.1.
        #: The same optional-when-present shape §10.2 gives `upstream[].food_item`
        #: in the bundle, for the same reason.
        #:
        #: `methodology.js` still has no column for it. That is a real gap and it
        #: belongs to the API landing; an export that carries the distinction and
        #: a page that does not render it is recoverable, and a page rendering two
        #: identical rows is not.
        "upstream": [
            {key: value for key, value in row.items()
             if key != "food_item" or value is not None}
            for row in data["upstream"]
        ],
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
