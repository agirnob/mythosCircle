# Deferred Work Ledger

Findings routed to `defer` during reviews, kept for future planning and story tracing.

## Deferred from: code review of spec-1-2-versioned-world-store (2026-08-30)

- Undo rejects unknown event types with `CorruptEventError` — forward-compat break when later stories (session events, media manifest, queue) append new event types to the shared log; extend per new type. [backend/app/store/undo.py]
- No log-vs-materialized audit/verification surface — no replay/consistency helper exists; `CorruptEventError` reachable only via undo. [backend/app/store/]
- Read helpers cannot distinguish unknown campaign from empty world — 1.6 API mapping must handle 404s itself. [backend/app/store/read.py]
- Edge counters have no semantic/range validation — pipeline stories assign counter meanings. [backend/app/store/models.py]
- `backup.sh` lacks a single-instance guard (cron vs manual overlap) — 1.7 ops item. [deploy/backup.sh]
- Frontend has no wire-contract foundation — 1.6 builds the OpenAPI-client/api/error-envelope layer. [frontend/src/]

## Deferred from: code review of spec-1-3-persistent-generation-queue (2026-08-30)

- source_spec: `spec-1-3-persistent-generation-queue.md`
  summary: 409 duplicate-job response offers no path to the existing job — a retry-heavy client must follow up with GET /api/jobs/{id} after a 409.
  evidence: the 201 reply carries no Location header and the 409 envelope includes no job state; idempotent-retry ergonomics are unaddressed by the wire contract.
- source_spec: `spec-1-3-persistent-generation-queue.md`
  summary: `deploy/config.toml` `[queue] max_in_flight_per_campaign = 1` (running-only semantics) conflicts with the runtime env cap `MYTHOSCIRCLE_MAX_PENDING_PER_CAMPAIGN` (queued+running, default 10) with no cross-reference.
  evidence: config consumption is deferred to Story 1.7; the two limits overlap in name and semantics — 1.7 must reconcile and document the relationship.
- source_spec: `spec-1-3-persistent-generation-queue.md`
  summary: WS `job_cancelled`/`job_done`/`job_failed` (terminal) carry no `queue_position` — subscribers tracking other pending jobs must re-poll the list after every terminal transition.
  evidence: AD-17's shape pins `queue_position?` as optional; per-subscriber position or a position-delta was out of scope for 1.3 — revisit when the frontend wires the queue view.

## Deferred from: code review of spec-1-4-openai-compatible-inference-adapter (2026-08-30)

- source_spec: `spec-1-4-openai-compatible-inference-adapter.md`
  summary: A cancel landing while the provider call is in flight still wastes the call up to the timeout; the terminal completion then raises JobStateConflict which the worker logs as a secondary error.
  evidence: the cancel-race poll (review round 1) prevents the call when the cancel lands between claim and call, but a mid-call cancel can't be preempted without cancellation support in httpx; the queue never wedges (the job stays cancelled, the slot stays freed) — only log noise remains; revisit when streaming/abort lands.
- source_spec: `spec-1-4-openai-compatible-inference-adapter.md`
  summary: There is no general schema-migration mechanism — `init_db` does `create_all` plus the single `job.result` column ADD; future column additions need hand-rolled ALTERs.
  evidence: review round 1 added `_migrate_job_result`; a proper migration tool (Alembic) or a schema-versioning convention belongs in a later ops story (1.7 or Epic 6), not per-column ad-hoc ALTERs.
## Deferred from: code review of spec-1-5-dm-authentication (2026-08-30)

- source_spec: `spec-1-5-dm-authentication.md`
  summary: Session rows accumulate without bound — every login inserts, expiry/revocation marks rather than purges, and nothing reclaims them.
  evidence: review round 1 flagged unbounded `session` growth; a TTL sweep / purge job (or row deletion on match of a configurable retention) belongs in a later ops story (1.7 or Epic 6).
- source_spec: `spec-1-5-dm-authentication.md`
  summary: No rehash-on-login — future argon2 parameter bumps never upgrade existing accounts' hashes.
  evidence: `verify_login` verifies but never calls `check_needs_rehash`; a hardening story should add lazy rehash on successful login.
- source_spec: `spec-1-5-dm-authentication.md`
  summary: Proxy-trust model is loopback-only — `_client_ip` honors X-Forwarded-For only from 127.0.0.1/::1 (the Caddy proxy).
  evidence: single-tenant beta is fine behind the same-host reverse proxy; a broader trusted-proxy/header configuration is a deployment hardening (1.7).
- source_spec: `spec-1-5-dm-authentication.md`
  summary: Login CSRF is accepted — a cross-site top-level form POST could set a session cookie for an attacker-chosen account.
  evidence: SameSite=Lax mitigates most browsers' cross-site POSTs; the single-tenant owner tool accepts the residual risk — revisit with an Origin/Host check or CSRF token if the deployment expands (deferred-work note).
