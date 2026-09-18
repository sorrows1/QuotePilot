# QT-005 commercial persistence

QT-005 adds trusted server persistence, with no HTTP CRUD or authentication changes.
COM-001 is the commercial contract; QT-004 owns settings, QT-006 imports, QT-007
calculation, and QT-008 quote revisions. No implicit tenant, pricebook, cost or stock
is seeded. PostgreSQL 17 and the existing locked dependencies are sufficient; no
extension is required.

## Storage and evidence

`commercial_schema.py` registers SQLAlchemy Core tables in the existing `Base.metadata`.
The frozen `0002_commercial` migration follows `0001_tenant_baseline`.

| Tables | Authority |
| --- | --- |
| customers, products | Tenant-local external customer keys and SKUs; typed descriptions/manufacturer/model |
| product_aliases, product_successors | Customer-scoped aliases and directed successors with source evidence |
| pricebooks, pricebook_assignments | Book/version identity, effective windows and explicit customer/default assignment |
| prices | Product/book/UOM price and half-open quantity tiers |
| uom_conversions | Directed, product-specific factors and effective windows |
| product_costs | Separate effective cost; one per product regardless of UOM |
| inventory | Tenant/product aggregate evidence, observation/import timestamps, quantity and UOM |
| discount_policies | Versioned default/customer percentage policy, rate and permission |
| commercial_write_guards | Internal per-tenant transaction serialization only |

Every commercial relation has a composite tenant FK with RESTRICT deletion. Prices
expose `(tenant_id, product_id, pricebook_id, id)` as a candidate key; conversion,
cost and inventory expose `(tenant_id, product_id, id)`. Downstream evidence FKs must
use these full paths when storing both an evidence ID and its parent IDs. The test
suite proves a same-tenant wrong-product price reference is rejected. Alias customer
ownership is part of its tenant/customer/alias unique key. External keys are exact,
case-sensitive values; import normalization remains the importer's explicit contract.

All authority is immutable after insertion, except one-way archival and shortening
an effective window. Replacement is close-old + insert-new in one transaction. Closed
windows cannot be extended. Numeric authority is never overwritten. Deletion of
commercial records is rejected even before downstream quote references exist.
Archival removes current eligibility, while `get` still retrieves original evidence.
These rules intentionally require new versions for corrected authority. Customers and
products retain their stable keys when archived; they cannot be recreated under the
same key. Retention/destructive maintenance is outside this trusted interface.

## Exact numeric contract

Inputs accept decimal text or `Decimal`, reject integers/bools/floats, non-finite
values, scale above six and magnitudes >= 10^18. Unit prices and costs allow explicit
zero; quantities/tier bounds/factors must be positive; policy rates are in [0,1].
Inventory quantity is signed because COM-001 does not prescribe a nonnegative stock
policy. There is no inferred reservation or availability guarantee.

Columns use unconstrained PostgreSQL `NUMERIC` with explicit CHECKs for scale <= 6,
abs(value) < 10^18 and applicable ranges. This deliberately avoids NUMERIC(p,6)'s
round-before-check coercion: even direct database input with excess scale fails.
The range permits 18 integer and six fractional digits; it is a storage limit, not
a business maximum. The trusted boundary revalidates constructed/copied input models
before SQL. Derived converted quantity multiplies under a local 48-digit context and
retains up to twelve fractional digits for downstream tier selection. No calculator,
currency rounding or margin arithmetic is implemented here.

## Transactions, lookups and concurrency

`CommercialService.create_batch(context, records)` commits the entire ordered batch
or rolls it all back. Insert parents before children. Repositories never commit.
`CommercialRepository.add`, `get`, `archive`, `close_window` and all lookup methods
require a validated `TenantContext`. Tenant IDs cannot be mass-assigned in input
models. No public endpoint should expose these operations without QT-003 authorization.

