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
## Deferred from: code review of spec-3-1-plain-language-ask-returns-2-3-candidates.md — round 3 (2026-09-04)

- source_spec: `_bmad-output/implementation-artifacts/spec-3-1-plain-language-ask-returns-2-3-candidates.md`
  summary: Staged edge counters are never validated against `edge_counter_semantic` — a non-positive or semantically invalid counter (`debt: -5`, `ally_of: 0`) stages and would surface on the accept screen although it can never commit.
  evidence: `_valid_edge` checks endpoint/type/direction/int-ness only; the prompt embeds the semantics ("counter: <integer, default 1>") half-enforced at the stage boundary — the 2.3 counter-ranges deferral owns the bounds decision, extend the stage boundary when they land. [backend/app/pipeline/generate.py `_valid_edge`]
- source_spec: `_bmad-output/implementation-artifacts/spec-3-1-plain-language-ask-returns-2-3-candidates.md`
  summary: The candidates read has no job/status scoping — rows from every generate job (old batches, superseded retries, crash-window cancelled-job rows) interleave oldest-first, so story 3.3 cannot fetch "the latest batch" atomically.
  evidence: `list_candidates` filters by campaign + rowid only; the 3.3 accept screen needs a `job_id`/`status` filter or a newest-batch contract (this also subsumes the round-2 crash-window ghost-row deferral — GC by job state). [backend/app/store/candidates.py `list_candidates`]
- source_spec: `_bmad-output/implementation-artifacts/spec-3-2-candidate-lifecycle-and-atomic-commit.md`
  summary: Settled candidate rows carry no provenance to what they became — no accepted entity id or accept revision id is recorded, and the accept route discards the store's returned revision.
   evidence: Review finding (blind hunter, story 3-2 round 1): the audit-trail rationale ("only what I accept became real") cannot trace row -> entity/revision except by name matching, and story 3.3's accept screen gets no new-entity/revision reference from the 200 response. Store accept_candidate already returns (candidate, revision); the API route drops it. Fix belongs with 3.3's accept screen (return the new entity id + revision id, or persist them on the row).
  resolved: Addressed with the 3-3 closure (commit `9b4c4cc`, 2026-09-04): `accepted_entity_id` + `accept_revision_id` are persisted on the row in the accept transaction and returned on `CandidateResponse` via an idempotent additive migration. Verified end-to-end against a copy of the dev DB.

## Deferred from: review of spec-3-3-accept-screen-on-substance.md (2026-09-04)

- source_spec: `_bmad-output/implementation-artifacts/spec-3-3-accept-screen-on-substance.md`
  summary: The generate prompt advertises a `level_cr` format ("level <n>" for NPC/BBEG, "CR <n>" for Monster) but `_candidate_violations` enforces only non-blank — a wrong-format or role-mismatched `level_cr` stages and commits.
  evidence: Strict format validation would drop otherwise-fine candidates against a real LLM (false-drop risk vs contract purity); the prompt advertises the format, the mechanics block is independently AR25-validated, and `level_cr` is display-only today. Enforce or loosen the prompt wording when the AR24 record gets a real consumer (Epic 4 portrait prompts, Epic 5 exports). [backend/app/pipeline/generate.py `_candidate_violations`, OUTPUT CONTRACT]
- source_spec: `_bmad-output/implementation-artifacts/spec-3-3-accept-screen-on-substance.md`
  summary: WorldView duplicates the backend counter-type set as a local COUNTER_TYPES copy (same forward-compat gap CandidatesView had) — a new counter-typed edge type renders without its counter in world-view relation lines.
  evidence: WorldView.vue's copy predates 3.3 (2.7 surface); only CandidatesView's copy was fixed in the 3-3 review round. Align both with a shared counter-rendering helper or a generated contract when the vocabulary next changes. [frontend/src/views/WorldView.vue:116-126]
- source_spec: `_bmad-output/implementation-artifacts/spec-4-2-bbeg-fast-tier-short-video-beta-not-a-launch-priority.md`
  summary: Generated media is never pruned — every portrait and now every reveal video accumulates as a manifest row + file per entity with no retention cap, and video files are the expensive kind.
  evidence: `videoFor`/`portraitFor` pick the newest row and old rows/files simply stay (4-1 shipped the same for portraits; spec-4.2's Never list excludes reclaim-on-delete, which is story 4.3's scope). Unbounded `media_dir` growth on a long campaign needs a retention/pruning decision (keep-latest, keep-N, or DM-visible history) — land with 4.3's reclaim semantics.
