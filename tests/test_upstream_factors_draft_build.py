"""``data/upstream-factors-draft/build_upstream_factors_draft.py``'s own
guard rails, under test for the first time.

That script carries two mechanical checks that exist to stop a silent
defect, and until now nothing ran either of them except the script itself:

* ``_assert_only_table2_co2_moved()`` re-runs the measurement the 2026-09-21
  client revision was made on -- table 1 unchanged, table 2's labels,
  life-cycle column and water column unchanged, only its CO2-eq column
  replaced. It is the successor to the ``TABLE2_CO2_COPY_SOURCE`` check,
  which asserted the copy-paste defect the client has now fixed and which
  would fail on correct data.
* ``_assert_completeness()`` refuses to write a factor set that is missing a
  row a submission could need, because a missing row prices at zero and a
  zero on screen is indistinguishable from a real measurement of none.

A check nothing exercises is a check nobody knows still works. Each test
below breaks exactly one thing and asserts the failure names it, rather than
asserting only that *something* failed -- ``SystemExit`` is raised from half
a dozen places in that script and "it raised" would pass for the wrong
reason.

The module is loaded by file path: ``data/upstream-factors-draft/`` has no
``__init__.py`` and its name is not a valid Python identifier, the same
reason ``tests/db/test_load_upstream_factors_draft.py`` does it this way.
Nothing here touches a database; the build script reads two committed JSON
fixtures and writes nothing unless ``main()`` is called, which it is not.
"""

from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_BUILD_PATH = (
    REPO_ROOT / "data" / "upstream-factors-draft" / "build_upstream_factors_draft.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "build_upstream_factors_draft", _BUILD_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: Loaded once: importing it only defines names and reads two path constants.
build_module = _load_module()


@pytest.fixture
def restore_tables():
    """Put ``TABLE1`` and ``TABLE2`` back however the test leaves them.

    The tests below mutate the transcribed client data in place, which is
    module-level state shared with every other test in the session. Copying
    the lists and restoring their contents (rather than rebinding the names)
    means the restoration reaches the build script too, which imported the
    same list objects.
    """
    table1 = build_module.TABLE1
    table2 = build_module.TABLE2
    saved1 = list(table1)
    saved2 = list(table2)
    yield
    table1[:] = saved1
    table2[:] = saved2


def test_the_committed_transcription_passes_its_own_check():
    """The negative case for every test below: as committed, the client's
    two tables satisfy the measurement the 2026-09-21 revision was made on.
    Without this, a check that raised unconditionally would pass all three
    of the tests that follow."""
    build_module._assert_only_table2_co2_moved()


def test_a_table1_cell_that_moved_stops_the_build(restore_tables):
    """Table 1 was measured unchanged between the client's two revisions,
    and the provenance in `rawtec_source_data.py` and
    `docs/upstream-factors-draft.md` says so. If a later edit moves a table 1
    cell, that prose becomes false, so the build must stop rather than write
    a factor set whose documentation no longer describes it."""
    original = build_module.TABLE1[2]
    assert original.food == "Cheese", "table 1's row order has changed; fix this test"
    build_module.TABLE1[2] = original._replace(
        water_l_per_kg=original.water_l_per_kg + Decimal("1")
    )

    with pytest.raises(SystemExit) as caught:
        build_module._assert_only_table2_co2_moved()

    message = str(caught.value)
    assert "TABLE1 row 2 (Cheese) water_l_per_kg" in message
    assert "3969.00" in message and "3968.00" in message


def test_a_table2_water_cell_that_moved_stops_the_build(restore_tables):
    """Table 2's *water* column was measured identical across both client
    revisions. It is the column most likely to be edited by accident while
    editing the CO2-eq column beside it, which is exactly why it is frozen."""
    original = build_module.TABLE2[4]
    assert original.destination == "Landfill", "table 2's row order has changed; fix this test"
    build_module.TABLE2[4] = original._replace(water_l_per_kg=Decimal("0.07"))

    with pytest.raises(SystemExit) as caught:
        build_module._assert_only_table2_co2_moved()

    message = str(caught.value)
    assert "TABLE2 row 4 (Landfill) water_l_per_kg" in message
    assert "0.07" in message and "0.06" in message


def test_a_row_still_carrying_the_withdrawn_co2_column_stops_the_build(restore_tables):
    """What a half-applied revision looks like: one destination left holding
    the 2026-09-05 value, which was a table 1 food figure and not a
    destination figure at all. Compost is the row that made the defect
    visible -- 10.13 against landfill's 4.95 priced composting as twice as
    bad as landfilling."""
    original = build_module.TABLE2[2]
    assert original.destination == "Compost", "table 2's row order has changed; fix this test"
    build_module.TABLE2[2] = original._replace(co2e_per_kg=Decimal("10.13"))

    with pytest.raises(SystemExit) as caught:
        build_module._assert_only_table2_co2_moved()

    message = str(caught.value)
    assert "'Compost' still carries the withdrawn" in message
    assert "10.13" in message


def test_the_corrected_column_reaches_the_built_factor_set():
    """The transcription is only half the job: the client's corrected values
    have to survive the mapping into New Zealand destinations. These four are
    one-to-one in `NZ_DESTINATION_SOURCES` (one client row each), so the
    built figure must equal the client's own printed number exactly -- no
    aggregation stands between them.

    `compost` against `landfill` is the pair the correction inverted, and is
    checked as an ordering as well as two values: under the withdrawn column
    compost was priced *worse* than landfill, which is the defect this
    revision removes."""
    data = build_module.build()
    built = {
        row["destination"]: Decimal(row["value_per_kg"])
        for row in data["downstream"]
        if row["metric"] == "co2e"
    }

    assert built["compost"] == Decimal("-0.11")
    assert built["landfill"] == Decimal("0.60")
    assert built["anaerobic_digestion"] == Decimal("-0.04")
    assert built["upcycling"] == Decimal("-0.15")

    assert built["compost"] < built["landfill"], (
        "composting must price better than landfill under the client's "
        "corrected column; the withdrawn 2026-09-05 column had it the other "
        "way round"
    )


def test_a_dropped_downstream_row_stops_the_build():
    """`_assert_completeness()` still does its job after this revision.

    A destination missing a metric's row does not fail anywhere else: the
    documented lookup order resolves it to zero, and a zero on screen reads
    as a real measurement of none. This is the check that turns that into a
    refusal, and it must keep catching the case after the table 2 rewrite.
    """
    data = build_module.build()
    before = len(data["downstream"])
    data["downstream"] = [
        row for row in data["downstream"]
        if not (row["destination"] == "compost" and row["metric"] == "co2e")
    ]
    assert len(data["downstream"]) == before - 1, "nothing was dropped; fix this test"

    with pytest.raises(SystemExit) as caught:
        build_module._assert_completeness(data)

    message = str(caught.value)
    assert "downstream compost/co2e: found 0, expected 1" in message
