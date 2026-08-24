# Adversarial Review — mythosCircle PRD

Reviewer: adversarial (edge-case / contradiction / verification-gap hunt)
Target: `prd.md` (2026-08-23, status: draft) + `addendum.md`
Scope: read-only. Findings below; no files modified.

**Verdict: FAIL — not buildable as written.** The two load-bearing Phase 1 claims ("entity lands in the VTT table-ready", "every world-state change is undoable") have no mechanism behind them, the launch metrics cannot be measured from the described system, and the single-server deployment has no backup, no concurrency model, and no account model. Fix the criticals before any sprint planning.

---

## Findings

### Critical

**C1. No backup or disaster-recovery story for the crown-jewel graph. (§4.1, §4.6, §5, §8)**
The entire value prop is a persistent, growing world graph — stored on one personal machine, no replica, no backup cadence, no offsite copy, no RPO/RTO anywhere in the document. "Fully exportable" (§4.6) is a user-ownership feature, not a backup: it requires every DM to remember to click export. One weekend power event on the owner's server silently zeros the retention metric and the beta relationship.
*Fix:* add an NFR: automated offsite graph backup (nightly JSON export to a second location), stated RPO, and a restore test before beta launch.

**C2. No concurrency or commit semantics for world state. (§4.2, §4.5, §3.B, §3.A)**
§4.5 says the DM keeps editing while an async generation runs; §4.2 says "every world-state change is undoable" and "the DM's edit always beats the LLM's" — but there is no revision number, no atomic commit of a generated subgraph, no last-write-wins/merge/reject rule, and no definition of what "undoable" means across an LLM commit that landed after the DM's edit. The LLM's output was computed from the pre-edit world state; committing it resurrects the exact values the DM just changed. "Never silently corrupt" is a slogan, not an invariant — there is no referential-integrity rule to violate it against.
*Fix:* specify: each world-state change is an atomic, versioned transaction; generated subgraphs commit as one unit against the latest revision; concurrent DM edits force a visible rebase or reject; a single undo stack where one undo never crosses a transaction boundary.

**C3. Deleting an entity with references is undefined. (§3.A, §4.2, §3.B)**
"Every generated entity becomes a node woven into existing edges" — then Wren deletes the mayor. Do the edges orphan, cascade-delete, block, or re-home? "Sister of the mayor's scribe" is an edge into a deleted node. The "single source of truth" claim dies the moment dangling edges are legal, and P3's state machine will happily compute consequences off orphaned edges.
*Fix:* pick and specify a policy (recommend: block delete of referenced nodes, or delete with explicit edge cascade and a confirmation listing affected entities); add an integrity invariant: zero dangling edges, verified on every commit.

### High

**H1. "5e-ready stats" are promised in exports but generated nowhere. (§3.E, §3.B, §7)**
§3.E: exports "carry the 5e-ready stats and the generated portrait, so the entity lands in the VTT table-ready." But the entity model (§3.A) and the candidate spec (§3.B: name, role, personality, lore connections, secret/rumor/hook) contain no stat block — no AC, HP, abilities, spells. §3.B even says "a lazy DM wants immediate playability, not just names and stats." So either the tool has an unlisted stat-block generation step (an extra LLM call, an operation missing from the pricing scale in §5) or "table-ready" is fiction and exports ship lore prose into a VTT. The export formats (Owlbear JSON, Fantasy Grounds V4 import, MapTool — which has *no* documented character import format to begin with) cannot be planned until this is decided.
*Fix:* add an explicit stat-block requirement (who generates it, which 5e fields, at candidate-accept time) or downgrade the export promise to "lore-ready" and delete "table-ready."

**H2. Token economics don't hold together. (§5, §6, §4.1)**
The free allowance is "enough for, e.g., one character plus one image" — and "a single premium render can consume the user's entire token balance." If the balance equals the free allowance, then *zero* video fits in it: the product's flagship "Give them a face" (journey step 5, Phase 1, includes "short video for the BBEG reveal") is unaffordable by the free tier it is supposed to "get started" with. A fast/rough video tier exists but is never placed on the scale. Worse, "tokens" is one unit of account across three heterogeneous compute types (LLM text, image diffusion, video diffusion) with no conversion factor — video render is GPU-seconds, not LLM tokens; a 24GB 3090 video render can plausibly cost 50–200× an NPC in wall-clock compute, so the "relative scale" is doing the work of a missing cost model. And the journey's *first* operation — digesting her whole world + notes (step 1) — is the biggest text job in the product and is absent from the pricing scale entirely.
*Fix:* define the token as a convertible credit (state the image/video multipliers or cost ceilings per render), place world-build-in on the scale, and state whether *any* video (fast tier included) fits the free allowance.

