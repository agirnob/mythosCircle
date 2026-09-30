# mythosCircle

Living-world NPC and world generator for TTRPG Game Masters. The DM remains the final author. The backend uses Python 3.12, FastAPI, SQLAlchemy 2, and SQLite (WAL); the frontend uses Vue 3, Vite, TypeScript, and Pinia.

## Working rules

- Solo repository: commit and push directly; no PR gate.
- Write world state only through `backend/app/store/`, the sole graph writer.
- Keep secrets out of `deploy/config.toml` and the client build; use environment variables.
- Campaigns are private to their invited user. Do not introduce public campaign endpoints.
- Subagents may run in parallel.

## Project layout and checks

- Backend: `backend/app/{api,core,store,pipeline,providers,media}` and `backend/tests/`.
- Frontend: `frontend/src/`.
- Deployment: `deploy/`.
- Run backend tests from `backend/` with `.venv/bin/pytest -q`.
- Run frontend tests, type checks, and a production build from `frontend/` with `npm test -- --run`, `npm run typecheck`, and `npm run build`.

## Conventions

- `snake_case` Python, `kebab-case` API routes, `camelCase` TypeScript/Vue.
- Entity, edge, job, and event IDs are ULIDs, not UUIDs.
- Use UTC ISO-8601 timestamps, `{code, message, details?}` error envelopes, and cursor pagination.
