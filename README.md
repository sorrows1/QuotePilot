# QuotePilot

QuotePilot is a quotation-preparation application for wholesale and distribution SMEs. QT-001 establishes the repository and CI foundation only; business features are implemented by later tasks.

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

```bash
npm run setup
```

This command performs setup in fail-fast order:

- first runs the cross-platform database setup script, which uses Docker Compose to create the PostgreSQL container and persistent volume when missing or start/reconcile the service when already present;
- waits for PostgreSQL's container health check;
- performs an authenticated TCP `psql` transaction using the configured `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB`, including a temporary write/read check that is rolled back;
- only after database validation succeeds, installs the frontend from `apps/web/package-lock.json` and synchronizes the Python environment in `apps/server` from `pyproject.toml`/`uv.lock`.

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

`/health` is the API liveness endpoint. It currently proves that the FastAPI application is running and returns its stable health payload; it does not query PostgreSQL. Database-aware readiness belongs in a later persistence task once the server has a real database connection layer.

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

The root command delegates to the existing frontend and server checks. The server pytest suite includes an in-process test of `/health`; Starlette's supported `httpx2` test-client dependency is locked for that test path. CI separately exercises the live `/health` endpoint through the fully containerized stack. Focused commands are also available:

```bash
npm run lint
npm run typecheck
npm test
npm run build
```

CI installs from the committed lockfiles, runs the same meaningful frontend/server checks, validates the native Vite launcher path, validates the committed Codex project configuration, verifies authenticated PostgreSQL provisioning including rejection of stale credential configuration, and smoke-tests the full Docker Compose stack from a clean checkout.

## Configuration

Application secrets and machine-specific state must stay outside Git. `.env` files, virtual environments, build output, caches, and local Codex state are ignored. Docker build contexts also exclude local `.env` files so secrets are not copied into application images. Add only documented, non-secret example configuration when a future task introduces a real configuration requirement.
