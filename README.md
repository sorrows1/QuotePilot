# QuotePilot

QuotePilot is a quotation-preparation application for wholesale and distribution SMEs. QT-001 establishes the repository and CI foundation only; business features are implemented by later tasks.

## Repository layout

- `apps/web` — React + Vite + TypeScript single-page app.
- `apps/api` — FastAPI + Python API.
- `.codex` — project-scoped Codex adapter and read-only helper roles.
- `AGENTS.md` — vendor-neutral repository engineering policy.
- `docker-compose.yml` — reproducible local PostgreSQL, API, and web environment.

## Prerequisites

For containerized development, install Docker Desktop (or another Docker Compose-compatible engine).

For native development and checks, install:

- Node.js 22 or newer and npm 10 or newer.
- Python 3.12 or 3.13.
- `uv` 0.12.x.

## Start the full local stack

```bash
docker compose up --build
```

Then open:

- Web: http://localhost:5173
- API health: http://localhost:8000/health

The Compose PostgreSQL credentials are development-only defaults. Override `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB` in your local environment when needed; never reuse these values outside local development.

Stop the stack with:

```bash
docker compose down
```

Use `docker compose down -v` only when you intentionally want to delete the local database volume.

## Native frontend development

```bash
cd apps/web
npm ci
npm run dev
```

Frontend checks:

```bash
npm run lint
npm run typecheck
npm test
npm run build
```

## Native API development

```bash
cd apps/api
uv sync --frozen
uv run uvicorn quotepilot_api.main:app --reload --host 0.0.0.0 --port 8000
```

API checks:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run pytest
```

## Full local quality gate

Run both frontend and API checks above before requesting independent review. CI runs the same deterministic gates from a clean checkout.

## Configuration

Application secrets and machine-specific state must stay outside Git. `.env` files, virtual environments, build output, caches, and local Codex state are ignored. Add only documented, non-secret example configuration when a future task introduces a real configuration requirement.
