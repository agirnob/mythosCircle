---
title: 'Deploy to the operator host: config consumption, logging, one-command deploy'
type: 'feature'
created: '2026-08-30'
status: 'done'
review_loop_iteration: 0
baseline_commit: 'e7b0076efdd22748dc9c99126e4893130175fa13'
context:
  - '/home/main/Projects/mythosCircle/_bmad-output/planning-artifacts/architecture/architecture-mythosCircle-2026-08-23/ARCHITECTURE-SPINE.md'
  - '/home/main/Projects/mythosCircle/_bmad-output/specs/spec-mythosCircle/conventions.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The app is env-configured while `deploy/config.toml` (the AD-22 single config) is read by nothing; there is no JSON-lines log despite `log_file` being promised; the `[queue] max_in_flight_per_campaign` value and the runtime pending-cap env disagree (deferred 1.3 finding); `backup.sh` lacks a single-instance guard (deferred 1.2 finding); and there is no single-command deploy that installs Caddy, systemd, config, and the backup cron — MythosCircle cannot actually reach an operator's host yet.

**Approach:** Consume `deploy/config.toml` at startup (env overrides; secrets stay env-only per AD-22) for the queue cap, LLM endpoint/model, campaign themes, DB path, and log file — removing the runtime mirrors and honoring every deferred reconciliation. Wire a JSON-lines file logger. Add `deploy/deploy.sh`: one idempotent command that installs Caddyfile/systemd unit/config/env-file/cron, builds and installs the frontend, and starts the services — with a `DEPLOY_STAGE` dry-run root so the whole flow is testable without root. Add the backup flock guard.

## Boundaries & Constraints

**Always:**
- `deploy/config.toml` is the operator config (AD-22); environment variables override it; secrets are environment-only (never in config.toml, never in the client build).
- The runtime config is loaded once (lazily, module-level) from `MYTHOSCIRCLE_CONFIG` (default: the repo `deploy/config.toml` if present during dev/tests, else `/etc/mythoscircle/config.toml`; missing file → code defaults; malformed toml → fail loudly at first settings read).
- Queue cap reconciliation (deferred 1.3): `[queue] max_in_flight_per_campaign` IS the pending cap (in-flight + queued, AR28); `MYTHOSCIRCLE_MAX_PENDING_PER_CAMPAIGN` overrides it; both default to 10 when absent. The config file's comment is corrected to this semantic.
- Campaign themes (deferred 1.6 mirror): `[campaigns].themes` drives the validation list at runtime; `store/campaigns.py` reads the configured themes (code seed as fallback when absent); unknown theme → 422 naming the configured list. The deploy-contract test now pins config → consumed, not config == code seed.
- DB path (deferred 1.2 composition): `[world].sqlite_path` feeds the store's default URL when `MYTHOSCIRCLE_DB` is unset; the 1.2 deploy-contract test keeps passing (both now derive from the same config).
- Logging (deferred 1.1/1.2 "zero logging"): `[world].log_file` (env `MYTHOSCIRCLE_LOG_FILE` overrides) gets a JSON-lines file handler configured at app startup; console logging stays; `handle_unexpected_error` logs the exception with its traceback before returning the generic 500 envelope.
- `deploy/deploy.sh` (NEW): idempotent single-command deploy — installs Caddyfile, systemd unit, config.toml, the generated env file (non-secret values from config; an `# add secrets here` marker — AD-22), backup.cron, builds+installs the frontend dist, creates the `mythoscircle` user + dirs, enables+starts caddy and mythoscircle.service. `DEPLOY_STAGE` (default empty = real `/`) redirects every install target under the stage root so the flow runs unprivileged. `SUDO` (default `sudo`) prefixes privileged steps; `SKIP_SERVICE=1` skips systemctl. Drops `set -euo pipefail`, fails on unknown stage paths.
- `deploy/backup.sh`: add a `flock -n` single-instance guard (deferred 1.2).
- The existing deploy-contract tests from 1.2/1.5/1.6 keep passing; 1.7 extends them where semantics changed (themes, DB path) rather than replacing.
- Everything here is verified deterministically without root: stage-dry-run deploy, config parse/precedence unit tests, logging file tests, `bash -n` on the scripts.

