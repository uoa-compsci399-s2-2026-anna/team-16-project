"""MySQL 8 checks. Set TEST_DATABASE_URL to a dedicated Compose database to run."""

import os
import uuid
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.models import (
    Destination,
    DestinationGroup,
    FactorDownstream,
    FactorSet,
    FactorSetStatus,
    FoodCategory,
    Metric,
)

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="set TEST_DATABASE_URL to a dedicated MySQL 8 database",
    ),
]


def test_mysql_upgrade_constraints_decimal_and_cascade(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.upgrade(config, "head")

    from sqlalchemy import create_engine

    engine = create_engine(TEST_DATABASE_URL)
    assert {
        "factor_set",
        "factor_downstream",
        "submission",
        "audit_log",
        "staff",
        "staff_recovery_code",
    }.issubset(inspect(engine).get_table_names())

    suffix = uuid.uuid4().hex[:10]
    with Session(engine) as session:
        group = DestinationGroup(
            code=f"group_{suffix}", name="Integration group", is_waste=True
        )
        food = FoodCategory(
            code=f"food_{suffix}", name="Integration food", is_standard_mix=False
        )
        metric = Metric(
            code=f"metric_{suffix}",
            name="Integration metric",
            unit="unit",
            display_precision=2,
        )
        factor_set = FactorSet(
            version_label=f"integration-{suffix}",
            status=FactorSetStatus.draft,
            is_mock=True,
        )
        session.add_all([group, food, metric, factor_set])
        session.flush()
        destination = Destination(
            group_id=group.id,
            code=f"destination_{suffix}",
            name="Integration destination",
        )
        session.add(destination)
        session.flush()
        row = FactorDownstream(
            factor_set_id=factor_set.id,
            destination_id=destination.id,
            food_category_id=None,
            metric_id=metric.id,
            value_per_kg=Decimal("-0.1234567890"),
        )
        session.add(row)
        session.flush()
        assert session.get(FactorDownstream, row.id).value_per_kg == Decimal(
            "-0.1234567890"
        )
        factor_set_id = factor_set.id
        destination_id = destination.id
        metric_id = metric.id
        session.commit()

    with Session(engine) as session:
        session.add(
            FactorDownstream(
                factor_set_id=factor_set_id,
                destination_id=destination_id,
                food_category_id=None,
                metric_id=metric_id,
                value_per_kg=Decimal("1"),
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    with Session(engine) as session:
        factor_set_id = session.scalar(
            select(FactorSet.id).where(
                FactorSet.version_label == f"integration-{suffix}"
            )
        )
        if factor_set_id is not None:
            session.delete(session.get(FactorSet, factor_set_id))
            session.flush()
            assert session.scalar(
                select(func.count()).select_from(FactorDownstream).where(
                    FactorDownstream.factor_set_id == factor_set_id
                )
            ) == 0
            destination = session.scalar(
                select(Destination).where(
                    Destination.code == f"destination_{suffix}"
                )
            )
            group = session.scalar(
                select(DestinationGroup).where(
                    DestinationGroup.code == f"group_{suffix}"
                )
            )
            food = session.scalar(
                select(FoodCategory).where(
                    FoodCategory.code == f"food_{suffix}"
                )
            )
            metric = session.scalar(
                select(Metric).where(Metric.code == f"metric_{suffix}")
            )
            session.delete(destination)
            session.delete(group)
            session.delete(food)
            session.delete(metric)
            session.commit()
    engine.dispose()
