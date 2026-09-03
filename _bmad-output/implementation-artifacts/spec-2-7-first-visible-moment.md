---
title: 'First Visible Moment — the World on Screen'
type: 'feature'
created: '2026-09-03'
status: 'done'
baseline_commit: '6a17d1feff92dad78bfb804b40be6d9345e838ca'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** After a build-in commits, the DM's world is invisible — the only surface is a JSON export. Epic 2's payoff is the first visible moment: nodes, typed edges, her barkeep with stats ready to roll initiative, and second-wave entities appearing live as the job streams behind (FR1, NFR9).

**Approach:** A read-only world view (`/campaigns/:id/world`) fed by the existing export-JSON projection (the latest-revision snapshot, 2.6) and kept live by the existing jobs WebSocket: build-in progress/terminal frames trigger a world re-fetch, so wave-2 entities appear without a reload. No backend changes expected.

## Boundaries & Constraints

**Always:**
- The world view is read-only: it renders the export-JSON projection and never writes world state (AD-1, AR18).
- Live updates ride the existing `connectJobSocket` frames — no polling timers, no new WS event types. Re-fetch the world when a build-in job of this campaign reports `job_progress` with `progress >= 0.5` (core wave committed), on any terminal frame (`job_done`/`job_failed`/`job_cancelled` — a mid-wave-2 failure still leaves wave 1 committed), and on WS reconnect (`onReconnect`).
- Re-fetches coalesce: a frame arriving while a fetch is in flight marks dirty and re-fetches once after; never stack parallel fetches.
- Entity/edge/stat-block rendering tolerates absent optionals (`text: null`, empty `data`, missing `stat_block`); counter display uses the per-type semantics (debt=amount, grudge/loyalty=score, ally/enemy=intensity, others bare) as in 2.6's `_edge_label`.
- Auth and error handling via the existing `apiFetch`/ApiError path: generic 401 redirect; foreign/unknown campaign 404 renders a not-found state (no oracle).
- camelCase TS/Vue, Pinia store, kebab-case route path, dark-theme styling consistent with App.vue's card language.

