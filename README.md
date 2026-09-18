# QuotePilot

QuotePilot is a quotation-preparation application for wholesale and distribution SMEs. The foundation includes PostgreSQL migrations and explicit tenant ownership; business features are implemented by later tasks.

## Repository layout

- `apps/web` — React + Vite + TypeScript single-page app.
- `apps/server` — FastAPI + Python server/API.
- `.codex` — project-scoped Codex adapter and read-only helper roles.
- `AGENTS.md` — vendor-neutral repository engineering policy.
- `docker-compose.yml` — reproducible PostgreSQL and containerized integration environment.

## Prerequisites

Install:

- Node.js 22.13 or newer and npm 10 or newer.
- Python 3.12 or 3.13.
- `uv` 0.12.x.
- Docker Desktop or another Docker Compose-compatible engine, with the Docker engine running before setup.

## Initial setup

From the repository root:

Optionally copy `.env.example` to `.env` to customize development settings (`Copy-Item .env.example .env` in PowerShell, or `cp .env.example .env` in a POSIX shell).

```bash
npm run setup
```

This command performs setup in fail-fast order:

- first runs the cross-platform database setup script, which uses Docker Compose to create the PostgreSQL container and persistent volume when missing or start/reconcile the service when already present;
- waits for PostgreSQL's container health check;
- performs an authenticated TCP `psql` transaction using the configured `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB`, including a temporary write/read check that is rolled back;
- only after database validation succeeds, installs the frontend from `apps/web/package-lock.json` and synchronizes the Python environment in `apps/server` from `pyproject.toml`/`uv.lock`;
- applies versioned database migrations to head through the serialized migration runner.

This makes setup fail before dependency installation if Docker is unavailable, PostgreSQL cannot become healthy, the configured database/user/password do not work, or SQL read/write validation fails. The PostgreSQL service is left running so `npm run dev` can reuse it. CI uses `uv sync --locked` for dependency setup so a stale lockfile fails instead of being rewritten.

The database step can also be run independently:

```bash
npm run setup:db
```

PostgreSQL initialization variables only define the database/user/password when the persistent data directory is first created. Changing `POSTGRES_USER`, `POSTGRES_PASSWORD`, or `POSTGRES_DB` later does not rewrite an existing database volume. `npm run setup:db` detects that mismatch because its authenticated SQL check fails instead of reporting success. If the local database is disposable and you intentionally want to recreate it with new values, run:

```bash
docker compose down -v
npm run setup:db
```

`docker compose down -v` deletes the local PostgreSQL volume and its data, so do not use it when you need to preserve that database.

## Daily local development

```bash
npm run dev
```

The launcher:

- checks that `apps/web/node_modules` and `apps/server/.venv` already exist and tells you to run `npm run setup` if either is missing;
- starts/reconciles the PostgreSQL service with Docker Compose and waits for it to become healthy;
- starts FastAPI/Uvicorn with reload on the host at http://localhost:8000;
- starts Vite with hot reload on the host at http://localhost:5173;
- stops the web/server processes on Ctrl+C while leaving the PostgreSQL container running for the next development session.

Stop the development database explicitly when needed:

```bash
docker compose stop db
```

Use `docker compose down -v` only when you intentionally want to delete the local database volume.

## Containerized integration stack

To build and run the web app, server, and PostgreSQL entirely with Docker Compose:

```bash
docker compose up --build
```

Then open:

- Web: http://localhost:5173
- API health: http://localhost:8000/health

`/health` is the API liveness endpoint. It proves that the FastAPI application is running and returns its stable health payload; it does not query PostgreSQL or validate database configuration.

