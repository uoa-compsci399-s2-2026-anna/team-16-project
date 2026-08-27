from decimal import Decimal
from types import SimpleNamespace

from datetime import timedelta

import pytest
from sqlalchemy import delete, func, select

from db.errors import FactorSetStateError
from db.models import (
    AuditLog,
    Constant,
    FactorDownstream,
    FactorSet,
    FactorSetStatus,
    FactorUpstream,
    Metric,
    Scenario,
    Submission,
    SubmissionEntry,
    SubmissionLine,
    utcnow,
)
from db.repository import (
    build_bundle_data,
    clone_factor_set,
    find_missing_prevention_upstream,
    get_public_stats,
    get_published_factor_set_id,
    invalidate_factor_bundle,
    load_factor_bundle,
    publish_factor_set,
    rollback_to,
    upsert_submission,
    write_audit,
)


def _request(current="10", alternative="999", food_category="dairy"):
    """One entry, in the §3 shape.

    Was a `CalculationRequest` with `current` / `alternative` `ScenarioInput`s
    at the top level. v1.2 moved the sector and the food category onto
    `EntryInput` and made the scenarios plain tuples of `ScenarioLine`, so the
    single-entry request these tests exercise is now a one-element
    `req.entries`. The behaviour each test asserts is unchanged; only the
    shape of the request that produces it is. Multi-entry behaviour lives in
    tests/db/test_repository_entries.py.
    """
    line = lambda qty: SimpleNamespace(destination_code="landfill", qty_kg=Decimal(qty))
    entry = SimpleNamespace(
        sector_code="processing",
        food_category_code=food_category,
        current=(line(current),),
        alternative=(line(alternative),) if alternative is not None else None,
        #: v1.48. `EntryInput` defaults all three to `None`; a hand-built
        #: stand-in has to state that default explicitly.
        total_input_kg=None,
        total_value_nzd=None,
        wasted_value_nzd=None,
    )
    return SimpleNamespace(entries=(entry,), gwp_horizon=100)


def _lines_of(session, submission_id):
    return session.scalars(
        select(SubmissionLine)
        .join(SubmissionEntry, SubmissionLine.submission_entry_id == SubmissionEntry.id)
        .where(SubmissionEntry.submission_id == submission_id)
    ).all()


def test_upsert_reuses_a_valid_token_and_replaces_lines(seeded_session):
    factor_set_id = seeded_session.scalar(select(Submission.factor_set_id).limit(1))
    if factor_set_id is None:
        from db.repository import get_published_factor_set_id
        factor_set_id = get_published_factor_set_id(seeded_session)
    submission_id, token = upsert_submission(seeded_session, None, _request(), factor_set_id)
    second_id, second_token = upsert_submission(seeded_session, token, _request("20", "30"), factor_set_id)
    assert second_id == submission_id
    assert second_token == token
    # Lines now hang off the entry, so "this submission's lines" is a join
    # rather than a column. Counting the whole table would also pass here --
    # and would keep passing if the rebuild started leaking orphans.
    #
    # `(scenario, qty_kg)` pairs, not a set of quantities: `_request("20",
    # "30")` puts 20 kg in `current` and 30 kg in `alternative`, and a set of
    # quantities alone is satisfied by a rebuild that writes them under the
    # wrong scenario. §1.4 gives `scenario` exactly two values and §5.4 reads
    # `current` only, so swapping them silently halves or doubles every
    # public statistic without changing anything this test could see.
    assert {
        (line.scenario, line.qty_kg)
        for line in _lines_of(seeded_session, submission_id)
    } == {
        (Scenario.current, Decimal("20.000")),
        (Scenario.alternative, Decimal("30.000")),
    }
    assert seeded_session.scalar(select(func.count()).select_from(SubmissionLine)) == 2


def test_public_stats_use_current_only(seeded_session):
    from db.repository import get_published_factor_set_id
    upsert_submission(seeded_session, None, _request("10", "999"), get_published_factor_set_id(seeded_session))
    stats = get_public_stats(seeded_session, threshold=1)
    assert stats.by_destination[0].total_kg == Decimal("10.000")
    # The scenario predicate belongs on every breakdown, not only on the one
    # grouped over submission_line: by_sector and by_food_category sum the
    # same lines through the entry, so a missing filter doubles them too.
    assert stats.by_sector[0].total_kg == Decimal("10.000")
    assert stats.by_food_category[0].total_kg == Decimal("10.000")