- source_spec: `_bmad-output/implementation-artifacts/spec-4-3-reclaim-on-delete-and-export-media-validation.md`
  summary: Undo of an entity-CREATION revision (undo.py `_inverse_entity_deleted` for `entity_created`) deletes the entity row but leaves its media manifest rows and files behind — an orphaning path AD-10's reclaim rule doesn't cover.
  evidence: Pre-existing since 4-1 (undo shipped 1.2; the path predates this story and is unchanged by it). Not DM-reachable today — no HTTP route exposes undo — but it is a public store path (app.store.undo), so rows orphaned this way are invisible to the export's FR14 flagging while list_media still serves them. Needs the same rows-in-txn + post-commit file reclaim treatment the delete path got (spec-4.3), or an explicit decision that undo-of-creation keeps media. [backend/app/store/undo.py:212]
- source_spec: `_bmad-output/implementation-artifacts/spec-5-1-export-engine-pure-projection.md`
  summary: Binary/attachment responses are mis-typed in the generated wire contract — media get_file and the export markdown/html routes declare no explicit `responses=`, so schema.ts types their 200 as application/json.
  evidence: The 5.1 gen:api regeneration flipped get_file's content from 'application/octet-stream': string to 'application/json': unknown (blind-review finding) — the same root cause as the 2.7 entry about the export route. Fix = backend `responses={200: {"content": {...}}}` declarations + regenerate; generalizes the 2.7 deferral. [backend/app/api/media.py, backend/app/api/exports.py, frontend/src/api/schema.ts]
- source_spec: `_bmad-output/implementation-artifacts/spec-5-1-export-engine-pure-projection.md`
  summary: Sheet-hero vs UI-portrait divergence — the HTML sheet embeds the newest AVAILABLE image by rowid, the UI portraitFor picks newest created_at regardless of availability.
  evidence: Verification-gap + blind review layers both traced it; observable only once entities carry image history (unbounded accumulation is the 4-2/4-3 deferred item). The two rules converge under a keep-latest retention ruling — unify the selectors when 4-retro-item-13 lands. [backend/app/api/export_sheets.py _hero_portrait_src, frontend/src/stores/world.ts portraitFor]

## Deferred from: build-in record-gate dogfood live smoke (2026-09-09)

- source_spec: `_bmad-output/implementation-artifacts/spec-2-3-core-first-two-wave-build-in-pipeline.md`
  summary: Wave-2 structural violations (an orphan entity with no edge to the core) still hard-fail the whole wave — the live gemma run committed the wave-1 core, then dropped wave 2 over 'Kaelen the Mute' carrying no edges. Records and stat blocks now each get one bounded repair pass; structural violations have none.
  evidence: `run_build_in` wave 2 dies in `_validate_subgraph`'s orphan check (JobPayloadError), job fails with wave 1 committed (documented resilience). Candidate fixes: one bounded wave-2 re-prompt naming the orphans, or auto-dropping entities whose only defect is the missing edge — an AR25-scope decision for the owner. [backend/app/pipeline/build_in.py]

## Deferred from: build-in record-gate dogfood — round 2 (2026-09-09)

- source_spec: `_bmad-output/implementation-artifacts/spec-2-3-core-first-two-wave-build-in-pipeline.md`
  summary: FIXED in 1eceaa3+ — a nameless wave entity (gemma shipped a fully-detailed character with no name field at all) used to hard-fail the whole wave on 'entity N name must be a non-blank string'. `_validate_subgraph` now falls back to the character record's `data.name` when the entity-level name is absent (no repair burned), and a new bounded `_enforce_entity_names` gate (one title pass, `{"names": [{"ref": "E<position>", "name": "..."}]}`, name applied to the entity + record for characters) runs before the record gate in both waves. Remaining structural hard-fail (open): wave-2 ORPHAN — an entity with no edge to the core still fails the wave (owner decision pending, see above).
  evidence: Live repro 'wave 1: entity 3 name must be a non-blank string' (Tavern of Whostbos wave, 2026-09-09) — entity-level and record-level name both missing at once. Regression tests NAME_REPAIR/NAME_FALLBACK/NAME_REPAIR_STRICT/NAME_REPAIR_WAVE2; live smoke round 2 re-runs the same world against gemma. [backend/app/pipeline/build_in.py]

