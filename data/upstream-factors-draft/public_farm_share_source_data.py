"""Public data used to fill the five ReFED-missing Farm-stage upstream rows.

``build_upstream_factors_draft.py`` needs a primary-production ("Farm") share
of the total embodied footprint for five New Zealand food categories --
``meat``, ``seafood``, ``dairy``, ``bakery_grains`` and ``beverages`` -- for
which ReFED's own published factors carry no Farm-stage value at all (see
that script's module docstring). This module holds the one public source used
to fill that gap for the ``co2e`` metric, transcribed row by row, exactly as
published. Nothing here is estimated or interpolated.

SOURCE (READ 2026-09-05)
------------------------
Poore, J. & Nemecek, T. (2018), "Reducing food's environmental impacts
through producers and consumers", *Science* 360(6392):987-992 -- the
supply-chain-stage breakdown of greenhouse gas emissions per kilogram of
product (Supplementary Data S2), republished as a machine-readable table by
Our World in Data:

    https://ourworldindata.org/grapher/food-emissions-supply-chain

fetched as CSV on 2026-09-05
(``https://ourworldindata.org/grapher/food-emissions-supply-chain.csv``).
Units: kg CO2-eq per kg of product, at the point the product leaves retail
(i.e. before any consumer-stage cooking or storage losses -- see the caution
below). Eight stages are published per product: Land Use Change, Farm,
Animal Feed, Processing, Transport, Retail, Packaging and Losses ("Losses"
is the embodied footprint of food lost earlier in the chain, not a
supply-chain stage in its own right, and is excluded from the farm-share
calculation below for that reason).

WHAT "FARM SHARE" MEANS HERE
-----------------------------
``build_upstream_factors_draft.py`` computes, for each product row below,

    farm_share = (land_use + farm + animal_feed)
                 / (land_use + farm + animal_feed + processing + transport
                    + retail + packaging)

-- i.e. the fraction of the farm-to-retail total (excluding "Losses") that is
attributable to on-farm production, land-use change and feed-crop growing,
all of which happen before the farm gate. ``Processing``, ``Transport``,
``Retail`` and ``Packaging`` are downstream of it. This is an approximation,
stated plainly: it is calculated at a farm-to-*retail* boundary because that
is what this source publishes, and is then applied to the client's own
farm-to-*shelf* (or, for ``beverages``, farm-to-*fork*) total in the build
script -- which for ``beverages`` means the resulting primary-production
figure is very likely a slight overstatement of the true farm share, since
"farm to fork" includes further consumer-stage steps this source does not
itemise. That overstatement is recorded in the build script's own
``source_note`` for every row it produces this way, not hidden here.

NO PUBLIC PER-STAGE BREAKDOWN OF *WATER* USE WAS FOUND
-------------------------------------------------------
This source is a greenhouse-gas breakdown only. Our World in Data's water
figures for food (e.g. "Freshwater withdrawals per kilogram of food
product", https://ourworldindata.org/grapher/water-withdrawals-per-kg-poore)
report a single farm-to-retail total per product, not a stage breakdown, so
no equivalent "water farm share" can be read off it the way the co2e share
above can. Rather than invent a citation for a number this source does not
give, the build script uses an explicit, stated assumption for water's
primary-production share instead (see its own docstring and every affected
row's ``source_note``) -- motivated by, but not computed from, the
frequently-reported finding that irrigation dominates global agricultural
freshwater withdrawal (the same Poore & Nemecek dataset, as summarised in Our
World in Data's food FAQ: "transport accounts for just 5% of greenhouse gas
emissions from food" and, separately, that irrigation accounts for roughly
two-thirds of global freshwater withdrawals). That is general context, not a
per-product figure, which is exactly why it grounds an assumption rather
than a sourced value.

WHICH NEW ZEALAND CATEGORY DRAWS ON WHICH ROW(S)
--------------------------------------------------
See ``FOOD_SHARE_SOURCES`` in ``build_upstream_factors_draft.py`` for the
mapping and the unweighted-mean rule applied where more than one row is used
(the same rule the rest of this draft uses throughout, for the same reason:
no production weights were supplied for anything).
"""