**Ask First:**
- Any backend change at all (new REST endpoint, WS event type, store/pipeline edit) — the design assumes zero backend edits.
- Any need to alter the export payload shape or the export route to serve the view.
- A graph/visual layout of nodes (that is Epic 7's spike 7-1, phase-2 boundary).

**Never:**
- No editing, creating, or deleting entities/edges from this view (hand editing is 3-6; delete UI stays API-only).
- No candidates/proposed entities — only committed state (AR7).
- No media/portraits (Epic 4).
- No pagination — the world view renders the full latest revision (export JSON is already unpaginated).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| HAPPY_PATH | Authed owner, committed world | Entities grouped by kind, each with text, relations (`Name --type(counter)--> Name`), and for key figures the rendered stat block (identity line, six ability scores, AC/HP, skills/actions/traits/spells) | N/A |
| EMPTY_WORLD | Campaign, zero revisions | Empty state with a build-in CTA, no error | N/A |
| FOREIGN_CAMPAIGN | Other owner's campaign id | Not-found state; indistinguishable from unknown id | 404 envelope via ApiError |
| UNAUTHENTICATED | No/invalid session | Redirect to login | generic 401 handler |
| LIVE_WAVE2 | Build-in running; wave-2 commits | `job_progress` frame (progress 1.0) triggers one re-fetch; new entity appears without reload | N/A |
| FAILED_MID_WAVE2 | Wave 1 committed, wave 2 failed | `job_failed` frame triggers re-fetch; committed wave-1 entities render | N/A |
| WS_DROP | Socket drops then reconnects | `onReconnect` triggers one re-fetch (REST re-sync) | N/A |
| ODD_DATA | Entity with `text: null`, `data: {}`, or no stat_block | Renders gracefully (placeholder text, relations omitted) | N/A |

</frozen-after-approval>

## Code Map

- `backend/app/api/exports.py` -- the data source: `GET /api/campaigns/{id}/export?format=json` (:222-281) returns `WorldExport{campaign, revision, entities[EntityExport{id,kind,name,text,data}], edges[EdgeExport{id,src,dst,type,counter}]}` (:43-77); rowid-ordered, non-finite-coerced. Reuse verbatim — do not add a `/world` endpoint.
- `backend/app/api/ws.py` -- WS hub; frames `{type, job_id, state, queue_position?, progress?}` (`_build_message` :49-59); `progress` present only on `job_progress`.
- `backend/app/pipeline/build_in.py:149,159` -- wave commits emit `report_progress(0.5)` (core) and `(1.0)` (complete): the refetch thresholds.
- `backend/app/store/commit.py:30-43,55-65` -- `EDGE_TYPES` + `EDGE_COUNTER_SEMANTICS`; frontend mirrors the display semantics only (debt=amount, grudge/loyalty=score, ally/enemy=intensity, others bare).
- `frontend/src/api/client.ts` -- `apiFetch<T>` + `ApiError{status,code}`; the only fetch path.
- `frontend/src/api/schema.ts` -- STALE (generated 2.1, commit 0c166fe): regenerate with `npm run gen:api` (backend on 127.0.0.1:8000) so `WorldExport`/export route types exist; closes the deferred-work 2.6 item.
- `frontend/src/ws.ts` -- `connectJobSocket(campaignId, onMessage, {onReconnect, onAuthFailure})`; teardown fn; 4401 fatal.
- `frontend/src/stores/jobs.ts` -- WS-dispatch precedent: monotonic merge, `handleWsMessage` (:102-124), per-campaign getters; pattern to mirror for the world store.
- `frontend/src/views/BuildInView.vue` -- 2.1 view conventions (loading/error classes, job status card); add a link to the world view here.
- `frontend/src/views/CampaignsView.vue:38-40` -- campaign card CTA row; add "Open world" beside "Open build-in".
- `frontend/src/router.ts` -- 4 routes; add `/campaigns/:id/world` (name `world`, `requiresAuth`).
- `frontend/src/stores/jobs.test.ts`, `frontend/src/ws.test.ts` -- vitest conventions for the new world-store tests.
- `frontend/src/App.vue:66-78` -- `.card`/`.muted`/`.error` shared styles.

## Tasks & Acceptance

**Execution:**
- [x] `frontend/src/api/schema.ts` -- regenerate from the running backend (`npm run gen:api`); wire-in only, no hand edits.
- [x] `frontend/src/stores/world.ts` -- new Pinia store: `load(campaignId)` fetches export JSON into `byCampaign`, tracks loading/error/notFound; `handleJobMessage(campaignId, WsMessage)` implements the refetch rule (progress >= 0.5, any terminal build-in frame) with in-flight coalescing + dirty-flag trailing refetch; exposes the WS teardown wiring.
- [x] `frontend/src/views/WorldView.vue` -- route view: campaign header + revision, entities grouped by kind, per-entity relations lines with counter semantics, stat block for key figures, empty/loading/error/not-found states; owns the WS subscription lifecycle (connect on mount, teardown on unmount, reconnect refetch).
- [x] `frontend/src/components/StatBlock.vue` -- minimal 5e stat-block renderer for `data['stat_block']` (identity, attributes, combat, skills/actions/traits, spells); tolerates absence.
- [x] `frontend/src/router.ts` -- add the `world` route.
- [x] `frontend/src/views/CampaignsView.vue`, `frontend/src/views/BuildInView.vue` -- links to the world view.
- [x] `frontend/src/stores/world.test.ts` -- vitest: refetch on qualifying frames only, coalescing (frame during in-flight fetch), reconnect refetch, 404 not-found mapping, non-build_in jobs ignored.
- [x] `frontend/src/views/WorldView.test.ts` -- vitest (@vue/test-utils + happy-dom, added during the matrix audit): I/O-matrix render rows — HAPPY_PATH (kind groups, counter semantics, stat block), EMPTY_WORLD, FOREIGN_CAMPAIGN, ODD_DATA.

**Acceptance Criteria:**
- Given a committed core wave, when the DM opens the world view, then every committed entity renders grouped by kind with its typed edges and counters, and each key figure shows a complete minimal stat block ready to roll initiative (FR1).
- Given a build-in job whose second wave commits while the view is open, when the WS `job_progress` frame arrives, then the new entity appears without a reload or manual refresh (NFR9).
- Given an empty world, when the view opens, then an empty state with a build-in CTA renders; a foreign/unknown campaign renders not-found; an unauthenticated visit redirects to login.
- Given a WS drop and reconnect, when the socket reopens, then the world re-syncs from REST exactly once.

## Design Notes

- Re-fetch, not entity-push: frames only signal "the world changed"; the view re-reads the export snapshot. Worlds are small (≤ ~100 entities), the endpoint is already deterministic, and this keeps the WS contract untouched. If a frame lands mid-fetch, one trailing refetch covers the delta.
- Threshold rationale: 0.5 = wave-1 commit (first visible moment), 1.0 = wave-2; terminal frames refetch because a mid-wave-2 failure still leaves wave 1 committed. Frames with `progress < 0.5` and non-build_in kinds are ignored.
- Keep the store thin: it holds the fetched projection plus flags only; rendering decisions (grouping, counter labels, stat-block sections) live in the view/components.
- `revision` from the export gives the header a "revision N" stamp — visible proof the view tracks commits.

## Verification

**Commands:**
- `npm run --prefix frontend test` -- expected: new world-store tests + existing suites green
- `npm run --prefix frontend run typecheck && npm run --prefix frontend run lint` -- expected: clean
- `uv run --directory backend pytest -q` -- expected: 404 passing, unchanged (no backend edits)
- `make lint && make typecheck` -- expected: clean

**Manual checks (if no CLI):**
- With backend + frontend dev servers up, register/login, create a campaign, run a build-in, open the world view: entities/edges/stat block render; while a second build-in runs, wave-2 entities appear live without reload.

## Suggested Review Order

**Live-update rule (the story's core)**

- WS frames only signal change; kind-resolved refetch rule with wave-1 threshold 0.5.
  [`world.ts:132`](../../frontend/src/stores/world.ts#L132)
- Coalesced single fetch path — in-flight calls mark dirty, one trailing fetch lands the delta.
  [`world.ts:82`](../../frontend/src/stores/world.ts#L82)
- Unresolved-kind terminal frames still refetch (REST recovery may fail; job_done is the last frame).
  [`world.ts:140`](../../frontend/src/stores/world.ts#L140)

**View state machine & socket lifecycle**

- Idempotent start(): load, then connect once per campaign; disposed/connected guards close the unmount race.
  [`WorldView.vue:35`](../../frontend/src/views/WorldView.vue#L35)
- Gate on notFound/error, not a null world — a coalesced remount still gets live updates.
  [`WorldView.vue:37`](../../frontend/src/views/WorldView.vue#L37)
- Refetch failures surface over a displayed snapshot; initial-load errors get a Retry.
  [`WorldView.vue:165`](../../frontend/src/views/WorldView.vue#L165)

**Rendering the projection**

- Entities grouped by kind; relations render in edge direction from both endpoints.
  [`WorldView.vue:119`](../../frontend/src/views/WorldView.vue#L119)
- Counter display mirrors 2.6 `_edge_label` (debt/grudge/loyalty/ally/enemy carry counters, neutrals bare).
  [`WorldView.vue:100`](../../frontend/src/views/WorldView.vue#L100)
- Stat-block renderer tolerates absence; role joins the filtered identity parts; index-safe v-for keys.
  [`StatBlock.vue:81`](../../frontend/src/components/StatBlock.vue#L81)

**Wiring**

- World route (kebab-case, auth-guarded).
  [`router.ts:23`](../../frontend/src/router.ts#L23)
- Entry points: secondary CTA on campaigns, matching link in build-in.
  [`CampaignsView.vue:42`](../../frontend/src/views/CampaignsView.vue#L42)
- schema.ts regenerated via gen:api — closes the deferred 2.6 staleness item (no hand edits).
  [`schema.ts:1014`](../../frontend/src/api/schema.ts#L1014)

**Tests (peripherals)**

- Store matrix: qualifying frames, coalescing, reconnect, 404-on-refetch, preserved snapshot, unresolved terminal.
  [`world.test.ts:127`](../../frontend/src/stores/world.test.ts#L127)
- View matrix incl. loading state, socket-wiring assertions, FOREIGN_CAMPAIGN no-socket.
  [`WorldView.test.ts:103`](../../frontend/src/views/WorldView.test.ts#L103)
