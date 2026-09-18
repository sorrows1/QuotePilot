"""QT-004 typed setup and immutable history; destructive backout is disposable-only."""

from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0004_tenant_settings"
down_revision = "0003_auth_commercial_merge"
branch_labels = None
depends_on = None


def fields() -> list[sa.Column[Any]]:
    return [
        sa.Column("company_name", sa.String(200)),
        sa.Column("company_address", sa.String(1000)),
        sa.Column("current_logo_asset_id", sa.Uuid()),
        sa.Column("currency_code", sa.String(3), nullable=False, server_default="SGD"),
        sa.Column("tax_enabled", sa.Boolean()),
        sa.Column("tax_rate", sa.Numeric(7, 6)),
        sa.Column("freight_taxable", sa.Boolean()),
        sa.Column("default_quote_validity_days", sa.Integer()),
        sa.Column("inventory_max_age_minutes", sa.Integer()),
        sa.Column("quote_number_prefix", sa.String(10)),
        sa.Column("discount_approval_enabled", sa.Boolean()),
        sa.Column("discount_approval_threshold", sa.Numeric(7, 6)),
        sa.Column("quote_margin_control_enabled", sa.Boolean()),
        sa.Column("minimum_margin_threshold", sa.Numeric(7, 6)),
        sa.Column("line_margin_control_enabled", sa.Boolean()),
        sa.Column("minimum_line_margin_threshold", sa.Numeric(7, 6)),
        sa.Column("quote_value_approval_enabled", sa.Boolean()),
        sa.Column("quote_value_approval_threshold", sa.Numeric(20, 2)),
    ]