def test_audit_redacts_secrets_and_serialises_decimal(seeded_session):
    write_audit(
        seeded_session,
        "alice",
        "update",
        "staff",
        1,
        None,
        {"password_hash": "secret", "nested": {"code_hash": "secret", "amount": Decimal("1.20")}},
    )
    seeded_session.flush()
    row = seeded_session.scalar(select(AuditLog))
    assert row.after_json["password_hash"] == "[redacted]"
    assert row.after_json["nested"]["code_hash"] == "[redacted]"
    assert row.after_json["nested"]["amount"] == "1.20"


def test_audit_timestamps_are_utc_with_a_z_designator(seeded_session):
    """§1.3: always UTC, always `Z`, whichever kind of datetime arrives.

    `_json_safe` used to append "Z" to a naive value and leave an aware one
    untouched, so one column serialised two ways depending on whether the
    object had been round-tripped through MySQL. A non-UTC aware value kept
    its own offset and still went into the table as though it complied.
    Three kinds in one payload, because the defect is only visible when the
    outputs are compared against each other.
    """
    from datetime import datetime, timezone

    nzst = timezone(timedelta(hours=12))
    write_audit(
        seeded_session,
        "alice",
        "update",
        "factor_set",
        1,
        None,
        {
            "naive": datetime(2026, 8, 9, 3, 0, 0),
            "utc": datetime(2026, 8, 9, 3, 0, 0, tzinfo=timezone.utc),
            "local": datetime(2026, 8, 9, 15, 0, 0, tzinfo=nzst),
        },
    )
    seeded_session.flush()
    row = seeded_session.scalar(select(AuditLog))
    assert row.after_json["naive"] == "2026-08-09T03:00:00Z"
    assert row.after_json["utc"] == "2026-08-09T03:00:00Z"
    assert row.after_json["local"] == "2026-08-09T03:00:00Z"


def test_factor_bundle_cache_is_partitioned_and_explicitly_invalidated(seeded_session):
    published_id = get_published_factor_set_id(seeded_session)
    draft_id = seeded_session.scalar(
        select(FactorSet.id).where(FactorSet.status == FactorSetStatus.draft)
    )
    built = []

    def factory(data):
        built.append(data["version_label"])
        return {"version": data["version_label"], "build": len(built)}

    first = load_factor_bundle(seeded_session, published_id, bundle_factory=factory)
    assert load_factor_bundle(seeded_session, published_id, bundle_factory=factory) is first
    draft = load_factor_bundle(seeded_session, draft_id, bundle_factory=factory)
    assert draft is not first
    assert built == ["MOCK-v0", "DRAFT-v1"]

    # v1.4: a draft is never cached, so a second dry run of it rebuilds.
    # The panel's CRUD screens write factor rows directly and never call
    # invalidate_factor_bundle -- only publish/rollback do -- so a cached
    # draft would show a staff member their pre-edit numbers for the lifetime
    # of the process, which is indistinguishable from a formula that ignores
    # the column they just changed. Asserting `is not` rather than the build
    # count alone: a cache that returned an equal-but-rebuilt object would
    # still be serving stale factors.
    draft_again = load_factor_bundle(seeded_session, draft_id, bundle_factory=factory)
    assert draft_again is not draft
    assert built == ["MOCK-v0", "DRAFT-v1", "DRAFT-v1"]
    # ...and rebuilding the draft has not disturbed the published slot, which
    # is what §5.2's per-set partitioning is for.
    assert load_factor_bundle(seeded_session, published_id, bundle_factory=factory) is first

    invalidate_factor_bundle(published_id)
    rebuilt = load_factor_bundle(seeded_session, published_id, bundle_factory=factory)
    assert rebuilt is not first
    assert built == ["MOCK-v0", "DRAFT-v1", "DRAFT-v1", "MOCK-v0"]


