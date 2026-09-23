"""The factor tables. Contract §2.2.

These carry the numbers the calculator actually multiplies. Every row belongs
to exactly one `factor_set`, which is what makes a published result
reproducible: a submission stamps the set it was calculated against, so the
numbers can be recovered years later even after staff have revised them.

Separate from admin/taxonomy_models.py because the lifecycle is different —
taxonomy rows are edited in place and stay resolvable forever, factor rows
are cloned into a new version and the old version is archived intact.
"""

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DECIMAL, Boolean, CheckConstraint, DateTime, Enum, ForeignKey, Index,
    Integer, String, Text, UniqueConstraint, text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from admin.taxonomy_models import Destination, FoodCategory, FoodItem, Metric, Sector
from db.base import BIGINT_PK, Base


class FactorSetStatus(str, enum.Enum):
    draft = "draft"
    published = "published"
    archived = "archived"


class FactorSet(Base):
    """One version of every number in the calculator. Contract §2.2.

    At most one row may be `published` at any time. That invariant is not a
    database constraint — it is a statement about the table — and is enforced
    in admin/taxonomy_rules.py, called from AuditedModelView's
    validate_before_commit hook.
    """

    __tablename__ = "factor_set"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    version_label: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    status: Mapped[FactorSetStatus] = mapped_column(
        Enum(FactorSetStatus, native_enum=True), nullable=False,
        default=FactorSetStatus.draft,
    )
    #: Drives the site-wide placeholder-data banner, which is mandatory and
    #: non-dismissible while true. Defaults to true so a set nobody has
    #: vouched for cannot be published silently as real data.
    is_mock: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True,
                                          server_default="1")
    #: v1.54. Releases step 2.5 of the calculator — the screen that asks which
    #: *food* was wasted, not only which category.
    #:
    #: **It never reaches the engine.** Not a `FactorBundle` field, not a
    #: `bundle.json` key, not an argument to `calculate`. That is what keeps
    #: reproducibility free: a submission stamps its `factor_set_id`, and if the
    #: flag were an engine input then flipping it would change what a stored
    #: calculation recomputes to. It releases a question the interface asks; it
    #: is not a factor. `tests/test_item_level_inertness.py` is the guard.
    #:
    #: **On the set rather than global** so that it is versioned and audited
    #: like everything else here. It is emphatically *not* how the two levels
    #: are separated: one set holds item rows and category rows together
    #: (spec §3.5), because the client cannot be asked to maintain two.
    #:
    #: FALSE by default: a set nobody has authored item factors for must not
    #: claim item-level precision, and the guard that will refuse the flag on a
    #: set with no item-level rows is a later landing.
    item_level_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    effective_from: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __str__(self) -> str:
        #: version_label, not id: a factor set carries no `code` column, but
        #: version_label is unique and is the label every clone/publish/
        #: rollback screen already shows staff.
        return self.version_label