def upgrade() -> None:
    op.create_table(
        "brand_assets",
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("creator_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["tenant_id", "creator_id"], ["users.tenant_id", "users.id"]),
        sa.CheckConstraint("width BETWEEN 16 AND 2048 AND height BETWEEN 16 AND 2048"),
        sa.CheckConstraint("byte_size = octet_length(data) AND byte_size BETWEEN 1 AND 4194304"),
    )
    op.create_table(
        "settings_revisions",
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("business_timezone", sa.String(255), nullable=False),
        sa.Column("creator_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        *fields(),
        sa.ForeignKeyConstraint(["tenant_id", "creator_id"], ["users.tenant_id", "users.id"]),
        sa.CheckConstraint("revision > 0"),
    )
    for column in fields():
        op.add_column("tenant_settings", column)
    op.add_column("tenant_settings", sa.Column("setup_completed_at", sa.DateTime(timezone=True)))
    op.add_column(
        "tenant_settings",
        sa.Column("edit_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("tenant_settings", sa.Column("active_settings_revision", sa.Integer()))
    op.create_check_constraint("settings_edit_version", "tenant_settings", "edit_version > 0")
    op.create_check_constraint(
        "settings_complete_pair",
        "tenant_settings",
        "(setup_completed_at IS NULL) = (active_settings_revision IS NULL)",
    )
    op.create_foreign_key(
        "settings_active_revision",
        "tenant_settings",
        "settings_revisions",
        ["tenant_id", "active_settings_revision"],
        ["tenant_id", "revision"],
    )
    for table in ("tenant_settings", "settings_revisions"):
        op.create_foreign_key(
            table + "_logo",
            table,
            "brand_assets",
            ["tenant_id", "current_logo_asset_id"],
            ["tenant_id", "id"],
        )
        checks = {
            "currency": "currency_code = 'SGD'",
            "name": "company_name IS NULL OR length(btrim(company_name)) BETWEEN 1 AND 200",
            "validity": "default_quote_validity_days BETWEEN 1 AND 365",
            "freshness": "inventory_max_age_minutes > 0",
            "prefix": "quote_number_prefix ~ '^[A-Z0-9]{1,10}$'",
            "tax": "tax_rate BETWEEN 0 AND 1",
            "discount": "discount_approval_threshold BETWEEN 0 AND 1",
            "quote_margin": "minimum_margin_threshold BETWEEN 0 AND 1",
            "line_margin": "minimum_line_margin_threshold BETWEEN 0 AND 1",
            "value": (
                "quote_value_approval_threshold >= 0 AND "
                "quote_value_approval_threshold <> 'NaN'::numeric"
            ),
            "inactive_tax": "tax_enabled IS TRUE OR (tax_rate IS NULL AND freight_taxable IS NULL)",
        }
        controls = [
            ("discount_approval_enabled", "discount_approval_threshold"),
            ("quote_margin_control_enabled", "minimum_margin_threshold"),
            ("line_margin_control_enabled", "minimum_line_margin_threshold"),
            ("quote_value_approval_enabled", "quote_value_approval_threshold"),
        ]
        for enabled, threshold in controls:
            checks[enabled] = f"{enabled} IS TRUE OR {threshold} IS NULL"
        complete = (
            "company_name IS NOT NULL AND default_quote_validity_days IS NOT NULL "
            "AND inventory_max_age_minutes IS NOT NULL AND quote_number_prefix IS NOT NULL "
            "AND tax_enabled IS NOT NULL AND (tax_enabled IS FALSE OR "
            "(tax_rate IS NOT NULL AND freight_taxable IS NOT NULL))"
        )
        for enabled, threshold in controls:
            complete += (
                f" AND {enabled} IS NOT NULL AND ({enabled} IS FALSE OR {threshold} IS NOT NULL)"
            )
        checks["complete"] = (
            complete
            if table == "settings_revisions"
            else f"setup_completed_at IS NULL OR ({complete})"
        )
        for name, sql in checks.items():
            op.create_check_constraint(table + "_" + name, table, sql)
    op.create_table(
        "quote_number_counters",
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("next_sequence", sa.BigInteger(), nullable=False, server_default="1"),
        sa.CheckConstraint("next_sequence > 0"),
    )
    op.execute("INSERT INTO quote_number_counters (tenant_id) SELECT id FROM tenants")
    op.create_table(
        "quote_numbers",
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("subject_id", sa.Uuid(), primary_key=True),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("number", sa.String(40), nullable=False),
        sa.Column("prefix", sa.String(10), nullable=False),
        sa.Column("settings_revision", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "settings_revision"],
            ["settings_revisions.tenant_id", "settings_revisions.revision"],
        ),
        sa.UniqueConstraint("tenant_id", "sequence"),
        sa.UniqueConstraint("tenant_id", "number"),
        sa.CheckConstraint("sequence > 0"),
        sa.CheckConstraint("subject_id <> '00000000-0000-0000-0000-000000000000'::uuid"),
        sa.CheckConstraint("prefix ~ '^[A-Z0-9]{1,10}$'"),
        sa.CheckConstraint(
            "number = prefix || '-' || "
            "lpad(sequence::text, greatest(6, length(sequence::text)), '0')"
        ),
    )
    op.execute(
        """CREATE FUNCTION reject_settings_history_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Settings history is immutable'; END; $$"""
    )
    for table in ("settings_revisions", "brand_assets", "quote_numbers"):
        op.execute(
            f"CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE OR TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION reject_settings_history_mutation()"
        )


def downgrade() -> None:
    op.drop_table("quote_numbers")
    op.drop_table("quote_number_counters")
    op.drop_constraint("settings_active_revision", "tenant_settings", type_="foreignkey")
    op.drop_constraint("tenant_settings_logo", "tenant_settings", type_="foreignkey")
    op.drop_table("settings_revisions")
    op.drop_table("brand_assets")
    op.execute("DROP FUNCTION reject_settings_history_mutation()")
    # Dropping columns removes their dependent check constraints.
    for name in [
        "active_settings_revision",
        "setup_completed_at",
        "edit_version",
        *[c.name for c in fields()],
    ]:
        op.drop_column("tenant_settings", name)