@pytest.mark.parametrize("started_mock, becomes", [(True, False), (False, True)])
def test_a_change_to_is_mock_is_not_served_from_a_warm_cache(
    seeded_session, started_mock, becomes
):
    """Contract §2.2. The placeholder flag is the one field of a *published*
    set that legitimately moves while it stays published, and the panel that
    moves it is a different process from the API that holds this cache — so
    an invalidation call cannot carry the change across and this cache has no
    expiry to age it out. Without the re-check in `load_factor_bundle` the
    warm slot serves the old flag until the API is restarted.

    Both directions are asserted, and the second is the one that matters
    most: *setting* the flag is the direction §2.2 requires to be instant, so
    that anyone who doubts a published set can put the placeholder warning in
    front of the public immediately. A cache that defeats that makes the safe
    direction the broken one.

    Asserted on the flag the bundle was built with, not merely on object
    identity: a version that rebuilt on every hit would pass an identity
    check while quietly throwing the cache away, and a version that returned
    a stale object would fail both.
    """
    published_id = get_published_factor_set_id(seeded_session)
    invalidate_factor_bundle()
    seeded_session.get(FactorSet, published_id).is_mock = started_mock
    seeded_session.flush()

    def factory(data):
        return {"is_mock": data["is_mock"]}

    first = load_factor_bundle(seeded_session, published_id, bundle_factory=factory)
    assert first["is_mock"] is started_mock
    # Warm: nothing changed, so the same object comes back.
    assert load_factor_bundle(
        seeded_session, published_id, bundle_factory=factory
    ) is first

    seeded_session.get(FactorSet, published_id).is_mock = becomes
    seeded_session.flush()

    after = load_factor_bundle(seeded_session, published_id, bundle_factory=factory)
    assert after["is_mock"] is becomes


def test_the_bundle_carries_each_upstream_rows_destination(seeded_session):
    """Contract §10.2 (v1.8): every `upstream[]` row publishes a `destination`,
    `null` for the generic row that applies to every destination.

    Without the key the engine cannot implement §4.1's exact-destination-then-
    generic-then-zero order at all, and O-7 stays open no matter what the
    database holds — `prevention`'s zero row would be loaded, keyed on the same
    tuple as the general row, and one of the two would win at random.
    """
    data = build_bundle_data(seeded_session, get_published_factor_set_id(seeded_session))
    rows = {row["destination"]: row for row in data["upstream"]}

    assert set(rows) == {None, "prevention"}, data["upstream"]
    assert rows[None]["value_per_kg"] == "1.9000000000"
    #: O-7: prevented waste was never produced, so its upstream is zero.
    assert Decimal(rows["prevention"]["value_per_kg"]) == 0
    #: §1.1 - `code` crosses the layer boundary, never a primary key.
    assert all("destination_id" not in row for row in data["upstream"])


def test_the_bundle_carries_each_downstream_rows_sector(seeded_session):
    """Contract §10.2 (v1.31): every `downstream[]` row publishes a `sector`,
    `null` for the row that applies to every sector.

    **The seeded set cannot prove this on its own**, and that is the point.
    All fifteen of its downstream rows leave `sector_id` NULL, so a projection
    that hard-coded `"sector": None` — or dropped the join and let every row
    default — would emit a byte-identical document and every other test in this
    repository would stay green while the calculator priced every supply-chain
    stage the same. So the test writes a sector-specific row first, and asserts
    both states come back distinguishable.

    A `key in row` check is asserted separately from the value, for the reason
    §10.2 gives about `upstream[].destination`: `row.get("sector")` is `None`
    both when the row applies to every sector and when the projection forgot
    the field, and those are not the same thing.
    """
    from db.models import Destination, FactorDownstream, Metric, Sector

    published = get_published_factor_set_id(seeded_session)
    landfill_id = seeded_session.scalar(
        select(Destination.id).where(Destination.code == "landfill")
    )
    metric_id = seeded_session.scalar(select(Metric.id).where(Metric.code == "co2e"))
    sector_id = seeded_session.scalar(
        select(Sector.id).where(Sector.code == "primary_production")
    )
    seeded_session.add(
        FactorDownstream(
            factor_set_id=published,
            destination_id=landfill_id,
            sector_id=sector_id,
            food_category_id=None,
            metric_id=metric_id,
            value_per_kg=Decimal("0.3100000000"),
        )
    )
    seeded_session.flush()

    data = build_bundle_data(seeded_session, published)
    landfill_co2e = [
        row for row in data["downstream"]
        if row["destination"] == "landfill" and row["metric"] == "co2e"
    ]

    assert all("sector" in row for row in data["downstream"]), (
        "a downstream row published no `sector` key at all"
    )
    by_sector = {row["sector"]: row["value_per_kg"] for row in landfill_co2e}
    #: The sector-specific row and the two every-sector rows, told apart.
    assert by_sector["primary_production"] == "0.3100000000"
    assert None in by_sector, "the every-sector rows lost their null"
    #: §1.1 — `code` crosses the layer boundary, never a primary key.
    assert all("sector_id" not in row for row in data["downstream"])

    #: And it reaches the engine through the real path, priced only for the
    #: sector it names. 1000 kg of primary_production/vegetables to landfill
    #: draws 0.31 where processing/vegetables still draws the generic 0.70.
    bundle = load_factor_bundle(seeded_session)
    assert bundle.validate() == []
    assert bundle.downstream(
        "landfill", "primary_production", "vegetables", "co2e"
    ) == Decimal("0.3100000000")
    assert bundle.downstream(
        "landfill", "processing", "vegetables", "co2e"
    ) == Decimal("0.7000000000")