from __future__ import annotations

from decimal import Decimal
from typing import NamedTuple


class StageRow(NamedTuple):
    product: str              # Our World in Data's own product label
    land_use: Decimal          # food_emissions_land_use
    farm: Decimal              # food_emissions_farm
    animal_feed: Decimal        # food_emissions_animal_feed
    processing: Decimal        # food_emissions_processing
    transport: Decimal          # food_emissions_transport
    retail: Decimal             # food_emissions_retail
    packaging: Decimal          # food_emissions_packaging
    losses: Decimal             # food_emissions_losses -- not used in farm_share


def _d(value: str) -> Decimal:
    return Decimal(value)


# Transcribed verbatim from the CSV export named in the module docstring,
# for the twelve product rows this draft's five gap-filling categories use.
STAGE_ROWS: dict[str, StageRow] = {
    row.product: row
    for row in [
        StageRow("Beef (beef herd)", _d("23.237535"), _d("56.22806"), _d("2.6809785"), _d("1.811083"), _d("0.4941246"), _d("0.23353784"), _d("0.35208446"), _d("14.439998")),
        StageRow("Pig Meat", _d("2.2440686"), _d("2.4770977"), _d("4.298476"), _d("0.41584185"), _d("0.50126386"), _d("0.27809602"), _d("0.43228632"), _d("1.6585511")),
        StageRow("Poultry Meat", _d("3.5084288"), _d("0.9278218"), _d("2.452357"), _d("0.6073727"), _d("0.3810783"), _d("0.24397661"), _d("0.29270238"), _d("1.452086")),
        StageRow("Lamb & Mutton", _d("0.648247"), _d("27.02575"), _d("3.2831702"), _d("1.5395962"), _d("0.6788543"), _d("0.30081406"), _d("0.34760362"), _d("5.8982344")),
        StageRow("Fish (farmed)", _d("1.1947172"), _d("8.056115"), _d("1.8344203"), _d("0.04459863"), _d("0.24795863"), _d("0.08997562"), _d("0.13753739"), _d("2.0271227")),
        StageRow("Shrimps (farmed)", _d("0.33056307"), _d("13.453979"), _d("4.0299387"), _d("0"), _d("0.33085158"), _d("0.3523611"), _d("0.5361473"), _d("7.832022")),
        StageRow("Cheese", _d("4.4674764"), _d("13.095539"), _d("2.3530576"), _d("0.7403863"), _d("0.13855666"), _d("0.33333775"), _d("0.17209093"), _d("2.5771365")),
        StageRow("Milk", _d("0.5122225"), _d("1.508862"), _d("0.24381407"), _d("0.15418904"), _d("0.0932354"), _d("0.26514977"), _d("0.100180455"), _d("0.2740663")),
        StageRow("Wheat & Rye", _d("0.09713003"), _d("0.82030016"), _d("0"), _d("0.21012877"), _d("0.12544097"), _d("0.05604907"), _d("0.086838976"), _d("0.1779351")),
        StageRow("Wine", _d("-0.061264314"), _d("0.6260269"), _d("0"), _d("0.13862641"), _d("0.093284145"), _d("0.039322365"), _d("0.7492315"), _d("0.20353575")),
        StageRow("Coffee", _d("3.8187985"), _d("10.75444"), _d("0"), _d("0.61318517"), _d("0.13452813"), _d("0.052486792"), _d("1.6874963"), _d("11.466971")),
        StageRow("Soy milk", _d("0.17997183"), _d("0.09276804"), _d("0"), _d("0.1625091"), _d("0.10983519"), _d("0.27017248"), _d("0.09839521"), _d("0.06152727")),
    ]
}


def farm_share(product: str) -> Decimal:
    """(land_use + farm + animal_feed) / (that + processing + transport +
    retail + packaging) for one product row -- see the module docstring."""
    row = STAGE_ROWS[product]
    numerator = row.land_use + row.farm + row.animal_feed
    denominator = numerator + row.processing + row.transport + row.retail + row.packaging
    return numerator / denominator
