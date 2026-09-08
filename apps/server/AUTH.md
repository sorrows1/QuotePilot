# Authentication and tenant administration (QT-003)

## Operator bootstrap

Run `npm run setup`, then `npm run auth:bootstrap` in an interactive terminal.
The command prompts for an organization login code, first administrator login and a
hidden, confirmed initial password. No password argument/environment variable or
seed credential exists. The tenant, settings, administrator and audit event commit
atomically. Concurrent/repeated bootstrap fails without resetting credentials.
For a pre-existing QT-002 tenant with no users, explicitly run
`npm run auth:bootstrap -- --existing-tenant <tenant-uuid>` after verifying its identity
in the operator's inventory. The command never guesses an existing tenant.

Deliver the organization code/login and initial password to the intended person
through the organization's approved secure credential channel; verify the recipient.
Do not put passwords in tickets, ordinary email, terminal arguments or logs. Users
must replace the initial password before using business or admin capabilities.
There is no public signup/bootstrap or final recovery workflow. ADR-017/QT-028 owns
production invitation/recovery. An administrator who disables their own last active
admin account requires an explicitly approved future recovery procedure; create and
verify another tenant administrator before disabling the last one.

## Identity and session contract

The login identifier is ASCII, 1–128 characters: letters/digits followed by letters,
digits or `. @ _ + -`. Trim surrounding whitespace and lowercase; no Unicode or
email-provider-specific alias rewriting. PostgreSQL enforces normalized uniqueness
per tenant. The same login can exist in separate tenants. Organization codes are
provisioned locators (3–64 lowercase ASCII letters/digits/hyphens), not authority.
Every authenticated operation uses the stored session's tenant and current user;
request IDs, headers or role fields cannot replace that context.

Passwords are 15–128 characters, at most 512 UTF-8 bytes, hashed with argon2-cffi's
RFC 9106 low-memory Argon2id profile: 64 MiB, 3 iterations, 4 lanes, 16-byte salt,
32-byte output. No custom cryptographic primitive is introduced. Parameters are
explicit and locked, and valid logins rehash if parameters change. Unknown users
perform a dummy verification using the same parameters.

A random 256-bit opaque cookie is the only session authority; only its SHA-256 hash
is stored in PostgreSQL. The browser never puts credentials in local/session storage
or URLs. Production cookie `__Host-quotepilot` is Secure, HttpOnly, SameSite=Strict,
Path=/, with no Domain. Local HTTP uses a distinct `quotepilot_dev` cookie.
Sessions have a 30-minute idle deadline and an immutable 12-hour absolute deadline.
`POST /api/auth/refresh` renews the idle deadline on the existing server session;
it does not mint refresh credentials and cannot extend absolute lifetime. Repeated
renewal is idempotent regarding authority. Logout revokes the current session only.
Disable and explicit administrator revocation revoke all sessions of the target user.
Password replacement revokes all previous sessions and issues a fresh cookie and
CSRF token. Enabling a user never unrevokes old sessions. Login creates fresh authority.

Tenant security transactions acquire a PostgreSQL transaction advisory lock before
checking current session/user status and retain it through mutation/audit commit.
This deliberately simple tenant-local serialization makes renewal versus logout,
disable or password change unable to resurrect authority. A request that acquired
authority before revocation may finish first; after revocation commits, subsequent
requests fail. Later domain consumers should authorize within their mutation
transaction, using this contract; a detached principal is not lasting authority.

## Browser boundary and configuration

Production requires exact `AUTH_ORIGIN=https://your-host` and no `AUTH_LOCAL_HTTP`.
Missing/invalid auth configuration fails closed for `/api` without changing `/health`
liveness. Native `npm run dev` and the development-only Compose file explicitly set
`AUTH_ORIGIN=http://localhost:5173` and `AUTH_LOCAL_HTTP=1`. That flag accepts only
loopback HTTP origins. Do not use development Compose as a production deployment.
QT-026 owns TLS termination and ingress topology. API processes run with forwarded
headers disabled: abuse limits use the direct peer, never spoofable X-Forwarded-For.
If a proxy is deployed, source limits conservatively aggregate at that proxy until
QT-026 establishes a verified trusted-source configuration.

All unsafe `/api` calls, including login, require exact Origin, JSON content type and
`X-QuotePilot-Request: 1`. No cross-origin CORS permission is granted. This prevents
simple cross-site form requests and requires an ungranted preflight for cross-origin
custom headers. Authenticated unsafe requests additionally require
`X-CSRF-Token` matching the current session's synchronizer token, returned by login
and `GET /api/me`. SameSite is defense in depth. Responses are no-store. Payloads
are capped at 4096 bytes before JSON parsing. API error responses omit input values,
SQL, hashes and credentials, including validation failures.