def _seam_request(sector, food_category, current, alternative=None):
    from engine.types import CalculationRequest, EntryInput, ScenarioLine

    def lines(rows):
        return tuple(
            ScenarioLine(destination_code=code, qty_kg=Decimal(qty)) for code, qty in rows
        )

    return CalculationRequest(
        entries=(
            EntryInput(
                sector_code=sector,
                food_category_code=food_category,
                current=lines(current),
                alternative=None if alternative is None else lines(alternative),
            ),
        )
    )


def test_the_default_bundle_factory_reaches_the_real_engine(seeded_session):
    """**The seam this branch exists to close, and until now it had no test.**

    `_default_bundle_factory` -> `build_bundle_data` -> `FactorBundle.from_json`
    -> `calculate` is the production path of every public calculation, and
    every test that touches either end stubs the other: `tests/api/` injects a
    `FakeEngineAdapter`, and every other test in this file passes an explicit
    `bundle_factory`. So the one call with **no** `bundle_factory` — the call
    the API actually makes — was exercised by hand and by nothing else.

    **If `build_bundle_data` ever stops emitting one of `from_json`'s twelve
    required keys, nothing in the suite goes red and every public calculation
    returns 500.** That is not hypothetical: Task 3 found `from_json` parsing a
    three-key upstream shape while the repository emitted four, and found it by
    accident. This is the test that would have caught it.

    The two figures are chosen to exercise both halves of the projection:

    - 10 kg of `processing`/`dairy` to `landfill` is 10 x (1.9 + 0.99) = 28.9,
      and moving it to `prevention` gives 0 — the whole offset, through the
      real `destination_id` column rather than a hand-built bundle.
    - 1000 kg of `primary_production`/`vegetables` to `landfill` is
      1000 x (0 + 0.70) = 700.0: an absent upstream row falling back to zero
      (§4.1) and the **generic** `food_category IS NULL` downstream row being
      selected for a category that has no row of its own. Neither of those
      resolutions exists anywhere in `build_bundle_data`; both are properties
      of the two documents lining up.
    """
    from engine.calculate import calculate

    bundle = load_factor_bundle(seeded_session)

    assert bundle.validate() == []

    prevented = calculate(
        _seam_request(
            "processing", "dairy", [("landfill", "10")], [("prevention", "10")]
        ),
        bundle,
    )
    assert prevented.entries[0].current.metrics["co2e"].total == Decimal("28.9")
    assert prevented.entries[0].alternative.metrics["co2e"].total == Decimal("0")
    assert prevented.entries[0].net_benefit["co2e"] == Decimal("28.9")

    generic = calculate(
        _seam_request("primary_production", "vegetables", [("landfill", "1000")]),
        bundle,
    )
    row = generic.entries[0].current.metrics["co2e"].by_destination[0]
    assert (row.upstream, row.downstream) == (Decimal("0"), Decimal("0.7"))
    assert generic.entries[0].current.metrics["co2e"].total == Decimal("700.0")


