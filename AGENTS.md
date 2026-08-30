<!-- bmad:context -->
<!-- Verified 2026-08-25 against 0e45156. Managed by bmad-project-context; edits inside this block are replaced on refresh. Keep anything you want preserved outside the markers. -->

## mythosCircle

Living-world NPC & world generator for TTRPG DMs — a tool for the DM, not an AI DM; the DM always keeps the wheel. Python 3.12 + FastAPI + SQLAlchemy 2 + SQLite (WAL) backend; Vue 3.5 + Vite + TypeScript + Pinia frontend; local llama.cpp LLM behind Caddy TLS. Greenfield: planning lives in `_bmad-output/`; the code layout below is the intended seed.

## Policy

- Solo repo: commit and push directly; no PR gate.
- Never write world state outside `backend/app/store/` — the store's commit path is the only writer to the graph (AD-1).
- Never put secrets in `deploy/config.toml` or the client build — environment variables only (AD-22).
- Campaigns are private, one invited user each — no sharing or public endpoints in beta (AD-9).

## Where things are

- PRD: `_bmad-output/planning-artifacts/prds/prd-mythosCircle-2026-08-23/prd.md`
- World-state invariants (one commit path, event-sourced revisions, closed edge vocabulary, no RAG): `_bmad-output/planning-artifacts/architecture/architecture-mythosCircle-2026-08-23/ARCHITECTURE-SPINE.md` — read before touching `backend/app/store/` or `backend/app/pipeline/`.
- Spec, conventions, stack, glossary: `_bmad-output/specs/spec-mythosCircle/`
- Intended code layout: `backend/app/{api,core,store,pipeline,providers,media}`, `backend/tests/`, `frontend/src/`, `deploy/`

## Running and verifying

- No code or build tooling yet (greenfield) — verify these on the first refresh once code exists.
- Backend (Python/FastAPI): ruff (lint+format) and mypy (typecheck) are the chosen tools — exact invocations TODO; store + pipeline carry unit tests.
- Frontend (Vue/TS/Vite): eslint + prettier (lint+format) and vue-tsc (typecheck) are the chosen tools — exact invocations TODO.
- No UI e2e in beta — the owner dogfoods the UI.

## Conventions that differ from defaults

- `snake_case` Python, `kebab-case` API routes, `camelCase` TS/Vue; entity/edge/job/event IDs are ULIDs, not UUIDs.
- UTC ISO-8601 timestamps; error envelope `{code, message, details?}`; cursor pagination.
<!-- /bmad:context -->

## Agent rules (owner)

- Subagents may run in parallel while building this project — no serialization constraint (the earlier llama.cpp concurrency restriction was lifted 2026-08-30).
