"""`serialize_result` against a stand-in for A's §3 `CalculationResult`.

This mapping is the only place the engine's domain objects become the §6.2
wire body, and until this file existed it had no test of any kind: the API
tests inject a fake adapter, so they never reached it. The stand-in below is
built from §3's field names exactly — nothing here invents a shape, and if A
delivers `engine/types.py` as §3 specifies, these same assertions hold
against the real dataclasses.
"""

from decimal import Decimal
from types import SimpleNamespace

from api.engine_adapter import DefaultEngineAdapter
from api.schemas import CalculatePayload, EntryPayload, ScenarioLinePayload
from api.serialization import wire


def _breakdown(destination, qty, value):
    return SimpleNamespace(
        destination_code=destination,
        qty_kg=Decimal(qty),
        upstream=Decimal("1.9000000000"),
        downstream=Decimal("0.9900000000"),
        value=Decimal(value),
    )


def _rolled_up_breakdown(destination, qty, value):
    """v1.48, amending §3 rule 2: a totals-level row. Unlike `_breakdown`
    above, the two rate fields are zero — they are per-kilogram rates that
    can differ between the entries sharing a destination, so a cross-entry
    row cannot state one."""
    return SimpleNamespace(
        destination_code=destination,
        qty_kg=Decimal(qty),
        upstream=Decimal("0.0000000000"),
        downstream=Decimal("0.0000000000"),
        value=Decimal(value),
    )


def _metric(total, by_destination=()):
    return SimpleNamespace(
        metric_code="co2e",
        unit="kg CO2e",
        display_precision=1,
        total=Decimal(total),
        by_destination=tuple(by_destination),
    )


def _scenario(total_kg, metric, equivalence_value):
    return SimpleNamespace(
        total_kg=Decimal(total_kg),
        metrics={"co2e": metric},
        equivalences=(
            SimpleNamespace(
                code="km_driven",
                label="Equivalent to driving 14,500 km",
                value=Decimal(equivalence_value),
                source_metric_code="co2e",
            ),
        ),
    )


def _result(*, with_alternative=True):
    """Two entries of **different** masses, and totals that match neither.

    1500.000 kg and 800.000 kg, rolling up to 2300.000 kg. Deliberate: this
    stand-in used to give `totals.current.total_kg` and
    `entries[0].current.total_kg` the same 1500.000, so §3's one defined wire
    hoist was unpinned at exactly the level it can be got wrong — an adapter
    that read the first entry's mass instead of the roll-up passed every
    assertion in this file. Three distinct figures mean only the correct
    source produces the expected one.
    """
    current = _scenario(
        "1500.000",
        _metric("3468.0000000000", [_breakdown("landfill", "1200.000", "3468.0000000000")]),
        "14500.0000000000",
    )
    alternative = (
        _scenario(
            "1500.000",
            _metric(
                "1368.0000000000",
                [_breakdown("anaerobic_digestion", "1200.000", "1368.0000000000")],
            ),
            "5700.0000000000",
        )
        if with_alternative
        else None
    )
    net_benefit = {"co2e": Decimal("2100.0000000000")} if with_alternative else None
    entry = SimpleNamespace(
        sector_code="processing",
        food_category_code="dairy",
        current=current,
        alternative=alternative,
        net_benefit=net_benefit,
    )
    second_current = _scenario(
        "800.000",
        _metric("456.0000000000", [_breakdown("not_harvested", "800.000", "456.0000000000")]),
        "1906.0800000000",
    )
    second_alternative = (
        _scenario(
            "800.000",
            _metric(
                "360.0000000000",
                [_breakdown("prevention", "800.000", "360.0000000000")],
            ),
            "1504.8000000000",
        )
        if with_alternative
        else None
    )
    second_net = {"co2e": Decimal("96.0000000000")} if with_alternative else None
    second_entry = SimpleNamespace(
        sector_code="primary_production",
        food_category_code="vegetables",
        current=second_current,
        alternative=second_alternative,
        net_benefit=second_net,
    )
    totals_net = (
        {"co2e": Decimal("2196.0000000000")} if with_alternative else None
    )
    #: The totals-level rows are a real partition of the two figures above:
    #: 3468 (landfill) + 456 (not_harvested) = 3924, and on the alternative
    #: side 1368 (anaerobic_digestion) + 360 (prevention) = 1728 — the same
    #: totals this stand-in already gave the metric before v1.48.
    totals = SimpleNamespace(
        current=_scenario(
            "2300.000",
            _metric(
                "3924.0000000000",
                [
                    _rolled_up_breakdown("landfill", "1200.000", "3468.0000000000"),
                    _rolled_up_breakdown("not_harvested", "800.000", "456.0000000000"),
                ],
            ),
            "16406.0800000000",
        ),
        alternative=_scenario(
            "2300.000",
            _metric(
                "1728.0000000000",
                [
                    _rolled_up_breakdown(
                        "anaerobic_digestion", "1200.000", "1368.0000000000"
                    ),
                    _rolled_up_breakdown("prevention", "800.000", "360.0000000000"),
                ],
            ),
            "7204.8000000000",
        )
        if with_alternative
        else None,
        net_benefit=totals_net,
        #: §4.5, v1.48. Not exercised by this file's own numbers -- none of
        #: this stand-in's entries carry a money figure -- but present
        #: because `_totals()` reads `totals.money` unconditionally.
        money=None,
    )
    return SimpleNamespace(
        factor_set_version="MOCK-v0 — PLACEHOLDER",
        is_mock=True,
        gwp_horizon=100,
        totals=totals,
        entries=(entry, second_entry),
    )