class FactorUpstream(Base):
    """Per-kilogram impact of producing the food, by sector and category.

    Contract §2.2. Roughly 270 rows per factor set.

    `destination_id` is nullable and means "applies to every destination for
    this (sector, food_category, metric)" — the same pattern
    `FactorDownstream.food_category_id` uses, and the lookup order is the same
    shape: exact destination, then the NULL row, then zero. Almost every row is
    the NULL one; producing a kilogram of dairy costs what it costs whatever
    later becomes of it.

    **The column exists for `prevention`, and closing open item O-7 is the
    whole of its job.** `docs/architecture.md` §4.1 says `prevention`'s factors
    are all zero — a 100% offset — and that this is what stops `net_benefit`
    being inflated by simply assuming less waste. Before v1.8 the data model
    could not express it: upstream was keyed on (sector, food_category, metric)
    and could not see the destination, so a line moved to `prevention` kept the
    entry's full upstream factor and only the downstream delta survived into
    the net benefit. Measured on `tests/fixtures/`, 800 kg of `not_harvested`
    moved to `prevention` yielded 96.000 kg CO2e where a true offset yields
    456.000 — 78.9% of the benefit missing, one-directional, and always
    understating the client's "wasting less" story.

    The same NULL trap applies as on `factor_downstream`, for the same reason
    and with the same fix: MySQL compares NULLs as distinct inside a UNIQUE
    key, so the declared UNIQUE below is silent on precisely the generic rows.
    `uq_factor_upstream_generic` collapses NULL to 0 with COALESCE before
    comparing. It is declared here so `Base.metadata.create_all()` produces it,
    created again as raw SQL in
    alembic/versions/0009_upstream_destination.py because the migration chain
    is an independent path to the same schema, and excluded from
    `compare_metadata` in tests/test_migrations.py because SQLAlchemy reflects
    the expression key part back out as a plain column. See
    `FactorDownstream`'s docstring below for the full account.
    """

    __tablename__ = "factor_upstream"
    __table_args__ = (
        UniqueConstraint("factor_set_id", "sector_id", "food_category_id",
                         "food_item_id", "destination_id", "metric_id",
                         name="uq_factor_upstream"),
        #: **Two nullable key parts since v1.54, and both must be collapsed.**
        #: `factor_downstream` below records what happens when only one of a
        #: pair is: the index exists, is unique, contains a COALESCE, and has
        #: silently stopped enforcing half of what it was written for.
        Index(
            "uq_factor_upstream_generic",
            "factor_set_id", "sector_id", "food_category_id",
            text("(COALESCE(food_item_id, 0))"),
            text("(COALESCE(destination_id, 0))"),
            "metric_id",
            unique=True,
        ),
    )

    #: Contract §2.2 specifies BIGINT here, not INT: roughly 270 upstream
    #: and 600 downstream rows per factor set, once per version, is what
    #: actually exhausts an INT. See db/base.py for why it is a variant.
    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    factor_set_id: Mapped[int] = mapped_column(
        ForeignKey("factor_set.id", ondelete="CASCADE"), nullable=False
    )
    sector_id: Mapped[int] = mapped_column(ForeignKey("sector.id"), nullable=False)
    food_category_id: Mapped[int] = mapped_column(
        ForeignKey("food_category.id"), nullable=False
    )
    #: v1.54. NULL means "every food item in this category" — the category
    #: average, which is the normal row and what the whole table held until
    #: this column existed. A row that names an item carries **both** columns:
    #: `food_category_id` stays NOT NULL, so an item factor is always reachable
    #: through the category it refines.
    #:
    #: **There is no silent-zero trap here**, and that is what separates this
    #: dimension from the one O-7 closed. An item with no row of its own falls
    #: through to its category's row — a defined, meaningful average — so a set
    #: carrying item factors for a handful of foods and category factors for
    #: everything else is coherent, and the switch needs no full-coverage
    #: guard. That is what lets the vocabulary grow — forty-seven foods since
    #: v1.72 — without any set having to grow a row for each.
    #:
    #: `factor_downstream` gains no item dimension: the destination split is
    #: shared across the leaves a chain forks into (design decision 1), and
    #: downstream already varies by `(destination, sector, food_category)`.
    food_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("food_item.id"), nullable=True
    )
    #: NULL means "every destination for this (sector, food_category, metric)".
    #: See the class docstring: this column is what makes `prevention` a real
    #: 100% offset rather than a downstream-only one.
    destination_id: Mapped[int | None] = mapped_column(
        ForeignKey("destination.id"), nullable=True
    )
    metric_id: Mapped[int] = mapped_column(ForeignKey("metric.id"), nullable=False)
    value_per_kg: Mapped[Decimal] = mapped_column(DECIMAL(20, 10), nullable=False)
    source_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_quality: Mapped[str | None] = mapped_column(String(32), nullable=True)

    factor_set: Mapped[FactorSet] = relationship()
    sector: Mapped[Sector] = relationship()
    food_category: Mapped[FoodCategory] = relationship()
    food_item: Mapped[FoodItem | None] = relationship()
    destination: Mapped[Destination | None] = relationship()
    metric: Mapped[Metric] = relationship()

    def __str__(self) -> str:
        #: No natural name of its own - composed from the sector/category
        #: pair and the metric it prices, the combination the unique
        #: constraint above is keyed on. `destination` is nullable ("every
        #: destination"), and a row that overrides one - `prevention` at zero
        #: - must be distinguishable from the general row in a select box,
        #: which is the whole reason the column exists. `food_item` (v1.54) is
        #: nullable on exactly the same terms and shown on exactly the same
        #: argument: "processing/dairy — co2e" would otherwise name both the
        #: category average and every item that refines it.
        item = f"/{self.food_item.code}" if self.food_item else ""
        scope = f" → {self.destination.code}" if self.destination else ""
        return (f"{self.sector.code}/{self.food_category.code}{item}{scope}"
                f" — {self.metric.code}")


