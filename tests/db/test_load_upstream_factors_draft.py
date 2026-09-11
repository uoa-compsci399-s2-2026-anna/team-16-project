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

from admin.factor_models import Equivalence
from admin.seed import seed_taxonomy

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