## Deferred from: build-in record-gate dogfood — round 3 (2026-09-09)

- source_spec: `_bmad-output/implementation-artifacts/spec-2-3-core-first-two-wave-build-in-pipeline.md`
  summary: FIXED — a malformed repair response (gemma shipped a record-repair JSON whose traits/actions members were bare strings: invalid JSON — "record repair: output is not valid JSON (Expecting ':' delimiter)") used to fail the whole wave. The three bounded repair gates (name, record, stat) now share `_run_repair`: one JSON parse, and on non-parseable output exactly ONE re-elicitation feeding the invalid text back with explicit JSON rules (bare-string members, escaped quotes, no prose, no stat_block in records); a second malformed response fails with a clear message; CONTRACT violations still fail immediately. `parse_json_object` (fencing) rescues prose-wrapped balanced JSON for the REPAIR-gate parses only (name/record/stat repairs + generate stat-repair). The top-level wave parse (`parse_build_output`) and the generate candidate parse stay strict fence-strip + `json.loads` by owner decision 2026-09-09 (fail loud on malformed waves; leniency lives inside the bounded repair gates). Generate's stat-repair path treats a malformed output as "repairs nothing" -> the candidate drops per the 2-3 contract (it never fails the job).
  evidence: Live user build_in failed 2026-09-09 job 01M232XP67RVJAZCVRM9RH4B9P. Regression tests: record/name/stat malformed-retry, record/name/stat retry-exhausted fail, prose-extraction, stat parse None-contract, repair-merge + regenerate mapping canonicalization, huge-ref contract failure; generate drop test re-pinned to the new contract. Review 2026-09-09 follow-up: per-gate retry notes, C<index> key enforcement, ref length guards. [backend/app/pipeline/build_in.py, backend/app/pipeline/fencing.py, backend/app/pipeline/statblocks.py, backend/app/store/candidates.py]

## Deferred from: review of spec-combat-power-enforcement (2026-09-09)

- source_spec: `_bmad-output/implementation-artifacts/spec-combat-power-enforcement.md`
  summary: A block with two Multiattack routines (e.g. melee + ranged) counts only the first; the second is silently dropped from DPR.
  evidence: audit_stat_block takes the first name-matched routine (`if multiattack is None`, backend/app/pipeline/combat.py); no failing case observed, single-routine handling covers the model output seen in dogfooding — revisit if multi-routine blocks appear.
- source_spec: `_bmad-output/implementation-artifacts/spec-combat-power-enforcement.md`
  summary: Regenerate re-rolls of stat_block run neither shape nor power validation, while the embedded rules text describes validator enforcement.
  evidence: regenerate._validate_output checks AR24 shape + byte-identical sections only (backend/app/pipeline/regenerate.py:394-416); wiring validate_stat_block into that path changes job outcomes (bad re-rolls would fail instead of stage) — an owner decision. The prompt wording is scoped to build-in/generate in the meantime.
## Owner verdicts 2026-09-09 (5-2 spec gates)

- level_cr (spec-3-3 deferral): LOOSEN — export derives level/CR from
  stat_block.identity numerics; top-level string stays display-only; prompt
- retention (epic-4 retro item 13): KEEP-5 — bounded per-entity history
  (current + 4 priors); export references newest available.
- wave-2 orphan (build-in dogfood): ONE bounded wave-2 re-prompt naming the
  orphans; second miss fails loudly. Mirrors record/stat one-pass semantics.
- regenerate power (combat review deferral): WIRE validate_stat_block into
  regenerate._validate_output — bad re-rolls fail instead of stage; matches
- portrait URL mechanism: SIGNED URLs — HMAC over path+expiry, origin-public
  assumption holds (dynamic IP fine via DNS); base_url still placeholder
  world.example.tld, set real hostname at 5-2 spec time.
- source_spec: `_bmad-output/implementation-artifacts/spec-5-2-owlbear-export.md`
  summary: Damaged HP state has no export representation — Z005/Z006 both mirror the single stored combat.hp.
  evidence: 5-2 review (blind hunter): no stored current-vs-max split exists in the commit path, so the Forge payload cannot carry in-combat damage; stat-model evolution, not export scope.
