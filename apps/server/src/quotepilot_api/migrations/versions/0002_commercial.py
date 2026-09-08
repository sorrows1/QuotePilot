"""QT-005 commercial authority; frozen DDL, no runtime model dependency."""

from alembic import op

revision = "0002_commercial"
down_revision = "0001_tenant_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE customers (
                id UUID NOT NULL,
                tenant_id UUID NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
                archived BOOLEAN DEFAULT 'false' NOT NULL,
                external_key VARCHAR(100),
                name VARCHAR(255) NOT NULL,
                PRIMARY KEY (id),
                UNIQUE (tenant_id, external_key),
                CONSTRAINT customers_tenant_id UNIQUE (tenant_id, id),
                FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT,
                CONSTRAINT external_key_not_blank CHECK (external_key IS NULL OR
        length(trim(external_key)) > 0),
                CONSTRAINT name_not_blank CHECK (name IS NULL OR length(trim(name)) > 0)
        )
    """)
    op.execute("""
        CREATE TABLE products (
                id UUID NOT NULL,
                tenant_id UUID NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
                archived BOOLEAN DEFAULT 'false' NOT NULL,
                sku VARCHAR(100) NOT NULL,
                name VARCHAR(255) NOT NULL,
                description VARCHAR(4000) NOT NULL,
                manufacturer VARCHAR(255),
                model_number VARCHAR(255),
                PRIMARY KEY (id),
                UNIQUE (tenant_id, sku),
                CONSTRAINT products_tenant_id UNIQUE (tenant_id, id),
                FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT,
                CONSTRAINT sku_not_blank CHECK (sku IS NULL OR length(trim(sku)) > 0),
                CONSTRAINT name_not_blank CHECK (name IS NULL OR length(trim(name)) > 0),
                CONSTRAINT description_not_blank CHECK (description IS NULL OR
        length(trim(description)) > 0),
                CONSTRAINT manufacturer_not_blank CHECK (manufacturer IS NULL OR
        length(trim(manufacturer)) > 0),
                CONSTRAINT model_number_not_blank CHECK (model_number IS NULL OR
        length(trim(model_number)) > 0)
        )
    """)
    op.execute("""
        CREATE TABLE product_aliases (
                id UUID NOT NULL,
                tenant_id UUID NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
                archived BOOLEAN DEFAULT 'false' NOT NULL,
                customer_id UUID NOT NULL,
                product_id UUID NOT NULL,
                alias VARCHAR(255) NOT NULL,
                source VARCHAR(1000) NOT NULL,
                PRIMARY KEY (id),
                FOREIGN KEY(tenant_id, customer_id) REFERENCES customers (tenant_id, id) ON
        DELETE RESTRICT,
                FOREIGN KEY(tenant_id, product_id) REFERENCES products (tenant_id, id) ON
        DELETE RESTRICT,
                UNIQUE (tenant_id, customer_id, alias),
                CONSTRAINT product_aliases_tenant_id UNIQUE (tenant_id, id),
                FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT,
                CONSTRAINT alias_not_blank CHECK (alias IS NULL OR length(trim(alias)) > 0),
                CONSTRAINT source_not_blank CHECK (source IS NULL OR length(trim(source)) >
        0)
        )
    """)
    op.execute("""
        CREATE INDEX ix_product_aliases_customer_id ON product_aliases (tenant_id,
        customer_id)
    """)
    op.execute("""
        CREATE INDEX ix_product_aliases_product_id ON product_aliases (tenant_id,
        product_id)
    """)
    op.execute("""
        CREATE TABLE product_successors (
                id UUID NOT NULL,
                tenant_id UUID NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
                archived BOOLEAN DEFAULT 'false' NOT NULL,
                product_id UUID NOT NULL,
                successor_id UUID NOT NULL,
                source VARCHAR(1000) NOT NULL,
                PRIMARY KEY (id),
                FOREIGN KEY(tenant_id, product_id) REFERENCES products (tenant_id, id) ON
        DELETE RESTRICT,
                FOREIGN KEY(tenant_id, successor_id) REFERENCES products (tenant_id, id) ON
        DELETE RESTRICT,
                CHECK (product_id <> successor_id),
                UNIQUE (tenant_id, product_id, successor_id),
                CONSTRAINT product_successors_tenant_id UNIQUE (tenant_id, id),
                FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT,
                CONSTRAINT source_not_blank CHECK (source IS NULL OR length(trim(source)) >
        0)
        )
    """)
    op.execute("""
        CREATE INDEX ix_product_successors_product_id ON product_successors (tenant_id,
        product_id)
    """)
    op.execute("""
        CREATE INDEX ix_product_successors_successor_id ON product_successors (tenant_id,
        successor_id)
    """)
    op.execute("""
        CREATE TABLE pricebooks (
                id UUID NOT NULL,
                tenant_id UUID NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
                archived BOOLEAN DEFAULT 'false' NOT NULL,
                key VARCHAR(100) NOT NULL,
                version INTEGER NOT NULL,
                valid_from TIMESTAMP WITH TIME ZONE NOT NULL,
                valid_to TIMESTAMP WITH TIME ZONE,
                source VARCHAR(1000) NOT NULL,
                PRIMARY KEY (id),
                CONSTRAINT valid_window CHECK (isfinite(valid_from) AND (valid_to IS NULL OR
        (isfinite(valid_to) AND valid_to > valid_from))),
                CHECK (version > 0),
                UNIQUE (tenant_id, key, version),
                CONSTRAINT pricebooks_tenant_id UNIQUE (tenant_id, id),
                FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT,
                CONSTRAINT key_not_blank CHECK (key IS NULL OR length(trim(key)) > 0),
                CONSTRAINT source_not_blank CHECK (source IS NULL OR length(trim(source)) >
        0)
        )
    """)
    op.execute("""
        CREATE TABLE pricebook_assignments (
                id UUID NOT NULL,
                tenant_id UUID NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
                archived BOOLEAN DEFAULT 'false' NOT NULL,
                customer_id UUID,
                pricebook_id UUID NOT NULL,
                valid_from TIMESTAMP WITH TIME ZONE NOT NULL,
                valid_to TIMESTAMP WITH TIME ZONE,
                source VARCHAR(1000) NOT NULL,
                PRIMARY KEY (id),
                FOREIGN KEY(tenant_id, customer_id) REFERENCES customers (tenant_id, id) ON
        DELETE RESTRICT,
                FOREIGN KEY(tenant_id, pricebook_id) REFERENCES pricebooks (tenant_id, id)
        ON DELETE RESTRICT,
                CONSTRAINT valid_window CHECK (isfinite(valid_from) AND (valid_to IS NULL OR
        (isfinite(valid_to) AND valid_to > valid_from))),
                CONSTRAINT pricebook_assignments_tenant_id UNIQUE (tenant_id, id),
                FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT,
                CONSTRAINT source_not_blank CHECK (source IS NULL OR length(trim(source)) >
        0)
        )
    """)
    op.execute("""
        CREATE INDEX ix_pricebook_assignments_customer_id ON pricebook_assignments
        (tenant_id, customer_id)
    """)
    op.execute("""
        CREATE INDEX ix_pricebook_assignments_pricebook_id ON pricebook_assignments
        (tenant_id, pricebook_id)
    """)
    op.execute("""
        CREATE TABLE uom_conversions (
                id UUID NOT NULL,
                tenant_id UUID NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
                archived BOOLEAN DEFAULT 'false' NOT NULL,
                product_id UUID NOT NULL,
                from_uom VARCHAR(30) NOT NULL,
                to_uom VARCHAR(30) NOT NULL,
                factor NUMERIC NOT NULL,
                valid_from TIMESTAMP WITH TIME ZONE NOT NULL,
                valid_to TIMESTAMP WITH TIME ZONE,
                source VARCHAR(1000) NOT NULL,
                PRIMARY KEY (id),
                FOREIGN KEY(tenant_id, product_id) REFERENCES products (tenant_id, id) ON
        DELETE RESTRICT,
                CONSTRAINT factor_exact CHECK (factor IS NULL OR (scale(factor) <= 6 AND
        abs(factor) < 1000000000000000000 AND factor > 0)),
                CONSTRAINT valid_window CHECK (isfinite(valid_from) AND (valid_to IS NULL OR
        (isfinite(valid_to) AND valid_to > valid_from))),
                CHECK (from_uom <> to_uom),
                UNIQUE (tenant_id, product_id, id),
                CONSTRAINT uom_conversions_tenant_id UNIQUE (tenant_id, id),
                FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT,
                CONSTRAINT from_uom_not_blank CHECK (from_uom IS NULL OR
        length(trim(from_uom)) > 0),
                CONSTRAINT to_uom_not_blank CHECK (to_uom IS NULL OR length(trim(to_uom)) >
        0),
                CONSTRAINT source_not_blank CHECK (source IS NULL OR length(trim(source)) >
        0)
        )
    """)
    op.execute("""
        CREATE INDEX ix_uom_conversions_product_id ON uom_conversions (tenant_id,
        product_id)
    """)
    op.execute("""
        CREATE TABLE prices (
                id UUID NOT NULL,
                tenant_id UUID NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
                archived BOOLEAN DEFAULT 'false' NOT NULL,
                product_id UUID NOT NULL,
                pricebook_id UUID NOT NULL,
                uom VARCHAR(30) NOT NULL,
                unit_price NUMERIC NOT NULL,
                quantity_min NUMERIC NOT NULL,
                quantity_max NUMERIC,
                valid_from TIMESTAMP WITH TIME ZONE NOT NULL,
                valid_to TIMESTAMP WITH TIME ZONE,
                source VARCHAR(1000) NOT NULL,
                PRIMARY KEY (id),
                FOREIGN KEY(tenant_id, product_id) REFERENCES products (tenant_id, id) ON
        DELETE RESTRICT,
                FOREIGN KEY(tenant_id, pricebook_id) REFERENCES pricebooks (tenant_id, id)
        ON DELETE RESTRICT,
                CONSTRAINT unit_price_exact CHECK (unit_price IS NULL OR (scale(unit_price)
        <= 6 AND abs(unit_price) < 1000000000000000000 AND unit_price >= 0)),
                CONSTRAINT quantity_min_exact CHECK (quantity_min IS NULL OR
        (scale(quantity_min) <= 6 AND abs(quantity_min) < 1000000000000000000 AND
        quantity_min > 0)),
                CONSTRAINT quantity_max_exact CHECK (quantity_max IS NULL OR
        (scale(quantity_max) <= 6 AND abs(quantity_max) < 1000000000000000000 AND
        quantity_max > 0)),
                CONSTRAINT valid_window CHECK (isfinite(valid_from) AND (valid_to IS NULL OR
        (isfinite(valid_to) AND valid_to > valid_from))),
                CHECK (quantity_max IS NULL OR quantity_max > quantity_min),
                UNIQUE (tenant_id, product_id, pricebook_id, id),
                CONSTRAINT prices_tenant_id UNIQUE (tenant_id, id),
                FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT,
                CONSTRAINT uom_not_blank CHECK (uom IS NULL OR length(trim(uom)) > 0),
                CONSTRAINT source_not_blank CHECK (source IS NULL OR length(trim(source)) >
        0)
        )
    """)
    op.execute("""
        CREATE INDEX ix_prices_pricebook_id ON prices (tenant_id, pricebook_id)
    """)
    op.execute("""
        CREATE INDEX ix_prices_product_id ON prices (tenant_id, product_id)
    """)
    op.execute("""
        CREATE TABLE product_costs (
                id UUID NOT NULL,
                tenant_id UUID NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
                archived BOOLEAN DEFAULT 'false' NOT NULL,
                product_id UUID NOT NULL,
                uom VARCHAR(30) NOT NULL,
                unit_cost NUMERIC NOT NULL,
                valid_from TIMESTAMP WITH TIME ZONE NOT NULL,
                valid_to TIMESTAMP WITH TIME ZONE,
                source VARCHAR(1000) NOT NULL,
                PRIMARY KEY (id),
                FOREIGN KEY(tenant_id, product_id) REFERENCES products (tenant_id, id) ON
        DELETE RESTRICT,
                CONSTRAINT unit_cost_exact CHECK (unit_cost IS NULL OR (scale(unit_cost) <=
        6 AND abs(unit_cost) < 1000000000000000000 AND unit_cost >= 0)),
                CONSTRAINT valid_window CHECK (isfinite(valid_from) AND (valid_to IS NULL OR
        (isfinite(valid_to) AND valid_to > valid_from))),
                UNIQUE (tenant_id, product_id, id),
                CONSTRAINT product_costs_tenant_id UNIQUE (tenant_id, id),
                FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT,
                CONSTRAINT uom_not_blank CHECK (uom IS NULL OR length(trim(uom)) > 0),
                CONSTRAINT source_not_blank CHECK (source IS NULL OR length(trim(source)) >
        0)
        )
    """)
    op.execute("""
        CREATE INDEX ix_product_costs_product_id ON product_costs (tenant_id, product_id)
    """)
    op.execute("""
        CREATE TABLE inventory (
                id UUID NOT NULL,
                tenant_id UUID NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
                archived BOOLEAN DEFAULT 'false' NOT NULL,
                product_id UUID NOT NULL,
                uom VARCHAR(30) NOT NULL,
                quantity NUMERIC NOT NULL,
                observed_at TIMESTAMP WITH TIME ZONE NOT NULL,
                imported_at TIMESTAMP WITH TIME ZONE NOT NULL,
                source VARCHAR(1000) NOT NULL,
                PRIMARY KEY (id),
                FOREIGN KEY(tenant_id, product_id) REFERENCES products (tenant_id, id) ON
        DELETE RESTRICT,
                CONSTRAINT quantity_exact CHECK (quantity IS NULL OR (scale(quantity) <= 6
        AND abs(quantity) < 1000000000000000000)),
                CHECK (isfinite(observed_at) AND isfinite(imported_at) AND observed_at <=
        imported_at),
                UNIQUE (tenant_id, product_id, observed_at),
                UNIQUE (tenant_id, product_id, id),
                CONSTRAINT inventory_tenant_id UNIQUE (tenant_id, id),
                FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT,
                CONSTRAINT uom_not_blank CHECK (uom IS NULL OR length(trim(uom)) > 0),
                CONSTRAINT source_not_blank CHECK (source IS NULL OR length(trim(source)) >
        0)
        )
    """)
    op.execute("""
        CREATE INDEX ix_inventory_product_id ON inventory (tenant_id, product_id)
    """)
    op.execute("""
        CREATE TABLE discount_policies (
                id UUID NOT NULL,
                tenant_id UUID NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
                archived BOOLEAN DEFAULT 'false' NOT NULL,
                customer_id UUID,
                key VARCHAR(100) NOT NULL,
                version INTEGER NOT NULL,
                rate NUMERIC NOT NULL,
                permitted BOOLEAN NOT NULL,
                valid_from TIMESTAMP WITH TIME ZONE NOT NULL,
                valid_to TIMESTAMP WITH TIME ZONE,
                source VARCHAR(1000) NOT NULL,
                PRIMARY KEY (id),
                FOREIGN KEY(tenant_id, customer_id) REFERENCES customers (tenant_id, id) ON
        DELETE RESTRICT,
                CONSTRAINT rate_exact CHECK (rate IS NULL OR (scale(rate) <= 6 AND abs(rate)
        < 1000000000000000000 AND rate >= 0 AND rate <= 1)),
                CONSTRAINT valid_window CHECK (isfinite(valid_from) AND (valid_to IS NULL OR
        (isfinite(valid_to) AND valid_to > valid_from))),
                CHECK (version > 0),
                UNIQUE (tenant_id, key, version),
                CONSTRAINT discount_policies_tenant_id UNIQUE (tenant_id, id),
                FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT,
                CONSTRAINT key_not_blank CHECK (key IS NULL OR length(trim(key)) > 0),
                CONSTRAINT source_not_blank CHECK (source IS NULL OR length(trim(source)) >
        0)
        )
    """)
    op.execute("""
        CREATE INDEX ix_discount_policies_customer_id ON discount_policies (tenant_id,
        customer_id)
    """)
    op.execute("""
        CREATE FUNCTION qt005_preserve() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Commercial evidence cannot be deleted; archive instead' USING
        ERRCODE = '23514';
          END IF;
          IF (to_jsonb(NEW) - 'valid_to' - 'archived') IS DISTINCT FROM
             (to_jsonb(OLD) - 'valid_to' - 'archived') OR (OLD.archived AND NOT
        NEW.archived) THEN
            RAISE EXCEPTION 'Commercial evidence is immutable; create a replacement' USING
        ERRCODE = '23514';
          END IF;
          IF to_jsonb(OLD) ? 'valid_to' AND
             (to_jsonb(OLD)->>'valid_to') IS NOT NULL AND
             ((to_jsonb(NEW)->>'valid_to') IS NULL OR
              (to_jsonb(NEW)->>'valid_to')::timestamptz >
        (to_jsonb(OLD)->>'valid_to')::timestamptz) THEN
            RAISE EXCEPTION 'Closed windows cannot be extended' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TABLE commercial_write_guards (
         tenant_id uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE RESTRICT,
         generation bigint NOT NULL DEFAULT 0)
    """)
    op.execute("""
        CREATE FUNCTION qt005_lock() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
         INSERT INTO commercial_write_guards(tenant_id) VALUES (NEW.tenant_id)
         ON CONFLICT (tenant_id) DO NOTHING;
         UPDATE commercial_write_guards SET generation = generation + 1 WHERE tenant_id =
        NEW.tenant_id;
         RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER a_lock BEFORE INSERT OR UPDATE ON customers FOR EACH ROW EXECUTE
        FUNCTION qt005_lock()
    """)
    op.execute("""
        CREATE TRIGGER b_preserve BEFORE UPDATE OR DELETE ON customers FOR EACH ROW EXECUTE
        FUNCTION qt005_preserve()
    """)
    op.execute("""
        CREATE TRIGGER a_lock BEFORE INSERT OR UPDATE ON products FOR EACH ROW EXECUTE
        FUNCTION qt005_lock()
    """)
    op.execute("""
        CREATE TRIGGER b_preserve BEFORE UPDATE OR DELETE ON products FOR EACH ROW EXECUTE
        FUNCTION qt005_preserve()
    """)
    op.execute("""
        CREATE TRIGGER a_lock BEFORE INSERT OR UPDATE ON product_aliases FOR EACH ROW
        EXECUTE FUNCTION qt005_lock()
    """)
    op.execute("""
        CREATE TRIGGER b_preserve BEFORE UPDATE OR DELETE ON product_aliases FOR EACH ROW
        EXECUTE FUNCTION qt005_preserve()
    """)
    op.execute("""
        CREATE TRIGGER a_lock BEFORE INSERT OR UPDATE ON product_successors FOR EACH ROW
        EXECUTE FUNCTION qt005_lock()
    """)
    op.execute("""
        CREATE TRIGGER b_preserve BEFORE UPDATE OR DELETE ON product_successors FOR EACH ROW
        EXECUTE FUNCTION qt005_preserve()
    """)
    op.execute("""
        CREATE TRIGGER a_lock BEFORE INSERT OR UPDATE ON pricebooks FOR EACH ROW EXECUTE
        FUNCTION qt005_lock()
    """)
    op.execute("""
        CREATE TRIGGER b_preserve BEFORE UPDATE OR DELETE ON pricebooks FOR EACH ROW EXECUTE
        FUNCTION qt005_preserve()
    """)
    op.execute("""
        CREATE FUNCTION qt005_overlap_pricebooks() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
         IF EXISTS (SELECT 1 FROM pricebooks p WHERE p.tenant_id = NEW.tenant_id AND p.id <>
        NEW.id
         AND p.key = NEW.key AND tstzrange(p.valid_from, p.valid_to, '[)') &&
        tstzrange(NEW.valid_from, NEW.valid_to, '[)')) THEN
           RAISE EXCEPTION 'Overlapping pricebooks authority' USING ERRCODE = '23514';
         END IF;
         RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER c_overlap BEFORE INSERT OR UPDATE ON pricebooks FOR EACH ROW EXECUTE
        FUNCTION qt005_overlap_pricebooks()
    """)
    op.execute("""
        CREATE TRIGGER a_lock BEFORE INSERT OR UPDATE ON pricebook_assignments FOR EACH ROW
        EXECUTE FUNCTION qt005_lock()
    """)
    op.execute("""
        CREATE TRIGGER b_preserve BEFORE UPDATE OR DELETE ON pricebook_assignments FOR EACH
        ROW EXECUTE FUNCTION qt005_preserve()
    """)
    op.execute("""
        CREATE FUNCTION qt005_overlap_pricebook_assignments() RETURNS trigger LANGUAGE
        plpgsql AS $$
        BEGIN
         IF EXISTS (SELECT 1 FROM pricebook_assignments p WHERE p.tenant_id = NEW.tenant_id
        AND p.id <> NEW.id
         AND p.customer_id IS NOT DISTINCT FROM NEW.customer_id AND tstzrange(p.valid_from,
        p.valid_to, '[)') && tstzrange(NEW.valid_from, NEW.valid_to, '[)')) THEN
           RAISE EXCEPTION 'Overlapping pricebook_assignments authority' USING ERRCODE =
        '23514';
         END IF;
         RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER c_overlap BEFORE INSERT OR UPDATE ON pricebook_assignments FOR EACH
        ROW EXECUTE FUNCTION qt005_overlap_pricebook_assignments()
    """)
    op.execute("""
        CREATE TRIGGER a_lock BEFORE INSERT OR UPDATE ON uom_conversions FOR EACH ROW
        EXECUTE FUNCTION qt005_lock()
    """)
    op.execute("""
        CREATE TRIGGER b_preserve BEFORE UPDATE OR DELETE ON uom_conversions FOR EACH ROW
        EXECUTE FUNCTION qt005_preserve()
    """)
    op.execute("""
        CREATE FUNCTION qt005_overlap_uom_conversions() RETURNS trigger LANGUAGE plpgsql AS
        $$
        BEGIN
         IF EXISTS (SELECT 1 FROM uom_conversions p WHERE p.tenant_id = NEW.tenant_id AND
        p.id <> NEW.id
         AND p.product_id = NEW.product_id AND p.from_uom = NEW.from_uom AND p.to_uom =
        NEW.to_uom AND tstzrange(p.valid_from, p.valid_to, '[)') &&
        tstzrange(NEW.valid_from, NEW.valid_to, '[)')) THEN
           RAISE EXCEPTION 'Overlapping uom_conversions authority' USING ERRCODE = '23514';
         END IF;
         RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER c_overlap BEFORE INSERT OR UPDATE ON uom_conversions FOR EACH ROW
        EXECUTE FUNCTION qt005_overlap_uom_conversions()
    """)
    op.execute("""
        CREATE TRIGGER a_lock BEFORE INSERT OR UPDATE ON prices FOR EACH ROW EXECUTE
        FUNCTION qt005_lock()
    """)
    op.execute("""
        CREATE TRIGGER b_preserve BEFORE UPDATE OR DELETE ON prices FOR EACH ROW EXECUTE
        FUNCTION qt005_preserve()
    """)
    op.execute("""
        CREATE FUNCTION qt005_overlap_prices() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
         IF EXISTS (SELECT 1 FROM prices p WHERE p.tenant_id = NEW.tenant_id AND p.id <>
        NEW.id
         AND p.product_id = NEW.product_id AND p.pricebook_id = NEW.pricebook_id AND p.uom =
        NEW.uom AND numrange(p.quantity_min, p.quantity_max, '[)') &&
        numrange(NEW.quantity_min, NEW.quantity_max, '[)') AND tstzrange(p.valid_from,
        p.valid_to, '[)') && tstzrange(NEW.valid_from, NEW.valid_to, '[)')) THEN
           RAISE EXCEPTION 'Overlapping prices authority' USING ERRCODE = '23514';
         END IF;
         RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER c_overlap BEFORE INSERT OR UPDATE ON prices FOR EACH ROW EXECUTE
        FUNCTION qt005_overlap_prices()
    """)
    op.execute("""
        CREATE TRIGGER a_lock BEFORE INSERT OR UPDATE ON product_costs FOR EACH ROW EXECUTE
        FUNCTION qt005_lock()
    """)
    op.execute("""
        CREATE TRIGGER b_preserve BEFORE UPDATE OR DELETE ON product_costs FOR EACH ROW
        EXECUTE FUNCTION qt005_preserve()
    """)
    op.execute("""
        CREATE FUNCTION qt005_overlap_product_costs() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
         IF EXISTS (SELECT 1 FROM product_costs p WHERE p.tenant_id = NEW.tenant_id AND p.id
        <> NEW.id
         AND p.product_id = NEW.product_id AND tstzrange(p.valid_from, p.valid_to, '[)') &&
        tstzrange(NEW.valid_from, NEW.valid_to, '[)')) THEN
           RAISE EXCEPTION 'Overlapping product_costs authority' USING ERRCODE = '23514';
         END IF;
         RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER c_overlap BEFORE INSERT OR UPDATE ON product_costs FOR EACH ROW
        EXECUTE FUNCTION qt005_overlap_product_costs()
    """)
    op.execute("""
        CREATE TRIGGER a_lock BEFORE INSERT OR UPDATE ON inventory FOR EACH ROW EXECUTE
        FUNCTION qt005_lock()
    """)
    op.execute("""
        CREATE TRIGGER b_preserve BEFORE UPDATE OR DELETE ON inventory FOR EACH ROW EXECUTE
        FUNCTION qt005_preserve()
    """)
    op.execute("""
        CREATE TRIGGER a_lock BEFORE INSERT OR UPDATE ON discount_policies FOR EACH ROW
        EXECUTE FUNCTION qt005_lock()
    """)
    op.execute("""
        CREATE TRIGGER b_preserve BEFORE UPDATE OR DELETE ON discount_policies FOR EACH ROW
        EXECUTE FUNCTION qt005_preserve()
    """)
    op.execute("""
        CREATE FUNCTION qt005_overlap_discount_policies() RETURNS trigger LANGUAGE plpgsql
        AS $$
        BEGIN
         IF EXISTS (SELECT 1 FROM discount_policies p WHERE p.tenant_id = NEW.tenant_id AND
        p.id <> NEW.id
         AND p.customer_id IS NOT DISTINCT FROM NEW.customer_id AND tstzrange(p.valid_from,
        p.valid_to, '[)') && tstzrange(NEW.valid_from, NEW.valid_to, '[)')) THEN
           RAISE EXCEPTION 'Overlapping discount_policies authority' USING ERRCODE =
        '23514';
         END IF;
         RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER c_overlap BEFORE INSERT OR UPDATE ON discount_policies FOR EACH ROW
        EXECUTE FUNCTION qt005_overlap_discount_policies()
    """)
    op.execute("""
        CREATE FUNCTION qt005_inventory_time() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
         IF NEW.imported_at > clock_timestamp() OR NEW.observed_at > clock_timestamp() THEN
           RAISE EXCEPTION 'Future inventory evidence' USING ERRCODE = '23514';
         END IF;
         RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER c_inventory_time BEFORE INSERT ON inventory FOR EACH ROW EXECUTE
        FUNCTION qt005_inventory_time()
    """)

    op.execute("ALTER TABLE prices ADD CONSTRAINT uom_format CHECK (uom ~ '^[A-Z][A-Z0-9_]*$')")
    op.execute(
        "ALTER TABLE product_costs ADD CONSTRAINT uom_format CHECK (uom ~ '^[A-Z][A-Z0-9_]*$')"
    )
    op.execute("ALTER TABLE inventory ADD CONSTRAINT uom_format CHECK (uom ~ '^[A-Z][A-Z0-9_]*$')")
    op.execute(
        "ALTER TABLE uom_conversions ADD CONSTRAINT from_uom_format "
        "CHECK (from_uom ~ '^[A-Z][A-Z0-9_]*$')"
    )
    op.execute(
        "ALTER TABLE uom_conversions ADD CONSTRAINT to_uom_format "
        "CHECK (to_uom ~ '^[A-Z][A-Z0-9_]*$')"
    )


def downgrade() -> None:
    op.execute("""
        DROP TABLE discount_policies
    """)
    op.execute("""
        DROP TABLE inventory
    """)
    op.execute("""
        DROP TABLE product_costs
    """)
    op.execute("""
        DROP TABLE prices
    """)
    op.execute("""
        DROP TABLE uom_conversions
    """)
    op.execute("""
        DROP TABLE pricebook_assignments
    """)
    op.execute("""
        DROP TABLE pricebooks
    """)
    op.execute("""
        DROP TABLE product_successors
    """)
    op.execute("""
        DROP TABLE product_aliases
    """)
    op.execute("""
        DROP TABLE products
    """)
    op.execute("""
        DROP TABLE customers
    """)
    op.execute("""
        DROP TABLE commercial_write_guards
    """)
    op.execute("""
        DROP FUNCTION qt005_overlap_pricebooks()
    """)
    op.execute("""
        DROP FUNCTION qt005_overlap_prices()
    """)
    op.execute("""
        DROP FUNCTION qt005_overlap_product_costs()
    """)
    op.execute("""
        DROP FUNCTION qt005_overlap_discount_policies()
    """)
    op.execute("""
        DROP FUNCTION qt005_overlap_pricebook_assignments()
    """)
    op.execute("""
        DROP FUNCTION qt005_overlap_uom_conversions()
    """)
    op.execute("""
        DROP FUNCTION qt005_inventory_time()
    """)
    op.execute("""
        DROP FUNCTION qt005_preserve()
    """)
    op.execute("""
        DROP FUNCTION qt005_lock()
    """)
