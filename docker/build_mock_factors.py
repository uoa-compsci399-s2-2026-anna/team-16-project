"""Generate ``docker/mock-factors.json`` from ``admin/seed.py``'s taxonomy.

    python docker/build_mock_factors.py            # rewrite the file
    python docker/build_mock_factors.py --check    # fail if it is out of date

WHY THIS FILE EXISTS, AND IT IS NOT THAT HAND-EDITING WAS TEDIOUS.

``docker/mock-factors.json`` is the factor set the container images ship with:
``docker/init.sh`` loads and publishes it so that a first ``docker compose up``
produces a calculator that answers. ``db/repository.get_taxonomy`` narrows the
form **to what the published set prices**, so that file decides what a fresh
install can actually offer.

It had fallen a long way behind ``admin/seed.py``. Measured on 2026-10-06
against a stack brought up from the committed tree:

====================  =========================  ==========================
dimension             seeded by ``admin/seed``   priced by the mock set
====================  =========================  ==========================
sectors               6                          3
food categories       10                         3
destinations          14 (13 + ``prevention``)   6
====================  =========================  ==========================

So a fresh install offered a third of its own form, and the rest of it was
invisible rather than broken -- there is no error anywhere, the controls simply
are not drawn. Three consequences, in rising order of cost:

* a new team member's stack could not reach most of the calculator;
* a visitor to a fresh deployment could not name meat, fruit, seafood, grains,
  staples, beverages or nuts, or send waste to nine of its thirteen
  destinations;
* and **twelve browser cases failed on the browser job's first real run**,
  because ``tests/web`` was written against a developer's database -- where the
  client's draft set is published and prices all ten categories -- and asserts
  things like "the step lists every applicable destination and the seeded
  taxonomy has thirteen". That assertion is true of the seed and was false of
  the published set, and nothing connected the two.

**The durable part of the fix is this file plus
``test_the_mock_factor_set_prices_everything_the_seed_creates``, not the extra
rows.** Rows go stale the next time ``admin/seed.py`` gains a category -- which
has happened repeatedly: §2.1's prose still says eight categories where the seed
carries nine, and v1.72 took the food vocabulary from nineteen names to
forty-seven. A generator reads the seed, so it cannot fall behind it, and the
test fails the moment the two disagree.

WHAT THE NUMBERS ARE, SAID PLAINLY.

Every value here is a placeholder: open item O-1, the client has supplied no
confirmed New Zealand emissions factors. ``is_mock`` stays ``true``, which is
what keeps the non-dismissible placeholder-data banner on every results view
and export.

**Nothing is invented.** The six authored figures that were already in the file
are kept byte for byte, and every row this script adds takes one of them by a
rule stated on the row itself:

* an upstream row for a food category nobody authored takes the **mixed-waste**
  figure, so meat, fruit and beverages all price the same;
* a downstream row for a destination nobody authored takes the figure of a
  destination its **own group** already prices -- reuse from ``animal_feed``,
  recovery from ``compost``, disposal from ``landfill``.

The first of those is deliberately visible. A fresh install's results page
shows the same impact for a kilogram of meat as for a kilogram of fruit, and
that is the correct appearance for a set with no category data in it: inventing
a plausible spread would hide the placeholder behind numbers that look like
measurements. The banner says the data is placeholder; the figures should not
contradict it.

WHICH METRICS GET ROWS.

``mass`` is ``qty_kg`` and needs no factors. ``land`` has no formula in this
set. ``ch4`` and ``cost`` are destination properties, so they are priced
downstream and not upstream -- the ``ch4`` formula's own note says the upstream
term is zero throughout. That leaves ``co2e`` and ``water`` upstream, and
``co2e``, ``ch4`` and ``cost`` downstream, which is the shape the file already
had; this script widens it rather than changing it.

WHAT PUBLICATION REQUIRES, AND IS CHECKED HERE BEFORE IT IS CHECKED THERE.

``db.repository.publish_factor_set`` runs five guards, and ``init.sh`` is under
``set -e``, so a set that fails any of them stops the migrate service, which
stops api and admin: **the whole stack refuses to start.** Two of the five bear
on what this script emits:

* O-7 (``_refuse_incomplete_prevention``): every ``(sector, food_category,
  metric)`` carrying a general upstream row -- ``destination`` null, meaning
  every destination -- needs a ``prevention`` row at 0 against the same triple,
  or a prevented line is still charged its production burden. Note the key:
  ``food_item`` is **not** in it, which is why the twelve item-level dairy rows
  need no counterparts of their own and why the file published before this
  change;
* ``refuse_nonzero_prevention_factors``: those rows must be exactly 0, upstream
  and downstream alike.

So each general upstream row is emitted with its prevention twin, in the same
loop, and the twin's value is a literal zero.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from admin.seed import DESTINATIONS, FOOD_CATEGORIES, SECTORS  # noqa: E402

TARGET = HERE / "mock-factors.json"

#: The two metrics a production burden is expressed in. See WHICH METRICS GET
#: ROWS above for why the other four are absent rather than overlooked.
UPSTREAM_METRICS = ("co2e", "water")

#: The three a destination is priced in.
DOWNSTREAM_METRICS = ("co2e", "ch4", "cost")

#: WHERE THE PLACEHOLDER NUMBERS COME FROM, cited rather than alluded to.
#:
#: Both URLs are already committed elsewhere in this repository -- the first in
#: `docs/refed-comparison.md` and `tests/benchmark/refed/`, the second in
#: `docs/upstream-factors-draft.md` -- so naming them here records provenance
#: this project already holds rather than asserting anything new.
#:
#: **They also restore something a test depends on, and that is worth saying
#: because it looks like a coincidence and is not.**
#: `test_horizontal_overflow.py` measures whether `/methodology.html` wraps an
#: unbreakable token or widens its row, and it carries a second case --
#: `test_the_factor_notes_still_carry_the_token_that_caused_the_overflow` --
#: whose whole job is to fail when the published set stops containing one. Its
#: docstring says why: "Publish a set whose notes are three short words and
#: every assertion above passes on a page that was never capable of
#: overflowing." The overflow it was written against came from the client
#: draft's notes, which cite file paths; MOCK-v0's notes cited nothing, so on a
#: fresh stack both cases failed -- the precondition case reporting that the
#: longest token was 20 characters against the 60 it needs.
#:
#: The fix is not to lengthen a string until a test passes. It is that a factor
#: set's notes should say where its figures came from, which this set's did not,
#: and a set that cites its sources carries long tokens as a matter of course.
#: That is the condition the page has to cope with in production.
REFED_SOURCE = (
    "https://refed-roadmap.s3-us-west-2.amazonaws.com/csv/public_downloads/"
    "impact_calculator/impact_calculator_conversion_factors.csv"
)

#: The New Zealand waste disposal levy, which is the whole of the `cost` metric
#: under O-2 as closed at contract v1.48 (levy plus disposal cost; the value of
#: the food itself stays a constant at zero because that was the decision).
MFE_LEVY_SOURCE = (
    "https://environment.govt.nz/what-government-is-doing/areas-of-work/waste/"
    "waste-disposal-levy/expansion/"
)

#: A `Decimal(38, 10)` column, and the file has always spelled its values at ten
#: decimal places. Kept so a regeneration is not a whitespace diff on every row.
PLACES = 10


def _fixed(value: str) -> str:
    """``"1.9"`` -> ``"1.9000000000"``, idempotent on an already-padded value."""
    whole, _, frac = value.partition(".")
    return f"{whole}.{frac[:PLACES].ljust(PLACES, '0')}"


#: The authored upstream figures, by food category. These six numbers are the
#: whole of this set's real content and are reproduced from the file as it stood
#: before this script existed; the script's job is to spread them, not to add to
#: them.
AUTHORED_UPSTREAM = {
    "dairy": {"co2e": "1.9", "water": "1020"},
    "vegetables": {"co2e": "0.45", "water": "322"},
    "standard_mix": {"co2e": "2.6", "water": "640"},
}

#: The category an unauthored one borrows from. Mixed waste, because a set with
#: no category data should look like one.
FALLBACK_CATEGORY = "standard_mix"

#: `data_quality` as the authored rows spell it, by category. A `String(32)`
#: column, so these stay short.
AUTHORED_QUALITY = {"co2e": "proxy-US", "water": "modelled"}

#: The authored downstream figures, by destination. `None` where the authored
#: set priced that destination for that metric and nothing else.
AUTHORED_DOWNSTREAM = {
    "landfill": {"co2e": "0.7", "ch4": "0.027", "cost": "0.06"},
    "compost": {"co2e": "0.21", "ch4": "0.0002", "cost": "0.035"},
    "anaerobic_digestion": {"co2e": "0.07", "ch4": "0.001", "cost": "0.045"},
    "animal_feed": {"co2e": "-0.15"},
    "not_harvested": {"co2e": "0.12"},
}

#: Which authored destination each group borrows from. One per group, chosen as
#: the destination in that group the authored set priced most fully.
GROUP_REPRESENTATIVE = {
    "reuse": "animal_feed",
    "recycle_recovery": "compost",
    "disposal": "landfill",
}

#: What the representative does not price. `animal_feed` carries only a `co2e`
#: offset, so reuse takes an explicit zero for the other two rather than no row:
#: redistributed or re-eaten food releases no methane at a destination it never
#: reaches, and it pays no levy. A zero that is meant is worth stating, and the
#: engine's "then zero" fallback cannot distinguish a meant zero from a hole.
REPRESENTATIVE_GAPS = {"animal_feed": {"ch4": "0", "cost": "0"}}

#: The one category-specific downstream row the authored set carried, kept
#: because it is what demonstrates the §2.2 lookup order -- exact category, then
#: the null row, then zero -- in the shipped data rather than only in the tests.
AUTHORED_CATEGORY_SPECIFIC = [
    {
        "destination": "landfill",
        "sector": None,
        "food_category": "dairy",
        "metric": "co2e",
        "value_per_kg": _fixed("0.99"),
        "source_note": (
            "PLACEHOLDER. A category-specific row: it wins over the generic "
            "landfill row below for dairy. Source: "
            "https://refed-roadmap.s3-us-west-2.amazonaws.com/csv/"
            "public_downloads/impact_calculator/"
            "impact_calculator_conversion_factors.csv"
        ),
        "data_quality": "proxy-US",
    }
]

#: The twelve item-level dairy rows, which are the only place this set says
#: anything a category average does not. Reproduced verbatim: they are authored
#: data, and `refuse_item_level_without_item_rows` needs at least one of them to
#: exist while `item_level_enabled` is true.
AUTHORED_ITEM_ROWS = [
    ("cheese", "co2e", "3.5576709797"),
    ("milk", "co2e", "0.5303142329"),
    ("cream", "co2e", "1.7384473198"),
    ("butter", "co2e", "4.0001848429"),
    ("yoghurt", "co2e", "1.1554528651"),
    ("other_dairy", "co2e", "0.4179297597"),
    ("cheese", "water", "1453.0293583713"),
    ("milk", "water", "153.9412832575"),
    ("cream", "water", "1453.0293583713"),
    ("butter", "water", "1453.0293583713"),
    ("yoghurt", "water", "1453.0293583713"),
    ("other_dairy", "water", "153.9412832575"),
]

ITEM_ROW_SECTOR = "processing"
ITEM_ROW_CATEGORY = "dairy"
ITEM_ROW_NOTE = (
    "PLACEHOLDER. An item-level row, derived from the relativities in the "
    "client's own table rather than from a New Zealand measurement. It exists "
    "so that step 2.5 has something to price and so that `item_level_enabled` "
    "has rows behind it."
)


def _sector_codes() -> list[str]:
    return [code for code, *_ in SECTORS]


def _category_codes() -> list[str]:
    return [code for code, *_ in FOOD_CATEGORIES]


def _destinations() -> list[tuple[str, str, bool]]:
    """``(code, group_code, is_prevention)`` for every seeded destination."""
    return [(code, group, bool(is_prevention))
            for group, code, _name, is_prevention, _sort in DESTINATIONS]


def _upstream_rows() -> list[dict]:
    rows: list[dict] = []
    for sector in _sector_codes():
        for category in _category_codes():
            authored = AUTHORED_UPSTREAM.get(category)
            basis = authored or AUTHORED_UPSTREAM[FALLBACK_CATEGORY]
            for metric in UPSTREAM_METRICS:
                if authored is not None:
                    note = (
                        f"PLACEHOLDER derived from ReFED (United States). Not a "
                        f"New Zealand figure. Applied to every sector: this set "
                        f"holds one {metric} figure for {category} and no "
                        f"sector variation. Source: {REFED_SOURCE}"
                    )
                    quality = AUTHORED_QUALITY[metric]
                else:
                    note = (
                        f"PLACEHOLDER. No figure was authored for {category}, so "
                        f"it takes the mixed-waste one -- which means a fresh "
                        f"install prices a kilogram of {category} exactly as it "
                        f"prices a kilogram of anything else. That is the honest "
                        f"appearance of a set with no category data in it (O-1); "
                        f"a plausible spread here would read as measurement."
                    )
                    quality = "placeholder-undifferentiated"
                rows.append({
                    "sector": sector,
                    "food_category": category,
                    "food_item": None,
                    "destination": None,
                    "metric": metric,
                    "value_per_kg": _fixed(basis[metric]),
                    "source_note": note,
                    "data_quality": quality,
                })

    # The item-level rows sit beside the general row for their own triple, which
    # `refuse_item_rows_without_category_fallback` requires to exist.
    for item, metric, value in AUTHORED_ITEM_ROWS:
        rows.append({
            "sector": ITEM_ROW_SECTOR,
            "food_category": ITEM_ROW_CATEGORY,
            "food_item": item,
            "destination": None,
            "metric": metric,
            "value_per_kg": _fixed(value),
            "source_note": ITEM_ROW_NOTE,
            "data_quality": "proxy-client-relativities",
        })

    # O-7's twin for every general row, emitted in the same pass so the two
    # cannot drift. The key is (sector, food_category, metric) -- no food_item -
    # so one twin covers the item rows of its triple as well.
    prevention = _prevention_code()
    twins = []
    for sector in _sector_codes():
        for category in _category_codes():
            for metric in UPSTREAM_METRICS:
                twins.append({
                    "sector": sector,
                    "food_category": category,
                    "food_item": None,
                    "destination": prevention,
                    "metric": metric,
                    "value_per_kg": _fixed("0"),
                    "source_note": (
                        "O-7. Zero against a prevention destination, so a "
                        "prevented line is not charged its production burden "
                        "either. `publish_factor_set` refuses a set in which "
                        "any general upstream row lacks this twin."
                    ),
                    "data_quality": "definitional",
                })
    return rows + twins


def _prevention_code() -> str:
    for code, _group, is_prevention in _destinations():
        if is_prevention:
            return code
    raise RuntimeError(
        "admin/seed.py seeds no destination with is_prevention set, so there is "
        "nothing for O-7's upstream twin to point at and nothing could publish."
    )


def _downstream_rows() -> list[dict]:
    rows: list[dict] = list(AUTHORED_CATEGORY_SPECIFIC)
    for code, group, is_prevention in _destinations():
        if is_prevention:
            for metric in DOWNSTREAM_METRICS:
                rows.append({
                    "destination": code,
                    "sector": None,
                    "food_category": None,
                    "metric": metric,
                    "value_per_kg": _fixed("0"),
                    "source_note": (
                        "The 100% offset. Zero by definition, and "
                        "`refuse_nonzero_prevention_factors` refuses anything "
                        "else."
                    ),
                    "data_quality": "definitional",
                })
            continue

        representative = GROUP_REPRESENTATIVE[group]
        authored = AUTHORED_DOWNSTREAM.get(code, {})
        borrowed = AUTHORED_DOWNSTREAM[representative]
        gaps = REPRESENTATIVE_GAPS.get(representative, {})
        for metric in DOWNSTREAM_METRICS:
            if metric in authored:
                value = authored[metric]
                source = MFE_LEVY_SOURCE if metric == "cost" else REFED_SOURCE
                note = (
                    f"PLACEHOLDER. Order-of-magnitude only, and not a New "
                    f"Zealand figure. Source: {source}"
                )
                quality = "proxy-US"
            elif metric in borrowed:
                value = borrowed[metric]
                source = MFE_LEVY_SOURCE if metric == "cost" else REFED_SOURCE
                note = (
                    f"PLACEHOLDER. No {metric} figure was authored for {code}, "
                    f"so it takes the one its own group carries, from "
                    f"{representative}. The group is the most this set can "
                    f"distinguish; per-destination data arrives with O-1. "
                    f"Source of the borrowed figure: {source}"
                )
                quality = "placeholder-by-group"
            elif metric in gaps:
                value = gaps[metric]
                note = (
                    f"Zero, and meant rather than missing: {group} food is "
                    f"eaten or re-eaten, so it releases no {metric} at a "
                    f"destination it never reaches and pays no levy. Stated as "
                    f"a row because the engine's fall-through to zero cannot "
                    f"tell a meant zero from a hole."
                )
                quality = "definitional"
            else:  # pragma: no cover - every group representative covers these
                raise RuntimeError(
                    f"{code} has no {metric} figure, its group representative "
                    f"{representative} has none either, and no gap is declared "
                    f"for it -- so the row would be absent and the destination "
                    f"would price at zero without saying so."
                )
            rows.append({
                "destination": code,
                "sector": None,
                "food_category": None,
                "metric": metric,
                "value_per_kg": _fixed(value),
                "source_note": note,
                "data_quality": quality,
            })
    return rows


def build() -> dict:
    """The whole file, with everything but the two factor tables preserved."""
    current = json.loads(TARGET.read_text(encoding="utf-8"))
    built = dict(current)
    built["upstream"] = _upstream_rows()
    built["downstream"] = _downstream_rows()
    return built


def serialise(document: dict) -> str:
    """``indent=2``, ``ensure_ascii=False``, trailing newline -- as committed."""
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true",
        help="exit 1 if the committed file differs from what this would write",
    )
    args = parser.parse_args(argv)

    text = serialise(build())
    if args.check:
        if TARGET.read_text(encoding="utf-8") == text:
            print(f"{TARGET.name} is up to date with admin/seed.py")
            return 0
        print(
            f"{TARGET.name} is NOT what admin/seed.py would produce. Run "
            f"`python docker/build_mock_factors.py` and commit the result.",
            file=sys.stderr,
        )
        return 1

    TARGET.write_text(text, encoding="utf-8", newline="")
    document = json.loads(text)
    print(
        f"wrote {TARGET.name}: {len(document['upstream'])} upstream rows, "
        f"{len(document['downstream'])} downstream rows, across "
        f"{len(_sector_codes())} sectors x {len(_category_codes())} categories "
        f"x {len(_destinations())} destinations"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
