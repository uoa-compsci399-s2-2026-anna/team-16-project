"""``docker/mock-factors.json``'s three client equivalences, against the
values ``data/upstream-factors-draft/build_upstream_factors_draft.py``
produces.

**Why this file exists.** `docker/mock-factors.json` is what
`docker/seed_mock_factors.py` loads into `MOCK-v0` on any fresh checkout --
it is the file the client's own conversions actually reach a deployment
through, and until now nothing in the repository read it at all
(`grep -rl mock-factors.json tests/` returned nothing). `a208b95`'s own
commit message states the three rows it added -- `vehicles_year`,
`olympic_pools`, `meals` -- are "the same codes, factors and source_note
text" `build_upstream_factors_draft.py` already emits into
`upstream_factors_draft.json`; nothing enforced that claim, so a
transcription slip in either file could drift from the other silently.

**Wider than its name since 2026-10-07.** The three equivalences were the
first thing anything in the repository read out of that file; the tests at the
foot of this module now also hold what it *covers*, because the file had
fallen a long way behind ``admin/seed.py`` and nothing said so. The name is
left alone rather than churned -- what the file is about is this one JSON
document either way.

**Decimal, not string.** The two files legitimately differ in notation --
`docker/mock-factors.json` writes `"0.0000004000"`, the draft JSON writes
`"4.000E-7"` -- while being numerically identical. A string comparison would
fail on that difference for the wrong reason; parsing both through
`decimal.Decimal` and comparing the parsed values is what actually proves
"the same three conversions", which is what these two files are meant to be.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MOCK_FACTORS = REPO_ROOT / "docker" / "mock-factors.json"
DRAFT_FACTORS = REPO_ROOT / "data" / "upstream-factors-draft" / "upstream_factors_draft.json"

#: The three conversions the client's own document supplies (`a208b95`,
#: `b35bb1f`). `km_driven` is deliberately excluded -- it has no counterpart
#: in the draft file at all (see item 6 of the final review; its own
#: `source_note` is still a placeholder, not the client's).
CLIENT_EQUIVALENCE_CODES = ("vehicles_year", "olympic_pools", "meals")

#: The two metric families the set prices, which is a property of the shipped
#: file rather than of the schema: `mass` is `qty_kg` and needs no factors,
#: `land` has no formula in this set, and `ch4` and `cost` are destination
#: properties priced downstream. See `docker/build_mock_factors.py`.
UPSTREAM_METRICS = ("co2e", "water")

#: What `tests/web/test_horizontal_overflow.py` needs to find on
#: `/methodology.html` for its measurement to mean anything. Spelled here too,
#: so the precondition fails in the fast job rather than only in the browser one.
UNBREAKABLE_MIN = 60


def _equivalences_by_code(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {row["code"]: row for row in data["equivalences"]}


def test_mock_factors_carries_every_client_equivalence():
    """The premise the value-level assertions below rest on: all three codes
    are actually present in the file a fresh deployment loads."""
    mock = _equivalences_by_code(MOCK_FACTORS)
    missing = [code for code in CLIENT_EQUIVALENCE_CODES if code not in mock]
    assert not missing, f"docker/mock-factors.json is missing {missing}"


def test_mock_factors_and_the_draft_builder_specify_the_same_three_conversions():
    """Compares `value_per_unit` as `Decimal`, never as the JSON's own string
    -- `"0.0000004000"` and `"4.000E-7"` are the same division of 1 by
    2,500,000, and only a `Decimal` comparison sees that. `name`,
    `label_template`, `source_metric` and `source_note` are compared as
    text: `source_note` is the client's verbatim wording (§7.6 rule 9) and
    must be byte-for-byte the same string wherever it is copied, not merely
    numerically equivalent.
    """
    mock = _equivalences_by_code(MOCK_FACTORS)
    draft = _equivalences_by_code(DRAFT_FACTORS)

    for code in CLIENT_EQUIVALENCE_CODES:
        mock_row = mock[code]
        draft_row = draft[code]
        assert Decimal(mock_row["value_per_unit"]) == Decimal(draft_row["value_per_unit"]), (
            f"{code}: mock-factors.json's {mock_row['value_per_unit']!r} and "
            f"the draft's {draft_row['value_per_unit']!r} are not the same "
            f"conversion factor"
        )
        #: `description` (v1.80, #127) is compared as text for the reason
        #: `source_note` is, and one more: it is the ONLY prose a visitor
        #: reads behind a tangible-equivalence card, so a fresh deployment
        #: whose copy of it had drifted from the draft set's would show a
        #: different sentence to a visitor than the authored one, with
        #: nothing else in the repository disagreeing.
        for field in ("name", "label_template", "source_metric", "source_note",
                      "description"):
            assert mock_row[field] == draft_row[field], (
                f"{code}.{field}: mock-factors.json says {mock_row[field]!r}, "
                f"the draft says {draft_row[field]!r}"
            )


# ---------------------------------------------------------------------------
# What the file COVERS. §5.1: `get_taxonomy` narrows the form to what the
# published set prices, so this document decides what a fresh install offers.
# ---------------------------------------------------------------------------


def _seed_taxonomy():
    """``admin/seed.py``'s constants, read rather than restated.

    Imported inside the helper because `admin.seed` pulls in the SQLAlchemy
    models, and a module-level import would make a failure here look like a
    database problem.
    """
    from admin.seed import DESTINATIONS, FOOD_CATEGORIES, SECTORS

    return (
        {code for code, *_ in SECTORS},
        {code for code, *_ in FOOD_CATEGORIES},
        {code for _group, code, _name, _prevention, _sort in DESTINATIONS},
    )


def test_the_mock_factor_set_prices_everything_the_seed_creates():
    """**A fresh install must offer its whole form, not a third of it.**

    `db/repository.py::get_taxonomy` narrows every dimension to what the
    published set covers, and `docker/init.sh` publishes this file, so what is
    priced here is what a first `docker compose up` can actually offer.

    Measured on 2026-10-06 against a stack brought up from the committed tree:
    the seed created 6 sectors, 10 food categories and 14 destinations; the mock
    set priced **3, 3 and 6**. Nothing was broken and nothing errored -- the
    controls simply were not drawn. A new team member's stack could not reach
    most of the calculator, a visitor to a fresh deployment could not name meat,
    fruit, seafood, grains, staples, beverages or nuts, and twelve browser cases
    failed because `tests/web` was written against a developer's database, where
    the client's draft set is published and prices all ten.

    `_covered_by`'s rules, which this mirrors rather than guesses at: a sector
    or food category is covered where it appears in `factor_upstream` or as a
    non-NULL column of `factor_downstream`; a destination where it has any
    `factor_downstream` row or appears as a non-NULL
    `factor_upstream.destination_id`. `food_item` is parent-covered and so needs
    nothing here.
    """
    data = json.loads(MOCK_FACTORS.read_text(encoding="utf-8"))
    sectors, categories, destinations = _seed_taxonomy()

    priced_sectors = (
        {row["sector"] for row in data["upstream"] if row.get("sector")}
        | {row["sector"] for row in data["downstream"] if row.get("sector")}
    )
    priced_categories = (
        {row["food_category"] for row in data["upstream"] if row.get("food_category")}
        | {row["food_category"] for row in data["downstream"] if row.get("food_category")}
    )
    priced_destinations = (
        {row["destination"] for row in data["downstream"] if row.get("destination")}
        | {row["destination"] for row in data["upstream"] if row.get("destination")}
    )

    for label, seeded, priced in (
        ("sectors", sectors, priced_sectors),
        ("food categories", categories, priced_categories),
        ("destinations", destinations, priced_destinations),
    ):
        missing = sorted(seeded - priced)
        assert not missing, (
            f"admin/seed.py creates {len(seeded)} {label} and "
            f"docker/mock-factors.json prices {len(seeded) - len(missing)}, so a "
            f"fresh install would not offer {missing}. get_taxonomy narrows the "
            f"form to what the published set covers, and nothing errors when it "
            f"narrows - the controls are simply absent. Regenerate with "
            f"`python docker/build_mock_factors.py`."
        )


def test_the_mock_factor_set_is_what_its_generator_produces():
    """**The guard that keeps the coverage above from going stale.**

    Extra rows fix the file once. `admin/seed.py` gaining a category breaks it
    again, and that has happened repeatedly -- v1.72 took the food vocabulary
    from nineteen names to forty-seven, and §2.1's prose still says eight
    categories where the seed carries nine. So the file is generated from the
    seed, and this asserts the committed bytes are what the generator produces.

    Verified by mutation rather than assumed: adding an eleventh food category
    to `admin/seed.py` fails this, and restoring it passes.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_build_mock_factors", REPO_ROOT / "docker" / "build_mock_factors.py"
    )
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)

    expected = builder.serialise(builder.build())
    assert MOCK_FACTORS.read_text(encoding="utf-8") == expected, (
        "docker/mock-factors.json is not what docker/build_mock_factors.py "
        "produces from admin/seed.py today. Either the seed gained a taxonomy "
        "row and the file was not regenerated - in which case a fresh install "
        "silently stops offering it - or the file was hand-edited, in which "
        "case the next regeneration will discard the edit. Run "
        "`python docker/build_mock_factors.py` and commit the result."
    )