def test_publish_refuses_a_set_that_would_reopen_o7(seeded_session):
    """Contract §2.2/§5.2. The offset is data now, so data can un-do it.

    The migration covered every combination that existed and `clone_factor_set`
    carries the rows through clone-edit-publish, but nothing stops a staff
    member adding a new `(sector, food_category, metric)` to a draft with no
    `prevention` counterpart. That one combination silently reverts to
    pre-v1.8 behaviour — charging a prevented line its full upstream factor —
    while every other combination on the same results page stays correct,
    which is *harder* to notice than the original O-7 was.

    The message must name the tuples: "something is incomplete" leaves a staff
    member to find it among roughly 270 rows.
    """
    from db.models import Destination

    draft_id = seeded_session.scalar(
        select(FactorSet.id).where(FactorSet.status == FactorSetStatus.draft)
    )
    prevention_id = seeded_session.scalar(
        select(Destination.id).where(Destination.code == "prevention")
    )
    assert find_missing_prevention_upstream(seeded_session, draft_id) == []

    seeded_session.execute(
        delete(FactorUpstream).where(
            FactorUpstream.factor_set_id == draft_id,
            FactorUpstream.destination_id == prevention_id,
        )
    )
    seeded_session.flush()

    assert find_missing_prevention_upstream(seeded_session, draft_id) == [
        ("processing", "dairy", "co2e")
    ]
    with pytest.raises(FactorSetStateError) as excinfo:
        publish_factor_set(seeded_session, draft_id, "alice")

    message = str(excinfo.value)
    assert "processing/dairy/co2e" in message
    assert "prevention" in message
    assert seeded_session.get(FactorSet, draft_id).status == FactorSetStatus.draft


def test_publish_refuses_a_prevention_row_that_is_present_but_not_zero(seeded_session):
    """**The guard checks the value, not the row's existence**, and this is the
    case that tells the two apart.

    An existence check is satisfied completely by a `prevention` upstream row
    at 1.9 — the same value as the general row — which is O-7 reopened for that
    tuple with one extra step and no error, no warning and nothing in the log.
    It is a worse position than the absent row, because both callers' messages
    tell a staff member to add a row "at 0", so a set that fails an existence
    check gets fixed and a set that passes it looks finished.

    The value is a modelling decision (`architecture.md` §4.1, and the
    `source_note` on the shipped rows): prevented food was never produced, so
    there is no upstream burden to attribute. Any other value is a claim
    nothing in the system supports, so anything but zero is refused — not only
    a value equal to the general row's.
    """
    from db.models import Destination

    draft_id = seeded_session.scalar(
        select(FactorSet.id).where(FactorSet.status == FactorSetStatus.draft)
    )
    prevention_id = seeded_session.scalar(
        select(Destination.id).where(Destination.code == "prevention")
    )
    assert find_missing_prevention_upstream(seeded_session, draft_id) == []

    seeded_session.execute(
        FactorUpstream.__table__.update()
        .where(
            FactorUpstream.factor_set_id == draft_id,
            FactorUpstream.destination_id == prevention_id,
        )
        .values(value_per_kg=Decimal("1.9000000000"))
    )
    seeded_session.flush()

    assert find_missing_prevention_upstream(seeded_session, draft_id) == [
        ("processing", "dairy", "co2e")
    ]
    with pytest.raises(FactorSetStateError) as excinfo:
        publish_factor_set(seeded_session, draft_id, "alice")

    assert "processing/dairy/co2e" in str(excinfo.value)
    assert seeded_session.get(FactorSet, draft_id).status == FactorSetStatus.draft


def test_a_prevention_row_just_above_zero_is_refused_too(seeded_session):
    """One unit in the last place DECIMAL(20,10) carries. The check is
    `== 0`, not "small enough" — there is no tolerance to tune and no value
    below which a partial offset becomes acceptable."""
    from db.models import Destination

    draft_id = seeded_session.scalar(
        select(FactorSet.id).where(FactorSet.status == FactorSetStatus.draft)
    )
    prevention_id = seeded_session.scalar(
        select(Destination.id).where(Destination.code == "prevention")
    )
    seeded_session.execute(
        FactorUpstream.__table__.update()
        .where(
            FactorUpstream.factor_set_id == draft_id,
            FactorUpstream.destination_id == prevention_id,
        )
        .values(value_per_kg=Decimal("0.0000000001"))
    )
    seeded_session.flush()

    assert find_missing_prevention_upstream(seeded_session, draft_id) == [
        ("processing", "dairy", "co2e")
    ]