The Compose PostgreSQL credentials are development-only defaults. Override `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, and `POSTGRES_PORT` in your local environment when needed; never reuse these values outside local development.

Stop the stack with:

```bash
docker compose down
```

## Quality checks

Run the complete local quality gate from the repository root:

```bash
npm run check
```

The root command delegates to the frontend and server checks, including real PostgreSQL migration and tenant isolation tests. Run `npm run setup` first. Tests create uniquely named disposable databases on the configured PostgreSQL instance (the test role needs `CREATEDB`), downgrade only those databases, and remove them afterward. An unavailable database fails the gate; tests are never silently skipped. Do not point tests at production. Starlette's supported `httpx2` test-client dependency remains locked for `/health` tests. CI separately exercises live `/health` through the containerized stack. Focused commands are also available:

```bash
npm run lint
npm run typecheck
npm test
npm run build
node scripts/server.mjs pytest
```

CI installs from the committed lockfiles, runs the same meaningful frontend/server checks, validates the native Vite launcher path, validates the committed Codex project configuration, verifies authenticated PostgreSQL provisioning including rejection of stale credential configuration, and smoke-tests the full Docker Compose stack from a clean checkout.

## Configuration

Application secrets and machine-specific state must stay outside Git. `.env` files, virtual environments, build output, caches, and local Codex state are ignored. Docker build contexts also exclude local `.env` files so secrets are not copied into application images. Add only documented, non-secret example configuration when a future task introduces a real configuration requirement.

For native development, the root commands resolve `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, and `POSTGRES_PORT` from `docker compose config`, including root `.env` values and shell overrides. They construct the host connection URL with escaped credentials. Compose supplies the same credentials to the container, where the database host is `db` and the port is 5432. The Python connection factory handles both through one configuration boundary and sets database sessions to UTC. Direct Python commands require explicit configuration; use `node scripts/server.mjs <command>` for the local Compose configuration.

`DATABASE_URL` is the explicit deployment/connection override and takes precedence, including when set in the root `.env`. Use `postgresql://` or `postgresql+psycopg://` with percent-encoded credentials and any required TLS query parameters. Its host must be reachable from the process consuming it; omit it for the automatic native/container development mapping. A deployment must supply its own configuration; the server has no implicit production credentials. Setup still validates the Compose database before installing dependencies, even when an explicit connection override selects another migration target.

## Migrations and tenant boundary

The QT-002 compatibility target is **PostgreSQL 17**, matching Compose and CI. On 2026-09-08, the project owner verified Lightsail `ap-southeast-1` database blueprints directly in AWS CloudShell: 13.23-rds.20260224, 14.24, 15.19, 16.15, **17.11**, and 18.6 (AWS default). The owner selected 17 to preserve the established baseline; AWS's default 18 is not a requirement to upgrade. QT-002 requires no PostgreSQL extensions. This region-specific evidence supersedes the older general AWS overview listing versions only through 16.

```bash
npm run db:migrate
# Container migration against the container's effective configuration:
docker compose run --rm api python -m quotepilot_api.migrate
```

Run migrations explicitly after pulling schema changes. Fresh setup applies them after dependency installation; `npm run dev` and application startup do not run migrations. Both commands use the same Alembic runner and a PostgreSQL transaction advisory lock, so simultaneous runners serialize before reading migration state. Failure rolls back transactional DDL and returns a nonzero status. Migration files ship inside the server package and Docker image.

Initial revision `0001_tenant_baseline` adds `tenants` and one-to-one `tenant_settings` with native UUID keys, non-null timezone-aware timestamps, and a typed `business_timezone` field. No extensions, seed tenant, business tables or commercial settings are introduced. UUIDs are generated by the trusted application provisioning operation. Business timezone is a validated IANA name, distinct from UTC storage/session timestamps. Later money, quantity and rate fields must use exact decimal types and the confirmed commercial rules; this migration introduces none.

`TenantService` owns commit/rollback. Repositories require a non-null `TenantContext`, scope reads and updates by that identity, and never commit. Provisioning is a trusted application operation with no public endpoint; QT-003 will derive context from the authenticated principal. A supplied record ID never selects the authoritative tenant. QT-004 owns the later settings API/UI and fields.

Backout `node scripts/server.mjs python -m quotepilot_api.migrate downgrade base` drops both baseline tables and their data. Use it only on a disposable database for smoke testing; populated environments require an explicit backup/data-preservation plan before backout. Never change a PostgreSQL image major against an existing populated volume in place.

## Authentication

See [authentication operations and session contract](apps/server/AUTH.md) for trusted bootstrap, local/production origin settings, RBAC, migration/backout and browser verification. Run `npm run auth:bootstrap` after setup; run `npm run test:e2e` for the real browser gate.
