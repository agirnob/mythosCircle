# Review — Adversarial Lens

**Spine:** `ARCHITECTURE-SPINE.md` (mythosCircle, 2026-08-23, 25 ADs + conventions + 3 diagrams)
**Method:** for each builder pair one level down, I constructed two systems in which each unit obeys every AD to the letter, then checked whether the two systems interoperate. Only divergences on a *shared Phase-1 contract* count as real holes; details a single unit owns once code exists (exact column types, file names, internal table shapes) are seed-level and do not count.
**Verdict: FAIL** — six real holes (F1–F6). None is exotic; all are one-line AD tightenings, but each would force a mid-flight renegotiation between two builders who have done nothing wrong.

**Prior items confirmed fixed — NOT re-reported:** rubric-walker's FAIL items are resolved in the current text: AD-17 now pins the WS frame shape (`{type: job_progress|job_done|job_failed|queue_changed, job_id, state, queue_position?, progress?}`) and AD-9 now pins auth transport (httpOnly session cookie, Secure, SameSite=Lax, path `/api`).

---

## Pair-by-pair analysis

### Pair 1 — Backend store builder ↔ Vue frontend builder

**Fixed by the spine:** ULID IDs (log prefixes only, AD-17/conventions), UTC ISO-8601 timestamps, error envelope `{code, message, details?}` + 4xx/5xx semantics, WS frame shape, cursor pagination, naming conventions, the graph-of-record shape (entities + typed edges, AD-1), closed edge vocabulary + per-type counters (AD-5/AD-23), hard-truth content lists (AD-23), candidate content list (AD-24), campaign-time granularity set (AD-18), single FIFO + position visibility (AD-3), media path layout (AD-10), Vue/Pinia view-only boundary (AD-20).

**Left to each side to invent:** *every REST payload shape* — entity JSON, edge JSON, candidate/proposal JSON, job-submission payload, campaign fields, media-reference fields. AD-17's stated scope is channel/ID/error/progress contracts; its "Prevents" line claims to stop drifting "shapes between backend and Vue builders," but only those shapes are pinned. The semantic content of the payloads is pinned (AD-23/24/18) but field names, nesting, casing, and types are not.

