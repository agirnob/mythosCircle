# PRD ↔ Architecture Spine — Reconciliation Review

- **PRD:** `_bmad-output/planning-artifacts/prds/prd-mythosCircle-2026-08-23/prd.md` (status: final, locked 2026-08-23)
- **Spine:** `_bmad-output/planning-artifacts/architecture/architecture-mythosCircle-2026-08-23/ARCHITECTURE-SPINE.md` (status: draft)
- **Review:** 2026-08-23, read-only reconciliation
- **Method:** every PRD §3 capability (A–G), §4 NFR 1–12, §5 constraint with structural weight, and §7 phase-scoping statement was checked against the spine's ADs, consistency conventions, capability→architecture map, and Deferred table. Phase 2/3 capabilities are assessed at boundary level per the spine's declared scope; items already in the Deferred table with a revisit condition are not re-reported.

## Verdict: **FAIL** — four load-bearing requirements did not land

The spine's structure (event-sourced graph-of-record + single FIFO pipeline, one commit path, provider abstraction, single-machine deploy) lands the PRD's core architecture with high fidelity. However, four load-bearing PRD requirements — two explicit acceptance criteria and two explicit NFRs — are not bound by any AD, convention, or deferred entry. All are surgical one-line fixes, but left unaddressed a builder following the spine faithfully would ship beta missing them.

---

## §3 Capabilities — what landed

| PRD requirement | Spine binding | Status |
| --- | --- | --- |
| A. World = graph of nodes + typed edges | AD-1 | ✅ |
| A. Closed, directional edge vocabulary (relationship, debt, grudge, loyalty) | AD-5 (superset: + member_of, located_in, rival_of, kin_of, ally_of, enemy_of; extensible, never free text) | ✅ |
| A. Cascade delete with explicit confirmation; zero dangling edges | AD-5 | ✅ |
| A. Guided build-in + notes digested by LLM | AD-19 (job kind), AD-15 staging | ✅ |
| A. **Acceptance: "world state exportable as JSON + Markdown at any time"** | AD-11 lists Markdown / Owlbear / RPGToken / Fantasy Grounds only | ❌ **JSON full-state export missing — Finding F1** |
| B. 2–3 candidates per ask; stat block; **secret / rumor / party-hook triple**; **≥1 edge into existing world state** | AD-15 (staging), AD-2 (commit), AD-23 (stat block only); secret/rumor/hook and the existing-world-edge requirement appear in no AD | ❌ **Candidate shape contract dropped — Finding F2** |
| B. Regenerate — whole or per-field; DM edit beats LLM; accept commits atomically | AD-2 (atomic, rebase-or-reject, never silent overwrite); per-field scope not addressed | ⚠️ minor — Finding F5 |
| B. Candidate lifecycle proposed→accepted/rejected; reject never mutates state | AD-15, convention "generation failures never mutate state" | ✅ |
| C. Portrait per accepted entity; video fast-tier in beta; media reclaimed on delete; export validates references | AD-10 (owned by entities, reclaimed, validated on export), AD-6 (video adapter), AD-3 (video jobs in queue) | ✅ |
| D. Relationship graph (Phase 2, surface only) | AD-20 (contained view dep, no reach into store/API) | ✅ boundary-level, by design |
| E. Exports: Markdown, Owlbear, MapTool (RPGToken), Fantasy Grounds; Roll20 P2; Foundry deferred | AD-11 (exact Phase-1 format list); Roll20 is P2 → boundary | ✅ |
| E. Acceptance: format-validated artifact, stat block + portrait embedded, export-failure event | AD-11 (validation failures counted as export-failure events; export never writes) | ✅ |
| F. Session logger (P3): typed player actions, campaign time | AD-7 (typed events), AD-18 (campaign timescale) | ✅ boundary-level, by design |
| F. Transcription from session recordings | Deferred: "Session-recording transcription \| Phase 3" | ✅ deferred |
| G. Simulate History (P3): deterministic deltas, LLM narrates; P1 edge schema + P3 action-log schema must pre-exist | AD-7, AD-18, AD-23 (boundaries); rule engine Deferred "Phase 3 spec" | ✅ deferred, by design |
| G. Long-horizon simulation ("what would happen in x years") stays Later | Boundary-level (P3/Later) | ✅ by design |

