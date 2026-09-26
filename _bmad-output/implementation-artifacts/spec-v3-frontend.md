---
title: 'v3 frontend phase — registry pickers, Tonight feed, Tier-2 chips, reason picker, dial input'
type: 'feature'
status: 'done'
baseline_commit: 'bd3cde8'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-mythosCircle-2026-09-25/ARCHITECTURE-SPINE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-v3-tier2-routes.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-v3-slice-backend.md'
  - '{project-root}/_bmad-output/planning-artifacts/ux-designs/ux-mythosCircle-2026-09-23/EXPERIENCE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/handover-2026-09-27.md'
---

# v3 frontend phase — the registry/feed/chips/dial surface

> BASE DECISION PENDING (owner): this spec is written against the owner's
> staged frontend Rebuild (AppShell + `ui/*` + `NewWorldView`/`NewEntityView`/
> `NewAskView`) as the hosting surface. Until the owner releases it
> (commit or explicit go-ahead), no `frontend/src/*` file is touched — the
> phase is code-mapped but not started. If the legacy committed views
> (WorldView.vue + friends) become the host instead, only the code map
> changes; features, boundaries, and acceptance are identical.

## Intent

**Problem:** The v3 backend slice + Tier-2 wire slice shipped every API surface
(registry reads, revisions feed, verb/toggle routes, run-state read, required
edge reasons, dial record key) and the dev API serves it on :8000 — but the
frontend's generated schema predates all of it. Every v3 capability is
API-visible and UI-invisible: the DM cannot pick a kind from the live matrix,
cannot see Tonight's feed, cannot fire a consequence verb or flip a knowledge
chip, cannot attach an edge reason, cannot set a dial.

**Approach:** One phase, no backend changes. Regenerate the API surface from
the live OpenAPI, then build the five v3 surfaces into the hosting views
(overview + entity detail + ask flow) using the owner's rebuild design system
(`tokens.css`, `ui/*`). Where a hosted view is missing (e.g. an edge-creator
in the rebuild), the phase builds the minimal surface with it — never a
parallel legacy path.

## Boundaries & Constraints

**Always:** backend frozen (any wire gap found → flag it; fixing crosses into
a new backend slice); schema regen from the LIVE :8000 OpenAPI (the dev API IS
current v3 — the gen:api caution is satisfied); UX follows ux-2026-09-23
FINAL where the spine does not supersede it (spine wins on conflict, AD-26/
AD-29); the four consequence verbs are UI affordances over the open session
image — no client-side verb vocabulary; the kinds registry is re-fetched per
walk mount, NEVER bundled (AD-34), commit-time validation is the backstop;
take-back and verb commits BOTH render `edited` — the feed never distinguishes
undo from edit (AD-27); a flip NEVER writes the record (AD-29); edge creation
requires a non-blank reason client-side (AD-32, backend 422 backstop); dial
reads as authored-or-absent on pre-dial records (AD-36 — absent dial renders
muted, never an error).

**Ask First:** base decision (above); whether the rebuild's edge surface gets a
minimum edge-creator+reason-picker in THIS phase or the reason picker first
lands on the legacy WorldView relation editor (REST surface identical either
way — this affects only which file hosts the picker).

**Never:** backend JSON/route changes; a second API client; baked kinds state
(or a stale version token) surviving past the walk mount; client-side verdict
logic that duplicates `action` (feed renders what the API sends); record
writes from a knowledge toggle; one-off fetch paths that bypass the coalesced
refetch pattern (the world-store precedent).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected UI Behavior | Failure Handling |
|----------|--------------|---------------------|------------------|
| Walk mounts | NewWorldView load | `GET /kinds` fired per mount; pickers show matrix cells + counter semantics + dial levels; version token kept per-entry | Fetch failure → pickers disabled + error line; commit is backend-backstopped |
| Feed opens | Tonight panel | `GET /revisions?limit=20`; newest first; every event line rendered (`edited` verbatim from API); take-back lines listed, never filtered | 404 campaign → existing not-found flow; refresh button/auto-refetch on job frames |
| Verb fired | Verb-row click | `POST session-verb`; optimistic chip flip; 204 = success; double-fire 204 = no-op (UI may disable the repeated affordance) | 409 → "world moved — refresh" (walk refetch pattern); 422 → inline error, chip stays |
| Knowledge flip | Chip click | `POST knowledge-toggle` `{field, known: !current}`; chip flips on 204; record untouched | same as verb |
| Edit + flip saved | Entity form save | ONE PATCH with content + `knowledge_flips` bundled; one revision (UI shows one feed line) | 422 malformed flips → inline; 409 → refresh |
| Edge create (reason) | Edge editor submit | `reason` required; blank blocks submit (dirty-flag hint); reason rides POST/PATCH | 422 blank-reason (backend backstop) → inline, keep the form |
| Dial set | Enrich/regenerate gesture | `dial` rides the regenerate payload (closed set from kinds); absent pre-dial → muted "unspecified" | 422 invalid dial → inline |
| Run-state read | Detail view | `GET run-state` joined to the walk; chips/toggles reflect rows (absent = default) | row refetch on refetch events; never blocks the record view |
| 401 anywhere | any call | existing single generic handler → login | — |
| Stale base | verb/toggle/flip | 409 → refresh notice (walk refetch), no silent retry | — |
| Campaign deleted while open | any v3 call | 404 → existing entry notFound flow | — |

