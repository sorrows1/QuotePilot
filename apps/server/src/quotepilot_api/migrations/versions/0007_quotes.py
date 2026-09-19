"""QT-008 append-only manual quotes. Back up quote evidence before downgrade."""

from alembic import op

revision = "0007_quotes"
down_revision = "0006_archive_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE quote_cases (
          tenant_id uuid NOT NULL REFERENCES tenants(id), id uuid NOT NULL,
          creator_id uuid NOT NULL, created_at timestamptz NOT NULL,
          version integer NOT NULL CHECK (version >= 0), PRIMARY KEY (tenant_id,id),
          FOREIGN KEY (tenant_id,creator_id) REFERENCES users(tenant_id,id));
        CREATE TABLE quote_revisions (
          tenant_id uuid NOT NULL, id uuid NOT NULL, case_id uuid NOT NULL,
          revision integer NOT NULL CHECK (revision > 0), customer_id uuid NOT NULL,
          creator_id uuid NOT NULL, created_at timestamptz NOT NULL,
          settings_revision integer NOT NULL, pricing_as_of timestamptz NOT NULL,
          state varchar(32) NOT NULL CHECK (state IN
            ('CALCULATED','HARD_BLOCK','CLARIFICATION_REQUIRED','APPROVAL_REQUIRED')),
          commercial_fingerprint varchar(64) NOT NULL, freight numeric NOT NULL,
          total numeric, snapshot jsonb NOT NULL, inputs jsonb NOT NULL,
          exception_set jsonb NOT NULL, PRIMARY KEY (tenant_id,id),
          UNIQUE (tenant_id,case_id,revision),
          FOREIGN KEY (tenant_id,case_id) REFERENCES quote_cases(tenant_id,id),
          FOREIGN KEY (tenant_id,customer_id) REFERENCES customers(tenant_id,id),
          FOREIGN KEY (tenant_id,creator_id) REFERENCES users(tenant_id,id),
          FOREIGN KEY (tenant_id,settings_revision)
            REFERENCES settings_revisions(tenant_id,revision));
        CREATE TABLE quote_lines (
          tenant_id uuid NOT NULL, revision_id uuid NOT NULL, line_id varchar(100) NOT NULL,
          product_id uuid NOT NULL, quantity numeric NOT NULL CHECK (quantity > 0),
          quote_uom varchar(30) NOT NULL, pricing_uom varchar(30) NOT NULL,
          negotiated_unit_price numeric CHECK (negotiated_unit_price >= 0),
          PRIMARY KEY (tenant_id,revision_id,line_id),
          FOREIGN KEY (tenant_id,revision_id) REFERENCES quote_revisions(tenant_id,id),
          FOREIGN KEY (tenant_id,product_id) REFERENCES products(tenant_id,id));
        CREATE TABLE quote_requests (
          tenant_id uuid NOT NULL REFERENCES tenants(id), request_key varchar(100) NOT NULL,
          payload_hash varchar(64) NOT NULL, response jsonb NOT NULL,
          PRIMARY KEY (tenant_id,request_key));
        CREATE FUNCTION qt008_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Quote evidence is immutable' USING ERRCODE = '23514'; END $$;
        CREATE TRIGGER immutable_revision BEFORE UPDATE OR DELETE ON quote_revisions
          FOR EACH ROW EXECUTE FUNCTION qt008_immutable();
        CREATE TRIGGER immutable_line BEFORE UPDATE OR DELETE ON quote_lines
          FOR EACH ROW EXECUTE FUNCTION qt008_immutable();
        CREATE TRIGGER immutable_request BEFORE UPDATE OR DELETE ON quote_requests
          FOR EACH ROW EXECUTE FUNCTION qt008_immutable();
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE quote_requests, quote_lines, quote_revisions, quote_cases;
        DROP FUNCTION qt008_immutable();
    """)