def test_both_callers_of_the_o7_guard_refuse_the_same_non_zero_row(seeded_session):
    """§5.2 and v1.9: the query has one home in `db/` and two callers, and the
    panel calls `admin/factor_lifecycle.publish_factor_set`, not this module's.
    A value rule enforced in only one of them leaves the staff path — the one
    the failure description is written about — unguarded."""
    from admin.factor_lifecycle import LifecycleError
    from admin.factor_lifecycle import publish_factor_set as panel_publish
    from db.models import Destination

    draft_id = seeded_session.scalar(
        select(FactorSet.id).where(FactorSet.status == FactorSetStatus.draft)
    )
    prevention_id = seeded_session.scalar(
        select(Destination.id).where(Destination.code == "prevention")
    )
    seeded_session.execute(
        FactorUpstream.__table__.update()
        .where(
            FactorUpstream.factor_set_id == draft_id,
            FactorUpstream.destination_id == prevention_id,
        )
        .values(value_per_kg=Decimal("1.9000000000"))
    )
    seeded_session.flush()

    with pytest.raises(LifecycleError) as excinfo:
        panel_publish(seeded_session, draft_id, actor="alice")

    assert "processing/dairy/co2e" in str(excinfo.value)
    assert seeded_session.get(FactorSet, draft_id).status == FactorSetStatus.draft


def test_rollback_is_deliberately_not_subject_to_the_o7_check(seeded_session):
    """The asymmetry is the decision, not an oversight.

    Rollback restores a version that was published before — including one
    archived before v1.8 existed, which will legitimately fail the
    completeness check. Refusing an emergency rollback over it would be a
    worse failure than the one the check prevents.
    """
    from db.models import Destination

    published_id = get_published_factor_set_id(seeded_session)
    draft_id = seeded_session.scalar(
        select(FactorSet.id).where(FactorSet.status == FactorSetStatus.draft)
    )
    prevention_id = seeded_session.scalar(
        select(Destination.id).where(Destination.code == "prevention")
    )
    publish_factor_set(seeded_session, draft_id, "alice")
    seeded_session.execute(
        delete(FactorUpstream).where(
            FactorUpstream.factor_set_id == published_id,
            FactorUpstream.destination_id == prevention_id,
        )
    )
    seeded_session.flush()

    rollback_to(seeded_session, published_id, "alice")

    assert seeded_session.get(FactorSet, published_id).status == FactorSetStatus.published


def test_the_o7_check_is_silent_on_a_taxonomy_with_no_prevention_row(seeded_session):
    """An unseeded taxonomy is `check_prevention_destination`'s problem, not this
    function's. Reporting every combination in the set would be noise, and a
    second rule stated in terms of the same reserved row is a second thing to
    keep in step."""
    from db.models import Destination

    draft_id = seeded_session.scalar(
        select(FactorSet.id).where(FactorSet.status == FactorSetStatus.draft)
    )
    seeded_session.execute(
        delete(FactorUpstream).where(FactorUpstream.destination_id.is_not(None))
    )
    seeded_session.execute(delete(Destination).where(Destination.code == "prevention"))
    seeded_session.flush()

    assert find_missing_prevention_upstream(seeded_session, draft_id) == []


def test_the_o7_guard_reads_the_flag_and_not_the_code(seeded_session):
    """What proves the string is gone from this guard.

    Clear the tick on the row called `prevention` and give the role to another
    row. The seed's `prevention` upstream override is then an override against
    an ordinary destination and satisfies nothing, so the guard must report the
    tuple — and must stop reporting it once the *flagged* row has its own zero.
    """
    from db.models import Destination

    draft_id = seeded_session.scalar(
        select(FactorSet.id).where(FactorSet.status == FactorSetStatus.draft)
    )
    prevention = seeded_session.scalar(
        select(Destination).where(Destination.code == "prevention")
    )
    prevention.is_prevention = False
    avoided = Destination(group_id=prevention.group_id, code="waste_avoided",
                          name="Waste avoided", is_prevention=True, sort_order=6)
    seeded_session.add(avoided)
    seeded_session.flush()

    assert find_missing_prevention_upstream(seeded_session, draft_id) == [
        ("processing", "dairy", "co2e")
    ]

    row = seeded_session.scalar(
        select(FactorUpstream).where(
            FactorUpstream.factor_set_id == draft_id,
            FactorUpstream.destination_id == prevention.id,
        )
    )
    row.destination_id = avoided.id
    seeded_session.flush()

    assert find_missing_prevention_upstream(seeded_session, draft_id) == []


