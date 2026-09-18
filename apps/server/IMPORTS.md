# QT-006 CSV/XLSX imports

Implements [QT-006](https://app.notion.com/p/343de723e8a182199fed814f7bfffd94)
against QT-004 settings, QT-005 immutable commercial authority and COM-001 rules.
Main was integrated before implementation. The existing app shell is reused.

## Supported contract

System administrators with `manage_imports` may upload one UTF-8 CSV or single-sheet
XLSX file, map columns, validate, review row/field errors, download errors, and commit
the whole file. Setup completion links to Imports; administrators can reopen Imports
from Company settings. Recent jobs retain their staged content and result across reloads.
Sales roles, disabled accounts and accounts requiring a password change cannot import.
Tenant identity comes only from the authenticated principal. File tenant columns and
record UUIDs are never accepted as authority.

Types: customers, products, pricebooks, prices, inventory, pricebook assignments,
product-specific UOM conversions and product costs. Import parents first. Exact fields
and their required/optional status are defined in `import_contract.py` and returned to
the wizard by the server. Mapping is target field → unique source column; extra source
columns may be ignored. Headers and values are trimmed; identifiers remain case-sensitive.
Blank optional fields are omitted. Blank required fields are row errors. No inferred
references, tax rates, prices, UOM conversions or numeric rounding are introduced.

All commercial quantities and prices remain decimal text through parsing and model
validation, including numeric XLSX `<v>` values (no float conversion). Time inputs
must be timezone-aware ISO text; Excel serial dates are not interpreted as dates.
Inventory retains the source observation time and gets its actual server commit time.
Provider, model and embedding services are not called and no semantic queue is created.
QT-019 owns later source-versioned durable indexing intent.

## Duplicate and update policy

- **Insert** rejects existing stable keys or overlapping authority.
- **Skip identical** skips records whose business fields match exactly after typed
  normalization. Source provenance and inventory import timestamps are excluded from
  equivalence; original stored provenance remains unchanged. Conflicting changes fail.
- **Replace effective** is limited to prices and costs. Exactly one unarchived open
  predecessor must start strictly earlier; UOM and price tier must match. The old window
  closes at the new start, and a new evidence ID is inserted. Numeric history is never
  overwritten or deleted. Closed windows cannot be extended or rewritten.

Duplicate identities inside the same file are errors even with Skip identical. Archived
keys remain reserved. Pricebook key/version is explicit; new versions require valid,
non-overlapping windows. A blank assignment customer means tenant default. No automatic
catalog deletion, customer/product overwrites, partial imports or transformation language.

## Transactions and retries

`POST /api/admin/imports` stages parsed rows and records the SHA-256 of original bytes.
`POST /{id}/preview` binds kind, file hash, mapping, mode, normalized operations and
current references to a preview token. Only staging, internal locking and audit change;
no customer/catalog/price/inventory rows are inserted, even temporarily.

`POST /{id}/commit` accepts that token plus an idempotency key. The service acquires the
existing tenant commercial guard before reading current authority, recomputes the plan,
and rejects changes with HTTP 409 and an instruction to preview again. One transaction
saves all records, the durable receipt and audit; exceptions roll it all back. Different
tenants use different guards. Existing auth transaction locking also serializes requests
within a tenant and keeps authorization current.

A successful receipt reserves `(tenant, key)` for exactly that job and preview.
Exact retries return the stored summary without executing writes again. Conflicting reuse
fails. A committed job requires its original key; the UI always uses the job UUID, even
after reload or a lost response. Failed transactions reserve no successful receipt;
they may be safely retried. Two different jobs competing for the same catalog key cannot
double-apply: revalidation plus database constraints reject the stale one.

GET job/history/error endpoints are tenant-scoped. History returns the latest 50 jobs.
Error CSVs contain row/field/reason, omit source values, neutralize formula prefixes and
are served with no-store. Raw samples are rendered as escaped text in React. Staged
content is retained for review; no automatic retention deletion is introduced.

## Bounds and parser behavior

- 2 MB source files; 2,000 data rows; 40 columns; 4,000 characters/cell.
- 2.8 MB upload request body accommodates base64 JSON; other import requests are 16 KB.
- XLSX: at most 100 unique ZIP entries, 12 MB total expanded, 6 MB/entry and 200:1 ratio.
- DefusedXML rejects DTD/entities. Macros, encrypted entries, external links and formulas
  are rejected. No formulas execute, no filesystem extraction, no network references.
- Exactly one worksheet, ordered bounded row/cell addresses; shared and inline strings
  and raw numeric cells are supported. Unsupported booleans/error cells fail explicitly.

The narrow OOXML reader uses the existing standard-library ZIP/XML data model plus
`defusedxml`. Bounds are checked before expansion. Relevant parser guidance:
[Python ZIP documentation](https://docs.python.org/3/library/zipfile.html),
[XML security](https://docs.python.org/3/library/xml.html#xml-vulnerabilities),
[openpyxl security notice](https://openpyxl.readthedocs.io/en/stable/).

## Verification and operation

Run `node scripts/server.mjs pytest tests/test_imports.py`, `npm run check`, and
`npm run test:e2e`. Tests use unique disposable databases; never reset an existing
database. Coverage includes parsing/exact decimals, unsafe files, mapping/errors,
zero commercial writes during preview, atomic rollback, reference changes, history,
tenant/RBAC boundaries, idempotency/concurrency and migration/backout. Browser coverage
includes invalid rows/download, mapping, mobile preview, lost commit response, safe
retry and reopening a receipt. Browser plugin is not available in this session, so
the repository's existing Playwright gate is used.

Bounded fixtures adapt DATA-001's documented SYN-* namespace and scenarios. Its Notion
status is Done, but attachment download returned 404; the full attachment pack has not
been verified. See `tests/fixtures/imports/README.md` for provenance and dictionaries.

Migration `0005_imports` adds only `import_jobs`. Apply with `npm run db:migrate` before
running the changed app. Backout to `0004_tenant_settings` drops staged files, previews
and idempotency receipts; it does not undo committed business records. On a populated
environment first stop import writes, preserve a verified database backup and reconcile
receipts/business records before any downgrade or subsequent replay. Do not use this
destructive backout as a production undo operation. The automated downgrade test uses
only a disposable database.