- source_spec: `_bmad-output/implementation-artifacts/spec-record-repair-chunking.md`
  summary: Consider chunking the stat-gate repair if giant-output JSON flakes ever appear there.
  evidence: Step-04 review noted stat/name gates share the single-shot large-response shape; record gate flaked at 14 records (dropped brace), stat blocks are smaller and have never flaked — spec Ask First gates the split on observed failure.

## Owner verdicts 2026-09-11 (edgeless wave-1)

- wave-1 orphan (ladder rung 10): DROP confirmed — the 2026-09-09 "ONE
  bounded wave-2 re-prompt" verdict's wave-1 extension (2026-09-11,
  same-mechanism re-emit) is reversed: edgeless wave-1 entities commit
  as-is with one straight call and no re-emit; the DM prunes. Rationale:
  rung 10 attempt 3 died over E8 unwired twice — wiring the model will
  not invent. The wave-2 half stands (anchor, signal, one re-emit, drop
  guard byte-identical). Implementation also pins edge `type` as a schema
  enum single-sourced from `store.EDGE_TYPES` and scopes the stat repair
  to per-issue EDIT SCOPE lines plus a strict repair-response schema
  (breaches log, never fail).
  evidence: `spec-build-in-edgeless-repair-scope.md`; supersede-note in
  `spec-wave2-orphan-reprompt.md` Spec Change Log 2026-09-11.
  [backend/app/pipeline/build_in.py, backend/app/pipeline/statblocks.py,
  backend/app/store/commit.py `allow_orphans`]

## Deferred from: build-in stress ladder vs 26B, step 1 (2026-09-10)

- source_spec: `_bmad-output/implementation-artifacts/spec-2-3-core-first-two-wave-build-in-pipeline.md`
  summary: Wave-level malformed JSON fails the job with no retry — the gates (name/record/stat) and the wave-2 orphan re-emit each get one bounded second chance, but a wave-1 control-char/truncation parse failure (parse_build_output JobPayloadError) is terminal. Stress step 1 (10 chars/10 places/6 factions, scratch stack, gemma-4-26B): attempt 1 died this way (control char at ~char 9705). Candidate fix mirrors _run_repair: one bounded wave re-emit quoting the JSON error.
  evidence: 0/3 identical-payload attempts at 10/10/6 committed anything; scratch api on :8001, jobs 01M269E7J051Q9C3N2Z1Z5ED2G (malformed), 01M269M3Q0ASAFHEPWP85KSH9F (DPR under), 01M269VGR6CRQTJKKV05KWA3CH (DPR over). [backend/app/pipeline/build_in.py parse_build_output, run_build_in wave-1 call]
- source_spec: `_bmad-output/implementation-artifacts/spec-2-3-core-first-two-wave-build-in-pipeline.md`
  summary: The single stat-repair pass cannot steer DPR into band from either direction — attempt 2 under-powered (DPR 16 vs 27-32 at level 4) and attempt 3 over-powered (DPR 21 vs 9-14 at level 1) both survived the DPR-recipe repair unchanged. The model picks levels arbitrarily and the one-shot repair does not converge. Needs an owner decision: steer harder (exact target number in repair prompt, pinned level set), allow a second repair pass, or widen bands — 25/50-scale rungs are pointless until this converges at 10-scale.
  evidence: Same 0/3 stress run as above; DPR recipes + record targets shipped 54e3a8b did not save either attempt. Code-side scaling is clean (validation 1ms at 120 entities; prompts 4-5k tokens at all scales), so the bottleneck is purely model-output calibration. [backend/app/pipeline/build_in.py _enforce_stat_blocks, backend/app/pipeline/statblocks.py]
  resolved: Owner decision 2026-09-11 — **second bounded repair pass**, with the repair prompt carrying exactly what still needs repairing. `_enforce_stat_blocks` runs pass 1, then pass 2 whose prompt re-reads the block pass 1 wrote plus the violations that survived it (`build_stat_repair_prompt(issues, attempt=2)`); the deterministic `conform_stat_power` valve and the fail-loud message follow pass 2. Two passes is the ceiling (never a loop); the fail message now says "still invalid after the repair passes". Spec-2-4's `## Spec Change Log` (2026-09-11) renegotiates the frozen "exactly one repair pass" rule. [backend/app/pipeline/statblocks.py build_stat_repair_prompt, backend/app/pipeline/build_in.py _enforce_stat_blocks]

