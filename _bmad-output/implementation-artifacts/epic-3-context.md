# Epic 3 Context: Living Candidates — Generation & Acceptance

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

This epic delivers the core generation loop: the DM asks for an entity in plain language, the system retrieves relevant lore from the existing world graph, and the LLM returns 2–3 fully-shaped candidates woven into her world. She accepts on substance (full sectioned profile, not just name + stats), regenerates any part she dislikes without disturbing the rest, wires relations inline, and hand-edits anything — only what she accepts ever becomes real. This is the heart of the product promise ("the barkeep's secret pays off in act three") and carries the value checkpoint: candidate acceptance rate ≥ ~50% plus the retrieval-relevance proxy and a playable-at-the-table verdict from the DM.

## Stories

- Story 3.1: Plain-Language Ask Returns 2–3 Candidates
- Story 3.2: Candidate Lifecycle and Atomic Commit
- Story 3.3: Accept Screen on Substance
- Story 3.4: Inline Relation Editing
- Story 3.5: Regeneration — Whole and Per-Section
- Story 3.6: Hand Editing — the DM Is the Final Author

## Requirements & Constraints

- A plain-language ask plus current world state yields 2–3 candidates; each candidate must carry name, role, personality, the secret/rumor/party-hook triple, a 5e stat block (level, AC, HP, abilities, key skills), and at least one typed edge into existing world state. Acceptance validates that existing-world edge before commit.
- Stat blocks are validated against local 5e reference data (classes, races, spells, SRD 5.1 stat caps) with constraint enforcement (e.g., role=Wizard limits spells to the wizard list). Invalid generated stats get flagged and exactly one bounded repair pass; repair passes count against the job's LLM-call budget.
- Candidates exist only as proposed staging rows — invisible to retrieval and export until accepted. Each proposal is all-or-nothing: a partial failure rolls back the whole candidate. Reject/timeout garbage-collects the proposal.
- Only an accepted candidate's subgraph commits, as exactly one atomic transaction. Regenerating an existing entity stages a new candidate and never mutates accepted state. Rejected candidates must never leak into the graph.
- Any section of the canonical sectioned character model can be regenerated independently: the rest of the character's sections go into the prompt as context, the other sections end up byte-identical, and the previous revision is the undo. Whole-character regeneration is also one transaction.
- Hand edits to any field or section persist as committed transactions and always beat LLM output. A hand edit landing during a pending commit forces a visible rebase-or-reject — never a silent overwrite.
- Inline relation editing works from a character's screen against any existing entity (places, factions, other characters), using only the closed directional edge vocabulary with per-type counters. Counters change only via commits; zero dangling edges after every commit.
- The NFR2 retrieval-relevance proxy (% of candidates whose top-k retrieved entities appear in final lore connections) must be logged. P95 generation job duration is logged (shared gauge with Epic 2).

## Technical Decisions

- Entity generation lives in `pipeline/` and stages through `store/` — the store is the sole world-state writer; no raw SQL outside it.
- Retrieval is deterministic bounded graph traversal from the request over typed edges (depth cap, entity cap — seed 24) against the latest revision. No embeddings, similarity, or full-text search. The prompt receives hard truths only: the full structured record of each reached entity (attributes, counters, goals, economy) plus its generated text, plus the request. Same state + same request ⇒ byte-identical prompt.
- Proposals carry a `kind` (`build_in` | `entity` | `field`); the full candidate shape contract applies to `entity` kind only — `build_in` proposals have no stat blocks, `field` proposals scope to one field of one entity.
- The canonical character record is structured JSON keyed by section (identity anchor; narrative-lore sections; mechanics block; conditional boss section; world-integration block). Sections are the regeneration unit. Forward compatibility: every consumer (export, UI, KnowledgeProvider) renders known sections and skips unknown ones, never fails.
- Inference runs through the OpenAI-compatible provider adapter (HTTP only, swappable by config) inside the persistent FIFO queue — one job at a time, queue position visible via REST + WebSocket, per-campaign in-flight/queued caps, cancel with `job_cancelled`. Each generation job declares a max LLM-call budget from config; exceeding it fails the job.
- Wire contract: Pydantic schemas are the source of truth; frontend TS types derive from generated OpenAPI; ULID IDs; UTC ISO-8601; error envelope `{code, message, details?}`; 4xx never changes state; WebSocket progress messages `{type, job_id, state, queue_position?, progress?}`. Nothing on the path of play blocks on generation — jobs are backgrounded (the spinner is the enemy).
- Unit tests required for pipeline retrieval/prompt determinism and store commit/undo paths with deterministic fixtures; no UI e2e in beta.
- Theme and custom lore from the world seed model flow into generation prompts (weight them deliberately — the Epic 2 checkpoint suspects the prompt contract, not the store, if relevance is low).

## UX & Interaction Patterns

- The accept screen renders the full sectioned profile — appearance, personality, voice, secret, mechanics, world-integration — so acceptance is on substance, not name + stats.
- Reject and edit must be at least as easy as accept: same tap depth, one-tap accept is never the path of least resistance (the tool must not nudge toward "accept all").
- New commits appear in the world view without reload, via WebSocket progress (asynchronous with progress; the DM preps the next entity while generation runs).
- No formal UX design exists — the owner dogfoods the UI; keep flows minimal and functional.

## Cross-Story Dependencies

- Builds directly on Epic 2's build-in world (the graph that candidates weave into) and Epic 1's substrate: versioned store with atomic commits/undo, persistent FIFO queue, OpenAI-compatible inference adapter, auth, and the conventions/test harness.
- Media (portraits) is Epic 4 and out of scope here; exports/VTT is Epic 5. Nothing in this epic blocks on them, but the canonical sectioned record and appearance section are designed to feed Epic 4's portrait prompts and Epic 5's table-ready exports.
- The closed edge vocabulary, counter semantics, and deterministic retrieval established here are prerequisites for Phase 3 Simulate History — keep the vocabulary strict.
