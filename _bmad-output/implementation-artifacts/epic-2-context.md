# Epic 2 Context: Bring the World In — Guided Build-In

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Epic 2 turns the DM's campaign (Epic 1) into an actual world: a guided build-in flow carries her seed plus key places, factions, key figures, and free-form notes into a queued generation job; the LLM digests it into the versioned graph with typed edges (never free-text mush), no orphans, key figures carrying minimal 5e stat blocks (so the world is playable at the first visible moment), and the world state exportable as JSON + Markdown at any time. By its end the DM sees her world on screen — nodes, typed edges, her barkeep with stats ready — and the build-in world is actually playable (`epics.md` Value Checkpoints).

## Stories

- Story 2.1: Guided build-in flow — frontend foundation + the `build_in` job kind
- Story 2.2: Typed edge vocabulary on commit
- Story 2.3: Core-first two-wave build-in pipeline (retrieval starts here — AR6)
- Story 2.4: Key figures carry a minimal 5e stat block (AR24 section shape, AR25 constraints + one bounded repair pass)
- Story 2.5: No orphans and cascade delete
- Story 2.6: World state export — JSON + Markdown, read-only from the latest revision (AR18)
- Story 2.7: First visible moment — world view renders nodes, typed edges, stat blocks; second-wave entities appear live via WebSocket (NFR9)

## Requirements & Constraints

- Build-in is a queued generation job (AD-19, AR5): submission enqueues, the screen is never blocked; queue position + progress visible over REST + WS (AD-17).
- Core-first: key figures + their edges generate and commit before any second-wave entity — the world is visible and playable in minutes (the first bandage on the 30-second market promise; P95 job duration logged).
- Key figures carry a minimal 5e stat block (AR24 section shape, AR25-validated, role-limited, SRD 5.1 field set) or exactly one bounded repair pass; an invalid stat block is never committed — a fail event is logged.
- Retrieval for later generation starts here (AR6): deterministic-prompt invariant — same world state + same request ⇒ byte-identical prompt; bounded graph traversal, full world never enters a prompt.
- Typed edges from the closed directional vocabulary (relationship, debt, grudge, loyalty, member_of, located_in, rival_of, kin_of, ally_of, enemy_of — extensible by adding a type, never free text); per-type counters (debt = amount, grudge/loyalty = score, ally/enemy = intensity); counters change only via commits; zero dangling edges after every commit (AR8, AD-23).
- No orphans: every generated entity carries ≥1 typed edge into existing world state; delete with live edges requires an explicit cascade confirmation listing affected entities (FR4, AD-23).
- Export is read-only from the latest revision, JSON + Markdown; Markdown is Obsidian-level complete (the no-lock-in claim; Epic 5 dependency).
- Candidates staged invisible until accepted (AR7) — reconciliation with core-first "committed" language is a 2.3 design note (owner-reviewed).
- Each build-in job declares a max LLM-call budget from config; exceeding it fails the job with an error event (AR21).
- World-seed fields (AR27) flow from the campaign into the build-in and generation prompts.

## Technical Decisions

- Layering unchanged: `api/` orchestrates; `pipeline/` proposes (retrieval, generators, commit); `store/` commits; `providers/` are leaves. Nothing outside `store/` writes world state (AD-1).
- Wave scheduling: one world, two waves on the same FIFO queue — key figures + edges commit first, the rest streams behind.
- Frontend consumes the build-in as a job lifecycle: submit → queue_position + state → WS progress → terminal; world view renders from Pinia stores (AD-20).
- Export engine produces JSON + Markdown purely as a projection of the latest revision (AD-11): export never writes.
- ULIDs, UTC ISO-8601, kebab-case routes, camelCase TS/Vue, error envelope, cursor pagination (conventions.md).

## UX & Interaction Patterns

- Nothing on the path of play blocks on generation: jobs are backgrounded with visible position + progress; second-wave entities appear in the world view without a reload (WebSocket).
- The DM keeps the wheel: the first visible moment shows her barkeep with stats ready to roll initiative — the world is playable, not just visible.

## Cross-Story Dependencies

- 2.1 ships the frontend foundation + the `build_in` job kind; its runner is 2.3.
- 2.2 (typed edge vocabulary) is store-level and may run in parallel with 2.1; the vocabulary is enforced on commit before 2.3 generates edges.
- 2.3 consumes the queue (1.3), the store commit path (1.2), and the provider (1.4); retrieval (AR6) + budget (AR21) land here.
- 2.4 builds on 2.3's output shape (minimal stat block per key figure) and 2.2's commit enforcement; AR24/AR25 reference data + bounded repair pass.
- 2.5 (no orphans + cascade delete) extends the store's commit + delete semantics; depends on 2.2's edge vocabulary.
- 2.6 (export) is a pure read projection over the committed graph — depends on the store's revision/read surface (1.2) and the stat-block shape (2.4).
- 2.7 (first visible moment) depends on 2.3 (core wave), 2.2 (edge rendering), 2.4 (stat block render), and the frontend foundation from 2.1 + WS client (NFR9).
- Epic 3 consumes the staged-candidate pipeline (AR19) and the sectioned character model (AR24) shipped by 2.3/2.4.

## Open Items / Notes

- Build-in commit-path reconciliation (AD-19 proposed vs core-first committed) → 2.3 design note, owner-reviewed.
- Retro item 2 (cursor-pagination convergence, owner "pipeline story dev") → 2.3.
- Retro item 1 (ULID predicate) / 3 (campaigns→StoreError) / 4 (theme seed dedup) absorbed into 2.1.
- Deferred-work: general schema migrations + session-row TTL stay out of Epic 2 scope (Epic 6).