## Code Map

- `frontend/src/api/schema.ts` — REGENERATED (`npm run gen:api` against the
  live :8000 API; verify the v3 paths appear: `kinds`, `revisions`,
  `run-state`, `session-verb`, `knowledge-toggle`). Owner WIP note: this file
  is committed-tracked and currently pre-v3; regen is a real diff, review it.
- `frontend/src/stores/` — new `tonight` store (run-state + revisions, per
  campaign, coalesced refetch mirroring `stores/world.ts`) or extended world
  store; the kinds fetch lives in the VIEW (AD-34 per-mount, never a bundle).
- `frontend/src/views/NewWorldView.vue` — kinds-picker fetch per mount; the
  overview kind grouping refines against registry availability
  (`edge_kind_ok` semantics from the kinds payload); Tonight feed panel
  (SectionHeader + list rows, `ui/*` components).
- `frontend/src/views/NewEntityView.vue` — knowledge chips on the existing
  secret/rumor/hook story cards (AD-29 2-state); verb row (new `ui/VerbRow`);
  run-state chips; dial input (record key, AD-36) on the edit surface.
- `frontend/src/views/NewAskView.vue` (+ candidates surfaces) — dial rides the
  regenerate/enrich payload (AD-38: one engine, `sections: null`,
  guide + dial).
- Edge reason picker host — Ask First (rebuild edge surface vs legacy
  WorldView relation editor); the REST call is the SAME (`POST/PATCH edges`
  with `reason`) either way.
- Backend tests / API: UNTOUCHED in this phase.

## Tasks & Acceptance

**Execution:**
- [x] Regen `schema.ts` from the live :8000 OpenAPI; v3 paths present; `npm run typecheck` still green (or fix only regen fallout).
- [x] Kinds picker: per-mount fetch + token tracking + registry-driven availability on walk/entity create.
- [x] Tonight feed: revisions panel rendering `{action, target_names, kind}` lines verbatim; take-back lines listed; refresh wiring.
- [x] Tier-2 detail: verb row + knowledge chips + run-state join; double-fire safe; edit+flip bundles one PATCH.
- [x] Reason picker on the edge surface (host per Ask First); blank blocks submit.
- [x] Dial input on the enrich/regenerate gesture + absent-dial rendering.
- [x] Frontend tests for the new store + chip/verb/picker interactions (existing test conventions: `*.test.ts` beside components, vue-tsc + eslint/prettier clean).

**Acceptance Criteria:**
- [x] Given a walk mount, when the overview opens, then a fresh kinds payload is fetched and the pickers reflect the current matrix — a stale token never serves a previous walk.
- [x] Given a verb commit and its take-back, when the feed renders, then both lines read `edited` with the same verbatim shape the API sent.
- [x] Given a knowledge flip on a record with a secret, when the chip is clicked, then the record export keeps the secret and only the marker moves.
- [x] Given an edge form with an empty reason, when the DM submits, then the submit is blocked client-side and the backend 422 never fires (the backstop stays armed).
- [x] Given a pre-dial record, when the detail view renders, then no error surfaces and the dial control reads as unspecified.
- [x] Given a double-clicked verb, when both clicks resolve, then exactly one revision exists (UI-safe by backend no-op; the UI never assumes it created two).

## Design Notes