def test_every_general_upstream_row_has_its_prevention_twin():
    """O-7, asserted on the file before the database refuses it.

    `publish_factor_set` runs `_refuse_incomplete_prevention`, and `init.sh` is
    under `set -e`: a set that fails it stops the migrate service, which stops
    api and admin, so **the whole stack refuses to start**. That is the right
    behaviour and a miserable way to find out, because the failure arrives as
    containers that will not come up rather than as a message about a factor
    row. One hundred and twenty triples here, and the cost of checking them is
    reading a JSON file.

    The key is `(sector, food_category, metric)` and deliberately not
    `food_item` -- the same key `find_missing_prevention_upstream` groups on,
    which is why the twelve item-level dairy rows need no twins of their own.
    """
    data = json.loads(MOCK_FACTORS.read_text(encoding="utf-8"))
    from admin.seed import DESTINATIONS

    prevention = {code for _g, code, _n, is_prevention, _s in DESTINATIONS
                  if is_prevention}
    assert prevention, "the seed creates no prevention destination"

    general = {(r["sector"], r["food_category"], r["metric"])
               for r in data["upstream"] if r["destination"] is None}
    twins = {(r["sector"], r["food_category"], r["metric"])
             for r in data["upstream"]
             if r["destination"] in prevention
             and Decimal(r["value_per_kg"]) == Decimal("0")}

    missing = sorted(general - twins)
    assert not missing, (
        f"{len(missing)} of {len(general)} upstream triples carry a general row "
        f"(destination null, meaning every destination) with no prevention row "
        f"at 0 beside it, so a prevented line would still be charged its "
        f"production burden - the O-7 defect, which understated prevention's "
        f"own benefit by 78.9% on the canonical fixture. publish_factor_set "
        f"refuses the set, init.sh is under `set -e`, and the stack will not "
        f"start. First few: {missing[:5]}"
    )

    nonzero = [
        f"{table} {r['destination']} = {r['value_per_kg']}"
        for table in ("upstream", "downstream")
        for r in data[table]
        if r["destination"] in prevention
        and Decimal(r["value_per_kg"]) != Decimal("0")
    ]
    assert not nonzero, (
        f"{len(nonzero)} rows price a prevention destination at something other "
        f"than zero, which `refuse_nonzero_prevention_factors` refuses: "
        f"{nonzero[:5]}"
    )


