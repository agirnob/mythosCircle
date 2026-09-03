---
title: 'World State Export — JSON + Markdown'
type: 'feature'
created: '2026-09-03'
status: 'done'
review_loop_iteration: 0
baseline_commit: 'e8fc9a7a5c2eac6eb9acc2b9319e8a38253078b5'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The DM's committed world state is locked inside the tool. FR5/AD-11 promise the full world state (all entities + typed edges + counters of the latest revision) is always exportable as JSON + Markdown, independent of VTT targets — the no-lock-in claim and Epic 5's VTT exports both depend on it.

**Approach:** Two read-only GET endpoints on a new `exports` router: full-world JSON (also the surface that finally exposes the current revision id, deferred from 2.5) and an Obsidian-level Markdown document. Both are pure projections of the latest revision via the store's read surface — export never writes (AR18).

## Boundaries & Constraints

**Always:**
- Read only via `app.store` read helpers (`world_state` / `world_entities` / `world_edges` / `latest_revision`); rowid-ordered, deterministic output.
- Export never mutates state: no revision, no event, no store write (AR18, AD-1).
- Ownership-first auth: foreign campaign is an indistinguishable 404 (campaign route pattern).
- JSON export carries `revision` (id + created_at of the latest revision) — this closes the 2.5 deferral ("base_revision unusable until revision ids are exposed").
- Markdown is Obsidian-level complete: YAML frontmatter (campaign title/theme/description, revision, export timestamp), one section per entity (kind, text, full `data` incl. stat block in a fenced yaml block), typed edges with per-type counters rendered as [[Name]] wikilinks from both endpoints, plus a world-level edge table. Emitting the same content twice yields byte-identical output.

**Ask First:**
- Any need to change the store's read surface (new read.py helper) beyond wiring existing helpers.
- Any export payload shape beyond what entities/edges/counters/stat blocks already carry.