## Deferred from: second stat-repair pass — generate scope boundary (2026-09-11)

- source_spec: `_bmad-output/implementation-artifacts/spec-2-4-key-figures-carry-a-minimal-5e-stat-block.md`
  summary: The second stat-repair pass landed in the shared build-in/regenerate gate ONLY; `generate.py`'s inline candidate repair still makes one attempt and then DROPS the candidate. Its bad outcome is a missing candidate in a 2-3 list, not a lost build, and a second pass there costs one more call per bad candidate on the local model — so it was left alone deliberately. Extend it (same `attempt=` prompt + one more pass) if candidate drops become the visible complaint.
  evidence: `generate.py:191-205` (repair → drop with "stat block(s) still invalid after the repair pass: …"); the second pass exists only in `build_in._enforce_stat_blocks`, which generate does not call. [backend/app/pipeline/generate.py]

## Deferred from: wave-1 orphan hard-fail, deployed beta (2026-09-11)

- source_spec: `_bmad-output/implementation-artifacts/spec-wave2-orphan-reprompt.md`
  summary: FIXED — a wave-1 subgraph whose only defect is orphan entities used to fail the whole job with ZERO commits (wave 1 is the first wave, so unlike the wave-2 case there was no committed core to keep). Live report from a beta user: 10 entities, `'Myconid Colony' (E7), 'Grymforge' (E8), 'Flaming Fist' (E9)` unwired, job `01M27S237NNWFMZ2SHA7RG9383`. Wave 1 now gets the same ONE bounded orphan re-emit the owner approved for wave 2 (2026-09-09): `_OrphanRetryError` carries the wave, `_build_orphan_retry_prompt` names orphans with that wave's ref prefix, and the shared `_orphan_reemit` helper owns call/parse/cancel; second miss still fails loudly. Reproduced live against gemma-4-26B with the same entity names (the model emits a place/faction it never wires when a long flat list is submitted) — smoke run healed and committed 8 entities/9 edges.
  evidence: Spec Change Log entry 2026-09-11 in `spec-wave2-orphan-reprompt.md` (the frozen `Never: touch wave-1 orphan handling` line is superseded; owner veto = delete the wave-1 `except _OrphanRetryError` block). Pins: `test_orphan_wave1_raises_the_retry_signal`, `test_wave1_orphan_retry_heals_and_commits`, `test_orphan_wave1_second_miss_fails_naming_entity`, `test_wave1_orphan_retry_reemit_dropping_orphan_fails`, `test_wave1_orphan_retry_budget_exhausted`. [backend/app/pipeline/build_in.py run_build_in, _validate_subgraph]
- source_spec: `_bmad-output/implementation-artifacts/spec-wave2-orphan-reprompt.md`
  summary: Wave-1 orphan re-emit inherits the strict drop guard: a re-emit that DROPS the orphan instead of wiring it fails the job (nothing committed) rather than committing the wave without an entity the model itself proposed. That is the right default for entities the DM named in the seed, but wave-1 orphans are often model-volunteered extras (the live case: three Forgotten-Realms proper nouns), where dropping would be the cheaper outcome. Alternative if it bites: allow the re-emit to omit exactly the named orphans (and nothing else) for wave 1 only — an owner decision, not implemented.
  evidence: Same smoke run as above; guard is `_raise_on_reemit_entity_change` (wave-aware message). Follow-up triggered only by a live drop failure. [backend/app/pipeline/build_in.py]
- source_spec: `_bmad-output/implementation-artifacts/spec-2-3-core-first-two-wave-build-in-pipeline.md`
  summary: NOT CODE — the deployed beta is running a frontend from before a501c58, so the DM's seed text is still wiped on enqueue there (two users reported it 2026-09-11). The local tree is fixed and verified; the fix is a redeploy, not a patch. Deployed asset check: `BuildInView-dn8UCwOm.js` serves `Enqueuing…` and no `Building…`, i.e. pre-fix.
  evidence: `curl -s https://world.miscco.uk/assets/index-B2T6UGsp.js | grep -o 'BuildInView-[A-Za-z0-9_-]*\.js'` → `BuildInView-dn8UCwOm.js`; that chunk contains `Enqueuing…`, not the post-fix `Building…`/in-flight hold. [frontend/src/views/BuildInView.vue, deploy/docker/Dockerfile.web]

