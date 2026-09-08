---
title: '5.1 Export Engine — Pure Projection'
type: 'feature'
created: '2026-09-08'
status: 'done'
baseline_commit: d1eb6caa7f1ddc5328e413add6576451dbd5aa95
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-5-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Export today is world-level JSON + plain Markdown only (spec-2.6/4.3). Epic 5 needs an entity-level projection every VTT target will consume, and the owner requires the default export to be a beautiful artifact: markdown stays pure and portable; a styled HTML twin carries the CSS and converts to PDF via the browser.

**Approach:** One shared latest-revision snapshot (the existing world read), two renderers per surface: Obsidian-complete Markdown (unchanged data philosophy) and self-contained print-ready HTML (embedded CSS, portrait inlined as a data URI). A format assertion failing in a renderer is a commit-path regression: logged as an `export_failure` event (FR18), never silently patched.

## Boundaries & Constraints

**Always:** Export is read-only — no revision, no event row, no store write (AD-1/AD-11). Renderers are pure functions of the fetched snapshot, byte-identical across repeats (the `_exported_at` snapshot-timestamp rule, never wall clock). Data renders verbatim: never re-validate, never drop unknown keys (AR24). HTML is self-contained: embedded CSS, zero external assets, no `<script>`. Markdown carries no inline `<style>` (Obsidian sanitizes HTML and does not render Markdown inside HTML elements — .md is the portable twin, .html the beautiful twin). Ownership 404s are the single indistinguishable shape (foreign == unknown). `format=markdown`/`html` serve as attachments.

**Ask First:** changing the shipped CSS visual language mid-build; embedding world-level portraits as data URIs (size).

**Never:** server-side PDF toolchains (pandoc/weasyprint/wkhtmltopdf); `[server].base_url` plumbing (HTML embeds images — no absolute URLs needed); VTT-specific formats (Owlbear/FG/RPGToken = 5.2–5.4); validation in the export path; `level_cr` format enforcement (its enforce-vs-loosen decision lands with 5.2, the first table-facing sheet).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| HAPPY entity | committed character | json / markdown / html 200; html embeds the available portrait as data URI; stat block + sections render from committed `data` | N/A |
| HAPPY world html | existing world | `format=html` attachment: per-entity sections, no embedded binaries, media listed with paths + availability | N/A |
| MISSING/FOREIGN entity | eid unknown, or foreign campaign | identical 404 (no oracle) | 404 envelope |
| BROKEN / HUGE media | file gone / > 4 MiB | not embedded; broken marker or path caption (same honesty rule as 4.3) | N/A |
| ODD data | no stat_block / unknown keys / non-finite floats | values render; unknowns survive verbatim in a `<details>` JSON appendix; `_finite_only` coercion applies | N/A |
| RENDER FAILURE | renderer raises (simulated) | one `export_failure` log line with campaign/entity/format + traceback; request becomes the generic 500 | FR18 |
| BAD FORMAT | format=pdf | 422 | FastAPI validation |

</frozen-after-approval>

## Code Map