## §4 NFRs — what landed

| NFR | Requirement | Spine binding | Status |
| --- | --- | --- | --- |
| 1 | Local RTX-3090 inference, zero per-token cost at MVP | AD-14 (llama-server, abliterated 14–20B), AD-8 | ✅ |
| 1 | OpenRouter migration later; metered, bounded multi-calls, per-campaign caps (values at migration) | Deferred: "OpenRouter endpoints + credit multipliers \| post-beta migration"; "bounded multi-calls" (per-job LLM-call bound) unbound | ⚠️ minor — Finding F6 |
| 2 | Zero dangling edges | AD-5 | ✅ |
| 2 | **Entity identity stable across regeneration** | No AD binds it; AD-2 governs commit/rebase but is silent on ID preservation when a regeneration is accepted over an existing entity | ❌ **Finding F3** |
| 2 | Retrieval-relevance proxy (top-k metric) | Computable under AD-16 (deterministic retrieval) | ✅ |
| 3 | Versioned state; atomic subgraph commit vs latest revision; per-transaction undo; visible rebase-or-reject | AD-2 (verbatim) | ✅ |
| 4 | Single FIFO queue, no concurrent inference, position visible, serial 3090 ceiling | AD-3 (persistent queue, no bypass path, REST+WebSocket position) | ✅ |
| 5 | Nightly local snapshot of state + media manifest; restore tested pre-beta; export ≠ backup | AD-12 (verbatim, incl. "Export is ownership, not backup") | ✅ |
| 6 | Invited identity, per-campaign ownership, TLS | AD-9, AD-21 (Caddy TLS, localhost bind) | ✅ |
| 7 | Abliterated local model (grimdark without refusal) | AD-14 (abliterated 14–20B) | ✅ |
| 7 | OpenRouter migration selects non-over-censored endpoints | Deferred: "OpenRouter endpoints \| post-beta migration" | ✅ deferred |
| 8 | Swappable inference endpoint, no hard-coded vendor | AD-6 (OpenAI-compatible adapters, config change not rewrite) | ✅ |
| 9 | Async generation/image/video with progress while DM preps next | AD-3 + AD-17 (REST submit, WebSocket progress/queue) | ✅ |
| 10 | **World state always fully exportable (JSON + Markdown) independent of VTT targets** | AD-11 omits JSON | ❌ **Finding F1** |
| 10 | **Deleting a campaign actually deletes it** | AD-5 = entity-level cascade; AD-10 = entity-scoped media reclaim; no AD defines whole-campaign deletion (graph + event log + queue + media + manifest) | ❌ **Finding F4** |
| 11 | Hundreds of entities / thousands of edges; retrieval over the graph, never whole world | AD-4, AD-16 (bounded traversal, depth/entity cap); Deferred: traversal performance | ✅ |
| 12 | Python/FastAPI, Vue; Cytoscape.js / Vue Flow candidates | Stack section, AD-20; Deferred: visualizer library | ✅ |

## §5 Business model — structural constraints

| Constraint | Spine treatment | Status |
| --- | --- | --- |
| Closed beta, invited DMs, owner's local server, free | AD-8, AD-9 | ✅ |
| Post-beta credit model (credit unit, multipliers, weekly free credits, per-usage pricing, no subscription) | Deferred: "OpenRouter endpoints + credit multipliers \| post-beta migration (PRD §5)"; AD-9 defers sharing/registry to post-beta | ✅ deferred by design |
| Video layering (fast/premium tiers) | Fast tier only in beta (PRD §7); adapter interface already bound (AD-6); Deferred "Image/video model + server choice" | ✅ deferred by design |
| Shared cards / gallery growth loop (post-beta, redaction gate) | AD-9: re-enters through commit path | ✅ boundary |
| Per-usage metering infrastructure | See Finding F6 (minor) | ⚠️ |

No business-model item forces a structural choice the spine gets wrong.

## Phase scoping

- **Phase 1:** fully bound (all findings above are Phase-1-scope gaps).
- **Phase 2** (graph visualizer, Roll20 export): governed at boundary level — AD-20; by design, not a gap.
- **Phase 3** (session logger, Simulate History): governed at boundary level — AD-7/18/23 + Deferred entries; by design, not a gap.
- **Later** (import connectors, Pathfinder+, long-horizon sim, party-shared campaigns): AD-19 pre-empts connector writers; Deferred covers scaling/OAuth/i18n. ✅

