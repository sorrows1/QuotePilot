"""Make effective-authority overlap constraints ignore archived evidence."""

from alembic import op

revision = "0006_archive_authority"
down_revision = "0005_imports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION qt005_overlap_pricebooks() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
         IF NEW.archived THEN
           RETURN NEW;
         END IF;
         IF EXISTS (SELECT 1 FROM pricebooks p WHERE p.tenant_id = NEW.tenant_id
         AND p.id <> NEW.id AND NOT p.archived
         AND p.key = NEW.key AND tstzrange(p.valid_from, p.valid_to, '[)') &&
         tstzrange(NEW.valid_from, NEW.valid_to, '[)')) THEN
           RAISE EXCEPTION 'Overlapping pricebooks authority' USING ERRCODE = '23514';
         END IF;
         RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION qt005_overlap_pricebook_assignments() RETURNS trigger\n        LANGUAGE plpgsql AS $
        BEGIN
         IF NEW.archived THEN
           RETURN NEW;
         END IF;
         IF EXISTS (SELECT 1 FROM pricebook_assignments p WHERE p.tenant_id = NEW.tenant_id
         AND p.id <> NEW.id AND NOT p.archived
         AND p.customer_id IS NOT DISTINCT FROM NEW.customer_id
         AND tstzrange(p.valid_from, p.valid_to, '[)') &&
         tstzrange(NEW.valid_from, NEW.valid_to, '[)')) THEN
           RAISE EXCEPTION 'Overlapping pricebook_assignments authority' USING ERRCODE = '23514';
         END IF;
         RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION qt005_overlap_uom_conversions() RETURNS trigger\n        LANGUAGE plpgsql AS $
        BEGIN
         IF NEW.archived THEN
           RETURN NEW;
         END IF;
         IF EXISTS (SELECT 1 FROM uom_conversions p WHERE p.tenant_id = NEW.tenant_id
         AND p.id <> NEW.id AND NOT p.archived
         AND p.product_id = NEW.product_id AND p.from_uom = NEW.from_uom
         AND p.to_uom = NEW.to_uom AND tstzrange(p.valid_from, p.valid_to, '[)') &&
         tstzrange(NEW.valid_from, NEW.valid_to, '[)')) THEN
           RAISE EXCEPTION 'Overlapping uom_conversions authority' USING ERRCODE = '23514';
         END IF;
         RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION qt005_overlap_prices() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
         IF NEW.archived THEN
           RETURN NEW;
         END IF;
         IF EXISTS (SELECT 1 FROM prices p WHERE p.tenant_id = NEW.tenant_id
         AND p.id <> NEW.id AND NOT p.archived
         AND p.product_id = NEW.product_id AND p.pricebook_id = NEW.pricebook_id
         AND p.uom = NEW.uom AND numrange(p.quantity_min, p.quantity_max, '[)') &&
         numrange(NEW.quantity_min, NEW.quantity_max, '[)')
         AND tstzrange(p.valid_from, p.valid_to, '[)') &&
         tstzrange(NEW.valid_from, NEW.valid_to, '[)')) THEN
           RAISE EXCEPTION 'Overlapping prices authority' USING ERRCODE = '23514';
         END IF;
         RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION qt005_overlap_product_costs() RETURNS trigger\n        LANGUAGE plpgsql AS $
        BEGIN
         IF NEW.archived THEN
           RETURN NEW;
         END IF;
         IF EXISTS (SELECT 1 FROM product_costs p WHERE p.tenant_id = NEW.tenant_id
         AND p.id <> NEW.id AND NOT p.archived
         AND p.product_id = NEW.product_id AND tstzrange(p.valid_from, p.valid_to, '[)') &&
         tstzrange(NEW.valid_from, NEW.valid_to, '[)')) THEN
           RAISE EXCEPTION 'Overlapping product_costs authority' USING ERRCODE = '23514';
         END IF;
         RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION qt005_overlap_discount_policies() RETURNS trigger\n        LANGUAGE plpgsql AS $
        BEGIN
         IF NEW.archived THEN
           RETURN NEW;
         END IF;
         IF EXISTS (SELECT 1 FROM discount_policies p WHERE p.tenant_id = NEW.tenant_id
         AND p.id <> NEW.id AND NOT p.archived
         AND p.customer_id IS NOT DISTINCT FROM NEW.customer_id
         AND tstzrange(p.valid_from, p.valid_to, '[)') &&
         tstzrange(NEW.valid_from, NEW.valid_to, '[)')) THEN
           RAISE EXCEPTION 'Overlapping discount_policies authority' USING ERRCODE = '23514';
         END IF;
         RETURN NEW;
        END $$
    """)


def downgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION qt005_overlap_pricebooks() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
         IF EXISTS (SELECT 1 FROM pricebooks p WHERE p.tenant_id = NEW.tenant_id
         AND p.id <> NEW.id AND p.key = NEW.key
         AND tstzrange(p.valid_from, p.valid_to, '[)') &&
         tstzrange(NEW.valid_from, NEW.valid_to, '[)')) THEN
           RAISE EXCEPTION 'Overlapping pricebooks authority' USING ERRCODE = '23514';
         END IF;
         RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION qt005_overlap_pricebook_assignments() RETURNS trigger\n        LANGUAGE plpgsql AS $
        BEGIN
         IF EXISTS (SELECT 1 FROM pricebook_assignments p WHERE p.tenant_id = NEW.tenant_id
         AND p.id <> NEW.id AND p.customer_id IS NOT DISTINCT FROM NEW.customer_id
         AND tstzrange(p.valid_from, p.valid_to, '[)') &&
         tstzrange(NEW.valid_from, NEW.valid_to, '[)')) THEN
           RAISE EXCEPTION 'Overlapping pricebook_assignments authority' USING ERRCODE = '23514';
         END IF;
         RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION qt005_overlap_uom_conversions() RETURNS trigger\n        LANGUAGE plpgsql AS $
        BEGIN
         IF EXISTS (SELECT 1 FROM uom_conversions p WHERE p.tenant_id = NEW.tenant_id
         AND p.id <> NEW.id AND p.product_id = NEW.product_id
         AND p.from_uom = NEW.from_uom AND p.to_uom = NEW.to_uom
         AND tstzrange(p.valid_from, p.valid_to, '[)') &&
         tstzrange(NEW.valid_from, NEW.valid_to, '[)')) THEN
           RAISE EXCEPTION 'Overlapping uom_conversions authority' USING ERRCODE = '23514';
         END IF;
         RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION qt005_overlap_prices() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
         IF EXISTS (SELECT 1 FROM prices p WHERE p.tenant_id = NEW.tenant_id
         AND p.id <> NEW.id AND p.product_id = NEW.product_id
         AND p.pricebook_id = NEW.pricebook_id AND p.uom = NEW.uom
         AND numrange(p.quantity_min, p.quantity_max, '[)') &&
         numrange(NEW.quantity_min, NEW.quantity_max, '[)')
         AND tstzrange(p.valid_from, p.valid_to, '[)') &&
         tstzrange(NEW.valid_from, NEW.valid_to, '[)')) THEN
           RAISE EXCEPTION 'Overlapping prices authority' USING ERRCODE = '23514';
         END IF;
         RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION qt005_overlap_product_costs() RETURNS trigger\n        LANGUAGE plpgsql AS $
        BEGIN
         IF EXISTS (SELECT 1 FROM product_costs p WHERE p.tenant_id = NEW.tenant_id
         AND p.id <> NEW.id AND p.product_id = NEW.product_id
         AND tstzrange(p.valid_from, p.valid_to, '[)') &&
         tstzrange(NEW.valid_from, NEW.valid_to, '[)')) THEN
           RAISE EXCEPTION 'Overlapping product_costs authority' USING ERRCODE = '23514';
         END IF;
         RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION qt005_overlap_discount_policies() RETURNS trigger\n        LANGUAGE plpgsql AS $
        BEGIN
         IF EXISTS (SELECT 1 FROM discount_policies p WHERE p.tenant_id = NEW.tenant_id
         AND p.id <> NEW.id AND p.customer_id IS NOT DISTINCT FROM NEW.customer_id
         AND tstzrange(p.valid_from, p.valid_to, '[)') &&
         tstzrange(NEW.valid_from, NEW.valid_to, '[)')) THEN
           RAISE EXCEPTION 'Overlapping discount_policies authority' USING ERRCODE = '23514';
         END IF;
         RETURN NEW;
        END $$
    """)