**Never:**
- No pipeline, commit-path, or schema changes; no Epic 5 VTT-target exports (Owlbear/MapTool/Fantasy Grounds) here.
- No frontend in this story — REST surface only (the world view is 2.7).
- No media/portrait embedding (media ships in Epic 4; AD-11's portrait-embedded export is an Epic 4+ concern).
- No candidates/proposed entities in exports — only committed state (AR7: proposals invisible to export).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| HAPPY_PATH | Authed owner, campaign with committed world | JSON: `{campaign, revision, entities[], edges[]}` (200, application/json); Markdown equivalent (200, text/markdown, attachment disposition) | N/A |
| EMPTY_WORLD | Campaign with zero revisions/entities | Valid empty export: 200 with `revision: null`, empty arrays; Markdown frontmatter + no entity sections | N/A |
| FOREIGN_CAMPAIGN | Other owner's campaign id | Indistinguishable 404 envelope `not_found` | same as campaigns routes |
| UNAUTHENTICATED | No/invalid token | 401 via existing auth dependency | existing handler |
| READ_ONLY_INVARIANT | Any successful export | Snapshot of revision count/events before == after export; repeated export is byte-identical | N/A |
| STAT_BLOCK_PRESENT | Key figure entity with `data['stat_block']` | Stat block serialized verbatim (JSON) / as fenced yaml (Markdown) — no re-validation, export is a projection | N/A |

</frozen-after-approval>

## Code Map

- `backend/app/store/read.py` -- `latest_revision` (:15, rowid-ordered head), `world_entities` (:58), `world_edges` (:68), `world_state` (:92) — the entire read surface export needs; all rowid-ordered and read-only.
- `backend/app/store/models.py` -- Entity{id, campaign_id, kind, name, text?, data: JSON dict, created_at}, Edge{id, src, dst, type, counter, created_at} (unique on campaign+src+dst+type), Revision{id, campaign_id, base_revision?, created_at}. Entity/edge tables are the materialized latest-revision view.
- `backend/app/store/commit.py:26-63` -- EDGE_TYPES vocabulary + EDGE_COUNTER_SEMANTICS (debt=amount, grudge/loyalty=score, ally/enemy=intensity) — reference for rendering counters in Markdown; import, don't duplicate.
- `backend/app/api/campaigns.py` -- route pattern to mirror: module-level APIRouter, `get_campaign(current.id, campaign_id)` ownership → 404, try/except StoreError → mapper.
- `backend/app/api/entities.py` -- `Annotated[models.Account, Depends(get_current_account)]` idiom; confirm-body pattern (not needed here, reference only).
- `backend/app/api/common.py` -- `store_error_as_http` (:36) mapper; reuse for CampaignNotFound → 404.
- `backend/app/main.py` -- `create_app` include_router list — register the new exports router here.
- `backend/app/core/errors.py` -- ErrorEnvelope codes; 2xx responses are plain Pydantic/dicts, no envelope.
- `backend/tests/conftest.py` -- env pins before app import, per-test `client` fixture, TestClient CM; model for `test_export_api.py`.
- `backend/tests/test_campaigns_api.py` -- API test conventions: 404 no-oracle, envelope assertions.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/api/exports.py` -- new router: `GET /api/campaigns/{campaign_id}/export` with `format: Literal['json','markdown']` query (default `json`); JSON projection via a Pydantic response model (campaign meta, revision id+created_at, entities, edges with counters); Markdown projection rendering frontmatter + per-entity sections + edge table; attachment `Content-Disposition` for markdown.
- [x] `backend/app/main.py` -- import + include the exports router.
- [x] `backend/tests/test_export_api.py` -- I/O-matrix rows: happy JSON/Markdown, empty world, foreign 404, unauth 401, read-only invariant (no new revision), determinism (two exports byte-identical), stat-block passthrough.

**Acceptance Criteria:**
- Given any committed world, when the DM calls either export endpoint, then she receives the complete latest-revision state (all entities, typed edges, counters, stat blocks) and the store's revision/event count is unchanged afterward (FR5, NFR10, AR18).
- Given the Markdown export, when inspected, then every entity has a section with its data and stat block, every typed edge appears from both endpoints with its counter, and the document is valid Obsidian Markdown (frontmatter + [[wikilinks]]) (epic AC).
- Given a foreign or unknown campaign id, when exporting, then the response is the same 404 `not_found` envelope as the campaigns routes.

## Design Notes

- Renderer stays a pure function `dict -> str` inside `exports.py`: session assembly (store reads) separated from string building — the Markdown builder takes already-fetched rows, never a session.
- Determinism falls out of rowid ordering — do not add sorting beyond what read helpers already guarantee; keep dict insertion order stable.
- Markdown edge rendering: `[[Source Name]] --type(counter)--> [[Target Name]]` per line under the source entity's Relations heading; world-level table `| source | type | counter | target |` after the entity sections.
- Counter rendering uses EDGE_COUNTER_SEMANTICS: debt shows the amount, grudge/loyalty the score, ally/enemy intensity, others render bare.

## Verification

**Commands:**
- `uv run --directory backend pytest -q` -- expected: all pass, incl. new test_export_api.py
- `make lint && make typecheck` -- expected: clean

**Manual checks (if no CLI):**
- Inspect a sample Markdown export for Obsidian validity: frontmatter parses, wikilinks resolve to entity headings, stat block yaml fence is valid.

## Suggested Review Order

**Route + read-only contract**

- Entry point: ownership-first 404, snapshot re-check inside the txn, dual-format dispatch.
  [`exports.py:206`](../../backend/app/api/exports.py#L206)
- Router wiring — one include line.
  [`main.py:65`](../../backend/app/main.py#L65)

**Projection shapes**

- The full-export contract incl. `revision` (closes the 2.5 deferral) and `custom_lore`.
  [`exports.py:73`](../../backend/app/api/exports.py#L73)
- Deterministic snapshot timestamp — never wall-clock, the byte-identical basis.
  [`exports.py:196`](../../backend/app/api/exports.py#L196)

**Markdown renderer hardening**

- JSON-string frontmatter scalars: every control char round-trips as valid YAML.
  [`exports.py:80`](../../backend/app/api/exports.py#L80)
- Per-type counter semantics; neutral types render bare.
  [`exports.py:91`](../../backend/app/api/exports.py#L91)
- Non-finite float coercion — a read endpoint never 500s on its own data.
  [`exports.py:103`](../../backend/app/api/exports.py#L103)
- Duplicate-name disambiguation + wikilink-safe sanitization.
  [`exports.py:116`](../../backend/app/api/exports.py#L116)
- Pure-function renderer: dynamic fence sizing, wikilinked relations, edge table.
  [`exports.py:136`](../../backend/app/api/exports.py#L136)

**Tests (peripherals)**

- Frontmatter/fence parse-back helpers — the escaping contract, verified not substring-checked.
  [`test_export_api.py:131`](../../backend/tests/test_export_api.py#L131)
- Obsidian-completeness happy path.
  [`test_export_api.py:207`](../../backend/tests/test_export_api.py#L207)
- Multi-revision head exposure driving DELETE base_revision — the 2.5 closure end to end.
  [`test_export_api.py:329`](../../backend/tests/test_export_api.py#L329)
- Non-finite coercion + second neutral edge type.
  [`test_export_api.py:350`](../../backend/tests/test_export_api.py#L350)