def test_the_top_level_keys_are_exactly_the_ones_6_2_lists():
    body = DefaultEngineAdapter().serialize_result(_result())
    assert set(body) == {"factor_set", "gwp_horizon", "totals", "entries"}
    assert body["factor_set"] == {
        "version_label": "MOCK-v0 — PLACEHOLDER",
        "is_mock": True,
    }
    assert body["gwp_horizon"] == 100


def test_factor_source_and_token_are_not_the_engines_to_supply():
    """§4.2: neither has a §3 counterpart. The router adds them afterwards."""
    body = DefaultEngineAdapter().serialize_result(_result())
    assert "factor_source" not in body
    assert "token" not in body


def test_totals_total_kg_is_hoisted_from_totals_current():
    """§3: a wire-format hoist, not a fifth field on `CalculationTotals`.

    The roll-up, **not** the first entry's mass and not a sum computed here:
    the stand-in's two entries are 1500 and 800, so a hoist from the wrong
    level produces 1500 and reading the wrong scenario produces nothing
    2300.000 could be confused with.
    """
    body = DefaultEngineAdapter().serialize_result(_result())
    totals = body["totals"]
    assert totals["total_kg"] == Decimal("2300.000")
    assert totals["total_kg"] != body["entries"][0]["current"]["total_kg"]
    assert "total_kg" not in totals["current"]
    assert "total_kg" not in totals["alternative"]
    assert body["entries"][1]["current"]["total_kg"] == Decimal("800.000")


def test_the_hoist_is_the_only_arithmetic_free_reshaping():
    """§4.2: `serialize_result` performs no arithmetic. Every figure below is
    the one the engine produced, unchanged."""
    body = DefaultEngineAdapter().serialize_result(_result())
    assert body["totals"]["current"]["metrics"]["co2e"]["total"] == Decimal(
        "3924.0000000000"
    )
    assert body["totals"]["net_benefit"] == {"co2e": Decimal("2196.0000000000")}
    assert body["entries"][0]["current"]["total_kg"] == Decimal("1500.000")
    assert body["entries"][1]["net_benefit"] == {"co2e": Decimal("96.0000000000")}


def test_by_destination_is_per_entry_and_rolled_up_at_the_totals_level():
    """§3 rule 2, as v1.48 amends it: `by_destination` is real at both
    levels now. Per entry it carries each line's own rate; at the totals
    level the rates are zero and only `qty_kg`/`value` are meaningful."""
    body = DefaultEngineAdapter().serialize_result(_result())
    assert body["entries"][0]["current"]["metrics"]["co2e"]["by_destination"] == [
        {
            "destination": "landfill",
            "qty_kg": Decimal("1200.000"),
            "upstream": Decimal("1.9000000000"),
            "downstream": Decimal("0.9900000000"),
            "value": Decimal("3468.0000000000"),
        }
    ]
    assert body["totals"]["current"]["metrics"]["co2e"]["by_destination"] == [
        {
            "destination": "landfill",
            "qty_kg": Decimal("1200.000"),
            "upstream": Decimal("0.0000000000"),
            "downstream": Decimal("0.0000000000"),
            "value": Decimal("3468.0000000000"),
        },
        {
            "destination": "not_harvested",
            "qty_kg": Decimal("800.000"),
            "upstream": Decimal("0.0000000000"),
            "downstream": Decimal("0.0000000000"),
            "value": Decimal("456.0000000000"),
        },
    ]
    assert body["totals"]["alternative"]["metrics"]["co2e"]["by_destination"] == [
        {
            "destination": "anaerobic_digestion",
            "qty_kg": Decimal("1200.000"),
            "upstream": Decimal("0.0000000000"),
            "downstream": Decimal("0.0000000000"),
            "value": Decimal("1368.0000000000"),
        },
        {
            "destination": "prevention",
            "qty_kg": Decimal("800.000"),
            "upstream": Decimal("0.0000000000"),
            "downstream": Decimal("0.0000000000"),
            "value": Decimal("360.0000000000"),
        },
    ]


