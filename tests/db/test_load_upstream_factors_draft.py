"""``data/upstream-factors-draft/load_upstream_factors_draft.py``, against
real MySQL.

That script has never had a test of its own. Its directory
(``data/upstream-factors-draft/``) has no ``__init__.py`` and its name is not
a valid Python identifier, so it is not import-able the ordinary way -- it is
loaded here by file path via ``importlib``, same as any other standalone
script this repository might one day want to exercise directly rather than
only by running it.

That absence is exactly how the defect this file is about survived: the
script silently dropped the whole ``equivalences`` section of every factor
set it loaded (nothing read the key, and its counts dict at the top of
``load_factor_set`` never named it), and nothing caught that because nothing
ran the loader under test at all -- both live deployments found out from an
empty "Tangible equivalents" block instead.

This file runs against the ``kaicalc_test`` MySQL database via the ``session``
fixture from ``tests/conftest.py``, the same fixture ``tests/db/test_submissions.py``
and ``tests/admin/test_seed.py`` use, and for the same reason: the loader
inserts through real ``DECIMAL(20,10)`` columns, which SQLite does not model
faithfully.
"""

from __future__ import annotations

import importlib.util
import json
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from admin.factor_models import Equivalence, FactorSet, FactorUpstream
from admin.seed import seed_taxonomy
from admin.taxonomy_models import FoodItem

pytestmark = pytest.mark.db