## Findings — what did NOT land

### F1 (major) — JSON full-world-state export dropped from the export contract
PRD §3 A acceptance: "world state is exportable as **JSON + Markdown** at any time"; NFR 10: "The world state is always fully exportable (**JSON + Markdown**) independent of VTT targets". AD-11's rule enumerates only `Markdown / Owlbear / RPGToken / Fantasy Grounds` (latest revision, VTT-oriented). The JSON export — the data-ownership guarantee that the world leaves the tool regardless of VTT targets — is in no AD, convention, or deferred entry. **Fix:** extend AD-11's format list with a full-state JSON export (all entities + typed edges + counters of the latest revision), noting it is independent of VTT targets.

### F2 (major) — Candidate shape contract (secret / rumor / party-hook + existing-world edge) not bound
PRD §3 B: each candidate ships "a stat block + **secret/rumor/hook** + **at least one edge into existing world state**"; the journey makes this load-bearing ("the barkeep's secret pays off in act three"). AD-23's hard-truth model covers only "goals, life span, stat block" for characters, and no AD requires a candidate to carry ≥1 edge into existing state before accept. A builder could model candidates without the triple, silently degrading the product's core promise. **Fix:** add secret/rumor/party-hook to AD-23's character hard truths (or AD-15's candidate contract) and state that acceptance validates ≥1 candidate edge into the existing world.

### F3 (major) — "Entity identity stable across regeneration" (NFR 2 invariant) unbound
PRD NFR 2 lists verifiable invariants: zero dangling edges ✅ (AD-5), **entity identity stable across regeneration** ❌, retrieval-relevance proxy ✅. AD-2 commits a candidate's subgraph against the latest revision but is silent on what happens when a regeneration of an *existing* entity is accepted: the entity ID (and therefore its inbound edges, media, and references) must survive the replacement, or NFR 2 breaks. **Fix:** one sentence in AD-2 — accepting a regeneration of an existing entity replaces its content in place, preserving the entity ID; edge re-targeting is forbidden.

### F4 (major) — Whole-campaign deletion unbound
PRD NFR 10: "**deleting a campaign actually deletes it**." AD-5 covers entity-level cascade; AD-10's media reclaim is scoped to "when their entity is deleted." No AD defines deleting a whole campaign — its revision tables, event log, queue entries, media manifest rows, and media files. As written, campaign delete could plausibly be implemented as soft-hide or left unimplemented. **Fix:** extend AD-5 (or AD-13) with a campaign-delete rule: hard delete of all campaign state through the store, including media reclaim, with explicit confirmation.

### F5 (minor) — Per-field regeneration scope not addressed
PRD §3 B: "Regenerate — **whole or per-field**." AD-15 models candidates as all-or-nothing subgraphs; scoped regeneration (e.g., name-only) is a generator-interface contract the spine leaves implicit. **Fix (optional):** note in AD-15/AD-16 that a generation job carries a scope (full entity vs. single field) and a field-scoped candidate commits the same AD-2 atomic path.

### F6 (minor) — "Bounded multi-calls" per job has no binding
PRD NFR 1: "Once on API, generation is metered — **bounded multi-calls**, per-campaign caps (values set at migration)." The Deferred row ("OpenRouter endpoints + credit multipliers | post-beta migration") covers endpoint selection and pricing values, but the per-job bound on LLM calls (cost guardrail for multi-step generation) is not in any AD or convention. **Fix (optional):** add to AD-3 or the conventions: each job declares a max LLM/image call budget from `config.toml`; exceeding it fails the job, never mutates state.

## Confirmed NOT re-reported (explicitly deferred with revisit conditions)

Image/video model + server choice · graph traversal performance · graph visualizer library · OpenRouter endpoints + credit multipliers · session-recording transcription · Simulate History rule engine · i18n (Turkish) · scaling beyond one machine / hosted tier · OAuth / third-party auth · Foundry export · Roll20 (Phase 2).

## Required fixes for PASS

F1, F2, F3, F4 (all one-line AD amendments). F5/F6 recommended, non-blocking.
