# mythosCircle

Living-world NPC & world generator for TTRPG DMs — a tool for the DM, not an
AI DM; the DM always keeps the wheel.

Monorepo layout:

- `backend/` — Python 3.12 + FastAPI (uv-managed); world store, generation
  queue, and inference adapters land here (Epic 1+)
- `frontend/` — Vue 3.5 + Vite 8 + TypeScript + Pinia 4
- `deploy/` — Caddy TLS config, systemd unit, config template, backup/restore
  starting points (wired in Story 1.7)

## Requirements

- [uv](https://docs.astral.sh/uv/) — provisions the pinned Python 3.12
  automatically (the host Python may be anything; do not run the app on it)
- Node.js 20+ with npm
- Caddy 2 (deployment only)

## Setup

```sh
make setup
```

Provisions `backend/.venv` (uv) and `frontend/node_modules` (`npm ci` from the
committed lockfiles).

## Standard commands

| Command            | What it runs                                                            |
| ------------------ | ----------------------------------------------------------------------- |
| `make test`        | backend `pytest` + frontend `vitest`                                    |
| `make lint`        | `ruff check` + `ruff format --check` (backend) + `eslint` (frontend)    |
| `make format`      | `ruff format` + `ruff check --fix` (backend) + `prettier --write`       |
| `make typecheck`   | `mypy --strict` (backend) + `vue-tsc --noEmit` (frontend)               |

## Running locally

```sh
# backend — http://127.0.0.1:8000/api/health
# The store defaults to the production path (/var/lib/mythoscircle/mythoscircle.db,
# matching deploy/config.toml and the backup scripts); point local dev at a
# writable location:
MYTHOSCIRCLE_DB=sqlite:///./data/mythosCircle.db uv run --directory backend uvicorn app.main:app --port 8000

# frontend — Vite dev server, proxies /api and /ws to 127.0.0.1:8000
cd frontend && npm run dev
```

## Conventions

The repo-wide conventions are canonical in
`_bmad-output/specs/spec-mythosCircle/conventions.md`:

- ULID identifiers (not UUIDs); UTC ISO-8601 timestamps
- error envelope `{code, message, details?}`; cursor pagination
- 4xx = user error (never a state change); 5xx = server error
- `snake_case` Python, `kebab-case` API routes, `camelCase` TS/Vue

Backend layering (enforced by structure): `api/` orchestrates, `pipeline/`
proposes, `store/` is the **sole writer** of world state, `providers/` and
`media/` are leaves. Never write world state outside `backend/app/store/`.

No secrets in `deploy/config.toml` or the client build — environment
variables only (AD-22).