**H3. Closed beta on one 3090 has no capacity or reliability model. (§4.1, §5, §8)**
"A few invited DMs" on one GPU: no max-concurrent-generations, no queue depth, no wait-time target, no per-user beta quota, no model chosen ("an abliterated local model" — what size? 24GB VRAM caps this at roughly 14B Q8 / 30B Q4, which determines *all* of quality, throughput, and the uncensored-content NFR, yet the local model appears nowhere in Open items). "2–3 candidates" per ask = 2–3 parallel LLM calls per request. One heavy prep night starves everyone else; there is no degradation path (queue? reject? slow?). No uptime or crash-recovery NFR for a product whose beta promise is "your world is here when you get home."
*Fix:* name the local model and its VRAM/throughput budget; set beta concurrency limits and a queue with visible position; add an uptime/restart NFR.

**H4. Launch metrics cannot be measured from the described system. (§6)**
- "% of entities *actually exported to a VTT*" — the export is a file that leaves the tool; import success happens in Owlbear/MapTool/FG, which the tool never sees. As written, unmeasurable.
- "Export failure rate — the promise breaking on bad VTT imports" — same problem: the failure lives in the VTT.
- "Tokens per generated entity; free-token burn rate per week" — the beta is explicitly un-metered ("the cost is the owner's compute, not tokens"); there are no tokens to burn in Phase 1. This metric describes a post-beta state while sitting in the beta→launch success gate.
- "% of invited DMs… in week one" and "% of campaigns returning the following week" — require an account/campaign model, which is itself listed as unresolved (§8, "Accounts & campaign sharing — Unresolved").
- Statistically: at "a few" beta users, every percentage point is 10–30 users' worth of noise; none of these percentages will be interpretable at the stated cohort size.
*Fix:* measure what the tool can see (export clicks per entity, export-format validation pass rate), meter tokens from day one even during beta (free metering, no charge), define the account model before these metrics exist, and state the beta cohort size large enough for the metrics to mean something.

**H5. Simulate History — the emotional climax — is one sentence. (§3.G, §3.A, §1, §7)**
The journey ends on "the world has moved"; the one-liner promises "the world simulates what happens over time." What actually ships in P3 is "state machine computes deltas from logged actions" — with no definition of the state machine (rules? heuristics? decision table? who authors them and for which edge types), no edge-type schema (the graph lists "relationships, debts, grudges, loyalties" as an open vocabulary the LLM decides on, which no state machine can consume), and no acceptance criteria (what is a correct delta?). P3 also silently depends on the P3 logger's action schema (typed in one line) and on a P1 edge schema designed for a consumer that doesn't exist yet — none of which is stated as a dependency.
*Fix:* define a closed edge-type vocabulary in P1 (with directionality semantics), an action-log schema in the logger spec, and a rule-authoring model for the state machine (even "DM-authored rules + a fixed starter rulebook" is plannable; "the state machine computes" is not).

**H6. "Hosted" is the wedge, and the deployment has none of its properties. (§1, §4, §8)**
The defensible wedge per the addendum is *hosted* — the column that separates mythosCircle from the self-hosted OSS cluster. But the PRD specifies no authentication, no accounts (open item, "Unresolved"), no TLS/transport story, no uptime SLA, no rate limiting — a home-server beta that is strictly *less* available than a user's own self-hosted instance. The product claims a hosted position it cannot currently support, and its own metrics (activation, retention) require accounts that don't exist.
*Fix:* add a minimal auth/account NFR (invited-user identity, per-campaign ownership) as a Phase 1 requirement, not an open item, plus uptime/restart and basic transport security.

### Medium

**M1. Video is simultaneously a Phase 1 MVP feature and a candidate for deletion. (§7, §8, §4.1)**
MVP scope: "Image + video generation." Open items: "Local 24GB constraint: video is the first item to route to OpenRouter post-migration, or the first to cut." "Post-migration" means video runs locally during the beta — the least-capable window — or the MVP line is wrong. A scope section that lists a feature an open item offers to cut is not a scope section.
*Fix:* condition the MVP line explicitly ("video: local fast-tier only during beta; premium tier post-OpenRouter; cut decision gate at X") or move video to Phase 2.

