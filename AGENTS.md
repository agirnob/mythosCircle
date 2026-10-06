# mythosCircle

Living-world NPC and world generator for TTRPG Game Masters. The DM remains the final author. The backend uses Python 3.12, FastAPI, SQLAlchemy 2, and SQLite (WAL); the frontend uses Vue 3, Vite, TypeScript, and Pinia.

## Working rules

- Start new work from an up-to-date `develop` branch on `feature/<name>` or `fix/<name>`; never implement directly on `main` or `develop`.
- Push the work branch and open a pull request into `develop`. Require the `CI gate` check before merging. Release through a pull request from `develop` into `main`.
- `main` is production: its successful GitHub Actions run deploys the tested Docker images to the existing Portainer stack. Do not bypass checks or deploy development branches. Direct hotfix pushes require explicit user authorization.
- Before publishing, run the relevant local checks and keep CI green. Document deployment or workflow changes in `deploy/AUTO_DEPLOY.md`. Repository rules apply to future agents as well as human changes.
- Write world state only through `backend/app/store/`, the sole graph writer.
- Keep secrets out of `deploy/config.toml` and the client build; use environment variables.
- Campaigns are private to their invited user. Do not introduce public campaign endpoints.
- Subagents may run in parallel.

## Open Code Review in Codex

- For an Open Code Review request, use the plugin's `open-code-review-delegate` skill by default. Run `ocr delegate preview --format json` for the requested Git target, then `ocr delegate rule --format json` for every reviewable file and review the selected diffs in Codex.
- For a whole-code review of the backend and frontend, run `ocr scan --preview --format json --path backend/app,frontend/src` to enumerate files without an OCR LLM, use `ocr delegate rule --format json` to resolve rules, and review the full source files in bounded batches. Account for every excluded file; split source files marked `too_large` into sections for manual review, and include tests when the request covers them.
- Use OCR-managed `ocr review` only when the user explicitly requests it and an OCR LLM provider is configured. The Codex session does not supply an API endpoint or key to the OCR CLI.
- If the working tree is clean and no review target is named, review `HEAD` with `--commit HEAD`; report the chosen target.

## Project layout and checks

- Backend: `backend/app/{api,core,store,pipeline,providers,media}` and `backend/tests/`.
- Frontend: `frontend/src/`.
- Deployment: `deploy/`.
- Run backend tests from `backend/` with `.venv/bin/pytest -q`.
- Run frontend tests, type checks, and a production build from `frontend/` with `npm test -- --run`, `npm run typecheck`, and `npm run build`.
- Verify frontend changes with Node.js 22, matching GitHub Actions and Docker; a passing run under another Node version does not replace that check.

## Conventions

- `snake_case` Python, `kebab-case` API routes, `camelCase` TypeScript/Vue.
- Entity, edge, job, and event IDs are ULIDs, not UUIDs.
- Use UTC ISO-8601 timestamps, `{code, message, details?}` error envelopes, and cursor pagination.
