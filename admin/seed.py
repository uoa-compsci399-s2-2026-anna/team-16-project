"""The New Zealand food loss and waste taxonomy, as shipped.

Sources:
  Destination groups and destinations - Ministry for the Environment, "Food
  loss and waste definition for Aotearoa New Zealand" (2023, ME 1796).
  Sectors and food categories - University of Otago national baseline (2025).

Idempotent and non-destructive: matches on `code`, creates what is missing,
and never updates an existing row. Staff rename these through the panel, and
a seed that reasserted its own names on every deployment would silently undo
that.

The eight-versus-nine discrepancy: docs/interfaces.md §2.1 describes "the
eight Otago baseline categories" (plus standard_mix), but the Otago source
list has nine substantive categories (plus standard_mix, for ten codes
below). All nine are seeded - a category too many is a row a staff member
can deactivate through the panel, a category too few is data nobody can
enter. This needs the client's word and is recorded in docs/architecture.md
§10, Open Items.

The unit presets' kg_per_unit values are placeholders - the client has not
supplied conversion data. Each says so in its source_note.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from admin.taxonomy_models import (
    Destination, DestinationGroup, FoodCategory, FoodItem, Metric, Sector,
    UnitPreset,
)
from admin.taxonomy_rules import (
    check_prevention_destination, check_single_standard_mix,
)

DESTINATION_GROUPS = [
    # (code, name, is_waste, sort_order)
    ("reuse", "Reuse", False, 10),
    ("recycle_recovery", "Recycle and recovery", True, 20),
    ("disposal", "Disposal", True, 30),
]

#: `is_prevention` is seed *data*, not a rule: it says which row this seed
#: gives the role to, and nothing reads the code `prevention` to find it
#: afterwards. Staff may rename the row through the panel and every guard
#: follows the tick.
DESTINATIONS = [
    # (group_code, code, name, is_prevention, sort_order)
    ("reuse", "prevention", "Prevented — waste avoided", True, 5),
    ("reuse", "food_redistribution", "Food redistribution", False, 10),
    ("reuse", "upcycling", "Upcycling to other food products", False, 20),
    ("reuse", "animal_feed", "Animal feed", False, 30),
    ("recycle_recovery", "compost", "Composting (aerobic digestion)", False, 40),
    ("recycle_recovery", "anaerobic_digestion", "Anaerobic digestion", False, 50),
    ("recycle_recovery", "land_application", "Land application", False, 60),
    ("recycle_recovery", "not_harvested", "Not harvested or ploughed in", False, 70),
    ("recycle_recovery", "bioprocessing", "Processing into non-food items", False, 80),
    ("recycle_recovery", "other_recovery", "Other recovery, including biodiesel", False, 90),
    ("disposal", "combustion", "Combustion", False, 100),
    ("disposal", "landfill", "Landfill", False, 110),
    ("disposal", "refuse_discard", "Refuse or discard", False, 120),
    ("disposal", "sewer", "Sewer or wastewater", False, 130),
]

SECTORS = [
    # (code, name, sort_order)
    ("primary_production", "Primary production", 10),
    ("processing", "Processing and manufacturing", 20),
    ("wholesale_retail", "Wholesale and retail", 30),
    ("consumer_household", "Households", 40),
    ("consumer_hospitality", "Hospitality", 50),
    ("consumer_institution", "Institutions", 60),
]

FOOD_CATEGORIES = [
    # (code, name, is_standard_mix, sort_order)
    ("standard_mix", "Mixed food waste (composition unknown)", True, 5),
    ("fruit", "Fruit", False, 10),
    ("vegetables", "Vegetables", False, 20),
    ("nuts_seeds", "Nuts and edible seeds", False, 30),
    ("meat", "Meat", False, 40),
    ("seafood", "Seafood", False, 50),
    ("dairy", "Dairy", False, 60),
    ("bakery_grains", "Bakery and grains", False, 70),
    ("staples", "Staples", False, 80),
    ("beverages", "Beverages (non-dairy)", False, 90),
]

#: The food-item vocabulary (§2.1). **Twenty of its rows are the client's own
#: table 1** -- transcribed in `data/upstream-factors-draft/rawtec_source_data.py`
#: and already mapped to these categories by `NZ_FOOD_CATEGORY_SOURCES`, which is
#: what the nine category factors were averaged from. The parentage of those
#: twenty is therefore not invented here; it is read off the mapping the category
#: figures already use.
#:
#: **Six of table 1's twenty-six rows are deliberately absent.** `Fruit`,
#: `Vegetable`, `Seafood`, `Nuts and seeds`, `Drinks/Beverages` and `General mixed
#: food product` ARE the categories they sit under -- offering "Fruit -> Fruit"
#: asks the visitor to refine an answer into itself. What is left is exactly the
#: twenty rows that say something finer than the category they belong to.
#:
#: **The other twenty-seven rows exist because the client subdivided five
#: categories no further than their own name** (v1.72). Fruit, Vegetable,
#: Seafood, Nuts and seeds and Drinks/Beverages are one table-1 row each, so
#: until this revision step 2.5 offered nothing at all under half the
#: vocabulary's categories and `itemStepOffered()` hid the step outright for a
#: visitor who ticked only those. Adding a food with no factor row of its own is
#: safe by construction and the machinery is already built: §2.2's upstream chain
#: falls it through to its category's factor, which is a defined, meaningful
#: average -- the category factors *are* the averages of these foods -- and
#: §7.3c's fallback disclosure then says so on the page, in the text download and
#: on the PDF. **Every one of the twenty-seven names its source in
#: `FOOD_ITEM_SOURCES` below**, and nothing is generated or combinatorial: this
#: table is *global taxonomy*, shared by every factor set and every stored
#: submission, and this repository already carries what happens when a loader
#: writes its own vocabulary into a global table -- 48 `refed_*` rows sit in
#: `food_category` today, invisible only because `GET /taxonomy` narrows
#: categories to what the published set prices.
#:
#: **The seven rows the client's table gives no New Zealand home go to
#: `staples`.** `docs/upstream-factors-draft.md` §3.2 defines `staples` as the
#: pantry-staples grouping and fills it from ReFED's Dry Goods; Fats, Sauces/
#: Spreads/Dips, Herbs/Spices, Snack foods, Sweeteners and Other food types are
#: pantry goods and land there cleanly. The client's ruling, relayed 2026-09-18,
#: is that anything with no clean correspondence may take public data or an
#: invented home: *their figures are a reference, to be used creatively.*
#:
#: **`eggs` is the weakest of the seven, and it is NOT under `dairy`.** The
#: client's own table draws Eggs directly beneath the Dairy block, which makes
#: `dairy` the obvious home -- and the wrong one, because a parent category is
#: the FALLBACK for the metrics the client did not supply at item level, and
#: `dairy`'s methane is ruminant. Filing a poultry product under ruminant methane
#: returns a systematically high number to a visitor who just asked a more
#: specific question, which is the defect class this project keeps finding. Under
#: `staples` the fallback comes from Dry Goods and carries no enteric methane.
#: **The right long-term answer is a dedicated `eggs` category**; it is not taken
#: here because it would move the taxonomy, `NZ_TO_REFED_FOOD_SHAPE`, the factor
#: draft builder and every test asserting a category count, while open item O-5
#: has not settled whether there are eight categories or nine.
FOOD_ITEMS = [
    # (code, name, food_category code, sort_order)
    ("bread", "Bread", "bakery_grains", 10),
    ("bakery", "Bakery", "bakery_grains", 20),
    ("grains", "Grains", "bakery_grains", 30),
    ("coffee", "Coffee", "beverages", 10),
    ("tea", "Tea", "beverages", 20),
    ("beer", "Beer", "beverages", 30),
    ("wine", "Wine", "beverages", 40),
    ("fruit_juice", "Fruit juice", "beverages", 50),
    ("soft_drinks", "Soft drinks", "beverages", 60),
    ("cheese", "Cheese", "dairy", 10),
    ("milk", "Milk", "dairy", 20),
    ("cream", "Cream", "dairy", 30),
    ("butter", "Butter", "dairy", 40),
    ("yoghurt", "Yoghurt", "dairy", 50),
    ("other_dairy", "Other dairy", "dairy", 60),
    ("apples", "Apples", "fruit", 10),
    ("kiwifruit", "Kiwifruit", "fruit", 20),
    ("berries", "Berries", "fruit", 30),
    ("citrus", "Citrus", "fruit", 40),
    ("stone_fruit", "Stone fruit", "fruit", 50),
    ("bananas", "Bananas", "fruit", 60),
    ("red_meat", "Red meat", "meat", 10),
    ("pork", "Pork", "meat", 20),
    ("poultry", "Poultry", "meat", 30),
    ("other_meat", "Other meat", "meat", 40),
    ("almonds", "Almonds", "nuts_seeds", 10),
    ("walnuts", "Walnuts", "nuts_seeds", 20),
    ("peanuts", "Peanuts", "nuts_seeds", 30),
    ("sunflower_seeds", "Sunflower seeds", "nuts_seeds", 40),
    ("hoki", "Hoki", "seafood", 10),
    ("snapper", "Snapper", "seafood", 20),
    ("salmon", "Salmon", "seafood", 30),
    ("mussels", "Mussels", "seafood", 40),
    ("oysters", "Oysters", "seafood", 50),
    ("eggs", "Eggs", "staples", 10),
    ("fats", "Fats", "staples", 20),
    ("sauces_spreads_dips", "Sauces, spreads and dips", "staples", 30),
    ("herbs_spices", "Herbs and spices", "staples", 40),
    ("snack_foods_desserts", "Snack foods and desserts", "staples", 50),
    ("sweeteners", "Sweeteners", "staples", 60),
    ("other_food_types", "Other food types", "staples", 70),
    ("potatoes", "Potatoes", "vegetables", 10),
    ("kumara", "Kūmara", "vegetables", 20),
    ("carrots", "Carrots", "vegetables", 30),
    ("onions", "Onions", "vegetables", 40),
    ("tomatoes", "Tomatoes", "vegetables", 50),
    ("leafy_greens", "Leafy greens", "vegetables", 60),
]

#: Where every food that is **not** a row of the client's table 1 came from
#: (v1.72). One entry per such code, and `tests/admin/test_food_item_seed.py`
#: refuses a food that is in neither this mapping nor the client's table -- so a
#: name cannot reach the global vocabulary without a reader being able to find
#: out who chose it and why.
#:
#: Two provenances, and they are kept apart on purpose:
#:
#: * **`Poore & Nemecek (2018)`** names a product row already transcribed in
#:   this repository, in `data/upstream-factors-draft/public_land_use_source_data.py`
#:   -- the same study `land` was filled from, republished by Our World in Data.
#:   A name here is that source's own product label, shortened to what a New
#:   Zealand staff member would type (`Citrus Fruit` -> `Citrus`, `Berries &
#:   Grapes` -> `Berries`, `Groundnuts` -> `Peanuts`, `Root Vegetables` ->
#:   `Carrots`, `Onions & Leeks` -> `Onions`). Nothing is imported wholesale:
#:   the source publishes thirty-eight products and eleven of them are used.
#: * **`Team's judgement`** is a New Zealand food with no row in either source,
#:   chosen because a staff member in this country would recognise it and could
#:   have typed it. Each entry says what makes it a New Zealand food rather than
#:   asserting that it is one.
#:
#: **None of these foods carries a factor row anywhere**, and that is the point:
#: §2.2's chain prices each at its category's average and §7.3c discloses that
#: it did. A future staff member who authors an item-level factor for one of
#: them changes the number and nothing else.
#:
#: `Kūmara` is spelled with its macron. `ū` is U+016B and neither brand subset
#: has that code point, but `api/pdf_render.py::_is_drawable` accepts a
#: character its face can compose out of the NFD decomposition and both Geologica
#: Bold and Kumbh Sans carry `u` plus U+0304 -- measured on the shipped font
#: files, and `tests/api/test_pdf_render.py::test_a_macron_survives_the_document`
#: already uses this exact word as its example.
FOOD_ITEM_SOURCES: dict[str, str] = {
    # beverages
    "coffee": "Poore & Nemecek (2018), product row 'Coffee'.",
    "tea": (
        "Team's judgement: the other hot drink every New Zealand workplace, café "
        "and household makes, and the one a staff member would look for beside "
        "coffee."
    ),
    "beer": (
        "Team's judgement: New Zealand brewing, and the beverage a hospitality "
        "venue pours away most of. Poore & Nemecek publish Barley but no beer row."
    ),
    "wine": "Poore & Nemecek (2018), product row 'Wine'. New Zealand's largest crop-based export.",
    "fruit_juice": (
        "Team's judgement: the packaged non-dairy drink after the two alcoholic "
        "ones, and what a school or institution reports."
    ),
    "soft_drinks": (
        "Team's judgement: the remaining supermarket and hospitality beverage "
        "line, named as it is named on a shelf."
    ),
    # fruit
    "apples": "Poore & Nemecek (2018), product row 'Apples'. New Zealand's largest pipfruit crop.",
    "kiwifruit": (
        "Team's judgement: New Zealand's largest horticultural export, and the "
        "fruit a grower or packhouse here would name first. Neither source has a "
        "row for it; Poore & Nemecek would file it under 'Other Fruit'."
    ),
    "berries": "Poore & Nemecek (2018), product row 'Berries & Grapes'.",
    "citrus": "Poore & Nemecek (2018), product row 'Citrus Fruit'.",
    "stone_fruit": (
        "Team's judgement: Hawke's Bay and Central Otago summerfruit, which is "
        "what a New Zealand grower calls this group. Poore & Nemecek file it "
        "under 'Other Fruit'."
    ),
    "bananas": (
        "Poore & Nemecek (2018), product row 'Bananas'. Imported rather than "
        "grown here, and on every supermarket shelf in the country."
    ),
    # nuts and edible seeds
    "almonds": "Poore & Nemecek (2018), product row 'Nuts', named as the nut a shelf names.",
    "walnuts": (
        "Team's judgement: a nut grown commercially in New Zealand, Canterbury "
        "and Marlborough in particular. Poore & Nemecek's 'Nuts' row covers it."
    ),
    "peanuts": (
        "Poore & Nemecek (2018), product row 'Groundnuts', under the name a New "
        "Zealand staff member would type."
    ),
    "sunflower_seeds": (
        "Team's judgement: the edible seed this category is half named after, and "
        "the one sold on its own rather than as an ingredient."
    ),
    # seafood
    "hoki": (
        "Team's judgement: New Zealand's largest wild-capture fishery by volume "
        "and the white fish behind most of its fish and chips."
    ),
    "snapper": (
        "Team's judgement: the best-known inshore table fish in the North Island, "
        "and what a fishmonger or restaurant here would name."
    ),
    "salmon": (
        "Team's judgement: New Zealand's principal farmed finfish (king salmon). "
        "Poore & Nemecek's nearest row is 'Fish (farmed)'."
    ),
    "mussels": (
        "Team's judgement: green-lipped mussels, New Zealand's largest "
        "aquaculture product, under the one-word name a kitchen uses."
    ),
    "oysters": (
        "Team's judgement: Bluff and Pacific oysters, a New Zealand shellfish a "
        "staff member would recognise immediately."
    ),
    # vegetables
    "potatoes": "Poore & Nemecek (2018), product row 'Potatoes'.",
    "kumara": (
        "Team's judgement: the New Zealand sweet potato, grown in Northland, and "
        "a vegetable this country names in te reo Māori rather than in English. "
        "In neither source."
    ),
    "carrots": (
        "Poore & Nemecek (2018), product row 'Root Vegetables', named as New "
        "Zealand's principal root vegetable rather than as a group."
    ),
    "onions": (
        "Poore & Nemecek (2018), product row 'Onions & Leeks'. Onions are a major "
        "New Zealand vegetable export; leeks are not, so the name keeps the half "
        "that is."
    ),
    "tomatoes": "Poore & Nemecek (2018), product row 'Tomatoes'.",
    "leafy_greens": (
        "Team's judgement: lettuce, spinach, silverbeet and the cabbage family "
        "under one heading a visitor can scan. Poore & Nemecek split the same "
        "ground across 'Brassicas' and 'Other Vegetables'."
    ),
}


METRICS = [
    # (code, name, unit, display_unit, display_precision, sort_order)
    #
    # `display_unit` is a presentation variant of `unit` AT THE SAME SCALE
    # (§6.1) -- a typographic difference, never a conversion. These rows read
    # "t CO2e", "kL" and "t" until 2026-08-09, against totals the engine
    # returns in kg and L, and nothing in the system converts between the two:
    # the front end performs no arithmetic on an API figure (§7.6.1), so the
    # label alone would have made every greenhouse-gas figure read a thousand
    # times too small. If a metric should be reported in tonnes, that is its
    # `unit` and the formula produces tonnes.
    ("co2e", "Greenhouse gases", "kg CO2e", "kg CO₂e", 1, 10),
    ("ch4", "Methane", "kg CH4", None, 1, 20),
    ("water", "Water", "L", "L", 0, 30),
    ("cost", "Cost", "NZD", None, 0, 40),
    ("mass", "Mass", "kg", "kg", 1, 50),
    #: v1.70. The sixth metric, and the one column the client has supplied all
    #: along that this calculator did not report.
    #:
    #: **`m2` in `unit`, `m²` in `display_unit`**, which is exactly the shape
    #: `co2e` above uses for `kg CO2e` / `kg CO₂e`: same quantity, same scale,
    #: a typographic difference and nothing more. `unit` is what §6.2 puts on
    #: the wire beside every total and what `results.js` prints; `display_unit`
    #: is what the PDF prints. Measured rather than assumed: `²` (U+00B2) has a
    #: glyph in Geologica Bold, Kumbh Sans, Noto Sans and all four CJK subsets
    #: -- the CJK recut script includes the printable half of Latin-1
    #: Supplement for exactly this kind of character -- and a PDF carrying it
    #: was rendered and looked at rather than inferred from a cmap.
    #:
    #: **Precision 1, from the magnitudes the conversion actually produces.**
    #: The draft set's land factors span 0.1684 m²/kg (vegetables) to 64.0790
    #: (meat). `qty_kg` accepts three decimal places, so at precision 0 a
    #: vegetables entry below 2.97 kg would read `0 m2` -- a real measurement
    #: printed as none, which is the failure this project keeps finding. At
    #: precision 1 that floor is 0.30 kg. One place also matches `co2e`, whose
    #: per-kilogram factors sit in the same range.
    ("land", "Land use", "m2", "m²", 1, 60),
]

#: The one bulk density every row below is built from, in kg per litre.
#:
#: **Not a New Zealand measurement, and the rows say so.** The Food Loss and
#: Waste Protocol — the standard MfE's own *Aotearoa New Zealand Baseline Food
#: Loss and Waste Project* applies — publishes Table 3.2, "Selected Bulk Density
#: Factors Used in Previous FLW Studies (kg per liter)", in its *Guidance on FLW
#: Quantification Methods*, Chapter 3 "Assessing Volume". Two of its rows carry
#: 0.29 kg/L independently: household food waste in a small container (e.g. a
#: caddy or household bin), from WRAP 2010, *Material Bulk Density: Summary
#: Report*; and animal and vegetable wastes from commerce and industry, from
#: Jacobs Engineering UK Ltd 2010, *Survey of Commercial and Industrial Waste
#: Arisings 2010*. One number covering both halves of this calculator's audience
#: is why it was chosen over the same table's 0.50 (household food waste in a
#: skip) or 0.20 ("waste food — animal or mixed", commerce and industry).
#:
#: **No New Zealand figure was found to replace it with.** MfE's *Solid Waste
#: Analysis Protocol* publishes none and declines to: §5.4 records that visual
#: classification by volume "is not recommended due to the need to introduce
#: 'standard' densities to generate a weight figure. This creates a need for
#: extra data manipulation, and thereby creates an opportunity for error." That
#: caution is about this exact conversion and it is the reason O-6 stays open
#: rather than closing here. The same chapter of the FLW guidance says the same
#: thing more gently — an entity that does not measure its own factor "may use a
#: bulk density factor from another source", but should "refer to the original
#: source to understand how these factors were derived".
_BULK_DENSITY_KG_PER_LITRE = Decimal("0.29")

#: What the density is **not** evidence of, written into every row because the
#: panel shows `source_note` on the edit form and this is the sentence a staff
#: member replacing these numbers needs to read first.
_DENSITY_BASIS = (
    "Bulk density 0.29 kg/L from the Food Loss & Waste Protocol, Guidance on "
    "FLW Quantification Methods, Table 3.2 (household food waste, small "
    "container — WRAP 2010; the same figure appears for commerce-and-industry "
    "animal and vegetable wastes — Jacobs Engineering UK 2010). The capacity is "
    "a real New Zealand container size; the density is not a New Zealand "
    "measurement and no published one was found, so this conversion is a "
    "sourced PLACEHOLDER and not measured data. It also assumes the container "
    "holds food waste alone and is filled level. Replace with measured data "
    "before the calculator is published — architecture.md O-6."
)


def _from_capacity(litres: int) -> Decimal:
    """Capacity in litres to kilograms, at the one density above.

    Quantised to the four decimal places `unit_preset.kg_per_unit` actually
    stores (§2.1), so the constant in this file is the same number the database
    holds and the same number §6.1 puts on the wire — no silent widening
    between the three.
    """
    return (litres * _BULK_DENSITY_KG_PER_LITRE).quantize(Decimal("0.0001"))


#: Container capacities in litres, and what each one is.
#:
#: **Every capacity here is a container a New Zealander actually has.** The set
#: this replaced offered a 10 L bucket, a 20 L bucket, a 30 L crate and 120 L
#: and 240 L wheelie bins, which missed both ends of the client's stated case:
#: the 23 L kerbside food scraps bin that most of urban Auckland was issued from
#: March 2023, and the 660 L and 1100 L front-loader bins that are the standard
#: commercial sizes here — the ones a café or a school actually fills. Kerbside
#: rubbish and organics bins are 80 L, 120 L, 140 L and 240 L depending on the
#: council; all four are kept because a visitor should find their own bin rather
#: than round to someone else's.
_CONTAINERS = [
    # (code, litres, label)
    ("bucket_10l_full", 10, "10 L bucket (full)"),
    ("bucket_20l_full", 20, "20 L bucket (full)"),
    ("food_scraps_bin_23l", 23, "23 L kerbside food scraps bin (full)"),
    ("crate_30l_full", 30, "30 L crate (full)"),
    ("wheelie_bin_80l", 80, "80 L wheelie bin (full)"),
    ("wheelie_bin_120l", 120, "120 L wheelie bin (full)"),
    ("wheelie_bin_140l", 140, "140 L wheelie bin (full)"),
    ("wheelie_bin_240l", 240, "240 L wheelie bin (full)"),
    ("front_loader_660l", 660, "660 L front-loader bin (full)"),
    ("front_loader_1100l", 1100, "1100 L front-loader bin (full)"),
]

UNIT_PRESETS = [
    # (code, label, kg_per_unit, source_note)
    (
        code,
        label,
        _from_capacity(litres),
        f"{litres} L × 0.29 kg/L = {_from_capacity(litres)} kg. {_DENSITY_BASIS}",
    )
    for code, litres, label in _CONTAINERS
]


def _ensure(session: Session, model, code: str, **fields) -> bool:
    """Create one row if its code is absent. Returns True if it created it."""
    existing = session.scalar(select(model).where(model.code == code))
    if existing is not None:
        return False
    session.add(model(code=code, **fields))
    return True


def seed_taxonomy(session: Session) -> dict[str, int]:
    """Load the NZ taxonomy. Returns table name to rows created.

    Never commits - the caller owns the transaction, matching the convention
    the rest of admin/ follows. That leaves this function, not the caller,
    responsible for refusing to leave a broken taxonomy staged for that
    commit: it goes nowhere near AuditedModelView, so neither
    check_single_standard_mix nor check_prevention_destination would otherwise
    ever run against what it writes. A staff member who has renamed
    `standard_mix`'s *code* through the panel (a supported edit -
    FoodCategoryAdmin.form_columns includes `code`) makes this a live case,
    not a hypothetical one: re-running the seed no longer recognises the
    renamed row as `standard_mix`, creates a fresh one, and without the
    checks below would commit two active standard mixes with nothing to
    refuse it. Raising here propagates out to the caller - admin.cli's
    seed-taxonomy command never reaches its own `db_session.commit()`, and
    closing the session on the way out rolls back everything this call
    flushed.
    """
    created = {
        "destination_group": 0, "destination": 0, "sector": 0,
        "food_category": 0, "food_item": 0, "metric": 0, "unit_preset": 0,
    }

    for code, name, is_waste, sort_order in DESTINATION_GROUPS:
        created["destination_group"] += _ensure(
            session, DestinationGroup, code,
            name=name, is_waste=is_waste, sort_order=sort_order,
        )
    session.flush()  # destination rows need the group ids

    groups = {
        g.code: g.id for g in session.scalars(select(DestinationGroup)).all()
    }
    for group_code, code, name, is_prevention, sort_order in DESTINATIONS:
        created["destination"] += _ensure(
            session, Destination, code,
            group_id=groups[group_code], name=name,
            is_prevention=is_prevention, sort_order=sort_order,
        )

    for code, name, sort_order in SECTORS:
        created["sector"] += _ensure(
            session, Sector, code, name=name, sort_order=sort_order
        )

    for code, name, is_standard_mix, sort_order in FOOD_CATEGORIES:
        created["food_category"] += _ensure(
            session, FoodCategory, code,
            name=name, is_standard_mix=is_standard_mix, sort_order=sort_order,
        )

    #: After the categories, because every row names one as its parent. The
    #: lookup is by code against what this same function has just ensured, so a
    #: vocabulary row can never point at a category that does not exist.
    categories = {
        row.code: row.id
        for row in session.scalars(select(FoodCategory)).all()
    }
    for code, name, category_code, sort_order in FOOD_ITEMS:
        parent = categories.get(category_code)
        assert parent is not None, (
            f"food_item {code!r} names food_category {category_code!r}, which "
            "this seed does not create"
        )
        created["food_item"] += _ensure(
            session, FoodItem, code,
            name=name, food_category_id=parent, sort_order=sort_order,
        )

    for code, name, unit, display_unit, precision, sort_order in METRICS:
        created["metric"] += _ensure(
            session, Metric, code, name=name, unit=unit,
            display_unit=display_unit, display_precision=precision,
            sort_order=sort_order,
        )

    #: `food_category_id` stays NULL on every row: a wheelie bin is a wheelie
    #: bin, and this seed has no per-food density to put on one. The column is
    #: for a preset whose conversion is only true of one category — see the
    #: docstring on `UnitPreset` — and the front end narrows the list it offers
    #: by the category chosen at step 2, so a row added there appears only for
    #: that category. Nothing here may claim one without a measurement behind it.
    for code, label, kg_per_unit, source_note in UNIT_PRESETS:
        created["unit_preset"] += _ensure(
            session, UnitPreset, code, label=label, kg_per_unit=kg_per_unit,
            food_category_id=None, source_note=source_note,
        )

    # Flush so the checks below see every row this call just staged, then
    # refuse to hand back a taxonomy the panel itself would not accept - see
    # this function's own docstring for why nothing else stands guard here.
    session.flush()
    check_single_standard_mix(session)
    check_prevention_destination(session)

    return created