## Deferred from: structured stat aspects live smoke vs 26B (2026-09-11)

- source_spec: `_bmad-output/implementation-artifacts/spec-structured-attack-and-stats-fields.md`
  summary: The new OPTIONAL aspects are written by the model only sometimes. Live 4-run smoke (gemma-4-26B, level-17 paladin seed, scratch DB): the committed block carried structured attack damage (`Oathblade`, `to_hit: 19`, parts `2d6+13` / `3d10`) — the prompt change works — but `saves`, `combat.hit_dice`, `spellcasting`, `features` and `resources` were absent in the run that committed. They are optional by design (absence is legal), so this is a data-richness question, not a bug: if the DM wants them filled consistently, the next step is leading with them in the prompt (or a bounded "fill the missing aspects" pass on demand) rather than more validation.
  evidence: 4 live build-in runs against the local Unsloth 26B, scratch DB `/tmp/mythos-structured-smoke/scratch.db`; run 3 committed (3 LLM calls) with `stat_block keys: [actions, attributes, combat, identity, skills, spells, traits]` and the structured action above. [backend/app/pipeline/statblocks.py stat_block_rules_text, knowledge.validate_stat_block]
- source_spec: `_bmad-output/implementation-artifacts/spec-2-4-key-figures-carry-a-minimal-5e-stat-block.md`
  summary: An OVER-powered block still cannot be repaired by anything: the deterministic conform deliberately refuses to trim (it only lifts), so the two repair passes are the only lever and the model does not converge downward — the same live smoke failed 3 of 4 runs at `over-powered for level 20: estimated DPR 234/278/176 vs 123-140 expected`, on a key figure the DM declared level 17 (the model raised the declaration itself). Also visible: `spells require identity.class` / `spells must be a list of spell names` shape failures on the same character. Owner decision needed: let the conform TRIM to the band edge, allow the repair prompt to lower the declared level, or leave it (the DM retries).
  evidence: live runs 1, 2 and 4 above (each 3-5 LLM calls, zero commits); the conform's refusal is documented in `statblocks.conform_power` ("damage ABOVE the band ... deliberately out of scope"). [backend/app/pipeline/statblocks.py conform_power, backend/app/pipeline/build_in.py _enforce_stat_blocks]
  resolved: Owner verdict 2026-09-12 — **over-powered is fine and commits**: no trim, no fail. `knowledge.audit_power` stamps a deterministic `power: {dpr, band, verdict}` on the block in `canonicalize_stat_block` (over-powered verdict only; on-target stays byte-identical), `StatBlock.vue` renders the flag line for the DM, and the validator's over-powered branch is deleted (repairs never chase it). Ladder attempts 9-17 measured three overshoot deaths (60, 31.5, 189) that this verdict retires as a class.

## Deferred from: review of spec-json-schema-generation (2026-09-11)

- source_spec: `_bmad-output/implementation-artifacts/spec-json-schema-generation.md`
  summary: No fallback when a backend rejects `response_format` — a 400 on the schema'd wave body fails the job loudly via ProviderError("http") with no strip-and-retry or capability negotiation (the `enable_thinking=None` omission pattern has no schema equivalent).
  evidence: Blind-hunter + edge-case layers (step-04): all shippable backends (Unsloth, llama.cpp, OpenRouter) accept the field and failure is loud-never-silent, so this is a hardening story, not a blocker. [backend/app/providers/llm.py chat_completion]
- source_spec: `_bmad-output/implementation-artifacts/spec-json-schema-generation.md`
  summary: `pipeline/mechanics.py` has no input guards (negative challenge, unknown class key, empty tags, non-six ranked, non-int seed, inverted bands) and only Paladin tables — hardening plus multi-class tables land with the first caller (words-only path), which also decides fallback-vs-raise signaling.
  evidence: Blind-hunter + edge-case layers (step-04): the module is a callerless verbatim prototype port today, so guards would deviate from the parity mandate with no reachable trigger; pins stay exact. [backend/app/pipeline/mechanics.py]