def test_the_notes_still_cite_a_source_long_enough_to_overflow():
    """**A precondition of `tests/web/test_horizontal_overflow.py`, held here
    so it fails in the fast job.**

    That module measures whether `/methodology.html` wraps an unbreakable token
    or widens its row, and carries its own precondition case --
    `test_the_factor_notes_still_carry_the_token_that_caused_the_overflow` --
    whose docstring says why: "Publish a set whose notes are three short words
    and every assertion above passes on a page that was never capable of
    overflowing."

    The overflow it was written against came from the **client draft's** notes,
    which cite file paths. MOCK-v0's notes cited nothing, so on a fresh stack
    both cases failed and the precondition reported a longest token of 20
    characters against the 60 it needs. Two cases of the browser job's first
    real run (2026-10-06), and they were the only two of that module's 42 that
    were not the wrong-origin defect.

    The fix was not to lengthen a string until a test passed: a factor set's
    notes should say where its figures came from, this set's did not, and a set
    that cites its sources carries long tokens as a matter of course. Both URLs
    the generator now cites are already committed elsewhere in this repository.

    The token has to be in a **downstream** note, because that is what
    `/methodology.html` renders into `.review-destinations dd`.
    """
    data = json.loads(MOCK_FACTORS.read_text(encoding="utf-8"))
    tokens = [
        token
        for row in data["downstream"]
        for token in (row.get("source_note") or "").split()
        if len(token) >= UNBREAKABLE_MIN
    ]
    longest = max((len(t) for t in tokens), default=0)
    assert tokens, (
        f"no downstream source_note carries a token of {UNBREAKABLE_MIN} "
        f"characters or more (longest is {longest}), so /methodology.html has "
        f"nothing on it that could overflow and "
        f"test_the_long_token_wraps_instead_of_widening_its_row would pass on a "
        f"page that was never capable of the defect it guards. Cite the sources "
        f"in docker/build_mock_factors.py rather than padding a string."
    )
