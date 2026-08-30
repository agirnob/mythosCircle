---
title: 'Private world creation: campaign CRUD with the world seed'
type: 'feature'
created: '2026-08-30'
status: 'done'
review_loop_iteration: 0
baseline_commit: 'fc9ccefa36952ce1cc43ac84403c9d976c7105ba'
context:
  - '/home/main/Projects/mythosCircle/_bmad-output/planning-artifacts/architecture/architecture-mythosCircle-2026-08-23/ARCHITECTURE-SPINE.md'
  - '/home/main/Projects/mythosCircle/_bmad-output/specs/spec-mythosCircle/conventions.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A DM has an identity (1.5) but no home to pour a campaign into: the store's 1.2-era `Campaign` table is a bare `name`, has no owner, and carries none of the world-seed fields (AR27) that Epic 2's build-in and generation prompts depend on.

**Approach:** Extend the campaign model with the AR27 world-seed fields (owner account FK, title, description, theme from a validated list, custom_lore), and ship private, authenticated CRUD: create/list/get/update/delete. Every campaign belongs to exactly one account (AD-9 — one invited DM per campaign); list/get/update/delete are owner-scoped so a DM never sees or mutates another's worlds. Deletion is AR20's total hard delete — revisions, events, jobs, and media-manifest rows all cascade — guarded by an explicit `confirm` payload field. `get_current_account` (1.5) gates every endpoint; theme validation uses the seed list (also carried in `deploy/config.toml [campaigns]`).

## Boundaries & Constraints

**Always:**
- Every campaign has exactly one `owner_id` (account FK, AD-9). All endpoints require a valid session via `get_current_account`; a missing/invalid session is the 1.5 generic 401.
- Ownership isolation (NFR6): `GET /api/campaigns` lists only the caller's campaigns; `GET/PATCH/DELETE /api/campaigns/{id}` on another owner's campaign returns 404 — indistinguishable from a missing id (no oracle, AD-9).
- Creation requires `title` (1–300 chars), `description` (string), `theme` (validated against the seed list), `custom_lore` (string). Title+description+custom_lore trim whitespace; a blank title or blank-lore-after-trim is a 422.
- The theme list is the open user-extendable seed `["High Fantasy", "Grimdark", "Steampunk", "Planar"]` (AR27, and `deploy/config.toml [campaigns].themes`). Case-insensitive match; unknown theme → 422 naming the allowed list. Extending the list is a config change in 1.7 (config.toml consumption), not a code change here.
- Update is PATCH with exactly the seed fields (`title`, `description`, `theme`, `custom_lore`), all optional, at least one required; the same validation applies.
- Delete requires a JSON body `{"confirm": true}`; without it → 400. On confirm: one store transaction hard-deletes the campaign row AND cascades revisions, events, entities, edges, jobs, and media-manifest rows (AR20) — via the store's single-writer path, never raw SQL outside `store/`. The media *directory* reclamation is Epic 4 (the manifest is the DB part).
- Campaigns are world-state; creation does NOT create a revision or event (the world graph is empty until build-in; the seed is metadata, AR27). This is a deliberate, documented exception to the event-sourced substrate — the seed fields are the campaign's identity/config, not graph deltas.
- IDs remain ULIDs; timestamps UTC ISO-8601 `Z`; error envelope; kebab-case routes; `snake_case` Python; cursor pagination for the list.
- The legacy `Campaign.name` column is REMOVED (1.6 owns the model; nothing outside this story reads it — verified by the implementer via `lsp references` before the drop). Fresh databases get the new shape; an existing 1.5-era DB gets the additive columns via a `_migrate_campaign_seed` idempotent migration (mirrors `_migrate_job_result`); the `name` drop is a separate once-only migration that fails loudly if a legacy DB has rows relying on it (there are none in prod — greenfield).

**Ask First:** multi-user or invited-DM campaigns (beta is one owner each, AD-9); adding theme-list editing as a running API surface (config-extendable per AR27, consumed in 1.7); name/title alias or legacy rename support; deleting campaigns with a pending queue job (1.7/ops decision on in-flight semantics).

