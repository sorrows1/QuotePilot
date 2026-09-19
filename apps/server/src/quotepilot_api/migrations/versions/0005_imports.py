"""QT-006 durable import staging and idempotency. Backout removes import evidence."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_imports"
down_revision = "0004_tenant_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "import_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("filename", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("headers", postgresql.JSONB(), nullable=False),
        sa.Column("rows", postgresql.JSONB(), nullable=False),
        sa.Column("mapping", postgresql.JSONB()),
        sa.Column("mode", sa.String(32)),
        sa.Column("preview_token", sa.String(64)),
        sa.Column("preview", postgresql.JSONB()),
        sa.Column("commit_key", sa.String(100)),
        sa.Column("result", postgresql.JSONB()),
        sa.UniqueConstraint("tenant_id", "id"),
        sa.UniqueConstraint("tenant_id", "commit_key"),
        sa.ForeignKeyConstraint(["tenant_id", "actor_id"], ["users.tenant_id", "users.id"]),
    )


def downgrade() -> None:
    op.drop_table("import_jobs")
