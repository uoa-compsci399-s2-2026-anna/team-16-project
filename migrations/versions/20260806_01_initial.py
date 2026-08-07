"""Initial Part B schema plus E-owned staff tables in the shared chain."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "20260806_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("destination_group",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("is_waste", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table("sector",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table("food_category",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("is_standard_mix", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table("metric",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("display_unit", sa.String(32)),
        sa.Column("display_precision", sa.SmallInteger(), nullable=False, server_default="2"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.CheckConstraint("display_precision >= 0", name="ck_metric_precision"),
    )
    op.create_table("factor_set",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("version_label", sa.String(128), nullable=False, unique=True),
        sa.Column("status", sa.Enum("draft", "published", "archived", name="factorsetstatus"), nullable=False),
        sa.Column("is_mock", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("effective_from", sa.DateTime()),
        sa.Column("published_at", sa.DateTime()),
        sa.Column("published_by", sa.String(128)),
        sa.Column("notes", sa.Text()),
    )
    op.create_table("staff",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.Enum("admin", "staff", name="staffrole"), nullable=False, server_default="staff"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("mfa_secret_enc", mysql.VARBINARY(255)),
        sa.Column("mfa_enrolled_at", sa.DateTime()),
        sa.Column("mfa_last_counter", sa.BigInteger()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(64)),
        sa.Column("last_login_at", sa.DateTime()),
    )
    op.create_table("destination",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("destination_group.id"), nullable=False),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table("unit_preset",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("food_category_id", sa.Integer(), sa.ForeignKey("food_category.id")),
        sa.Column("kg_per_unit", sa.Numeric(12, 4), nullable=False),
        sa.Column("source_note", sa.Text()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.CheckConstraint("kg_per_unit >= 0", name="ck_unit_preset_kg"),
    )
    op.create_table("factor_upstream",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("factor_set_id", sa.Integer(), sa.ForeignKey("factor_set.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sector_id", sa.Integer(), sa.ForeignKey("sector.id"), nullable=False),
        sa.Column("food_category_id", sa.Integer(), sa.ForeignKey("food_category.id"), nullable=False),
        sa.Column("metric_id", sa.Integer(), sa.ForeignKey("metric.id"), nullable=False),
        sa.Column("value_per_kg", sa.Numeric(20, 10), nullable=False),
        sa.UniqueConstraint(
            "factor_set_id",
            "sector_id",
            "food_category_id",
            "metric_id",
            name="uq_factor_upstream_scope",
        ),
    )
    op.create_table("factor_downstream",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("factor_set_id", sa.Integer(), sa.ForeignKey("factor_set.id", ondelete="CASCADE"), nullable=False),
        sa.Column("destination_id", sa.Integer(), sa.ForeignKey("destination.id"), nullable=False),
        sa.Column("food_category_id", sa.Integer(), sa.ForeignKey("food_category.id")),
        sa.Column("metric_id", sa.Integer(), sa.ForeignKey("metric.id"), nullable=False),
        sa.Column("value_per_kg", sa.Numeric(20, 10), nullable=False),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_factor_downstream_scope "
        "ON factor_downstream "
        "(factor_set_id, destination_id, "
        "(COALESCE(food_category_id, 0)), metric_id)"
    )
    op.create_table("constant",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("factor_set_id", sa.Integer(), sa.ForeignKey("factor_set.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("value", sa.Numeric(20, 10), nullable=False),
        sa.Column("unit", sa.String(32)), sa.Column("note", sa.Text()),
        sa.UniqueConstraint("factor_set_id", "code", name="uq_constant_scope"),
    )
    op.create_table("formula",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("factor_set_id", sa.Integer(), sa.ForeignKey("factor_set.id", ondelete="CASCADE"), nullable=False),
        sa.Column("metric_id", sa.Integer(), sa.ForeignKey("metric.id"), nullable=False),
        sa.Column("expression", sa.Text(), nullable=False), sa.Column("notes", sa.Text()),
        sa.UniqueConstraint("factor_set_id", "metric_id", name="uq_formula_scope"),
    )
    op.create_table("equivalence",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("factor_set_id", sa.Integer(), sa.ForeignKey("factor_set.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(64), nullable=False), sa.Column("name", sa.String(128), nullable=False),
        sa.Column("source_metric_id", sa.Integer(), sa.ForeignKey("metric.id"), nullable=False),
        sa.Column("value_per_unit", sa.Numeric(20, 10), nullable=False),
        sa.Column("label_template", sa.String(255), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("factor_set_id", "code", name="uq_equivalence_scope"),
    )
    op.create_table("submission",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("token", sa.String(36), unique=True), sa.Column("token_expires_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("factor_set_id", sa.Integer(), sa.ForeignKey("factor_set.id"), nullable=False),
        sa.Column("sector_id", sa.Integer(), sa.ForeignKey("sector.id"), nullable=False),
        sa.Column("food_category_id", sa.Integer(), sa.ForeignKey("food_category.id")),
        sa.Column("gwp_horizon", sa.SmallInteger(), nullable=False, server_default="100"),
        sa.Column("excluded_from_public", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("exclusion_reason", sa.String(255)),
        sa.CheckConstraint("gwp_horizon IN (20, 100)", name="ck_submission_horizon"),
    )
    op.create_table("submission_line",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("submission_id", sa.BigInteger(), sa.ForeignKey("submission.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scenario", sa.Enum("current", "alternative", name="scenario"), nullable=False),
        sa.Column("destination_id", sa.Integer(), sa.ForeignKey("destination.id"), nullable=False),
        sa.Column("qty_kg", sa.Numeric(16, 3), nullable=False),
        sa.UniqueConstraint("submission_id", "scenario", "destination_id", name="uq_submission_line_scope"),
        sa.CheckConstraint("qty_kg >= 0", name="ck_submission_line_qty"),
    )
    op.create_table("audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("at", sa.DateTime(), nullable=False), sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("action", sa.String(32), nullable=False), sa.Column("table_name", sa.String(64), nullable=False),
        sa.Column("row_id", sa.BigInteger()), sa.Column("before_json", sa.JSON()), sa.Column("after_json", sa.JSON()),
    )
    op.create_table("staff_recovery_code",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("staff_id", sa.Integer(), sa.ForeignKey("staff.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code_hash", sa.CHAR(64), nullable=False), sa.Column("used_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    for table in (
        "staff_recovery_code", "audit_log", "submission_line", "submission",
        "equivalence", "formula", "constant", "factor_downstream",
        "factor_upstream", "unit_preset", "destination", "staff",
        "factor_set", "metric", "food_category", "sector", "destination_group",
    ):
        op.drop_table(table)