**Never:** returning a different status body for "another owner's campaign" vs "no such campaign" (both 404); owner-scalar leaks in list responses beyond the caller's own; raw SQL outside `store/`; a partial cascade (any failure rolls the whole delete back, AR20); theme validation against a hard-coded list that drifts from `deploy/config.toml` (the seed is mirrored, and a deploy-contract test pins both).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| CREATE | logged-in DM, valid seed fields | 201 `{campaign}` incl. owner_id = the caller's account id, ULID, created_at | N/A |
| CREATE_UNAUTH | no/invalid session | generic 401 | N/A |
| CREATE_BAD_THEME | theme not in the seed list | 422 error naming the allowed list | N/A |
| CREATE_BLANK | empty/whitespace title or lore | 422 | N/A |
| LIST_OWN | campaign(s) owned by the caller | 200 paginated list, only own campaigns | N/A |
| LIST_EMPTY | no campaigns | 200 with an empty list | N/A |
| GET_OWN | own campaign id | 200 `{campaign}` | N/A |
| GET_FOREIGN | another owner's campaign id | 404 (identical to unknown) | N/A |
| GET_UNKNOWN | bogus id | 404 | N/A |
| UPDATE_OWN | PATCH own campaign with seed fields | 200, only provided fields change, validation applies | N/A |
| UPDATE_FOREIGN | PATCH another owner's campaign | 404, nothing changes | N/A |
| DELETE_CONFIRMED | DELETE own campaign with `{"confirm": true}` | 204, cascade hard-deletes revisions/events/entities/edges/jobs/media rows; subsequent GET 404 | N/A |
| DELETE_UNCONFIRMED | DELETE without confirm / `{"confirm": false}` | 400, nothing deleted | N/A |
| DELETE_FOREIGN | DELETE another owner's campaign | 404, nothing deleted | N/A |
| CASCADE_MIXED | campaign with committed graph + queue jobs + media rows | delete removes all in one transaction | any failure rolls back entirely |

</frozen-after-approval>

## Code Map

