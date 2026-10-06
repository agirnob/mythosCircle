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
- Node.js 22 with npm (the version used by CI and Docker builds)
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
| `make dev`         | backend API + frontend dev server with writable local data paths        |
| `make test`        | backend `pytest` + frontend `vitest`                                    |
| `make lint`        | `ruff check` + `ruff format --check` (backend) + `eslint` (frontend)    |
| `make format`      | `ruff format` + `ruff check --fix` (backend) + `prettier --write`       |
| `make typecheck`   | `mypy --strict` (backend) + `vue-tsc --noEmit` (frontend)               |

## Running locally

Start new work on `feature/<name>` or `fix/<name>` from `develop`. Merge work
through a pull request into `develop`, then release with a `develop` → `main`
pull request. GitHub Actions checks the code and deploys successful `main`
releases to the production Docker stack. See [pipeline setup and operations](deploy/AUTO_DEPLOY.md).

```sh
make dev
```

Open http://127.0.0.1:5173/. The API runs at http://127.0.0.1:8000/ and
Vite proxies `/api` and `/ws` to it. Stop both with Ctrl+C.

`make dev` stores the database, media, and JSON log under ignored
`backend/data/`. To use a database stored elsewhere, override its path and
the matching media directory before starting, for example:

```sh
MYTHOSCIRCLE_DB=sqlite:////absolute/path/to/world.db \
MYTHOSCIRCLE_MEDIA_DIR=/absolute/path/to/media make dev
```

## Conventions

The repo-wide conventions are:

- ULID identifiers (not UUIDs); UTC ISO-8601 timestamps
- error envelope `{code, message, details?}`; cursor pagination
- 4xx = user error (never a state change); 5xx = server error
- `snake_case` Python, `kebab-case` API routes, `camelCase` TS/Vue

Backend layering (enforced by structure): `api/` orchestrates, `pipeline/`
proposes, `store/` is the **sole writer** of world state, `providers/` and
`media/` are leaves. Never write world state outside `backend/app/store/`.

No secrets in `deploy/config.toml` or the client build — environment
variables only (AD-22).
