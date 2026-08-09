from decimal import Decimal
from types import SimpleNamespace

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from db.errors import FactorSetStateError
from db.models import (
    AuditLog,
    Constant,
    FactorSet,
    FactorSetStatus,
    FactorUpstream,
    Scenario,
    Submission,
    SubmissionLine,
    utcnow,
)
from db.repository import (
    clone_factor_set,
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
    line = lambda qty: SimpleNamespace(destination_code="landfill", qty_kg=Decimal(qty))
    scenario = lambda qty: SimpleNamespace(
        sector_code="processing", food_category_code=food_category, lines=(line(qty),)
    )
    return SimpleNamespace(
        current=scenario(current),
        alternative=scenario(alternative) if alternative is not None else None,
        gwp_horizon=100,
    )


def test_upsert_reuses_a_valid_token_and_replaces_lines(seeded_session):
    factor_set_id = seeded_session.scalar(select(Submission.factor_set_id).limit(1))
    if factor_set_id is None:
        from db.repository import get_published_factor_set_id
        factor_set_id = get_published_factor_set_id(seeded_session)
    submission_id, token = upsert_submission(seeded_session, None, _request(), factor_set_id)
    second_id, second_token = upsert_submission(seeded_session, token, _request("20", "30"), factor_set_id)
    assert second_id == submission_id
    assert second_token == token
    lines = seeded_session.scalars(select(SubmissionLine).where(SubmissionLine.submission_id == submission_id)).all()
    assert {line.qty_kg for line in lines} == {Decimal("20.000"), Decimal("30.000")}


def test_public_stats_use_current_only(seeded_session):
    from db.repository import get_published_factor_set_id
    upsert_submission(seeded_session, None, _request("10", "999"), get_published_factor_set_id(seeded_session))
    stats = get_public_stats(seeded_session, threshold=1)
    assert stats.by_destination[0].total_kg == Decimal("10.000")


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

    invalidate_factor_bundle(published_id)
    rebuilt = load_factor_bundle(seeded_session, published_id, bundle_factory=factory)
    assert rebuilt is not first
    assert built == ["MOCK-v0", "DRAFT-v1", "MOCK-v0"]


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


def test_stats_exclude_staff_flagged_submissions_and_map_null_food_to_standard_mix(seeded_session):
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
    assert stats.by_food_category[0].code == "standard_mix"
    assert stats.by_food_category[0].count == 1
