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
- Docker Desktop or another Docker Compose-compatible engine.

## Initial setup

From the repository root:

```bash
npm run setup
```

This installs the frontend from `apps/web/package-lock.json` and synchronizes the Python environment in `apps/server` from `pyproject.toml`/`uv.lock`. Developer setup uses normal `uv sync`; CI uses `uv sync --locked` so a stale lockfile fails instead of being rewritten.

## Daily local development

```bash
npm run dev
```

The launcher:

- checks that `apps/web/node_modules` and `apps/server/.venv` already exist and tells you to run `npm run setup` if either is missing;
- starts the PostgreSQL service with Docker Compose and waits for it to become healthy;
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

The root command delegates to the existing frontend and server checks. Focused commands are also available:

```bash
npm run lint
npm run typecheck
npm test
npm run build
```

CI installs from the committed lockfiles, runs the same meaningful frontend/server checks, validates the committed Codex project configuration, and smoke-tests the full Docker Compose stack from a clean checkout.

## Configuration

Application secrets and machine-specific state must stay outside Git. `.env` files, virtual environments, build output, caches, and local Codex state are ignored. Docker build contexts also exclude local `.env` files so secrets are not copied into application images. Add only documented, non-secret example configuration when a future task introduces a real configuration requirement.