def test_one_flagged_destination_with_a_zero_override_satisfies_the_tuple(
    seeded_session,
):
    """Not "every flagged destination", deliberately.

    Two vocabularies share these tables (§10.3) and `MOCK-v0` has no
    `refed_prevention` rows and never will. Requiring one per flagged row would
    refuse a set for a vocabulary it was never built in.
    """
    from db.models import Destination

    draft_id = seeded_session.scalar(
        select(FactorSet.id).where(FactorSet.status == FactorSetStatus.draft)
    )
    prevention = seeded_session.scalar(
        select(Destination).where(Destination.code == "prevention")
    )
    seeded_session.add(
        Destination(group_id=prevention.group_id, code="refed_prevention",
                    name="Prevention (ReFED)", is_prevention=True, sort_order=803)
    )
    seeded_session.flush()

    assert find_missing_prevention_upstream(seeded_session, draft_id) == []
    publish_factor_set(seeded_session, draft_id, "alice")
    assert seeded_session.get(FactorSet, draft_id).status == FactorSetStatus.published


def test_publish_refuses_a_prevention_destination_priced_at_anything_but_zero(
    seeded_session,
):
    """The half nothing checked at all until the flag existed.

    `find_missing_prevention_upstream` can only see a non-zero upstream value
    where a generic row exists to compare it against, and no rule anywhere read
    `factor_downstream`. A prevention destination priced at anything is not a
    100% offset, so the improved scenario stops describing the same mass at no
    cost and `net_benefit` silently reports a smaller improvement than the
    scenario the user built.
    """
    from db.models import Destination

    draft_id = seeded_session.scalar(
        select(FactorSet.id).where(FactorSet.status == FactorSetStatus.draft)
    )
    prevention_id = seeded_session.scalar(
        select(Destination.id).where(Destination.is_prevention.is_(True))
    )
    metric_id = seeded_session.scalar(select(Metric.id).where(Metric.code == "co2e"))
    seeded_session.add(
        FactorDownstream(
            factor_set_id=draft_id, destination_id=prevention_id,
            food_category_id=None, metric_id=metric_id,
            value_per_kg=Decimal("0.5"),
        )
    )
    seeded_session.flush()

    with pytest.raises(FactorSetStateError) as excinfo:
        publish_factor_set(seeded_session, draft_id, "alice")

    message = str(excinfo.value)
    assert "downstream prevention" in message
    assert seeded_session.get(FactorSet, draft_id).status == FactorSetStatus.draft


def test_an_absent_prevention_factor_row_is_still_legal(seeded_session):
    """§4.1's lookup returns zero for a missing factor, so absence already *is*
    zero. The new refusal must only ever fire on a row that exists and
    disagrees — otherwise it would contradict the hold-out in
    `get_taxonomy`, which is built around a set that prices a prevention
    destination nowhere at all."""
    from db.models import Destination

    draft_id = seeded_session.scalar(
        select(FactorSet.id).where(FactorSet.status == FactorSetStatus.draft)
    )
    prevention_id = seeded_session.scalar(
        select(Destination.id).where(Destination.is_prevention.is_(True))
    )
    seeded_session.execute(
        delete(FactorDownstream).where(
            FactorDownstream.factor_set_id == draft_id,
            FactorDownstream.destination_id == prevention_id,
        )
    )
    seeded_session.flush()

    publish_factor_set(seeded_session, draft_id, "alice")
    assert seeded_session.get(FactorSet, draft_id).status == FactorSetStatus.published