No backend work in this phase — every surface above calls routes that are
committed, live on :8000, and pinned by 1525 backend tests. The regenerate
payload already accepts `sections:null` + `guide` + `dial` (AD-33/36/38);
the phase only ships keystrokes for them. The feed renders `action` verbatim
because the backend already maps verb/toggle/take-back to `edited` — a client
that re-derives undo-vs-edit would duplicate AD-27's rule and drift.

## Verification

**Commands:**
- `npm run typecheck` + `npm run lint` + frontend test suite — green after regen fallout fix.
- Live walk: dev API on :8000 (current v3) + dev frontend; dogfood the five surfaces on the live dev DB or a scratch demo world (owner's demo pattern: stop api − seed scratch via store − start api − demo − restore).
- Backend `uv run --directory backend pytest -q` — expected: still 1525, UNLESS a wire gap forces a backend follow-up (flagged, never silent).

## Spec Change Log

- 2026-09-27: Drafted against the owner's staged frontend Rebuild base.
  Base decision + edge-surface host are the two open Ask-First items; no
  `frontend/src/*` work starts until the owner releases the rebuild.
- 2026-09-27: Owner approved base option 1 (snapshot the rebuild, then
  build on it) — rebuild committed as d0e94a6, phase executed on top.
  Commits: e9ce7e3 (phase) + caf4170 (composer fix). Schema regenerated
  from the live :8000 OpenAPI; new `tonight` store (run-state + feed,
  coalesced world-store pattern; kinds refetched per walk mount under
  AD-34); Tonight feed panel on the overview rendering event lines
  verbatim (AD-27 take-backs read `edited`, never re-derived); entity
  detail gains knowledge chips (AD-29, record untouched — pinned by an
  export check), verb row over the open session image with take-back via
  the verb's own revision (AD-27), dial picker (AD-36 record key,
  absent-dial renders `unspecified`), and the EdgeComposer (matrix
  picker + required reason, AD-31/32; blank blocked client-side). The
  legacy WorldView relation editor now sends the AD-32 reason (it was
  about to 422 every authored edge) — tests updated. 321 frontend tests
  green; typecheck + eslint clean; phase files prettier-clean (rest of
  the tree carries a pre-existing prettier-3.9.6 debt — the owner's
  rebuild and legacy views alike; left untouched, owner's format pass).
  LIVE dogfood on the dev stack (browser): chip flip + run-state join,
  verb fire + take-back (feed shows `edited/session` for each), edge
  creation with reason, dial record set — all verified against the API
  truth; caught and fixed the null-src catch-all filter bug (null
  src/dst cells mean ANY kind — they were hidden from every picker).
- 2026-09-27 (round 2): Owner dogfood feedback round. Committed 498f749:
  VerbRow take-back is now the REVERSE VERB (`{defeated: false}`) — an
  active affordance's next click fires the inverse delta; deterministic
  from the session image, one revision, and it ends the revision-hunting
  undo that re-applied the state on take-back-of-take-back (the reported
  "mark defeated doesn't go back"). Tonight moved to its own page
  (`/campaigns/:id/tonight`, full feed + state summary) with the overview
  panel capped at five lines + deep link (the overview was flooding a
  20-line feed on the walk). Add-character's mirror-gate violations are
  touch-gated — the fresh-form 24-error wall is now a neutral hint; the
  submit gate is unchanged (still fully-authored sheets). Sidebar
  'Character' → 'Add character' (same route as the world view's link).
  Verified live in the browser: feed cap, take-back round trip, wall.
  OUT OF SCOPE this round (owner feedback, needs their decision/
  spec): build-in gaining the registry's dials/archetype pickers (dial
  threading into the build-in payload is backend work), ask-vs-candidates
  consolidation, the create-flow dial/relation wiring, and a palette
  pass — see handover-2026-09-27 §2.
  Dev-sandbox residue: campaign "V3 Dogfood Vale" + the v3dogfood
  account remain in data/mythos.db — deletable by the owner.
- 2026-09-27 (consolidation): owner picked 'consolidate the rebuild'.
  c551ef2: EntityEditor (in-place AR24 editor + dial + stat block) on the
  detail; World-section relation counter edit/delete; palette pass.
  En-route bug fixed: buildPatch emitted the text REF object instead of
  its string (every save failed); pinned in EntityEditor.test.ts.