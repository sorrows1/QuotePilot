# QT-008 manual quotations

Sales Administrators and Sales Managers with `EDIT_DRAFT` can select/create a customer, create a customer-bound internal case, manually select active products, calculate, save, reopen,
edit and preview provisional quotes after company setup. System Administrators retain
their existing setup/import/user workflows and cannot edit quotes.

The browser uses `/api/quotes`, `/api/quotes/{id}`, `/calculate`, `/revisions`,
`/api/customers`, and `/api/products`. Lookup is tenant-scoped, including customer
external key/name/UUID and product SKU/name/UUID matching, with at most 50 results.
Interactive customer creation requires the same non-empty tenant-scoped `external_key`
namespace used by QT-006 import. Reusing an active key returns that authoritative
customer without changing its name; name equality alone never merges customers.
Draft listing returns the newest 50 cases. All browser writes use the existing origin,
request-header and CSRF boundary. Quote calculate/revision POST bodies have an explicit
16 MiB transport limit so valid multi-line payloads allowed by the 1000-line DTO are
not rejected by the generic 4 KiB browser boundary; small quote-creation requests keep
the normal boundary.

## Persistence and authority

`quote_cases.customer_id` is required at creation and immutable for the life of the case;
changing customer means creating a new quote. `quote_cases.version` is both the optimistic edit version and latest revision number;
there is no separate redundant latest pointer. Each successful save appends a revision
and structured numeric lines, alongside the exact QT-007 input/result snapshot.
PostgreSQL triggers reject updates/deletes of revision, line and retry evidence.
Decimal inputs must be strings; outputs retain decimal strings. Client-supplied
tenant, customer-after-create, proposer, timestamp, revision, settings revision, pricing UOM,
substitution, availability-required and quote-number authority are rejected. The browser supplies only
line identity, product, quantity, quote UOM, optional negotiated price/reason and quote-level freight.
The server derives pricing UOM from current authoritative price records, fixes M1 manual lines as
resolved/non-substitution, and does not treat the provisional workspace as an availability assertion.
Negotiated proposals do not change pricebook data.

Save recalculates with `CalculationService` in its established read-only repeatable-read
commercial snapshot. The surrounding authenticated transaction retains the tenant
security lock and case row lock until the revision, lines, audit and retry receipt
commit together. Before resolving pricing UOM, the quote transaction takes a shared
lock on QT-005's per-tenant commercial generation row; QT-005 writes update that row,
so commercial authority cannot change between trusted UOM resolution and QT-007's
repeatable-read snapshot. Active customer/product rows are also checked within the
tenant and held with share locks during evaluation/save. The saved result captures the calculation
instant; it is not a claim that authority can never change afterward.

Every revision has a fresh exception-set UUID and fresh member UUIDs for every
`approval` finding. Members carry the typed code and optional line identity. Hard
blocks and clarification findings remain in the snapshot but never in the approval
set. The set is immutable with its revision; no approval decision is implemented.

Request keys are tenant-local and bind operation, authenticated actor and validated
payload. An identical retry returns the original receipt even after later revisions;
conflicting reuse or stale expected version returns 409. The browser retains the key
for an unchanged retry after a failed response. Reload obtains current case state;
history always returns captured snapshots, with a separate settings-staleness flag.

Draft creation/editing never invokes quote-number allocation or changes the number
counter. The UI offers no approval, READY, final generation or sending actions.
QT-038 structured catalog work and QT-009 ranked search remain outside this change.

## Migration and verification

`0007_quotes` adds four tables after `0006_archive_authority`. Upgrade through the
serialized migration runner. Downgrade drops quote history and retry evidence, so
back up these tables and stop quote writes before any non-disposable backout. Prefer
rolling application code back while retaining the additive tables. Existing master
data, settings and numbering tables are not changed by this migration.

Focused tests: `node scripts/server.mjs pytest tests/test_quotes.py`.
Complete gate: `npm run check`. Browser gate: `npm run test:e2e`.
The browser gate uses a disposable database, real setup and imports, a lost save
response/retry, browser reload, an API process restart, stale settings, a new revision,
two-editor conflict recovery, provisional preview and narrow-laptop layout checks.
Playwright is used because the Browser skill is not available in this session.

Source: [QT-008 implementation packet](https://app.notion.com/p/052de723e8a183c28d260163c82c2603).