def test_the_engines_trailing_code_suffixes_are_dropped_on_the_wire():
    """§3 carries `sector_code`, `destination_code`, `source_metric_code`;
    §6.2 carries `sector`, `destination`, `source_metric`. `metric_code`
    disappears entirely — it is the key of the `metrics` object."""
    body = DefaultEngineAdapter().serialize_result(_result())
    entry = body["entries"][0]
    assert entry["sector"] == "processing"
    assert entry["food_category"] == "dairy"
    assert "sector_code" not in entry and "food_category_code" not in entry
    assert "metric_code" not in entry["current"]["metrics"]["co2e"]
    assert entry["current"]["equivalences"][0] == {
        "code": "km_driven",
        "label": "Equivalent to driving 14,500 km",
        "value": Decimal("14500.0000000000"),
        "source_metric": "co2e",
    }


def test_an_absent_alternative_is_null_at_both_levels():
    """§3 rule 4."""
    body = DefaultEngineAdapter().serialize_result(_result(with_alternative=False))
    assert body["totals"]["alternative"] is None
    assert body["totals"]["net_benefit"] is None
    assert body["entries"][0]["alternative"] is None
    assert body["entries"][0]["net_benefit"] is None


def test_every_decimal_leaves_as_a_string_once_wired():
    """§1.2. `serialize_result` keeps `Decimal`; `wire()` is the one place a
    decimal becomes a JSON string, in both directions of the wire."""
    body = wire(DefaultEngineAdapter().serialize_result(_result()))
    assert body["totals"]["total_kg"] == "2300.000"
    assert body["totals"]["net_benefit"]["co2e"] == "2196.0000000000"
    assert (
        body["entries"][0]["current"]["metrics"]["co2e"]["by_destination"][0][
            "downstream"
        ]
        == "0.9900000000"
    )


def test_the_money_block_is_carried_present_and_not_summed_here():
    """§4.5, v1.48. `serialize_result` performs no arithmetic (§4.2): the four
    `MoneyResult` fields arrive already computed and this module only renames
    the object, on the same "present and null, not omitted" terms as
    `totals.alternative` and `totals.net_benefit`."""
    result = _result()
    result.totals.money = SimpleNamespace(
        total_value_nzd=Decimal("120000.00"),
        wasted_value_nzd=Decimal("4500.00"),
        wasted_share_percent=Decimal("3.75"),
        saving_nzd=None,
    )
    body = DefaultEngineAdapter().serialize_result(result)
    assert body["totals"]["money"] == {
        "total_value_nzd": Decimal("120000.00"),
        "wasted_value_nzd": Decimal("4500.00"),
        "wasted_share_percent": Decimal("3.75"),
        "saving_nzd": None,
    }


def test_the_money_block_is_null_when_the_engine_produced_none():
    body = DefaultEngineAdapter().serialize_result(_result())
    assert body["totals"]["money"] is None


def test_the_adapter_carries_the_new_entry_numbers_into_the_engine():
    """The three per-entry numbers reach `EntryInput`.

    **`time_frame` deliberately does not.** The engine is a pure function of
    a request and a bundle, and the client ruled that the period computes
    nothing - so putting it on `CalculationRequest` would be handing the
    engine a value it must promise never to use. It is stored by the
    repository and rendered by the front end, and the engine never sees it.
    """
    payload = CalculatePayload(
        gwp_horizon=100,
        time_frame="one_month",
        entries=[
            EntryPayload(
                sector="processing",
                food_category="bread_bakery",
                total_input_kg=Decimal("50000.000"),
                total_value_nzd=Decimal("120000.00"),
                wasted_value_nzd=Decimal("4500.00"),
                current=[
                    ScenarioLinePayload(destination="landfill", qty_kg="1200.500")
                ],
            )
        ],
    )

    request = DefaultEngineAdapter().make_request(payload)

    entry = request.entries[0]
    assert entry.total_input_kg == Decimal("50000.000")
    assert entry.total_value_nzd == Decimal("120000.00")
    assert entry.wasted_value_nzd == Decimal("4500.00")
    assert not hasattr(request, "time_frame"), (
        "the engine must not be handed a value it is required never to use"
    )
