---
title: '5.2 Owlbear Export (First Target)'
type: 'feature'
created: '2026-09-09'
status: 'done'
baseline_commit: 'f132bd7'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-5-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Committed characters cannot reach Owlbear Rodeo — the DM re-types stat blocks by hand, and portrait files sit behind session-cookie auth the VTT cannot present.

**Approach:** A `format=owlbear` adapter over the 5-1 entity projection emitting Forge's `{name, author, metadata}` transfer payload (BID map from the live AI Template dictionary), plus HMAC-signed expiring portrait URLs a DM pastes into Forge's per-unit portrait override.

## Boundaries & Constraints

**Always:** Export stays read-only — no revision, no event row, no store write (AD-1/AD-11); renderer pure of the snapshot, byte-identical across repeats (`_exported_at` rule). Ownership 404s stay the single indistinguishable shape. Level/CR derive from `stat_block.identity` numerics (owner verdict: top-level `level_cr` is display-only). Numerics coerce to JSON numbers (Forge `numb`); unmapped fields are omitted — sparse payloads import validly. List-entry ids are index-stable (`{entity-id-8}-{i}`), never fresh ULIDs. Signature failures use the same 404 shape (no oracle). Secret is env-only (`MYTHOSCIRCLE_MEDIA_URL_SECRET`), never config.toml/client (AD-22).

**Ask First:** changing the BID table below (it is measured from the live default-5e dictionary, not memory); including `fabd: true` differently (observed `true` in real Forge exports); expiry other than 7 days.

**Never:** VTT formats beyond Owlbear/Forge (FG/MapTool = 5.3–5.4); validation in the export path; embedding portrait binaries in the payload (references only, AD-10/AD-11); keep-5 pruning, wave-2 orphan re-prompt, regenerate power wiring (separate verdicts, separate specs); `level_cr` format enforcement.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| HAPPY NPC | committed NPC, portrait available | `.json` attachment `{name, author, metadata}`; saves Z001/Z003–Z007/Z014/Z017–Z028/Z034/Z035/Z039 per table; portrait-url route returns `{url, expires_at}` | N/A |
| HAPPY Monster | committed Monster (cr "1/2") | Z016 `0.5`, Z001 omitted; spells omitted when absent | N/A |
| NO portrait | entity with no available image | payload still 200; portrait-url route 404 (same shape) | 404 envelope |
| SIG ok | unsigned client GETs signed URL | file served `inline`, no session needed | N/A |
| SIG bad/expired | tampered sig / past exp | identical 404 (no oracle) | 404 envelope |
| NO secret | env unset | portrait-url route 500 + one error log line | generic 500 |
| MISSING/FOREIGN | unknown eid or campaign | identical 404 | 404 envelope |
| RENDER FAILURE | renderer raises (simulated) | one `export_failure` line, generic 500 | FR18 |

</frozen-after-approval>

## Code Map

- `backend/app/api/exports.py` — `export_entity` (:296-345): add `"owlbear"` to the format Literal + branch returning `_attachment(render_owlbear, .json, application/json)`; snapshot/404/failure patterns reused verbatim.
- `backend/app/core/config.py` -- parse `[server].base_url` (already in `deploy/config.toml`, 5-1 left it unused) with env override per the `MYTHOSCIRCLE_*` convention; the mint route builds absolute URLs from it.
- `backend/app/api/media.py` — `get_file` (:87-129): accept optional `exp`/`sig` query params; valid signature bypasses `get_current_account` (row + disk checks unchanged). NEW `portrait_url` route minting `{url, expires_at}` for the newest AVAILABLE image row (hero rule, `export_sheets.py:412` precedent); missing row → 404, missing secret → 500 + log.
- `backend/app/core/settings.py` + `backend/app/core/config.py` — NEW `MYTHOSCIRCLE_MEDIA_URL_SECRET` env-only secret (API-key convention, `settings.py:56`); HMAC-SHA256 over `{cid}.{eid}.{filename}.{exp}`, `hmac.compare_digest`.
- `backend/app/pipeline/generate.py` (:420-424) + `backend/app/pipeline/build_in.py` (:99-101) — loosen `level_cr` prompt wording to display-only (identity numerics authoritative); no validator change.
- `backend/tests/test_export_api.py` — extend matrix: BID pins, sparse omits, stable ids, sig verify/tamper/expiry, no-session fetch, byte-identical, read-only.
- `frontend/src/views/WorldView.vue` — Owlbear download anchor + portrait-URL copy field beside the Markdown/Sheet anchors (`portraitUrl` pattern :319-323); `schema.ts` regen via `npm run gen:api`.


