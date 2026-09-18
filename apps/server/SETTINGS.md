# QT-004 tenant setup and settings

Implements the [QT-004 packet](https://app.notion.com/p/915de723e8a183b2b6e401df5178cc89) on base `49df239e2b1498ba9984e32ff844ff8ca378ae37`, branch `qt-004`.

## Authority and API

`tenant_settings` is the existing tenant's editable row. Company identity, tax, four independent approval controls, validity, inventory freshness and quote prefix are typed columns. PostgreSQL NUMERIC stores exact rates (7,6) and SGD thresholds (20,2). SGD is fixed. Nullable booleans mean an explicit choice has not yet been made; disabled controls have null thresholds. Enabled controls can lack a threshold only in incomplete setup drafts.

Every authenticated role receives only the tenant-local `setup_complete` boolean through `/api/me` (also returned at login/password change/renewal). It is true only when both completion time and active revision exist. Admin configuration remains restricted to MANAGE_SETTINGS, with CONFIGURE_POLICY additionally required for approval-field mutations. Existing origin, custom-header, CSRF-token, session and tenant-lock protections apply.

- GET `/api/admin/settings`: current fields, optimistic edit version, active revision, completion flag and required-field errors.
- PATCH `/api/admin/settings`: partial fields plus mandatory `expected_edit_version`. Unknown fields, tenant IDs, currency edits and caller-provided asset IDs are rejected.
- POST `/api/admin/settings/complete`: mandatory `expected_edit_version`; validates and activates the first full settings snapshot.
- PUT `/api/admin/settings/logo`: `expected_edit_version`, `media_type` (image/png or image/jpeg), `data_base64` (standard base64). JSON keeps the existing browser boundary; the decoded input limit is 2 MiB and this endpoint alone has a bounded 2.8 MB JSON envelope.
- DELETE `/api/admin/settings/logo`: mandatory `expected_edit_version`.
- GET `/api/admin/settings/logo`: authenticated admin preview, fixed image/png. No arbitrary asset selector.

Decimal inputs must be JSON strings in plain non-negative notation, with up to six fraction digits for rates in [0,1], or two for money in [0,999999999999999999.99]. Numeric JSON tokens, scientific notation, signs, NaN and excess scale are rejected, never rounded. Integer fields require JSON integers (not booleans): validity 1–365, freshness 1–2147483647 minutes (database representation limit, not a business maximum). No commercial values are prefilled.

## Transactions, history and recovery

Auth's tenant transaction lock serializes edits. Stale writes return `409 SETTINGS_VERSION_CONFLICT`; the UI reloads authoritative values and requires review/reapplication. Valid partial saves increment edit_version without creating authority. First completion creates revision 1. Thereafter every material mutation creates the next immutable `settings_revisions` row and updates the active pointer atomically. Historical snapshots are read with tenant-scoped `read_revision`; the legacy provisioning timezone helper refuses changes after completion. No-op requests create neither revision nor audit; repeated completion with the current edit version is a no-op, and an old version conflicts.

Audit and mutation commit together. Audit failure rolls everything back. Evidence contains revision and a hexadecimal field bitmap, with bits following `SNAPSHOT_FIELDS` in settings.py; it never contains field values, company/address text, thresholds or image bytes. Database triggers reject UPDATE/DELETE/TRUNCATE of history, assets and number reservations. The current row and historical snapshots have tenant-consistent composite logo/revision foreign keys.

The four-step UI supports drafts, step completion, exit/resume, full review, field errors and conflict recovery. Completion explicitly leads to Go to Imports. The imports entry currently explains that QT-006 tools are not available; setup does not claim imports are done.

## Logo handling

Pillow decodes only PNG/JPEG, verifies the file, rejects multiple frames and bounds dimensions to 16–2048 each before pixel loading. Re-encoding fresh RGBA pixels strips metadata; sanitized PNG output is bounded to 4 MiB. Immutable `brand_assets` retain UUID, SHA-256, dimensions, byte count, creator and creation time. Replacing/removing an image preserves historical assets. Tenant-scoped `read_logo` supplies later renderers and denies foreign IDs. Identical sanitized content is a no-op.

Implementation references: [Pillow Image API](https://pillow.readthedocs.io/en/stable/reference/Image.html) and [Pydantic validators](https://docs.pydantic.dev/latest/concepts/validators/), checked during implementation. Pillow is locked in uv.lock.

## Number reservation contract

`NumberAllocator(sessions).allocate(TenantContext, subject_uuid)` is a service-owned transaction that commits before returning. The subject is the stable future QuoteCase UUID. `quote_number_counters.next_sequence` is a positive BIGINT initialized to 1 for existing and new tenants, advanced in the reservation transaction. Tenant lock plus unique constraints make concurrent retries converge and distinct subjects receive increasing numbers. The format is uppercase prefix + hyphen + sequence padded to at least six digits; the sequence grows, never resets, and is not editable. Immutable rows capture the prefix and settings revision. Prefix edits affect future reservations only. Reusing a subject returns its original number, including after render/publication failure. There is no HTTP allocation endpoint.

QT-008 must not reserve on draft/revision creation or editing. QT-022 calls only after final pre-generation eligibility/revalidation and immediately before rendering. QT-020/QT-008 consume the active settings revision to detect stale authority; QT-004 does not mutate quote eligibility or implement those future call sites.

## Migration and backout

`0004_tenant_settings` descends from the single `0003_auth_commercial_merge` head. Upgrade is additive and leaves existing tenants incomplete without inventing defaults. Auth and commercial data remain in place. Migration tests seed historical schemas using SQL appropriate to those schemas, then upgrade with today's ORM.

Downgrade is for disposable test databases only: it drops settings fields, revisions, branding and quote-number reservations. A populated environment requires an explicit backup/export and data-preservation decision before backout. Never downgrade operational reservations and then resume numbering from an empty history. Prefer an additive forward repair.

## Verification

Required gates: `npm run check` and `npm run test:e2e`. PostgreSQL 17 tests cover exact input, setup lifecycle, revision/no-op/conflict behavior, concurrent writers/allocations, tenant/RBAC isolation, logo sanitization/history, audit rollback and migration upgrade/backout. The real browser gate covers forced password change, save/reload/resume, non-admin pending state without admin API access, invalid-input recovery, completion/import handoff, stale edit recovery and persisted values after login. Desktop 1280×900 and mobile 390×844 screenshots are written outside the repository by the existing Playwright harness. Browser plugin unavailable; the repository's Playwright gate is used.

Verified on 2026-09-18: root `npm run check` passed (179 server tests, frontend test, lint/type checks and production build); the 41-test focused settings suite and `npm run test:e2e` passed. Fresh independent read-only review of the complete diff, including new files, found no blocking issues. This is implementation evidence for Review, not final acceptance or deployment.