**Ask First:** actually executing the systemd/Caddy install on this machine (the operator's host owns that; this repo builds + dry-runs the flow); TLS certificate issuance (Caddy automatic); a second deploy target (container/K8s); Alembic migrations (deferred to Epic 6 per the ledger).

**Never:** secrets in config.toml or the generated env file's committed form (the env file is generated at deploy, not committed); running systemd/Caddy inside tests; a deploy that needs an interactive prompt or fails silently on a typo'd value (fail loudly at the first settings read); changing the wire contract or store behavior beyond config sourcing.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| CONFIG_LOADED | config.toml present with all sections | queue cap, llm endpoint/model, themes, sqlite_path, log_file all consumed | N/A |
| CONFIG_MISSING | no config file | code defaults; env still overrides | N/A |
| CONFIG_MALFORMED | unparseable toml | first settings read raises, describing the file/path | loud startup failure |
| ENV_OVERRIDE | `MYTHOSCIRCLE_MAX_PENDING_PER_CAMPAIGN` set | env value wins over config.toml | N/A |
| QUEUE_RECONCILED | config `max_in_flight_per_campaign = 1`, no env | runtime pending cap = 1 (in-flight + queued, AR28) | N/A |
| THEMES_CONFIG | config `themes = ["Grimdark", "Steampunk"]` | `normalize_theme` accepts only those; unknown → 422 listing them | N/A |
| THEMES_ABSENT | no `[campaigns]` | code seed fallback (High Fantasy, Grimdark, Steampunk, Planar) | N/A |
| DB_PATH_CONFIG | config `sqlite_path` set, no `MYTHOSCIRCLE_DB` | `init_db` targets it | N/A |
| LOG_FILE | config `log_file` set | JSON-lines file created; a 500 logs the exception | malformed JSON never — the handler formats |
| DEPLOY_DRY_RUN | `DEPLOY_STAGE=/tmp/stage` | Caddyfile, unit, config, env file, cron, dist all land under the stage | exits non-zero on any failure |
| BACKUP_FLOCK | two concurrent runs | second exits with an explicit "already running" message | flock -n non-zero → exit 1 |

</frozen-after-approval>

## Code Map

- `backend/app/core/config.py` -- NEW: `runtime_config() -> RuntimeConfig` (lazy, cached; `reset_runtime_config()` for tests), `load_config(path) -> dict` (tomllib; `MYTHOSCIRCLE_CONFIG` env, defaults repo `deploy/config.toml` then `/etc/mythoscircle/config.toml`, `FileNotFoundError` → `{}`), fail-loud on parse errors; `RuntimeConfig` fields: queue_max_pending, llm_endpoint, llm_model, llm_timeout, themes, sqlite_path, log_file.
- `backend/app/core/settings.py` -- `queue_settings()/llm_settings()` read config-first, env-overrides (env > config > default); new `configured_themes() -> list[str]`, `configured_db_url() -> str | None`, `configured_log_file() -> str | None`. Reuse the existing `_env_*` parsers.
- `backend/app/store/db.py` -- `DEFAULT_DB_URL` becomes config-driven at `init_db` time: `app_db_url()` prefers env, then `configured_db_url()`, then the constant. `_migrate_*` unchanged.
- `backend/app/store/campaigns.py` -- `normalize_theme`/`SEED_THEMES` read `configured_themes()` at call time (cached); the module constant stays as the fallback seed.
- `backend/app/core/logging_setup.py` -- NEW: `setup_logging()` called from `create_app`; JSON-lines FileHandler to `configured_log_file()` (or console-only when None); sets the root level INFO.
- `backend/app/core/errors.py` -- `handle_unexpected_error` logs `exc` with `logger.exception` before the envelope.
- `backend/app/main.py` -- `create_app` calls `setup_logging()` first.
- `deploy/deploy.sh` -- NEW (see Always). Stage-aware install; generates `/etc/mythoscircle/mythoscircle.env` from config values (`MYTHOSCIRCLE_*`), builds the frontend (`npm ci && npm run build` in a temp, installs `dist` to the web root), installs `Caddyfile` → `/etc/caddy/`, `mythoscircle.service` → `/etc/systemd/system/`, `config.toml` → `/etc/mythoscircle/`, `backup.cron` → `/etc/cron.d/`; `systemctl enable --now` unless `SKIP_SERVICE=1`.
- `deploy/backup.sh` -- add the `flock` guard.
- `deploy/config.toml` -- correct the queue-cap comment to the AR28 semantic; add a `[server]` note that host/port feed the systemd unit (already pinned).
- `backend/tests/test_config.py` -- NEW: CONFIG_LOADED/MISSING/MALFORMED, ENV_OVERRIDE, QUEUE_RECONCILED, THEMES_CONFIG/ABSENT, DB_PATH_CONFIG.
- `backend/tests/test_logging.py` -- NEW: LOG_FILE writes JSON lines; a 500 via the app logs an exception line.
- `backend/tests/test_deploy_script.py` -- NEW: DEPLOY_DRY_RUN builds the expected tree under a stage; `bash -n` on deploy.sh/backup.sh/restore.sh; backup flock line present.
- `backend/tests/test_deploy_contract.py` -- update the theme pin to config → consumed; db-path test stays (both from config now).

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/core/config.py` -- runtime config loader (env-config-env precedence, fail-loud).
- [x] `backend/app/core/settings.py` -- settings read config-first.
- [x] `backend/app/store/db.py` -- config-driven default DB URL.
- [x] `backend/app/store/campaigns.py` -- configured themes.
- [x] `backend/app/core/logging_setup.py` + `errors.py` + `main.py` -- JSON-lines logging + 500 logging.
- [x] `deploy/deploy.sh` -- stage-aware single-command deploy.
- [x] `deploy/backup.sh` -- flock guard.
- [x] `deploy/config.toml` -- corrected queue-cap comment.
- [x] `backend/tests/test_config.py` + `test_logging.py` + `test_deploy_script.py` + `test_deploy_contract.py` updates.

**Acceptance Criteria:**
- Given the deploy config, when I run the deploy, then Caddy terminates TLS and the FastAPI service runs under systemd (AD-8, AR1) — the deploy script installs both and the stage dry-run proves the flow.
- Given the deployed instance, when the DM opens the site in a browser, then the login screen is served over HTTPS and the health check passes (NFR1) — the Caddyfile proxies `/api` to the loopback service and serves the built frontend (verified by the stage tree + the existing loopback contract test).
- Given the deploy, when the schedule runs, then the nightly backup cron is registered (AR1) — the deploy script installs `backup.cron`, pinning registration.

## Spec Change Log

<!-- Append-only. Populated by step-04 during review loops. Do not modify or delete existing entries.
     Each entry records: what finding triggered the change, what was amended, what known-bad state
     the amendment avoids, and any KEEP instructions (what worked well and must survive re-derivation).
     Empty until the first bad_spec loopback. -->

### Loop 1 (2026-08-30, review round 1)

**Findings fixed (all patch — no intent gap):**
- The deploy never provisioned the BACKEND the systemd unit starts
  (`ExecStart=/opt/mythoscircle/backend/.venv/bin/uvicorn` pointed at an
  empty dir) nor installed `backup.sh`/`restore.sh` the cron references —
  the "service runs under systemd" and nightly-backup ACs were
  unimplemented. `deploy.sh` now copies `backend/app`, pyproject/uv.lock,
  creates a venv (uv or pip fallback), and installs the deploy scripts
  under `/opt/mythoscircle/`.
- The generated env file carried zero config values; it now writes the
  non-secret `MYTHOSCIRCLE_*` values from config.toml (queue cap, LLM
  endpoint/model/timeout, DB, log file) above the secrets marker.
- `systemctl ... || true` swallowed real failures; both service enables
  now fail the deploy loudly. `[llm] max_llm_calls_per_job`/
  `max_media_calls_per_job` were still dead mirrors — consumed.
- Config values now validate (negative queue cap, zero/negative timeout,
  non-string themes, relative sqlite_path all fail loudly naming the key).
- Logging: duplicate FileHandler + missing-parent guards; `handle_
  unexpected_error` logs via `exc_info=<exception>` so the real traceback
  renders (the handler runs outside the except block).
- Tests added: stage tree asserts backend + scripts + env values; flock
  BEHAVIORAL test (lock held -> exit 1); Caddyfile content (https scheme,
  proxy paths, web root — the old `tls` substring matched only a comment);
  config themes at the STORE boundary; LLM env>config>default incl.
  set-but-empty env; budgets consumed; negative-value fail-loud.

**KEEP:** env > config > default precedence; secrets env-only (AD-22);
stage-dry-run deploy for root-free verification; fail-loud on malformed
config.

## Design Notes

Precedence everywhere is env > config.toml > code default — the operator's runtime env (or the generated `/etc/mythoscircle/mythoscircle.env`) wins over the shipped config, and the shipped config wins over the defaults. This keeps local dev working (repo config.toml exists) and production consistent (installed config), with the deploy-generated env file carrying the runtime values for systemd.

The queue-cap reconciliation is a semantic correction, not a new knob: `max_in_flight_per_campaign` (the name the deploy already ships) is re-documented as pending = in-flight + queued (AR28) and consumed as such. The env override keeps its name.

Themes move from a module constant to a runtime reading of `configured_themes()` — the store's `normalize_theme` calls it (cached per call; a config change requires a restart, which is fine for an operator config).

## Verification

**Commands:**
- `uv run --directory backend pytest -q` -- expected: all config/logging/deploy-script tests + full suite green (deterministic, no root).
- `make lint && make typecheck` -- expected: ruff, mypy strict, eslint, vue-tsc clean (backend only; deploy scripts checked by `bash -n`).

## Suggested Review Order

**Config consumption (the story's core)**

- The runtime resolver — env > config > default, validated, fail-loud
  [`config.py:74`](../../backend/app/core/config.py#L74)

- Settings read config-first; budgets consumed; set-but-empty env falls through
  [`settings.py`](../../backend/app/core/settings.py)

**Deploy (the single command)**

- Backend provisioning + env-file values + no silent service failures
  [`deploy.sh`](../../deploy/deploy.sh)

- Caddyfile https/proxy pins; backup flock behavior
  [`test_deploy_script.py`](../../backend/tests/test_deploy_script.py)
  [`test_config.py`](../../backend/tests/test_config.py)

**Logging**

- JSON-lines file handler + real-traceback 500 logging
  [`logging_setup.py`](../../backend/app/core/logging_setup.py)
  [`errors.py`](../../backend/app/core/errors.py)