Vite forwards `/api` to `http://127.0.0.1:8000` natively and `http://api:8000` in
Compose via `API_PROXY_TARGET`. The browser uses same-origin relative URLs.
Session restoration checks `/api/me`; open pages recheck on focus and every 30 seconds.
Only explicit Keep working renews idle lifetime; background checks do not renew it.

## Abuse limits

PostgreSQL counters span processes; deterministic fixed 15-minute windows count
successful and failed attempts. Login: 10 per normalized organization/login pair,
60 per direct source and 200 per organization locator. Unknown identities use the
same counters and generic credential errors. Renewal: 300 per source/window.
Password change: 20 per source/window. Limits are conservative for the initial SME
workload and bound expensive hash work and session growth. A 429 has a generic
message and conservative Retry-After: 900. No account-specific lock/unlock oracle.
Expired counters are discarded; this is not a business-audit retention policy.
Before raising limits, measure Argon2 memory/latency on the deployment hardware.

## RBAC and API

Sales administrators and managers have draft/generate-eligible capabilities; only
managers have approval capability (later QT-020 must also enforce configured
commercial authority and exact revision). System administrator means tenant admin,
with users/imports/settings/policy configuration, never cross-tenant or approval
rights. Optional system-admin sales/support and manager policy-edit rights are denied.
Audit capabilities distinguish allowed cases, tenant commercial, tenant operations;
there is no broad audit-read API. Forced-change principals cannot use these rights.
`Principal.require(Capability)` is the reusable deterministic matrix.

Typed OpenAPI is available at `/openapi.json`. Routes:
- POST `/api/auth/login`, `/refresh`, `/logout`, `/password`; GET `/api/me`.
- GET/POST `/api/admin/users`; PATCH `/api/admin/users/{id}`.
- User creation returns 201; an existing normalized login returns 409 without changing
  the user. After an uncertain response, refresh the list rather than assume failure
  or reset credentials. No password is returned. List is tenant-scoped, capped at 200.
- PATCH requires current `version` and only accepts `disabled` and/or
  `revoke_sessions`. Stale concurrent changes return 409; role/tenant/password
  mass assignment is rejected. Unknown and foreign IDs both return 404.
- Invalid credentials: AUTH_INVALID_CREDENTIALS/401; insufficient authority:
  AUTH_FORBIDDEN/403; invalid CSRF: AUTH_CSRF_REJECTED/403; malformed input: 422;
  conflict: 409; rate limit: 429; database failure: SERVICE_UNAVAILABLE/503.

## Audit and migration

Migration `0002_auth` follows `0001_tenant_baseline`, adding tenant_logins, users,
auth_sessions, business_audit_events and auth_limits. Existing tenant/settings data
is preserved. Composite foreign keys constrain session/user and audit/actor tenant
relationships. Business audit has actor/action/target/outcome/evidence/UTC time,
no case dependency, and a database trigger rejects UPDATE, DELETE and TRUNCATE.
Only append is exposed in application code. Database owners can change schema;
production database-role hardening and retention remain deployment/security work.
Bootstrap, known-tenant login success/failure, logout, password changes and admin
changes are recorded. Unknown-tenant attempts emit a minimized operational event.
Successful consequential mutations and audit commit together. Failed-login audit
commits before the generic authentication denial is raised.

Backout is destructive: downgrading 0002 drops users, sessions, locators, counters and
all auth audit history. Never downgrade an existing deployment as routine repair.
Take and validate a backup, stop writes, obtain an approved data-preservation/backout
plan, then restore or forward-fix. Downgrade/re-upgrade tests use disposable databases.

## Verification

- `npm run check`: frontend/server lint/types/tests/build, including real PostgreSQL 17
  auth/migration/concurrency tests. Required DB tests fail if unavailable.
- `node apps/web/node_modules/@playwright/test/cli.js install chromium` once locally.
- `npm run test:e2e`: disposable PostgreSQL database, trusted bootstrap, real API and
  Vite, Chromium login/forced change/restoration/admin disable/logout/mobile checks.
  Ports 8001 and 5174 must be free. CI installs Chromium and executes this gate.

Implementation references checked 2026-09-08:
- https://argon2-cffi.readthedocs.io/en/stable/parameters.html
- https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html
- PostgreSQL advisory locks and constraints are exercised against PostgreSQL 17.