- `backend/app/api/exports.py` — snapshot assembly lives inside `export_world` (:269-342); extract a reusable builder. Reuse points: models (:50-96), `_render_markdown` (:172), `_name_labels` (:146), `_media_refs` (:246), `_exported_at` (:237), `_finite_only` (:123).
- `backend/app/store/read.py` — `latest_revision` (:15), `world_state` (:92). Single-entity read = build the world snapshot, pick the entity, filter touching edges (rowid order; neighbor labels from the full `_name_labels` map).
- `backend/app/pipeline/statblocks.py` (:94-135) — the committed `data.stat_block` shape (identity/attributes/combat/skills/actions/traits/spells) the HTML layout mirrors; read-only reference.
- `backend/app/core/logging_setup.py` — JSON-lines handler; `export_failure` = one `logger.error` line with `exc_info` (pattern: `errors.py:114`).
- `backend/tests/test_export_api.py` — fixtures/helpers to extend: `_register_login`, `_commit_world`, `_parse_frontmatter`, read-only + byte-identical patterns.
- `frontend/src/views/WorldView.vue` — `portraitUrl` (:292) is the same-origin cookie-GET pattern; world header (:872+) and entity cards are the button homes.
- `deploy/config.toml` — `[server].base_url` exists but is NOT parsed by `core/config.py` — deliberately unused here.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/api/export_sheets.py` -- NEW pure-render module: shared `SHEET_CSS` (`@page` A4/Letter margins in physical units, `print-color-adjust: exact`, `break-inside: avoid` + legacy alias on cards/stat block, `break-after: avoid` on headings, block wrappers with inner flex, system serif stack, Monster-Manual-ish stat-block panel); `render_entity_markdown(export, entity_id)`, `render_entity_html(...)`, `render_world_html(export)`; portrait data-URI embedding ≤ 4 MiB with broken/huge markers; unknown keys in `<details>` appendix. No session access.
- [x] `backend/app/api/exports.py` -- extract the snapshot builder; add `format=html` to the world route; add `GET /api/campaigns/{campaign_id}/entities/{entity_id}/export?format=json|markdown|html` (ownership-404 first, re-check inside the snapshot like `export_world`; json → new `EntityExportDetail{entity, edges, revision}`); renderer calls wrapped: on Exception log `export_failure` (campaign/entity/format, exc_info) and re-raise → generic 500.
- [x] `backend/tests/test_export_api.py` -- matrix rows: entity ×3 formats, 404 parity, broken/oversized media, unknown-key appendix + non-finite survival, render-failure caplog → 500, byte-identical repeats, read-only invariant, 422, world html attachment.
- [x] `frontend/src/views/WorldView.vue` + `WorldView.test.ts` -- per-entity "Markdown" / "Sheet (HTML)" download anchors (same-origin GET, `download` attr); world header "Markdown" / "HTML" anchors; test hrefs carry campaign/entity ids.
- [x] `frontend/src/api/schema.ts` -- regenerate via `npm run gen:api` against the restarted dev api so the new route + `EntityExportDetail` land in the wire types.

**Acceptance Criteria:**
- Given a committed entity, when exported as markdown or html, then it is a pure projection — SRD stat block, sectioned data, typed edges with counters, and media refs render exactly as committed, and repeated calls are byte-identical.
- Given the same entity, when the html is opened offline and printed, then styling survives (backgrounds, stat-block panel, no split cards) without any network fetch.
- Given any export render failure, when it occurs, then exactly one `export_failure` JSON-lines event is logged and the response is the generic 500 envelope — world state untouched in both cases.
- Given the Markdown twin, when opened in Obsidian, then frontmatter parses and wikilinks/headings/tables stay intact (no inline `<style>`).

## Design Notes

Research (owner's research-before-spec rule, 2026-09-08): obsidian.md/help/html — HTML in notes is sanitized; Markdown is not rendered inside HTML elements → no styling in the .md. Print CSS (docweave.dev/blog/css-for-print-styling-pdfs, MDN): browsers strip print backgrounds unless `print-color-adjust: exact`; `break-inside: avoid` no-ops on over-tall blocks; physical units in `@page`; flex is flaky at break boundaries → block wrappers. Stat-block look = static-CSS take on the statblock5e/D&D visual language (parchment panel, dark-blue double rules), no scripts. Self-contained HTML + browser print is the zero-dependency PDF path.

## Verification

**Commands:**
- `uv run --directory backend pytest -q` -- expected: all green incl. new entity/sheet rows, no regressions.
- `uv run --directory backend ruff check . && uv run --directory backend mypy app` -- expected: clean.
- `bash -c 'cd frontend && npm run test && npm run typecheck'` -- expected: green incl. WorldView hrefs.
- Manual: open a downloaded entity .html offline, Ctrl+P → PDF preview keeps panel colors and card integrity.

## Suggested Review Order

**Engine & routes**

- The entity surface: ownership-404 first, shared snapshot, three formats
  [`exports.py:300`](../../backend/app/api/exports.py#L300)
- One snapshot builder for both routes; the mid-request delete guard
  [`exports.py:154`](../../backend/app/api/exports.py#L154)
- FR18 hook: one export_failure log event, then the generic 500
  [`exports.py:211`](../../backend/app/api/exports.py#L211)
- Deterministic human-readable download names (slug + ULID tail)
  [`exports.py:246`](../../backend/app/api/exports.py#L246)

**Styled renderers**

- The print contract lives here: @page auto, color-adjust, break rules
  [`export_sheets.py:54`](../../backend/app/api/export_sheets.py#L54)
- Pure-projection layout: identity consumption, appendix scoped to sheets
  [`export_sheets.py:466`](../../backend/app/api/export_sheets.py#L466)
- Hero: stat()-before-read; portrait-parity divergence deferred w/ retention
  [`export_sheets.py:412`](../../backend/app/api/export_sheets.py#L412)
- Attribute-escaped alt — the quote-breakout patch
  [`export_sheets.py:454`](../../backend/app/api/export_sheets.py#L454)
- Single-escape rule for stat-block list entries
  [`export_sheets.py:339`](../../backend/app/api/export_sheets.py#L339)
- Entity .md twin: plain neighbor names, no wikilinks, no inline style
  [`export_sheets.py:268`](../../backend/app/api/export_sheets.py#L268)
- World .html: no embedded binaries, no per-card appendix
  [`export_sheets.py:564`](../../backend/app/api/export_sheets.py#L564)

**Frontend**

- Same-origin cookie-authenticated download URLs
  [`WorldView.vue:302`](../../frontend/src/views/WorldView.vue#L302)
- World + per-entity export anchors
  [`WorldView.vue:909`](../../frontend/src/views/WorldView.vue#L909)

**Peripheral**

- The 5.1 I/O matrix as tests (incl. the review-round patches)
  [`test_export_api.py:609`](../../backend/tests/test_export_api.py#L609)
- Anchor href pins
  [`WorldView.test.ts:1546`](../../frontend/src/views/WorldView.test.ts#L1546)
- schema.ts regenerated; fixtures gained the required media field
- Two defers logged: OpenAPI binary-response drift; hero/portraitFor parity