**Execution:**
- [x] `backend/app/api/export_sheets.py` -- `render_entity_owlbear` pure mapper per BID table; numeric coercion, CR-fraction→decimal, saves as `floor((score-10)/2)`, stable entry ids -- the Forge payload.
- [x] `backend/app/api/exports.py` -- `format=owlbear` branch on the entity route (attachment `{stem}.json`, `application/json`); FR18 hook reused.
- [x] `backend/app/api/media.py` + `core/settings.py`/`config.py` -- NEW `GET /api/campaigns/{cid}/entities/{eid}/portrait-url` → `{url, expires_at}` (owner-only; absolute URL from configured `base_url`) + `exp`/`sig` verification branch on `get_file`; env-only secret; 404-parity on all signature failures.
- [x] `backend/app/pipeline/generate.py`, `build_in.py` -- `level_cr` prompt wording to display-only (owner verdict).
- [x] `backend/tests/test_export_api.py` -- full I/O matrix rows incl. tamper/expiry/no-session/byte-identical/read-only.
- [x] `frontend/src/views/WorldView.vue` + `schema.ts` -- Owlbear anchor + portrait-URL copy field; regen against restarted dev api.

**Acceptance Criteria:**
- Given a committed character, when exported as owlbear, then pasting the file into Forge Import populates every mapped field with committed values, and the portrait URL pasted into the override shows the portrait.
- Given the signed portrait URL, when fetched without a session, then the file serves inline; tampered or expired, then the campaign-missing 404.
- Given repeated exports, when compared byte-for-byte, then identical (entry ids stable, no wall clock).
- Given the full suite + lint + typecheck, when run, then green.

## Spec Change Log

## Design Notes

BID table (measured from the live Forge default-5e AI Template dictionary, 2026-09-09; `com.battle-system.forge/` prefix on all keys): `name` ← entity.name; `author` ← campaign.title; `fabd: true` (observed constant); Z001 ← `identity.level` (NPC/BBEG, else omit); Z003 ← record `alignment`; Z004 ← record `race_type`; Z005/Z006 ← `combat.hp`; Z007 ← `combat.ac`; Z014 ← skills as `"name +bonus"` join; Z016 ← `identity.cr` with fraction→decimal (Monster, else omit); Z017–Z022 ← attributes; Z023–Z028 ← derived saves; Z034 ← traits, Z035 ← actions, Z038 ← `boss.legendary_actions` if present, Z039 ← spell names with `""` descriptions; Z040 ← record `equipment` if a name/description list else omit. Everything else omitted (speeds, senses, languages, resistances, proficiency, Z036/Z037 bonus/reactions — no stored source; sparse imports validly). DM flow: download `.json` → Forge Import paste; copy portrait URL → Party-view portrait override. Under a minute, table-ready.

## Verification

**Commands:**
- `uv run --directory backend pytest -q` -- expected: all green incl. new owlbear/signature rows.
- `uv run --directory backend ruff check . && uv run --directory backend mypy app` -- expected: clean.
- `bash -c 'cd frontend && npm run test && npm run typecheck'` -- expected: green incl. new anchor pins.
- Manual: import a downloaded `.json` into Forge (default 5e), paste portrait URL into override — unit appears table-ready.

## Suggested Review Order

**Forge payload**

- Pure BID mapper — the measured dictionary as code
  [`export_sheets.py:735`](../../backend/app/api/export_sheets.py#L735)
- Numeric coercion incl. CR fractions, sparse omits
  [`export_sheets.py:667`](../../backend/app/api/export_sheets.py#L667)
- Index-stable list entry ids
  [`export_sheets.py:711`](../../backend/app/api/export_sheets.py#L711)

**Entity route**

- owlbear branch reusing the FR18 attachment hook
  [`exports.py:339`](../../backend/app/api/exports.py#L339)

**Signed portrait URLs**

- Mint route — hero rule, 7-day TTL, placeholder warning
  [`media.py:163`](../../backend/app/api/media.py#L163)
- Signature branch — every failure is the campaign 404
  [`media.py:209`](../../backend/app/api/media.py#L209)
- HMAC construction over cid.eid.filename.exp
  [`media.py:120`](../../backend/app/api/media.py#L120)
- Env-only secret + empty-safe base_url fallback
  [`settings.py:400`](../../backend/app/core/settings.py#L400)

**Prompt loosening**

- level_cr display-only in the generate prompt
  [`generate.py:418`](../../backend/app/pipeline/generate.py#L418)
- Same wording in the build-in record prompt
  [`build_in.py:97`](../../backend/app/pipeline/build_in.py#L97)

**Frontend**

- Download anchor + one-line Forge workflow hint
  [`WorldView.vue:1080`](../../frontend/src/views/WorldView.vue#L1080)
- Mint / copy / re-copy flow with inline errors
  [`WorldView.vue:347`](../../frontend/src/views/WorldView.vue#L347)

**Peripherals**

- The 5-2 I/O matrix as tests incl. review-round pins
  [`test_export_api.py:1094`](../../backend/tests/test_export_api.py#L1094)