**M2. "Open items — resolved at Finalize" — they are not resolved. (§8)**
This review *is* the Finalize gate, and the section still contains "Accounts & campaign sharing — Unresolved," TBD OpenRouter endpoints, and open per-operation prices. H4/H6 show why the account item is launch-blocking, not cosmetic.
*Fix:* close or explicitly defer each item with a named owner and a phase gate; account model must close before beta.

**M3. "Never silently corrupt" has no verifiable invariant. (§4.2, §4.7)**
An NFR nobody can check is not an NFR. There is no defined corruption (no integrity predicate), no retrieval-quality criterion for "only the lore relevant to the request enters generation" — retrieval failure means the world simply doesn't weave in, which is exactly the positioning failure, and no metric covers it.
*Fix:* state the invariant (referential integrity, entity identity stability across regeneration) and a retrieval proxy (e.g., % of generated candidates whose top-k retrieved entities appear in the final lore connections).

**M4. "Undoable" vs. candidate selection. (§3.B)**
Wren receives 2–3 candidates, edits detail on B, then prefers A. Is B's partial weave rolled back? When does a candidate enter the graph — at pick, at accept, at save? With async commits (C2), candidate lifecycle needs explicit state (proposed → accepted → rejected) or the graph fills with rejected barkeeps.
*Fix:* define candidate states and that only an accepted candidate's subgraph commits.

**M5. Media assets on entity deletion are orphaned. (§4.6, §3.C)**
"Deleting a campaign actually deletes it" covers campaigns; deleting one entity leaves its portrait/video on disk (24GB box — media fills it) and the JSON export may reference a dead file.
*Fix:* entity deletion reclaims its media; export validates media references.

**M6. MapTool has no character import format. (§3.E)**
Owlbear and Fantasy Grounds have import paths; MapTool is token-based with no documented character-import. "Export to MapTool" currently means "we will figure it out."
*Fix:* either define the MapTool artifact (token JSON + portrait) or drop it from Phase 1.

### Low

**L1. Free-grant cadence is undecided. (§5, §6)**
"Granted weekly/monthly" — the cadence is a free variable, and the burnout counter-metric ("exhaust their weekly free tokens") hard-codes weekly. Pick one.

**L2. "Premium-video share" counter-metric is dead in beta. (§6)**
No premium tier exists during the free, un-metered beta; the metric can only be collected post-migration. Fine as a post-beta metric, misfiled as a launch gate.

**L3. "Per-campaign caps" (§4.1) are never bounded.**
"Generation is metered — bounded multi-calls, per-campaign caps" with no cap values and no owner. A requirement without a number is a TODO.

---

## Coverage of the seven probes

1. **Token economics** — H2 (one unit of account across text/image/video, no conversion factor; free allowance vs "video = entire balance"), plus L1 (free-grant cadence undecided), L3 ("per-campaign caps" never bounded).
2. **World-state integrity** — C2 (no concurrency/commit semantics; "undoable" undefined across an async LLM commit), C3 (deleting a referenced entity is undefined; dangling edges), M4 (candidate-selection undo lifecycle).
3. **Closed beta on one 3090** — C1 (no backup/DR for the crown-jewel graph), H3 (no capacity/queue/uptime model, local model unnamed).
4. **Phase dependencies** — H5 (Simulate History depends on the P1 edge schema and the P3 action logger, none stated as a dependency; "state machine" undefined) and the Phase 2 visualizer being "surface only" atop an unspecified Phase 1 graph quality — dependencies exist but are not explicit enough to plan from.
5. **Hosted wedge vs. reality** — H6 (no auth/accounts, uptime SLA, TLS, or rate limiting to back the "hosted" claim).
6. **Export stat blocks** — H1 ("5e-ready stats" promised but generated nowhere), M6 (MapTool has no character import format).
7. **Unmeasurable metrics** — H4 (launch metrics can't be measured from the described system), M3 ("never silently corrupt" has no verifiable invariant; retrieval quality unmeasured), L2 (premium-video share dead in beta), M2 (unresolved open items incl. the account model these metrics depend on).