class FactorDownstream(Base):
    """Per-kilogram impact of the disposal route. Contract §2.2.

    **Two nullable dimensions, and the order between them is the dangerous
    part.** `food_category_id` is nullable and means "applies to every food
    category for this destination" — that is how a per-tonne charge like the
    waste levy is expressed. `sector_id` is nullable on the same terms (v1.31)
    and means "applies to every sector for this destination": NULL is the
    normal value, and a New Zealand set whose disposal routes cost the same
    wherever the waste arose takes NULL on every row.

    Four rows may therefore legally exist for one `(destination, metric)`, and
    §4.1 fixes which one wins:

        1. (sector, food_category)   -- both stated
        2. (sector, NULL)            -- this sector, every food category
        3. (NULL, food_category)     -- every sector, this food category
        4. (NULL, NULL)              -- every sector, every food category
        5. zero

    **Steps 2 and 3 both name one dimension, and the sector wins.** §2.2
    carries the reasoning; the operative half is that the sector is always
    something the caller stated — `submission_entry.sector_id` is NOT NULL —
    while the food category may be `standard_mix` substituted by §6.2 for a
    caller who declined to give one.

    `value_per_kg` **may be negative**: animal feed displaces feed that would
    otherwise have been produced, so diverting to it is a genuine credit.
    Nothing may clamp this to zero.

    Roughly 600 rows per factor set.

    The declared UNIQUE(factor_set_id, destination_id, sector_id,
    food_category_id, metric_id) below does not stop two rows that are NULL in
    either nullable column from coexisting — MySQL treats NULLs as distinct, so
    the constraint is silent on exactly the "applies to every category" and
    "applies to every sector" rows it most needs to guard. A functional index
    over COALESCE(sector_id, 0) **and** COALESCE(food_category_id, 0) is what
    actually closes that gap — both, because collapsing only one of the two
    leaves the other's duplicates legal — and it **is** declared here as a
    SQLAlchemy `Index`
    (`uq_factor_downstream_generic`, using a `text()` expression as its key
    part), so `Base.metadata.create_all()` — the path every test outside
    tests/admin/test_factor_models.py builds its schema with — produces it
    too. The same index is *also* created as raw SQL in
    alembic/versions/0005_factors.py's upgrade()/downgrade(), because the
    migration chain is a second, independent path to the same schema and
    autogenerate cannot be trusted to emit a functional index's expression
    key part on its own. It is excluded from `compare_metadata`
    (tests/test_migrations.py's `_include_object`) because this SQLAlchemy/
    PyMySQL combination reflects the expression key part back out as a plain
    `food_category_id` column rather than an expression, which would
    otherwise make `compare_metadata` report a permanent, spurious
    remove/add pair on every run.
    """

    __tablename__ = "factor_downstream"
    __table_args__ = (
        UniqueConstraint("factor_set_id", "destination_id", "sector_id",
                         "food_category_id", "metric_id",
                         name="uq_factor_downstream"),
        Index(
            "uq_factor_downstream_generic",
            "factor_set_id", "destination_id",
            text("(COALESCE(sector_id, 0))"),
            text("(COALESCE(food_category_id, 0))"),
            "metric_id",
            unique=True,
        ),
    )

    #: Contract §2.2 specifies BIGINT here, not INT: roughly 270 upstream
    #: and 600 downstream rows per factor set, once per version, is what
    #: actually exhausts an INT. See db/base.py for why it is a variant.
    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    factor_set_id: Mapped[int] = mapped_column(
        ForeignKey("factor_set.id", ondelete="CASCADE"), nullable=False
    )
    destination_id: Mapped[int] = mapped_column(
        ForeignKey("destination.id"), nullable=False
    )
    #: NULL means "every sector for this destination" (v1.31). See the class
    #: docstring: this column is what lets a factor set price the same disposal
    #: route differently by supply-chain stage, and NULL is what every row of a
    #: set that does not need to takes.
    sector_id: Mapped[int | None] = mapped_column(
        ForeignKey("sector.id"), nullable=True
    )
    food_category_id: Mapped[int | None] = mapped_column(
        ForeignKey("food_category.id"), nullable=True
    )
    metric_id: Mapped[int] = mapped_column(ForeignKey("metric.id"), nullable=False)
    value_per_kg: Mapped[Decimal] = mapped_column(DECIMAL(20, 10), nullable=False)
    source_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_quality: Mapped[str | None] = mapped_column(String(32), nullable=True)

    factor_set: Mapped[FactorSet] = relationship()
    destination: Mapped[Destination] = relationship()
    sector: Mapped[Sector | None] = relationship()
    food_category: Mapped[FoodCategory | None] = relationship()
    metric: Mapped[Metric] = relationship()

    def __str__(self) -> str:
        #: Both middle dimensions are nullable, and a row that names one has to
        #: be distinguishable in a select box from the row that does not —
        #: which is the whole reason §4.1 needs an order between them. Say
        #: "all sectors" / "all categories" rather than rendering a blank.
        sector = self.sector.code if self.sector else "all sectors"
        category = self.food_category.code if self.food_category else "all categories"
        return f"{self.destination.code}/{sector}/{category} — {self.metric.code}"