def test_clone_is_deep_and_publish_rollback_preserve_single_published(seeded_session):
    published_id = get_published_factor_set_id(seeded_session)
    clone_id = clone_factor_set(seeded_session, published_id, "CLONE-v1", "alice")
    clone = seeded_session.get(FactorSet, clone_id)
    assert clone.status == FactorSetStatus.draft
    assert seeded_session.scalar(
        select(func.count()).select_from(FactorUpstream).where(
            FactorUpstream.factor_set_id == clone_id
        )
    ) == seeded_session.scalar(
        select(func.count()).select_from(FactorUpstream).where(
            FactorUpstream.factor_set_id == published_id
        )
    )
    assert seeded_session.scalar(
        select(func.count()).select_from(Constant).where(Constant.factor_set_id == clone_id)
    ) == 1
    #: A row count alone cannot see a column the clone drops. `destination_id`
    #: is the one that matters: lose it and every cloned set's `prevention`
    #: rows collapse onto the general row, reopening O-7 on the next publish —
    #: and clone-edit-publish is the recommended staff workflow (§5.2), so the
    #: defect would arrive on the first real factor set rather than this one.
    #: Ordered by the column itself, not by `is_(None)` — a boolean sorts two
    #: rows deterministically by luck and stops doing so the moment a factor
    #: set carries two destination-specific rows, which is a flake nobody
    #: would enjoy diagnosing.
    assert seeded_session.scalars(
        select(FactorUpstream.destination_id)
        .where(FactorUpstream.factor_set_id == clone_id)
        .order_by(FactorUpstream.destination_id)
    ).all() == seeded_session.scalars(
        select(FactorUpstream.destination_id)
        .where(FactorUpstream.factor_set_id == published_id)
        .order_by(FactorUpstream.destination_id)
    ).all()

    publish_factor_set(seeded_session, clone_id, "alice")
    assert get_published_factor_set_id(seeded_session) == clone_id
    assert seeded_session.get(FactorSet, published_id).status == FactorSetStatus.archived
    assert len(
        seeded_session.scalars(
            select(FactorSet).where(FactorSet.status == FactorSetStatus.published)
        ).all()
    ) == 1
    with pytest.raises(FactorSetStateError):
        publish_factor_set(seeded_session, published_id, "alice")

    rollback_to(seeded_session, published_id, "alice")
    assert get_published_factor_set_id(seeded_session) == published_id
    assert seeded_session.get(FactorSet, clone_id).status == FactorSetStatus.archived
    assert seeded_session.scalar(select(func.count()).select_from(AuditLog)) >= 5


def test_expired_token_creates_a_new_submission_and_preserves_history(seeded_session):
    factor_set_id = get_published_factor_set_id(seeded_session)
    original_id, original_token = upsert_submission(
        seeded_session, None, _request(), factor_set_id
    )
    original = seeded_session.get(Submission, original_id)
    original.token_expires_at = utcnow() - timedelta(seconds=1)
    seeded_session.flush()

    new_id, new_token = upsert_submission(
        seeded_session, original_token, _request("20", None), factor_set_id
    )
    assert new_id != original_id
    assert new_token != original_token
    assert seeded_session.get(Submission, original_id).token is None
    assert seeded_session.scalar(select(func.count()).select_from(Submission)) == 2


def test_publish_refuses_a_preexisting_multiple_published_invariant_violation(seeded_session):
    published_id = get_published_factor_set_id(seeded_session)
    existing_draft = seeded_session.scalar(
        select(FactorSet).where(FactorSet.status == FactorSetStatus.draft)
    )
    existing_draft.status = FactorSetStatus.published
    target_id = clone_factor_set(
        seeded_session, published_id, "THIRD-v1", "alice"
    )
    seeded_session.flush()
    with pytest.raises(FactorSetStateError):
        publish_factor_set(seeded_session, target_id, "alice")
    assert len(
        seeded_session.scalars(
            select(FactorSet).where(FactorSet.status == FactorSetStatus.published)
        ).all()
    ) == 2


def test_stats_exclude_staff_flagged_submissions_and_bucket_null_food_as_unspecified(seeded_session):
    """Staff exclusion is unchanged; the NULL food category is not.

    This test asserted `standard_mix`, which is what the *engine* resolves a
    null food category to in order to pick a factor. v1.2's §5.4 rules that
    out for the statistics: `standard_mix` is a category a user chooses
    deliberately, and reporting the two as one both claims a composition the
    user never gave and makes the deliberate choice unreadable. The null is
    its own bucket, `unspecified`, suppressed on the same threshold as any
    other. The exclusion half of the test is untouched, and now also proves
    the join reaches `submission` -- `excluded_from_public` is not on
    `submission_entry`, which is what by_food_category groups over.
    """
    factor_set_id = get_published_factor_set_id(seeded_session)
    excluded_id, _ = upsert_submission(
        seeded_session, None, _request("100", None), factor_set_id
    )
    seeded_session.get(Submission, excluded_id).excluded_from_public = True
    upsert_submission(
        seeded_session,
        None,
        _request("7", None, food_category=None),
        factor_set_id,
    )
    seeded_session.flush()

    stats = get_public_stats(seeded_session, threshold=1)
    assert stats.total_calculations == 1
    assert stats.by_destination[0].total_kg == Decimal("7.000")
    assert stats.by_food_category[0].code == "unspecified"
    assert stats.by_food_category[0].label == "Not broken down by type"
    assert stats.by_food_category[0].count == 1