REPO_ROOT = Path(__file__).resolve().parents[2]
_LOADER_PATH = (
    REPO_ROOT / "data" / "upstream-factors-draft" / "load_upstream_factors_draft.py"
)
_DRAFT_JSON_PATH = (
    REPO_ROOT / "data" / "upstream-factors-draft" / "upstream_factors_draft.json"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "load_upstream_factors_draft", _LOADER_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: Loaded once at import time, not per test -- it only defines functions and
#: classes; nothing in it touches a database until `load_factor_set` is
#: called with a session.
loader = _load_module()


@pytest.fixture
def draft_data() -> dict:
    """The client's own draft file, read fresh per test -- not a hand-built
    fixture. The whole point of this file is to prove what THIS JSON, loaded
    by THIS script, actually lands as."""
    return json.loads(_DRAFT_JSON_PATH.read_text(encoding="utf-8"))


def _seeded(session):
    seed_taxonomy(session)
    session.flush()
    return session


def test_loading_the_client_draft_inserts_every_equivalence_it_carries(session, draft_data):
    """The defect itself: `equivalences` went unread, so a JSON carrying
    three rows produced zero. `upstream_factors_draft.json` currently carries
    three -- if a future edit changes that count, this test still holds,
    since both sides are read from the same file rather than one being
    hard-coded to "3"."""
    _seeded(session)

    set_id, counts = loader.load_factor_set(session, draft_data)
    session.flush()

    expected = len(draft_data["equivalences"])
    assert expected > 0, "the fixture file no longer carries any equivalences to prove this against"
    assert counts["equivalence"] == expected

    stored = session.scalars(
        select(Equivalence).where(Equivalence.factor_set_id == set_id)
    ).all()
    assert len(stored) == expected
    assert {row.code for row in stored} == {row["code"] for row in draft_data["equivalences"]}


def test_a_json_section_the_loader_does_not_recognise_is_refused_not_dropped(session, draft_data):
    """Generalises the fix rather than special-casing `equivalences` by
    name: an unrecognised section must stop the whole load with a named
    error, or the next section someone adds to the JSON without writing a
    matching loop repeats this exact defect silently."""
    _seeded(session)
    mutated = dict(draft_data)
    mutated["a_section_nothing_here_loads"] = [{"code": "x"}]

    with pytest.raises(
        loader.LoadError, match="a_section_nothing_here_loads"
    ):
        loader.load_factor_set(session, mutated)


def test_loaded_equivalence_values_match_the_divisions_the_client_specified(session, draft_data):
    """Closes the finding deferred from an earlier task: pin the values that
    actually entered the system through THIS loader, not a value re-derived
    or retyped by hand elsewhere -- `tests/test_calculator.py`'s
    `test_the_vehicle_equivalence_divides_by_kilograms_not_tonnes` computes
    its own `Decimal(1) / Decimal(2410)` and never reads what the builder or
    this loader actually produced, so a transcription slip in either could
    build, load and pass unnoticed.

    Expected values are the divisions the client's document specifies,
    quantized to the column's stored scale (`DECIMAL(20,10)`) -- never the
    JSON's own decimal strings, which would only prove the JSON equals
    itself.
    """
    _seeded(session)

    set_id, _ = loader.load_factor_set(session, draft_data)
    session.flush()

    stored = {
        row.code: row.value_per_unit
        for row in session.scalars(
            select(Equivalence).where(Equivalence.factor_set_id == set_id)
        ).all()
    }

    scale = Decimal("1E-10")
    assert stored["vehicles_year"] == (Decimal(1) / Decimal(2410)).quantize(scale)
    assert stored["olympic_pools"] == (Decimal(1) / Decimal(2500000)).quantize(scale)
    assert stored["meals"] == (Decimal(1) / Decimal("0.45")).quantize(scale)


def test_the_item_level_switch_and_the_item_rows_both_reach_the_database(session, draft_data):
    """The second half of the same defect class the `equivalences` slip was.

    `factor_upstream.food_item_id` and `factor_set.item_level_enabled` are two
    more keys this loader had no code path for, and a JSON carrying both would
    have landed a set with 342 item rows flattened onto their categories -- six
    dairy rows at the same `(sector, food_category, metric)` key, which
    `FactorBundle.from_json` resolves by letting the last row silently win -- and
    the switch off, so step 2.5 would have been unreachable anyway. Neither
    would have raised anything.

    Both sides are read from the same JSON rather than hard-coded, so the test
    holds when the count changes.
    """
    _seeded(session)

    set_id, counts = loader.load_factor_set(session, draft_data)
    session.flush()

    expected = [row for row in draft_data["upstream"] if row.get("food_item")]
    assert expected, "the draft file no longer carries any item-level rows"
    assert draft_data["item_level_enabled"] is True

    factor_set = session.get(FactorSet, set_id)
    assert factor_set.item_level_enabled is True
    #: And a draft, never published: `load_factor_set` does not call
    #: `publish_factor_set` and whether this set goes live is the owner's
    #: decision.
    assert factor_set.status.value == "draft"
    assert factor_set.is_mock is True

    items = {row.id: row.code for row in session.scalars(select(FoodItem)).all()}
    stored = session.scalars(
        select(FactorUpstream).where(
            FactorUpstream.factor_set_id == set_id,
            FactorUpstream.food_item_id.is_not(None),
        )
    ).all()
    assert len(stored) == len(expected)
    assert {items[row.food_item_id] for row in stored} == {
        row["food_item"] for row in expected
    }
    #: Every item row must also carry the category it refines -- `food_item_id`
    #: and `food_category_id` are two independent columns (`resolve_food_item`),
    #: and a row with one and not the other is what section 2.2's chain cannot
    #: resolve.
    assert all(row.food_category_id is not None for row in stored)


def test_an_unknown_food_item_code_stops_the_load(session, draft_data):
    """`ITEM_LEVEL_CLIENT_FOODS` is kept in step with `admin/seed.py`'s
    `FOOD_ITEMS` by hand, because the build script may not import `admin.seed`.

    So the loader is the backstop, and it must be a hard stop rather than a
    created row: writing the missing `food_item` here would put a factor
    loader's own vocabulary into a **global** taxonomy table, which is how
    forty-eight `refed_*` rows came to sit in `food_category`.
    """
    _seeded(session)
    mutated = dict(draft_data)
    mutated["upstream"] = [dict(row) for row in draft_data["upstream"]]
    target = next(row for row in mutated["upstream"] if row.get("food_item"))
    target["food_item"] = "brie_de_meaux"

    with pytest.raises(loader.LoadError, match="brie_de_meaux"):
        loader.load_factor_set(session, mutated)
