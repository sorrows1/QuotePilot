"""Tenant-local authentication; additive upgrade preserves the tenant baseline."""

import sqlalchemy as sa
from alembic import op

revision = "0002_auth"
down_revision = "0001_tenant_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_logins",
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("locator", sa.String(64), nullable=False, unique=True),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("login", sa.String(128), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(24), nullable=False),
        sa.Column("disabled", sa.Boolean(), nullable=False),
        sa.Column("must_change_password", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("tenant_id", "login"),
        sa.UniqueConstraint("tenant_id", "id"),
        sa.CheckConstraint("role IN ('sales_admin', 'sales_manager', 'system_admin')"),
        sa.CheckConstraint("login ~ '^[a-z0-9][a-z0-9.@_+-]{0,127}$'"),
        sa.CheckConstraint("version > 0"),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("csrf", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["tenant_id", "user_id"], ["users.tenant_id", "users.id"]),
    )
    op.create_index("ix_auth_sessions_tenant_id", "auth_sessions", ["tenant_id"])
    op.create_table(
        "business_audit_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("actor_id", sa.Uuid()),
        sa.Column("action", sa.String(48), nullable=False),
        sa.Column("target_id", sa.Uuid()),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("evidence", sa.String(128), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["tenant_id", "actor_id"], ["users.tenant_id", "users.id"]),
    )
    op.create_index("ix_business_audit_events_tenant_id", "business_audit_events", ["tenant_id"])
    op.execute("""
        CREATE FUNCTION reject_audit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Business audit is append-only'; END; $$
    """)
    op.execute("""
        CREATE TRIGGER audit_append_only BEFORE UPDATE OR DELETE OR TRUNCATE
        ON business_audit_events FOR EACH STATEMENT EXECUTE FUNCTION reject_audit_mutation()
    """)
    op.create_table(
        "auth_limits",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    # Disposable databases only: backout destroys all auth/session/audit history.
    op.drop_table("auth_limits")
    op.drop_table("business_audit_events")
    op.execute("DROP FUNCTION reject_audit_mutation()")
    op.drop_table("auth_sessions")
    op.drop_table("users")
    op.drop_table("tenant_logins")