- `backend/app/store/models.py` -- `Campaign` gains `owner_id` (FK account, index), `title`, `description` (Text), `theme`, `custom_lore` (Text), and drops `name`; `created_at` stays. Account FK import already exists.
- `backend/app/store/campaigns.py` -- NEW: `create_campaign(owner_id, title, description, theme, custom_lore)`, `list_campaigns(owner_id, cursor, limit)` (cursor-paginated, rowid order), `get_campaign(owner_id, campaign_id) -> Campaign | None` (owner-scoped — None for both unknown and foreign), `update_campaign(owner_id, campaign_id, **seed) -> Campaign | None`, `delete_campaign(owner_id, campaign_id) -> bool` (owner-scoped; cascades via the model relationships / explicit deletes in one `session_scope`). Replaces the current `create_campaign(name)` in `commit.py` — migrate that helper's callers (tests) to the new signature or a thin legacy shim is NOT used (clean cutover).
- `backend/app/store/db.py` -- `_migrate_campaign_seed(engine)` additive columns for legacy DBs (idempotent, mirrors `_migrate_job_result`); the legacy `name` column is dropped in the same migration only when empty (greenfield-safe).
- `backend/app/store/__init__.py` -- export the campaign CRUD surface; the old `create_campaign(name)` re-export is removed.
- `backend/app/api/campaigns.py` -- NEW router: `POST /api/campaigns`, `GET /api/campaigns` (cursor + limit), `GET /api/campaigns/{id}`, `PATCH /api/campaigns/{id}`, `DELETE /api/campaigns/{id}`; every endpoint depends on `get_current_account` (1.5); Pydantic schemas for the seed (`CampaignCreate` all-required, `CampaignUpdate` all-optional-with-min-1, `CampaignDelete` `confirm: bool`); envelope mapping 401/404/400/422.
- `backend/app/api/__init__.py` may be touched only if its docstring lists route groups — otherwise untouched.
- `backend/app/main.py` -- include `campaigns.router`.
- `backend/tests/test_campaigns.py` -- NEW store-level: create/list/get/update/delete owner-scoping + the AR20 cascade (seed a world with a commit + a job + a media row, delete, assert all gone in one transaction, and foreign-owner ops return None).
- `backend/tests/test_campaigns_api.py` -- NEW API-level: auth 401 on every route; create 201 + owner binding; theme 422; list pagination + empty; foreign 404; update validation; delete confirm-gate + cascade; the generic-401 envelope reuse from 1.5.
- `backend/tests/test_deploy_contract.py` -- extend: assert `deploy/config.toml [campaigns].themes` matches the code's seed list (both contain High Fantasy/Grimdark/Steampunk/Planar).

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/store/models.py` -- extend Campaign (owner_id, title, description, theme, custom_lore) + drop name -- AR27 seed model.
- [x] `backend/app/store/campaigns.py` -- NEW owner-scoped CRUD + AR20 cascade delete -- AD-9/AR20.
- [x] `backend/app/store/commit.py` -- remove the old `create_campaign(name)` and migrate its callers (tests) -- clean cutover.
- [x] `backend/app/store/db.py` -- `_migrate_campaign_seed` additive migration + name-drop when empty -- legacy DBs keep working.
- [x] `backend/app/store/__init__.py` -- export the campaign surface.
- [x] `backend/app/api/campaigns.py` -- NEW authenticated CRUD routes + schemas + envelope mapping.
- [x] `backend/app/main.py` -- include the campaigns router.
- [x] `backend/tests/test_campaigns.py` + `test_campaigns_api.py` -- I/O matrix + AC coverage incl. the cascade.
- [x] `backend/tests/test_deploy_contract.py` -- theme-list pin.

**Acceptance Criteria:**
- Given I am logged in, when I create a world with title, description, a theme from the open user-extendable list (seed: High Fantasy, Grimdark, Steampunk, Planar), and a custom-lore string, then the campaign is created and is private to me (AR27, AD-9, NFR6).
- Given an existing world, when I list, update, or delete it, then only my own worlds are visible and deletion is a total hard delete with confirmation (AR20).
- Given a world with a theme + custom lore, when it is saved, then those values are persisted to flow into the Epic 2 build-in and generation prompts (AR27).

## Spec Change Log

<!-- Append-only. Populated by step-04 during review loops. Do not modify or delete existing entries.
     Each entry records: what finding triggered the change, what was amended, what known-bad state
     the amendment avoids, and any KEEP instructions (what worked well and must survive re-derivation).
     Empty until the first bad_spec loopback. -->

### Loop 1 (2026-08-30, review round 1)

**Findings fixed (all patch — no intent gap):**
- The DELETE confirmation shipped as a `?confirm=` query param during
  implementation, silently deviating from the frozen body contract
  (`DELETE` with JSON `{"confirm": true}`). Restored: the route now
  echoes the body via `await request.json()` and honors the matrix —
  missing/false confirm -> 400, foreign id -> 404 regardless of confirm.
- CREATE_BLANK unimplemented: pre-trim `min_length` let `"   "` persist
  as an empty title. Store now rejects blank-after-trim title/theme
  (422) and the API maps it.
- PATCH with an explicit `null` field was a 500 (`None.strip()`); now a
  422 with nothing changed.
- `list_campaigns` cursor was neither owner-scoped nor validated — a
  foreign/deleted cursor silently reset the page or acted as an
  existence oracle (NFR6). Now raises ValueError -> 422, mirroring
  jobs' `_after_rowid`.
- The seed migration's `name`-drop was non-retryable and its additive
  ALTERs were not per-column idempotent; hardened, and the legacy
  migration is now pinned by a real test (previously the only legacy
  test passed even if the migration were deleted).
- Tests added: CREATE_BLANK 422, PATCH-null 422, LIST_EMPTY, foreign/
  deleted cursor rejection, API delete body contract incl. type-coercion
  guard, migration column assertion.

**KEEP (survive re-derivation):** foreign/unknown -> single 404;
owner-scoped CRUD; AR20 cascade in one transaction; theme seed pinned
by deploy-contract test; DELETE requires a body `{"confirm": true}`.

## Design Notes

Deletion is cascade-through-the-store: one `session_scope`, one transaction — delete the campaign row, then revisions/events/entities/edges/jobs/media for that campaign in dependency order, and commit. Any error rolls the whole delete back (AR20). This is the only place in the codebase that deletes world rows (the store's commit path is append-only for the graph; delete is a campaign-scoped, confirmed, total removal — the "history ends" boundary).

The foreign-owner 404 is deliberate and absolute: `get_campaign`/`update_campaign`/`delete_campaign` return None (or False) for both unknown and foreign ids, and the API maps that single result to a single 404 — a DM probing for another's world can't distinguish "doesn't exist" from "not yours" (NFR6, AD-9).

Theme validation is case-insensitive and the seed list lives in one place (`store/campaigns.py` `SEED_THEMES`), mirrored by `deploy/config.toml [campaigns].themes` and pinned by a deploy-contract test — 1.7 consumes config.toml and removes the mirror.

Campaign creation deliberately does not write an event: the seed fields describe the world's identity/config (AR27), not graph deltas; the world graph stays empty until build-in commits (Epic 2).

## Verification

**Commands:**
- `uv run --directory backend pytest -q` -- expected: all campaign tests + full suite green (deterministic).
- `make lint && make typecheck` -- expected: ruff, mypy strict, eslint, vue-tsc clean (backend only).

## Suggested Review Order

**The store (owner-scoped CRUD + cascade)**

- AR27 seed fields, blank/null validation, structured theme errors
  [`campaigns.py:44`](../../backend/app/store/campaigns.py#L44)

- Owner-scoped cursor pagination — foreign/deleted cursor -> 422 (NFR6)
  [`campaigns.py:84`](../../backend/app/store/campaigns.py#L84)

- AR20 total hard delete — one transaction, cascades all world rows
  [`campaigns.py:139`](../../backend/app/store/campaigns.py#L139)

**The API (auth-gated, envelope)**

- Authenticated CRUD; foreign/unknown -> single 404; theme/blank 422
  [`campaigns.py:74`](../../backend/app/api/campaigns.py#L74)

- DELETE with the frozen JSON-body confirm contract + ownership-first 404
  [`campaigns.py:155`](../../backend/app/api/campaigns.py#L155)

**Migration + tests**

- Per-column idempotent seed migration, name-drop only when empty
  [`db.py:116`](../../backend/app/store/db.py#L116)

- Cascade + owner-scoping + body-gate pinned
  [`test_campaigns.py`](../../backend/tests/test_campaigns.py)
  [`test_campaigns_api.py`](../../backend/tests/test_campaigns_api.py)
