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

## Deferred from: code review of spec-1-6-private-world-creation-campaign-crud-with-seed (2026-08-30)

- source_spec: `spec-1-6-private-world-creation-campaign-crud-with-seed.md`
  summary: The additive seed migration produces nullable columns while the model declares NOT NULL — legacy rows (none in greenfield) would need backfill before they are owner-visible.
  evidence: ALTER TABLE ADD COLUMN cannot declare NOT NULL without a default; the migration is greenfield-safe (no legacy rows), a backfill story is a 1.7 deploy-concern if a pre-1.6 DB exists.
- source_spec: `spec-1-6-private-world-creation-campaign-crud-with-seed.md`
  summary: The generic-401 pre-auth body-validation ordering — an unauthenticated request with an invalid body gets 422 before the auth dependency rejects it.
  evidence: FastAPI validates the body before dependencies resolve; a strict auth-first gate would need middleware — accepted for the beta owner tool (no sensitive body fields), revisit if the deployment expands.

## Deferred from: code review of spec-1-7-deploy-to-the-operator-s-host (2026-08-30)

- source_spec: `spec-1-7-deploy-to-the-operator-s-host.md`
  summary: Actual Caddy TLS certificate issuance and systemd execution remain the operator host's act — the repo proves the flow via the stage dry-run; `caddy validate` and a live `systemctl start` need the host.
  evidence: the deploy script installs and enables both, but the acceptance proof of a live https site is a host-side action (Epic 6's restore proof and the beta-launch gate exercise the running host).
- source_spec: `spec-1-7-deploy-to-the-operator-s-host.md`
  summary: Alembic/general schema migrations remain deferred to Epic 6 — 1.7 kept the per-column `_migrate_*` convention.
  evidence: the ledger's 1.4 deferral stands; config consumption is complete but migration tooling is an ops-story concern.

## Deferred from: code review of spec-2-2-typed-edge-vocabulary-on-commit (2026-08-31)

- source_spec: `spec-2-2-typed-edge-vocabulary-on-commit.md`
  summary: `edge_counter_semantic` silently resolves non-vocabulary types to "neutral" — no membership guard on the exported resolver.
  evidence: no caller exists yet; the 2.3 pipeline is the first consumer and validates LLM-proposed types before commit — decide raise-vs-Optional semantics when a real caller appears. [backend/app/store/commit.py]
- source_spec: `spec-2-2-typed-edge-vocabulary-on-commit.md`
  summary: `EDGE_COUNTER_SEMANTICS` ships as a mutable plain dict and the resolver returns bare `str` (MappingProxyType / Literal["amount","score","intensity","neutral"] proposed).
  evidence: spec's Code Map prescribes `dict[str, str]` and `-> str`; map contents are pinned by test so drift fails CI; harden when 2.3 imports the contract. [backend/app/store/commit.py]

## Resolved by: spec-2-3-core-first-two-wave-build-in-pipeline (2026-08-31)

- The 2.2 "resolver membership semantics" deferral is settled by contract: the build-in pipeline is the resolver's first caller and
  validates every edge type against `EDGE_TYPES` BEFORE resolving (pipeline/build_in.py `_validate_subgraph`); the store's neutral
  fallback for non-vocabulary types stays documented behavior. No raise-vs-Optional change needed.
- The 2.2 "map immutability / Literal return" deferral is closed: `EDGE_COUNTER_SEMANTICS` is now `MappingProxyType[str,
  EdgeCounterSemantic]` and `edge_counter_semantic` returns `Literal["amount","score","intensity","neutral"]` (contents unchanged;
  the store-level pins in test_store.py stay green). [backend/app/store/commit.py]
## Deferred from: review of spec-2-3-core-first-two-wave-build-in-pipeline (2026-08-31)

- source_spec: `spec-2-3-core-first-two-wave-build-in-pipeline.md`
  summary: Counter semantic ranges are still undecided while the pipeline now assigns counters — the store validates shape only (int, SQLite 64-bit), so a negative debt amount or a grudge score of 0 or 10^6 commits as-is.
  evidence: 2.2's design note deferred ranges to "the 2.3/2.4 pipeline stories"; 2.3's frozen contract kept shape-only, but the 2.3/2.4 boundary (stat-block repair passes are 2.4) never owned score/debt bounds. Decide bounds + negative-debt semantics before Phase-3 counter arithmetic consumes them. [backend/app/pipeline/build_in.py `_validate_subgraph`]
- source_spec: `spec-2-3-core-first-two-wave-build-in-pipeline.md`
  summary: World-level re-build idempotency is absent — resubmitting the same build-in payload re-commits the same named entities as fresh ULIDs instead of growing the world (the spec's "DM resubmits to grow the world" resilience note).
  evidence: `run_build_in` generates `ids.new_id()` per entity with no name/identity matching against committed rows; a re-run duplicates "The Gilded Bar"/"Mira Vane" and can hit the (src,dst,type) uniqueness constraint on identical edges. Needs an identity/dedup strategy decision (2.7 or Epic 3). [backend/app/pipeline/build_in.py]
- source_spec: `spec-2-3-core-first-two-wave-build-in-pipeline.md`
  summary: Wave-2 anchors truncate at the AR6 retrieval cap — when wave-1 commits more than `entity_cap` (24) entities, the model can only reference core anchors C0..C23; a wave-2 edge to a committed wave-1 entity beyond the cap fails as an orphan with no diagnostic.
  evidence: `core_count = min(len(entities_1), len(context_entities))` ties the orphan rule to retrieval truncation; legal input (up to 100 key figures) can fail the build with a misleading message. Revisit cap/anchor-set in 2.7 or Epic 3. [backend/app/pipeline/build_in.py, pipeline/retrieval.py]

## Deferred from: code review of spec-2-4-key-figures-carry-a-minimal-5e-stat-block (2026-08-31)

- source_spec: `spec-2-4-key-figures-carry-a-minimal-5e-stat-block.md`
  summary: An invalid `identity.role` suppresses all level/cr violation reporting — the repair round only sees the role error, so a double violation (bad role + level 99) survives the single bounded pass and fails the job the model could have fixed in one round.
  evidence: knowledge.py:485-501 skips the whole level/cr branch when `canonical_role` is None; which semantics apply is genuinely ambiguous with the role unknown, so a "report all at once" fix needs a design call. Revisit when repair-prompt quality gets measured (dogfood/2.7). [backend/app/pipeline/knowledge.py]

## Deferred from: review of spec-2-5-no-orphans-and-cascade-delete (2026-09-02)

- source_spec: `spec-2-4-key-figures-carry-a-minimal-5e-stat-block.md` (found in the spec-2-5 review)
  summary: Repair prompt embeds only the flagged blocks' classes in the spell reference — a repair that legally switches `identity.class` to an unflagged class never sees that class's spell line, so an off-list spell fails final validation with no second repair pass.
  evidence: `spells_reference_text(_flagged_classes(issues))` subsets by the flagged classes only; rules text tells the model to use its class's line. Real only when a spell violation coexists with a class switch — revisit with 2.4's repair-prompt quality measurement. [backend/app/pipeline/statblocks.py]
- source_spec: `spec-2-4-key-figures-carry-a-minimal-5e-stat-block.md` (found in the spec-2-5 review)
  summary: `_check_spells` early-returns after the Monster-role error, suppressing duplicate/unknown-spell reporting in the same pass — the same violation-suppression shape as the deferred invalid-role → level/cr issue, different code path.
  evidence: role error appends and returns before the duplicate/unknown spell checks; a Monster block with bad role AND bad spells reports only the role error. [backend/app/pipeline/knowledge.py]
- source_spec: `spec-2-4-key-figures-carry-a-minimal-5e-stat-block.md` (found in the spec-2-5 review)
  summary: Cancel landing DURING the stat-repair call is specified (repaired core commits) but untested — only the before-repair cancel has a test.
  evidence: spec-2.4 frozen bullet pins the semantics; `test_cancel_before_stat_repair_is_noop` covers only the pre-call poll. [backend/tests/test_build_in_pipeline.py]
- source_spec: `spec-1-1-repo-scaffold.md` (env-parse helpers; epic-1 retro item 5 — found in the spec-2-5 review)
  summary: env-parse helpers carry inconsistent boundary and leniency semantics — `env_int` raises on value < minimum but accepts lenient `int()` forms ("1_0", "+5", " 5 "); `env_float` raises on value <= minimum (strictly >) and accepts nan/inf, which bypass the >minimum guard entirely.
  evidence: same-signature helpers with opposite boundary semantics; NaN comparison `value <= minimum` is False so a NaN timeout reaches httpx. Epic-1 retro item 5 consolidation shipped these; hardening is an ops-story concern. [backend/app/core/config.py, backend/app/core/settings.py]
- source_spec: `spec-2-4-key-figures-carry-a-minimal-5e-stat-block.md` (found in the spec-2-5 review)
  summary: A stat-repair ref with ≥4301 decimal digits raises a raw ValueError (CPython int-string limit) from `_parse_ref`, bypassing the `JobPayloadError` fail-event channel.
  evidence: `int(digits)` raises ValueError before the canonical re-raise; adversarial refs escape AR25's structured failure. [backend/app/pipeline/statblocks.py `_parse_ref`]
- source_spec: `spec-2-4-key-figures-carry-a-minimal-5e-stat-block.md` (found in the spec-2-5 review)
  summary: Empty/whitespace-only action and trait descriptions pass stat-block validation while names are guarded — guard parity gap.
  evidence: name checks require non-blank strings; the description check accepts `""`/`"   "`. [backend/app/pipeline/knowledge.py]
- source_spec: `spec-2-5-no-orphans-and-cascade-delete.md`
  summary: Cascade delete leaves media rows pointing at the deleted entity until Epic 4's reclamation — story 4-3 must handle media whose entity was deleted post-2.5.
  evidence: AD-10 reclamation is explicitly deferred by 2.5's frozen constraints; the delete path appends no media events. [backend/app/store/commit.py `delete_entity`]
- source_spec: `spec-2-4-key-figures-carry-a-minimal-5e-stat-block.md` (found in the spec-2-5 review)
  summary: Wave-1 prompt's "STAT BLOCKS" section embeds rules text that opens with its own "STAT BLOCK RULES" heading (redundant nesting), and `spells_reference_text` emits "(none listed)" lines with no statement that the phrase means "no spells permitted".
  evidence: prompt-quality nit only — validation is unaffected; revisit when prompts are next measured. [backend/app/pipeline/build_in.py, backend/app/pipeline/statblocks.py]

## Deferred from: code review of spec-2-5-no-orphans-and-cascade-delete.md (2026-09-03)

- source_spec: `spec-2-5-no-orphans-and-cascade-delete.md`
  summary: `base_revision` is accepted on the entity-DELETE wire but unusable until revision ids are exposed — 2.6 export / 2.3 world view own that surface.
  evidence: DELETE returns 204 with no body and the route discards the `models.Revision` the store returns. [backend/app/api/entities.py:52-65]
- source_spec: `spec-2-5-no-orphans-and-cascade-delete.md`
  summary: No delete-side concurrency coverage — delete vs commit race and interleaved `base_revision=None` semantics are unpinned (the commit race has `test_concurrent_commits_same_base_exactly_one_wins`; delete has no equivalent).
  evidence: store tests pin commit races only. [backend/app/store/commit.py `delete_entity`]
- source_spec: `spec-2-5-no-orphans-and-cascade-delete.md`
  summary: Delete during an in-flight build-in job: the next wave dies on `DanglingEdgeError` surfaced as a structured job-fail event; the I/O matrix has no delete-during-active-job row — Epic 3 candidate lifecycle revisits.
  evidence: frozen constraint "no pipeline changes" keeps this out of 2.5 scope. [spec-2-5 I/O matrix, backend/app/pipeline/build_in.py]
- source_spec: `spec-2-5-no-orphans-and-cascade-delete.md`
  summary: Affected-listing guarantees (multi-neighbor dedup, rowid ordering, id+name item shape) are exercised only via the `entity_live_edges` helper test, never asserted through the shipped 409 API error payload.
  evidence: all API tests seed a single-neighbor world. [backend/tests/test_entities_api.py]
- source_spec: `spec-2-5-no-orphans-and-cascade-delete.md`
  summary: Async route runs a synchronous session_scope-backed SQLite transaction on the event loop, blocking concurrent requests for the txn duration (incl. busy-timeout waits) — inherited from the campaigns DELETE pattern, codebase-wide.
  evidence: campaigns DELETE (AR20) uses the identical async-def + sync-store shape. [backend/app/api/entities.py:35-65, backend/app/api/campaigns.py:153-180]

## Deferred from: code review of spec-2-6-world-state-export.md (2026-09-03)
- source_spec: `spec-2-6-world-state-export.md`
  summary: frontend/src/api/schema.ts is stale — generated once in 2.1 (gen:api) and never regenerated for the routes added by 2.3/2.5/2.6 (incl. GET /api/campaigns/{id}/export); nothing verifies its freshness.
  evidence: schema.ts last touched in commit 0c166fe (2.1); delete op present at schema.ts:646 but no export operation. Regeneration naturally belongs to 2.7's world-view frontend work. [frontend/src/api/schema.ts]

## Deferred from: review of spec-2-7-first-visible-moment.md (2026-09-03)
- source_spec: `_bmad-output/implementation-artifacts/spec-2-7-first-visible-moment.md`
  summary: WorldView renders only `data['stat_block']` — any other committed `data` keys are invisible with no indication of omission.
  evidence: EntityExport.data is an open map; the markdown export renders full data but the world view shows only the stat block; extra keys land with Epic 3+ sectioned profiles (AR24 tolerates unknown keys) — surface a muted "additional data" section when a real consumer appears. [frontend/src/views/WorldView.vue]
- source_spec: `_bmad-output/implementation-artifacts/spec-2-7-first-visible-moment.md`
  summary: Generated schema.ts types the export route's 200 response as application/json WorldExport even for format=markdown, which actually returns text/markdown with attachment disposition.
  evidence: Backend OpenAPI on exports.py declares only the JSON response; the generated frontend copy cannot be hand-edited — fix the backend response declaration (a backend OpenAPI change) and regenerate; revisit if a frontend caller ever consumes format=markdown. [backend/app/api/exports.py, frontend/src/api/schema.ts]

## Deferred from: code review of spec-2-7-first-visible-moment.md (2026-09-03)

- source_spec: `_bmad-output/implementation-artifacts/spec-2-7-first-visible-moment.md`
  summary: The world view shows the raw 26-char revision ULID with no last-synced timestamp — the DM cannot tell whether the displayed snapshot is seconds or days old.
  evidence: `revision.id` is displayed verbatim (the backend exposes the full id deliberately for the optimistic-concurrency DELETE flow); shortening or a "synced at" stamp is a display-format decision not covered by the frozen spec. [frontend/src/views/WorldView.vue]
- source_spec: `_bmad-output/implementation-artifacts/spec-2-7-first-visible-moment.md`
  summary: The frontend pins `WAVE1_PROGRESS = 0.5` and its own test suite, but nothing pins the backend wire contract — if `build_in.py` ever reports intermediate progress ≥ 0.5, the "refetch once per wave" property silently degrades into a refetch per frame.
  evidence: `build_in.py:149,159` emit exactly 0.5/1.0 (frozen spec behavior); a cross-stack contract test requires a backend edit, which the spec's ask-first constraint forbids without owner approval. [frontend/src/stores/world.ts, backend/app/pipeline/build_in.py]

## Deferred from: 2.1 create-world gap-close verification (2026-09-03)

- source_spec: `_bmad-output/implementation-artifacts/spec-2-1-guided-build-in-flow.md`
  summary: Cold-visiting any public route (/register, /login) redirects to /login — the auth hydrate probe's 401 fires the global AR29 unauthorized handler, which pushes to login regardless of the current route; a new DM with a bookmarked register link can never reach it directly.
  evidence: Browser-instrumented navigation shows frame /register -> GET /api/auth/me 401 -> frame /login; main.ts registers the handler unconditionally. Fix shape: skip the login redirect when `router.currentRoute.value.meta` marks the route public (or when the 401 originates from `hydrate`). [frontend/src/main.ts, frontend/src/api/client.ts, frontend/src/router.ts]

## Deferred from: code review of spec-3-1-plain-language-ask-returns-2-3-candidates.md (2026-09-03)

- source_spec: `_bmad-output/implementation-artifacts/spec-3-1-plain-language-ask-returns-2-3-candidates.md`
  summary: Worlds beyond the 24-entity AR6 retrieval cap silently lose ask-relevant context — the generate runner seeds retrieval with the full committed world (seed_ids=None), so in a world larger than the cap the entity the ask targets may be absent from the prompt and candidates weave to arbitrary rowid-first anchors with no diagnostic.
  evidence: generate.py retrieves with seed_ids=None and the default entity cap of 24; build-in may legally commit up to 100 key figures, so worlds routinely exceed the cap. This narrows the spec-2-3 deferred item ("revisit cap/anchor-set in 2.7 or Epic 3") for the candidates path — the cap/anchor-set decision (surfacing truncation in the job result, ask-target seeding) still needs an owner-reviewed story. [backend/app/pipeline/generate.py, backend/app/pipeline/retrieval.py]

## Deferred from: code review of spec-3-1-plain-language-ask-returns-2-3-candidates.md — round 2 (2026-09-04)

- source_spec: `_bmad-output/implementation-artifacts/spec-3-1-plain-language-ask-returns-2-3-candidates.md`
  summary: A hard process crash between the staging commit and the cancel-conflict discard leaves a CANCELLED job's staged rows listable indefinitely — GET /api/campaigns/{id}/candidates has no job-state filter, so the accept screen would serve candidates from a cancelled job alongside legitimate ones. Story 3.2's reject/timeout lifecycle owns the garbage collection; the candidates read may additionally join on job state.
  evidence: the discard-on-conflict path (generate.py) runs only while the process survives; a crash after stage_candidates commits and after cancel_job commits but before report_progress raises skips it, and recover_stale_running ignores cancelled (terminal) rows. [backend/app/pipeline/generate.py, backend/app/store/candidates.py]
- source_spec: `_bmad-output/implementation-artifacts/spec-3-1-plain-language-ask-returns-2-3-candidates.md`
  summary: Failure-path tests (FEWER_THAN_TWO / BAD_EDGE / INVALID_STATS / BUDGET_EXCEEDED / malformed output) assert job state + zero staged rows but never the world graph/revision unchanged (AC4); the runner-level delete-mid-job composition (world mutated between the provider call and stage_candidates; InvalidCandidateError propagating) is untested. Assertion-strength hardening for a later sweep.
  evidence: only test_happy_path_stages_three_candidates asserts _revision_count/world_entities/world_edges; the staged-window backstop is exercised only at the store level with a fabricated stray ULID. [backend/tests/test_generate_pipeline.py]