For a related set of reads, use `CommercialService.snapshot(context)` and pass the
same explicit UTC-aware `pricing_as_of` to each lookup. It yields a repository inside
a read-only REPEATABLE READ transaction, preventing mixed committed authority across
queries. Standalone repository calls compose into an existing application transaction;
the caller must choose a consistent snapshot for multi-record commercial evaluation.

- `pricebook`: customer assignment wins over default. Missing/ineligible selected
  book is MISSING_PRICEBOOK; multiple assignments are AMBIGUOUS_PRICEBOOK. A selected
  book lacking a price never falls back to another book.
- `price`: exact half-open time/tier selection; MISSING_PRICE or AMBIGUOUS_PRICE.
- `conversion`: direct active product-specific direction only; MISSING_CONVERSION
  or AMBIGUOUS_CONVERSION. No graph, inverse or generic conversion inference.
- `cost`: `None` explicitly means missing cost/MARGIN_UNKNOWN, never zero.
  Multiple eligible costs are AMBIGUOUS_COST.
- `policy`: customer precedence; `None` explicitly means NO_DISCOUNT_POLICY / zero
  effective discount for consumers. The selected row retains rate and `permitted`;
  applying the discount and approval rules belongs to QT-007.
- `stock`: latest observation already imported by the supplied instant, or `None`
  for MISSING_INVENTORY. Duplicate observation instants for a product are rejected.
  `inventory_freshness` compares exact observation age; threshold equality is fresh,
  older is stale, and future observation is invalid. Import time never resets age.
  QT-004 supplies the positive whole-minute threshold.
- `get`: historical ID access, optional parent predicates, tenant-scoped even when
  another tenant's ID is supplied. Current lookups reject archived parent records.

Database triggers update one dedicated guard row per tenant before checking overlap.
All commercial writes within one tenant serialize until transaction end; different
tenants use different guard rows. This conservative scope is sufficient for the
bounded catalog/import workload and avoids an extension dependency or an unstable
fine-grained lock design. Keep write transactions short and free of external calls.
A real UPDATE, rather than only a lock, forces stale REPEATABLE READ/SERIALIZABLE
writers to abort. Volatile trigger queries after the lock see committed conflicts
under READ COMMITTED. Applications must roll back on constraint/serialization errors;
retry a serialization failure only as a complete transaction. No silent retry/ignore
occurs here. Same-scope time overlaps are prohibited even for archived evidence;
price conflicts additionally require overlapping quantity ranges and matching UOM.
Adjacent time windows/tiers are valid. Assignment/policy customer and tenant-default
scopes are separate and may coexist.

## Verification and backout

Run `node scripts/server.mjs pytest tests/test_commercial.py` for focused verification
and root `npm run check` for the required gate. PostgreSQL must be available; tests
never skip it. Each integration test creates a unique disposable database. Existing
CI runs these tests through the unchanged root test gate and custom-configuration gate.

The bounded fixture test creates two separate catalogs of 181 products each (one
seed plus 180 SYN products), one customer/book per tenant, 180 exact prices, costs,
conversions, inventory snapshots and customer aliases per tenant, a successor,
a default assignment and policy. Every SYN product resolves price 12.345600 and
cost 7.123456 at the same instant. SKU/customer keys repeat across tenants. This is
synthetic task test data, not DATA-001's CSV/XLSX pack or a production seed.

Migration tests cover empty and populated baseline upgrade, preserved tenant settings,
downgrade to the prior head and re-upgrade, and metadata/DDL agreement. Downgrade drops
all new commercial tables and evidence. On populated environments it requires a backup
and an explicit data-preservation/backout decision. Never downgrade or reset an existing
development or production database just to run this test suite.

Before merging with QT-003, incorporate current accepted main, reconcile Alembic to
one head without editing an applied migration, retain both auth and commercial imports
in the same metadata registry, rerun root checks and obtain fresh independent review.

Implementation references: [PostgreSQL numeric semantics](https://www.postgresql.org/docs/17/datatype-numeric.html),
[transaction isolation](https://www.postgresql.org/docs/17/transaction-iso.html).