**Constructed divergence (both AD-compliant):** the backend builder reads AD-23 and emits `{goals: [], life_span: "", stat_block: {...}}` (Pydantic snake_case, FastAPI default). The frontend builder reads AD-23 + the conventions line "camelCase TS/Vue" and models `{goals: [], lifespan: "", statBlock: {...}}` — the conventions line is ambiguous about *wire* casing vs. *identifier* casing, so both readings of the spine are defensible. Same for edges (`counter` vs `count` vs `intensity`), job submission (`prompt` vs `ask`, `target_entity_id` vs `entity_id`, `scope: full|field` vs `scope: entity|single_field` — AD-15's "full entity or a single field" gives no enum), and campaign time fields (granularity + calendar label + current value, all unnamed). The AD-16 prompt's "full structured record of each reached entity" and the AD-15 candidate row inherit the same drift: "full structured record" means *whatever the store's entity shape happens to be*, and there is no single artifact both sides must agree on.

**Verdict: REAL HOLE → F1.**

### Pair 2 — Pipeline/generator builder ↔ store/commit builder

**Candidate row shape:** AD-15 pins "DB rows, flag `proposed`" and the job's scope; the row's columns are store-owned (AD-13: all access through `store/`), so the row *schema* is single-owned — seed-level. But the *ingestion contract* (the JSON the pipeline hands the store) is the same shared-surface hole as F1: the pipeline's candidate JSON and the store's commit schema must match on field names, and nothing pins them.

**Retrieval neighborhood field set (AD-16):** "full structured record of each reached entity (attributes, counters, goals, economy) plus its generated text." The *record set* is pinned (entities reached by the bounded traversal, depth/entity caps, against latest revision) and determinism is pinned (same state + request ⇒ same prompt). The *record shape* is not — it is the store's entity shape (F1). If the store builder and the prompt builder invent "full structured record" differently, the prompt content silently differs across rebuilds. No independent hole: a downstream instance of F1.

**Event log schema:** AD-7 pins the session-action event fields `(action_type, actors, targets, campaign_time [in the campaign's AD-18 unit], note)` and conventions/AD-17 pin `event` ULIDs + timestamps. The field *set* is therefore fixed. What is not fixed: the `action_type` vocabulary (open — the spine closes the *edge* vocabulary in AD-5 but leaves event types open) and the *event→revision* relationship (see Pair 6). → F3, F5.

**Verdict: F1 (payload ingestion) + F3 + F5; no additional hole.**

### Pair 3 — Pipeline builder ↔ media builder

**Who writes the media-manifest row:** AD-13 forces it: the manifest lives in the single SQLite store and all DB access goes through `store/` — so the manifest row is store-shaped and store-written. The media service is the sole writer of *files* (AD-10). Single owner per artifact: no divergence on the row itself.

**Portrait file naming:** `media/{campaign_id}/{entity_id}/` is pinned (AD-10); the file name inside is media-service-owned and recorded verbatim in the store's manifest path field — seed-level.

**Reference-validation schema on export:** the ERD pins `ENTITY ||--o{ MEDIA : has`, so the export validator (api/) resolves entity → manifest rows → file existence. The manifest row fields are store-owned (seed-level). No divergence.

**However — the job execution path is a structural hole.** A portrait is a Phase-1 acceptance criterion (PRD §3 C: every accepted entity ships a portrait). AD-3 puts image/video jobs in the single FIFO queue; the seed declares `pipeline/` "job runner, generators (character/faction/place/simulate)" — *no image generator listed*; the capability map assigns "C. Media generation" to **both** `media/` + `pipeline/`; AD-10 makes the media service the only unit allowed to write the file. The dependency diagram gives `PIPE --> PROV` and `MEDIA --> PROV` but **no `PIPE --> MEDIA` edge** (and no `MEDIA --> STORE` edge for the manifest write that AD-13 forces). Under the diagram's stated rule ("api orchestrates; pipeline proposes; store commits; providers are leaves"), the job runner has no compliant route to the media service, and the media service has no drawn route to the store. Constructed divergence (both AD-compliant): the pipeline builder implements image jobs end-to-end and hands bytes to the API, which delegates to the media service — the media builder, meanwhile, implements a media service that consumes *its own* trigger and never sees the pipeline's output; or the media builder implements a queue consumer for image jobs and the pipeline builder's image generator is dead code. Either system passes its own ADs; together they don't connect.

**Verdict: manifest/naming/validation = not holes (single-owned). Job path = REAL HOLE → F2.**

### Pair 4 — Export builder ↔ store builder (AD-11 vs AD-19)

AD-11 pins the export *content contract*: latest revision, all entities + typed edges + counters, always exportable as JSON + Markdown, export never writes, validation failures counted. The *field names* of the export JSON are not pinned — but they don't need to be, because in Phase 1 the export JSON has exactly one producer unit (the export builder in `api/`) and no second Phase-1 unit consumes it: the DM downloads it (NFR 10), the backup path snapshots the *DB*, not the export (AD-12), and AD-19's import connectors are a **Later** item whose rule ("produce proposed subgraphs through the same pipeline") is about parsing *external* formats (Obsidian/World Anvil/Kanka per PRD §7) — the spine never binds them to the export JSON.

**Verdict: NOT a real Phase-1 hole** — the export JSON shape is single-owned (code owns it once it exists). Boundary note for the deferred table: if round-trip import (export → edit → import) is ever intended, AD-11's JSON must be pinned and versioned before that connector is built; until then it is one unit's serialization.

### Pair 5 — Campaign time (AD-18) vs session-action event shape (AD-7)

Fixed: campaign declares granularity (day/month/year/century) + calendar label (AD-18); event `campaign_time` is "in the campaign's AD-18 unit" (AD-7 — the earlier "campaign-day" naming clash is already fixed); no wall-clock enters simulation input.

**Constructed divergence (both AD-compliant):** AD-18 says "session events advance campaign time" — but the session logger is Phase 3 (PRD §3 F), so Phase 1 has no session-action endpoint. Yet campaigns *exist* in Phase 1 with a time value, and the DM must be able to advance time for campaign time to mean anything. The API builder invents the mechanism: a session event with `action_type: "advance_time"` (or `"time_advance"`, or a direct campaign PATCH). The store builder implements "session events advance campaign time" by matching *its* chosen string. AD-7's `action_type` vocabulary is open, so neither unit violates any AD, and the two systems silently disagree on the one string that matters. Second divergence: `actors`/`targets` reference shape is unspecified — entity ULID references or free text — which the P3 rule engine (deferred, but the PRD §3 G dependency says the Phase-1 action-log schema must exist before P3 rules are authored) will need to interpret.

**Verdict: REAL HOLE (small but concrete) → F5.**

### Pair 6 — Undo (AD-2) vs the event log

AD-2: "Undo reverts one revision only." Paradigm: "append-only event log with a materialized latest revision." AD-1: the commit path "appends events and produces exactly one new revision" — so undo, being a state change, must itself be a commit (compensating events → new revision); pointer-rollback would violate AD-1's "appends events." That much is derivable. What is **not** pinned: the event→revision relationship. The ERD links `EVENT` to `CAMPAIGN` only — there is no `REVISION ||--o{ EVENT` edge and no AD states that an event is attributable to the revision it produced. "Revert one revision" is only checkable if the store can address *the last revision's event set* (its subgraph). Constructed divergence (both AD-compliant): the store builder stores events with an implicit `revision_id` column; the API builder's undo endpoint groups events by `commit_id` (a separate notion) or by timestamp range — each system satisfies "one transaction, one revision, undo reverts one revision," and the undo semantics (which exact changes get compensated, how a counter increment from a session event is reversed) differ between them. The rebase-or-reject rule (AD-2) compounds this: rebasing a candidate "against the new revision" requires the store to know the candidate's target revision — again an event/revision linkage the spine never fixes.

**Verdict: REAL HOLE → F3.**

### Pair 7 — Stat block (AD-23/AD-24) vs 5e SRD

AD-11 pins the dialect: "Stat blocks follow the SRD 5.1 field set (level for NPCs, CR for monsters)" — an external, public, stable field set, so the pipeline builder (LLM generation) and the export builder (rendering + validation in AD-11) resolve to the *same* fields. The JSON representation of that field set is F1's payload-contract territory (single owner once F1 is fixed), not a separate hole. Residuals are seed-level: the entity taxonomy (character/faction/place) carries no explicit NPC/monster discriminator, but SRD 5.1's block contains both the `level` and `CR` slots, so the generator fills the relevant one — no second unit is forced to disagree.

**Verdict: NOT a hole** — field set fixed by AD-11; JSON shape rides on F1.

### Pair 8 — Guided build-in job (AD-19) vs generation job (AD-15/AD-24)

Both produce "proposed subgraphs" through AD-15's staging ("DB rows, flag `proposed`") and land through the single commit path (AD-1/AD-2). Are they the *same* proposal JSON? The spine does not fix it, and the two units genuinely diverge if it is left open:

- **Generation candidates** (AD-24): 2–3 per ask; *every* candidate carries name, role, personality, the secret/rumor/party-hook triple, a 5e stat block, and ≥1 typed edge into *existing* world state; acceptance validates all of it before commit.
- **Build-in proposals** (AD-19, cross-referencing **AD-15 only, not AD-24**): a single subgraph digesting notes — factions, cities, places, figures — created in an **empty world**. Factions and places have no stat block (AD-23 gives them economy/goals, not a stat block); there are no secrets/rumors/party-hooks for a city; and "≥1 edge into existing world state" is unsatisfiable on an empty world.

Constructed divergence (both AD-compliant): the acceptance/commit unit implements one validator over all staged proposals and applies AD-24 ("every candidate carries…") → the build-in proposal fails validation and the Phase-1 core onboarding flow (PRD §3 A, "guided world-building flow") can never be accepted. The build-in builder, obeying AD-19/AD-15, happily emits subgraphs without triples or stat blocks. The fix requires a **proposal-kind discriminator** (`build_in` vs `entity` vs `field-scoped`) that no AD pins, plus per-kind validation scoping. Note the internal tension this exposes: AD-24's "every candidate carries a 5e stat block and the triple" literally contradicts AD-15's field-scoped candidates (a "regenerate the name only" candidate does not carry the triple or a stat block) — so even among generation candidates the validator's scope is ambiguous.

**Verdict: REAL HOLE → F4.**

---

## Findings (ranked — real holes only)

**F1 — No single owner for REST payload shapes (entity, edge, candidate, job submission, campaign-time fields).**
Units: backend store/API builder (Pydantic) ↔ Vue builder (TS/Pinia); downstream: pipeline↔store ingestion and the AD-16 "full structured record." AD-17 pins IDs, errors, WS frames, timestamps — but not payload field names, nesting, casing, or types, and the conventions line ("camelCase TS/Vue") is ambiguous about wire vs. identifier casing. Two fully AD-compliant systems disagree on `life_span` vs `lifespan`, the edge counter field, job-submission fields, and the campaign-time fields. **Fix: tighten AD-17** — one clause: the API's JSON wire shape is defined once in the backend (Pydantic models) as snake_case; the frontend's TS types are derived from the OpenAPI contract, never re-invented; the entity/edge/candidate/job/campaign field set is that single contract. (New AD not required — this is AD-17's stated territory, currently under-delivered.)

**F2 — The image/video job execution path has no compliant route; capability map assigns it to two units.**
Units: pipeline builder ("job runner") ↔ media builder (sole file writer, AD-10). AD-3 puts image/video in the single queue; the capability map says media generation lives in `media/` + `pipeline/`; the dependency diagram gives `PIPE → PROV` and `MEDIA → PROV` but no `PIPE → MEDIA` (and no `MEDIA → STORE` for the manifest write AD-13 forces). Each builder's AD-compliant implementation (pipeline runs image jobs and can't reach the media service / media service runs its own jobs) produces a system where portrait generation — a Phase-1 acceptance criterion (PRD §3 C) — doesn't connect. **Fix: tighten AD-3** (or AD-10) with one clause assigning the runner per job kind — e.g., the pipeline worker runs text/simulate; the media service runs image/video — both consuming the single store queue with the store as the one-at-a-time arbiter — and add the missing `MEDIA --> STORE` (and any `PIPE --> MEDIA`) edge to the dependency diagram.

**F3 — Event→revision relationship not fixed; undo expression is under-specified.**
Units: store builder (revisions + event log) ↔ API builder (undo endpoint). The ERD links `EVENT` only to `CAMPAIGN`; no AD states an event is attributable to its revision. "Undo reverts one revision only" (AD-2) is unaddressable without it, and the rebase rule ("rebase against the new revision") needs the same linkage. Two AD-compliant stores attribute events differently (implicit `revision_id` vs commit grouping vs timestamp ranges) and compensate different change sets. **Fix: tighten AD-2** — every event carries the revision it produced; undo is a commit that appends compensating events for exactly the last revision's changes, producing a new revision (no pointer rollback, no multi-revision ranges); add `REVISION ||--o{ EVENT` to the ERD.

**F4 — Proposal-kind discriminator missing: build-in subgraph vs generation candidate vs field-scoped candidate.**
Units: build-in job builder (pipeline) ↔ acceptance/commit unit (store, AD-2/AD-24) ↔ frontend build-in view. AD-19 references AD-15 only, so build-in proposals have no pinned shape; AD-24's "every candidate carries [triple, stat block, ≥1 existing-world edge]" is unsatisfiable by build-in subgraphs (empty world; factions/places have no stat block) and contradicts AD-15's field-scoped candidates. A single validator over all staged proposals either bricks Phase-1 onboarding or needs a `kind` field no AD defines. **Fix: tighten AD-15 + AD-24** — proposals carry a kind discriminator (`build_in | entity | field`); AD-24's shape applies to `entity` (and, for `field`, to the scoped field only); `build_in` = subgraph of entities with AD-23 hard truths, no triple/edge-into-existing-world requirement.

**F5 — Session-action event vocabulary open; Phase-1 time-advance path absent; actor/target reference shape unspecified.**
Units: API builder (session/campaign-time endpoints) ↔ store builder (event handling). AD-7 fixes the five fields but not the `action_type` vocabulary (contrast: AD-5 closes the edge vocabulary), while AD-18 says "session events advance campaign time" with the session logger deferred to P3 — so the one string that advances campaign time in Phase 1 is invented independently by each builder. `actors`/`targets` as entity-ULID references vs free text is also unspecified, and PRD §3 G requires the Phase-1 action-log schema to exist before P3 rules are authored. **Fix: tighten AD-7** — closed Phase-1 `action_type` set (at minimum `advance_time`), `actors`/`targets` are lists of entity ULIDs, and the Phase-1 time-advance path (DM-triggered `advance_time` event through the commit path) is named.

**F6 — Entity-type enum mismatch between the spine and the PRD it binds.**
Units: store builder (`entity.type` values) ↔ frontend builder (type filters/labels). AD-23 names "faction/**city**/**country**"; the PRD §9 glossary (which the spine binds) defines the node set as "character, faction, or **place**." Each builder reading its own document produces a different `type` enum. **Fix: tighten AD-23** — align to the PRD taxonomy `{character, faction, place}` (place subsumes city/country for the economy/hard-truths rule).

## Adjudicated — NOT real holes

- **Pair 4 (export JSON vs import connectors):** single producer unit in Phase 1; AD-19's connectors parse external formats and are not bound to the export JSON. Seed-level + boundary note (pin/version the export JSON only if round-trip import is ever deferred-opened).
- **Pair 7 (stat block):** dialect fixed by AD-11 (SRD 5.1, level/CR rule); JSON representation rides on F1.
- **Pair 3 (manifest row / file naming / validation):** store-owned row (AD-13), media-owned file names (seed-level), ERD pins the entity→media reference. Only the job path is a hole (F2).
- **ULID format, error envelope, WS frame, campaign-time granularity, edge vocabulary + counters, one-writer rule, media path layout:** pinned (AD-17, conventions, AD-5/23, AD-18, AD-1/13, AD-10).

## Verdict rationale

FAIL. The spine's channel/identity/error contracts are solid (the prior gate's two failures are fixed), but the *payload* contracts between units are not: five structural holes (F1, F2, F3, F4, F5) each admit two letter-perfect AD-compliant implementations that don't interoperate, plus one binding inconsistency (F6). All fixes are one-clause tightenings of existing ADs (AD-17, AD-3/10, AD-2, AD-15/24, AD-7, AD-23) plus two ERD/diagram edges. Recommend: apply F1–F6, re-run this lens on the affected pairs, then PASS.
