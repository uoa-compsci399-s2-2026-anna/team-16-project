"""Public land-use-per-kilogram figures, used to check the client's land column.

The client's *Rawtec calculations* document publishes a land column, and it is
the loosest thing in the document. It is published as **t/ha**, which is a
*yield* rather than a footprint -- see ``build_upstream_factors_draft.py``'s
``land_m2_per_kg_from_yield()`` for the inversion -- and once inverted several
of its rows are implausible: ``Poultry`` at 57.48 t/ha becomes 0.17 m2/kg, a
vegetable's footprint rather than a chicken's, and ``Nuts and seeds`` carries
Red Meat's ``0.22`` to two decimal places, which is a copy rather than a
measurement.

This module holds one public source against which every one of the client's
land cells is checked. Nothing here is estimated, averaged or interpolated;
the averaging that *is* done -- an unweighted mean over the proxy rows chosen
for a client food -- happens in the build script, beside the mapping that
chooses them, exactly as ``public_farm_share_source_data.py`` already does for
the co2e farm share.

SOURCE (READ 2026-09-23)
------------------------
Poore, J. & Nemecek, T. (2018), "Reducing food's environmental impacts
through producers and consumers", *Science* 360(6392):987-992 -- the same
study ``public_farm_share_source_data.py`` already draws the greenhouse-gas
supply-chain breakdown from, republished as a machine-readable table by Our
World in Data:

    https://ourworldindata.org/grapher/land-use-per-kg-poore

fetched as CSV on 2026-09-23
(``https://ourworldindata.org/grapher/land-use-per-kg-poore.csv``), and its
accompanying metadata document
(``https://ourworldindata.org/grapher/land-use-per-kg-poore.metadata.json``,
read the same day) read for the unit rather than assumed from the chart
title.

**Units: m2 per kilogram**, stated by the source itself --
``"shortUnit": "m²"``, ``"unit": "m² per kilogram"``, and in the chart's own
subtitle: *"Land use is measured in meters squared (m2) for one year to
produce one kilogram of a given food product."* That is the same quantity
this system's ``land`` metric reports, so **no unit conversion is applied to
anything in this file**; the only arithmetic anywhere near it is the
inversion of the *client's* t/ha column, which is in the build script.

Every row is dated 2010 in the source (the study's own reference year) and
the variable's ``lastUpdated`` is 2019-10-08. The figures are **global
means**, not New Zealand figures, which is the single most important caveat
about them and is repeated in every ``source_note`` that uses one: they are a
sanity check and a replacement of last resort, not a New Zealand measurement.
A New Zealand-specific land figure would be better than either column here and
neither the client nor this repository has one -- that is still **O-1**.

WHY A DIFFERENT PRODUCT VOCABULARY FROM ``public_farm_share_source_data.py``
----------------------------------------------------------------------------
Both tables come from the same study and Our World in Data publishes them as
two separate charts with two separate product lists. They are *nearly* the
same list but not identical, and the difference is not cosmetic: the
greenhouse-gas breakdown names ``Shrimps (farmed)`` where this table names
``Prawns (farmed)``, and this table carries ``Barley``, ``Oatmeal``,
``Beet Sugar`` and ``Cane Sugar``, which the other does not. Transcribing
this chart's own labels rather than reusing the other module's is what keeps
a lookup here from failing silently against a name that exists in the
neighbouring file.

WHAT IS TRANSCRIBED
--------------------
**All thirty-eight published rows**, not only the ones the build script's
proxy mapping happens to use today. ``public_farm_share_source_data.py``
carries only the twelve rows it needs, and that is right for a stage
breakdown nothing else will ever read; this table is a *reference column* that
a future maintainer will want to re-map against -- the mapping from a client
food to one or more of these products is a judgement, recorded in
``LAND_PUBLIC_PROXIES`` in the build script, and the next person to revisit it
should be choosing from the whole published list rather than from the subset
somebody once used.
"""

from __future__ import annotations

from decimal import Decimal


def _d(value: str) -> Decimal:
    return Decimal(value)


#: Our World in Data's own product label -> land use in **m2 per kilogram**,
#: transcribed verbatim from the CSV export named in the module docstring.
#: Every row is the source's 2010 figure; the source publishes no other year.
LAND_M2_PER_KG: dict[str, Decimal] = {
    "Apples": _d("0.63"),
    "Bananas": _d("1.93"),
    "Barley": _d("1.11"),
    "Beef (beef herd)": _d("326.21"),
    "Beef (dairy herd)": _d("43.24"),
    "Beet Sugar": _d("1.83"),
    "Berries & Grapes": _d("2.41"),
    "Brassicas": _d("0.55"),
    "Cane Sugar": _d("2.04"),
    "Cassava": _d("1.81"),
    "Cheese": _d("87.79"),
    "Citrus Fruit": _d("0.86"),
    "Coffee": _d("21.62"),
    "Dark Chocolate": _d("68.96"),
    "Eggs": _d("6.27"),
    "Fish (farmed)": _d("8.41"),
    "Groundnuts": _d("9.11"),
    "Lamb & Mutton": _d("369.81"),
    "Maize": _d("2.94"),
    "Milk": _d("8.95"),
    "Nuts": _d("12.96"),
    "Oatmeal": _d("7.6"),
    "Onions & Leeks": _d("0.39"),
    "Other Fruit": _d("0.89"),
    "Other Pulses": _d("15.57"),
    "Other Vegetables": _d("0.38"),
    "Peas": _d("7.46"),
    "Pig Meat": _d("17.36"),
    "Potatoes": _d("0.88"),
    "Poultry Meat": _d("12.22"),
    "Prawns (farmed)": _d("2.97"),
    "Rice": _d("2.8"),
    "Root Vegetables": _d("0.33"),
    "Soy milk": _d("0.66"),
    "Tofu": _d("3.52"),
    "Tomatoes": _d("0.8"),
    "Wheat & Rye": _d("3.85"),
    "Wine": _d("1.78"),
}


def land_use(product: str) -> Decimal:
    """One published product row, in m2 per kilogram.

    Raises rather than returning a default: a misspelled product name that
    resolved to zero would be a silent substitution of "no land at all" for a
    real figure, which is the defect class this whole draft exists to remove.
    """
    try:
        return LAND_M2_PER_KG[product]
    except KeyError:
        raise KeyError(
            f"No Our World in Data land-use row named {product!r}. Note that "
            "this chart's product vocabulary is not identical to the "
            "greenhouse-gas breakdown's in public_farm_share_source_data.py "
            "-- 'Prawns (farmed)' here is 'Shrimps (farmed)' there."
        ) from None