class Constant(Base):
    """A named number a formula can reference. Contract §2.2.

    `GWP_CH4_20` and `GWP_CH4_100` are the pair behind the special
    `const_GWP_CH4` binding (§4.3): the engine resolves it to one or the other
    according to the request's gwp_horizon, so a formula never hard-codes a
    horizon.
    """

    __tablename__ = "constant"
    __table_args__ = (
        UniqueConstraint("factor_set_id", "code", name="uq_constant_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    factor_set_id: Mapped[int] = mapped_column(
        ForeignKey("factor_set.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[Decimal] = mapped_column(DECIMAL(20, 10), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    factor_set: Mapped[FactorSet] = relationship()

    def __str__(self) -> str:
        #: code is unique per factor set, which is exactly the scope a
        #: select box for this table is ever populated from.
        return self.code


class Formula(Base):
    """How one metric is computed from one line. Contract §2.2, §4.3.

    The expression computes a single line's contribution; the engine sums.
    That is why the language needs no arrays, no loops and no sum() — which
    is what keeps the evaluator's security boundary unambiguous.
    """

    __tablename__ = "formula"
    __table_args__ = (
        UniqueConstraint("factor_set_id", "metric_id", name="uq_formula_metric"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    factor_set_id: Mapped[int] = mapped_column(
        ForeignKey("factor_set.id", ondelete="CASCADE"), nullable=False
    )
    metric_id: Mapped[int] = mapped_column(ForeignKey("metric.id"), nullable=False)
    expression: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    factor_set: Mapped[FactorSet] = relationship()
    metric: Mapped[Metric] = relationship()

    def __str__(self) -> str:
        #: No code of its own - UNIQUE(factor_set_id, metric_id) makes the
        #: metric it computes the natural identifier.
        return f"{self.metric.code} formula"


class Equivalence(Base):
    """"Equivalent to driving 14,500 km". Contract §2.2.

    `label_template` lives in the row, not in a front-end template, so staff
    can add a fourth equivalence or reword an existing one without a code
    change — the same reasoning as the metric table.

    **`family`, `min_value` and `max_value` make a set of rows into a ladder**
    (v1.71). Measured against the published set, "Olympic swimming pools" reads
    `0` for any submission below 638.755 kg and "passenger vehicles for a year"
    below 403.737 kg, so a 23 kg café's week showed two of three cards reading
    zero. The rungs below those units are rows here rather than code, for the
    same reason the equivalences themselves are: adding one is an INSERT.
    `engine/calculate.py::_select_rungs` does the choosing and names no family,
    no code and no band anywhere.
    """

    __tablename__ = "equivalence"
    __table_args__ = (
        UniqueConstraint("factor_set_id", "code", name="uq_equivalence_code"),
        #: v1.71, and both are in the schema as well as in the panel's form
        #: for the reason `ck_submission_period` is: a rule the panel holds
        #: and the schema does not is a rule that lasts until the first write
        #: that does not go through the panel — the loader in
        #: `data/upstream-factors-draft/`, a CLI, a correction made by hand.
        #:
        #: A band on a row with no family is a rule that can never fire,
        #: because selection only ever happens within a family. Refused rather
        #: than ignored, so "I set a minimum and nothing happened" is
        #: unreachable.
        CheckConstraint(
            "family IS NOT NULL OR (min_value IS NULL AND max_value IS NULL)",
            name="ck_equivalence_band_needs_family",
        ),
        #: An inverted band admits nothing, and a rung that can never be
        #: chosen is a rung that silently is not there.
        CheckConstraint(
            "min_value IS NULL OR max_value IS NULL OR min_value < max_value",
            name="ck_equivalence_band_ordered",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    factor_set_id: Mapped[int] = mapped_column(
        ForeignKey("factor_set.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    source_metric_id: Mapped[int] = mapped_column(
        ForeignKey("metric.id"), nullable=False
    )
    value_per_unit: Mapped[Decimal] = mapped_column(DECIMAL(20, 10), nullable=False)
    label_template: Mapped[str] = mapped_column(String(255), nullable=False)
    #: v1.71. Which ladder this row is a rung of, or NULL for a row that is
    #: not a rung of anything and is therefore always shown — the pre-v1.71
    #: meaning, and what every row in every database carried before this
    #: column existed.
    #:
    #: **`source_metric_id` cannot serve as this.** A vehicle kilometre, a
    #: vehicle-day and a vehicle-year are all conversions of `co2e` and *are*
    #: one ladder; two different framings of `co2e` would share the source
    #: metric too and must not displace each other. "Same source metric" and
    #: "same ladder" are different claims and only the second one selects.
    family: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: v1.71. The half-open band `[min_value, max_value)` of **this row's own
    #: converted value** — not of the metric total — within which this rung is
    #: eligible. NULL on either side means unbounded there; a rung with
    #: neither is its family's catch-all.
    #:
    #: On the converted value because a ten-minute shower is 90 litres
    #: whatever a kilogram of waste costs in water: real factors (open item
    #: O-1) change which rung a submission lands on and change nothing about
    #: where the rungs are.
    min_value: Mapped[Decimal | None] = mapped_column(DECIMAL(20, 10), nullable=True)
    max_value: Mapped[Decimal | None] = mapped_column(DECIMAL(20, 10), nullable=True)
    #: v1.71. The sentence to use when the interpolated whole number is
    #: exactly `1`. NULL means none was given and `label_template` is used as
    #: before.
    #:
    #: A second staff-typed string rather than a pluralisation rule in the
    #: engine: §7.6 rule 9 already forbids translating or rewording these
    #: sentences, which are the client's approved wording, and English
    #: grammar in a module that serves twenty languages is a rule that would
    #: be wrong in most of them. `Equivalent to 1 Olympic swimming pools of
    #: water` is what this repository printed before the column existed, and
    #: a ladder drives the displayed number toward 1 by design.
    label_template_one: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Open item O-3: the New Zealand basis for km driven, meals and showers
    #: is unsettled, and an equivalence with no stated source is the figure
    #: most likely to be challenged in public.
    source_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0,
                                            server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True,
                                         server_default="1")

    factor_set: Mapped[FactorSet] = relationship()
    source_metric: Mapped[Metric] = relationship()

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"
