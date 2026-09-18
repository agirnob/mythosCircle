---
title: 'Generate Retrieval-Cap Diagnostic — Surface Truncation & Ask-Target Seeding'
type: 'feature'
created: '2026-09-18'
status: 'done'
owner_decision: 'both (name-match boost + rowid bias) — 2026-09-18'
baseline_commit: 'main (post sprint-A 2026-09-18)'
--->

<!-- DRAFT for owner review (deferred ledger: spec-3.1 round-3 +
     spec-2.3 "wave-2 anchors truncate" counterpart, generate path). -->

## Problem

The generate runner seeds retrieval with the full committed world
(`seed_ids=None`) and an AR6 entity cap of 24 rows. A world built past the
cap (build-in may legally commit 100 key figures) silently loses context
for asks that target an entity beyond the 24-row top-k: the model never
sees the target, candidates weave to arbitrary rowid-first anchors, and
the job result carries **no diagnostic** that truncation happened. Two
open items, same root:

1. **Surface truncation** — the job result should prove the truncation
   happened (entities in world vs entities in prompt, entity_cap).
2. **Ask-target seeding** — the entity an ask names/implies should enter
   the prompt deterministically instead of losing the rowid-only race.

Fixes the generate-path sibling of the closed build-in item (two-tier
context, 100-entity cut, 2026-09-12).

## Scope (boundary)

- Generate path only (`backend/app/pipeline/generate.py` +
  `retrieval.py`). Regenerate path is untouched (single-entity by
  construction). No changes to retrieval's traversal order (AD-16 keeps
  same-state-same-prompt purity).

## Proposal (for owner choices)

1. **Truncation result field (mechanical, no decision needed):** the job
   result gains `context: {requested: N, included: M, truncated: bool,
   cap: 24}` — `truncated` true when `M < N`. Mirrors the build-in result
   banner. Tests pin the field on a >cap world.
2. **Ask-target seeding (owner decision):** two candidate rules —
   (a) **name-match boost**: canonicalize the ask, extract any entity
   name present in the world, seed it into the retrieval expansion
   (deterministic; no embeddings — AR6 stands); (b) **rowid bias**:
   prefer most-recently-committed entities for the remainder of the cap
   (the ask most often targets the newest additions). (a) needs a
   name-list match against full world names (cheap); (b) changes the
   anchor set and needs the AD-16 same-state-same-prompt invariant
   guarded (rowid order is deterministic — pure function of world state;
   safe). Owner picks (a), (b), or both.
3. **Violation hint:** when `truncated` and the ask names no entity in
   the world, the failure path MAY append "ask targets an entity outside
   the retrieval window" to the job error for ambiguity diagnostics.

## Acceptance

- A >24-entity world: generate job result carries the context block;
  omission fixes the silent-loss complaint.
- Deterministic: same world + same ask => same prompt (AD-16 pins).
- Existing BAD_EDGE/candidates tests stay green; new tests for the
  context block on a 25+-entity world.

## Out of scope

- Raising the cap (24 is AR6 seed; performance decision for deeper
  dogfood data).
- Build-in path (already closed, two-tier roster).
- UI surfacing beyond the job-result banner (the accept screen reads the
  result already; cosmetic).