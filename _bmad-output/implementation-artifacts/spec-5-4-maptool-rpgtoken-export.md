---
title: '5.4 MapTool / RPGToken Export (Third Target)'
type: 'feature'
created: '2026-09-14'
status: 'draft'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-5-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/research-5-4-maptool-token.md'
  - '{project-root}/_bmad-output/implementation-artifacts/research-5-3-fantasy-grounds.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Committed 5e characters cannot reach MapTool — the only token-file surface the client has is its own `.rptok` ZIP (XStream-serialized `content.xml` + embedded image assets), a shape never verified for hand authoring and previously unbuilt ("RPGToken JSON" in the epic is not a shipped format — research-5-4 §1).

**Approach:** A `format=maptool` adapter over the 5-1 entity projection emitting a **deterministic** `.rptok` ZIP: `content.xml` (Token fields verified from MapTool source; exact XStream wrappers pinned from a real gold file), `properties.xml` (version `1.18.6`), and `assets/<md5>` descriptor + raw PNG portrait bytes. The 5e stat block (2024-Core grammar, research-5-3 §2d) + race/type/alignment live in `notes`; the AR24 lore lives in `gmNotes`. The DM downloads the file and drags it onto an open map.

## Boundaries & Constraints

**Always:** Export stays read-only — no revision, no event row, no store write (AD-1/AD-11); renderer pure of the snapshot. Repeated exports are **byte-identical**: ZIP with fixed member timestamps (e.g. 1980-01-01), pinned `compress_type`, no member `extra` fields, stable entry order, no wall clock; the token `id` GUID is **derived** from the entity id — `base64(sha256(entity_id)[:16])` serialized as `<id><baGUID>…</baGUID></id>`, the exact on-disk shape from the owner's real 1.18.6 export (never random). `notesType`/`gmNotesType` are emitted explicitly (`text/plain` — the real-file default for our plain-text carrier; `text/html` is an Ask First). Portrait embedding: newest AVAILABLE `kind=image` row (rowid order, `_hero_portrait_src` precedent) whose on-disk file is ≤ `MAX_INLINE_BYTES` and starts with the PNG magic; asset path = `assets/<md5>` (Asset descriptor XML: id/name/extension/type) + `assets/<md5>.png` where `<md5>` = 32-lowercase-hex MD5 of the PNG bytes (MD5Key.java). Broken/absent portrait → **no image fields at all** in `content.xml` (MapTool falls back to its default image) and an honest marker in `notes` (`Portrait: <filename> [missing]` — the 5-3 honesty rule; no marker when there is no media at all). All text XML-escaped; stat-block text uses the verified 2024-Core Import-Text grammar lines (research-5-3 §2d) — the FG paste path stays available by copy. `tokenType = NPC`; notes/GM-notes/lore content verbatim from committed state. Renderer failure → exactly one `export_failure` log event + generic 500 (FR18) — never validation in the export path.

**Ask First:** Installing Java + MapTool 1.18.6 on the dev box (`java` is not installed) so the implementer can produce the formal gold `.rptok` and run the live round-trip — the alternative is the owner producing it on their machine (FG precedent); emitting attack/spell **macro buttons** into `macroPropertiesMap` (persists through import — verified in the owner's export; requires derived macro UUIDs for determinism + a command-grammar decision: bare `[4d6+5]` vs full hit-check via `!tokenprop`); emitting structured stats into `propertyMapCI` (persists, but its display is campaign-dependent); switching `notesType`/`gmNotesType` to `text/html`; adding thumbnails/`charsheetImage`; anything beyond the gold-pinned shape.

**Never:** the JSON token format (does not exist — research-5-4 §1); embedding non-PNG or oversized images or a live signed URL (breaks determinism — the signed `portrait-url` route stays presentation-only); random GUIDs; validation/re-validation in the export path; VTT targets beyond MapTool; the 5-5 kill-criterion demo; media retention (KEEP-5 story); guessing XStream wrappers instead of pinning them from the gold file.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| HAPPY NPC | committed NPC, stat_block, portrait available | `{stem}.rptok` (`application/zip`) whose content.xml carries name, `tokenType=NPC`, `notes` = identity header + 2024-Core stat lines, `gmNotes` = AR24 lore, `imageAssetMap` single null-key entry → `<net.rptools.lib.MD5Key>` of the embedded PNG (the owner's real export carries **no** `<portraitImage>` element — portrait rides the null-key image); `assets/<md5>` + `.png` present and byte-consistent | N/A |
| MACROS/PROPERTIES DEFERRED | any entity | no `macroPropertiesMap`, no `propertyMapCI` emitted by default (Ask First surfaces) — token still imports with defaults; notes stays the sole stat carrier | N/A |
| BROKEN portrait | entity whose image rows are all unavailable | no image fields in content.xml; notes carry `Portrait: <filename> [missing]`; file still imports (default MapTool image) | N/A |
| NO media | entity with no media rows | no image fields, no marker line | N/A |
| NO stat_block | place/faction entity | minimal token: name + kind + gmNotes; notes carry only the identity header / nothing | N/A |
| Monster | committed Monster, `cr "1/2"`, fraction→decimal precedent | Challenge line derives from `stat_block.identity` numerics (level for NPC/BBEG, CR decimal for Monster — same derivation as fg) | N/A |
| MISSING/FOREIGN | unknown entity or campaign id | identical 404 | 404 envelope |
| BAD FORMAT | `format=weird` | OpenAPI 422 before auth ordering (existing route pattern) | 422 envelope |
| RENDER FAILURE | renderer raises (simulated) | one `export_failure` log line, generic 500 | FR18 |
| ESCAPING | `&<>"` in name/text/traits | valid XML, values round-trip | N/A |
| DETERMINISM | same entity exported twice | byte-identical zips: fixed timestamps, stable order, derived GUID | N/A |
| LIVE IMPORT | generated `.rptok` dragged onto a MapTool 1.18.6 map | token lands with name, notes, GM notes, portrait image (owner live check at acceptance; gold fixture committed) | N/A |

</frozen-after-approval>

## Code Map

- `backend/app/api/exports.py` — `export_entity` `format` Literal (`"json","markdown","html","owlbear","fg"`) + one `if format == "fg"` branch (attachment `{stem}.xml`, `text/xml`) — add `"maptool"` + mirror branch; `_attachment(render, …)` (L~118) wraps any render result in Content-Disposition + FR18 logging; `_download_stem` gives the deterministic `{stem}`; render lambda may return `bytes` (Response body is untyped) — the ZIP bytes path
- `backend/app/api/export_sheets.py` — the pure renderers; mirror `render_entity_fg` (L~1060): add `render_entity_maptool(export, entity_id) -> bytes`
  - Reuse (fg/owlbear family): `_fg_text`, `_forge_number`, `_fg_ability_score`, `_ABILITY_ORDER`, `_STAT_BLOCK_ORDER`, `_fg_lore_paragraphs` (L~1044 — labelled AR24 paragraphs for `gmNotes`), `_forge_number` CR/level derivation; `_hero_portrait_src` (L~500) is the disk-bytes precedent (stat-before-read, `MAX_INLINE_BYTES` L39, OSError → None)
  - `stat_block` slots read exactly like the fg renderer: `block.identity.{role,level,cr,race,alignment,size}`, `block.combat.{ac,armor_class,hp,hit_points,hit_dice,speed}`, `block.attributes.*`, `block.saves.*`, `block.skills` (sign join), `block.{senses,languages,damage_*,condition_immunities}`, `block.traits/actions` (text lines), `block.spells` (names)
- `backend/app/media/service.py` — `PNG_SIGNATURE` constant (~L90) for the magic check
- `backend/app/core/settings.py` — `configured_media_dir` (L~425), `media_url_secret`, `configured_base_url` (presentation only here)
- `backend/app/api/media.py` — existing `portrait_url` mint (7-day TTL) — untouched; presentation only
- `_bmad-output/implementation-artifacts/research-5-4-maptool-token.md` — verified field inventory (§2c), file layout (§2b), worked example (§3); §5 flags the wrapper shapes to pin
- `_bmad-output/implementation-artifacts/research-5-3-fantasy-grounds.md` — §2d: the 2024-Core stat-block text grammar (line contract for `notes`)
- `backend/tests/test_export_api.py` — fg test block (L~1206-1403) + FR18 tests (L~884, L~1395); inline entity fixtures (`_SERA_DATA`/`_GNASHER_DATA`, `_commit_owlbear_cast`) — mirror for the maptool rows; golden round-trip against the committed gold fixture
- `frontend/src/views/WorldView.vue` — `entityExportUrl(entityId, 'markdown'|'html'|'owlbear'|'fg')` (L~437) union + the export `<a>` link row (L~1261-1268, "Fantasy Grounds" link + hint text) — add `'maptool'` + "MapTool (rptok)" link; WorldView.test.ts pins the row
- `frontend/src/api/schema.ts` — auto-generated; `npm run gen:api` against the live server picks up the new Literal member; commit the regen
- Fixture (committed this iteration): `_bmad-output/implementation-artifacts/fixture-maptool-token-1_18_6-owner-export/` — the owner's real MapTool 1.18.6 export, unzipped (`properties.xml` pins version `1.18.6`): content.xml (full technical field set + imageAssetMap null-key + propertyMapCI + macroPropertiesMap), asset descriptor + PNG pair, thumbnails. THE wrapper-shape reference. Formal gold `.rptok` + `…-import-roundtrip.rptok` (our export re-saved by MapTool) land at acceptance

## Tasks & Acceptance

**Execution:**
- [ ] `backend/app/api/exports.py` — add `"maptool"` to the export_entity format Literal + `_attachment` branch (`{stem}.rptok`, `application/zip`) — single consumer point, mirrors `format == "fg"`
- [ ] `_bmad-output/implementation-artifacts/` — mirror the committed provisional fixture (`fixture-maptool-token-1_18_6-owner-export/`) in the renderer FIRST: content.xml element set + wrapper shapes by copy, not guess; freeze the shape only after the acceptance round-trip — fixture-first, the 5-3 pattern
- [ ] `backend/app/api/export_sheets.py` — implement `_rptok_stat_block_text(block, data) -> str | None` — the 2024-Core grammar lines (research-5-3 §2d) from committed stat_block/identity — text for `notes`
- [ ] `backend/app/api/export_sheets.py` — implement `render_entity_maptool(export, entity_id) -> bytes` — deterministic ZIP (zipfile; fixed `date_time`, pinned `compress_type`, no `extra` fields, stable entry order): `content.xml` (Token XML mirroring the provisional fixture's element set: name, tokenType, notes, gmNotes, `notesType`/`gmNotesType` = `text/plain`, derived `<id><baGUID>…`, `imageAssetMap` null-key entry when embedding), `properties.xml` (`version` = `1.18.6`, `herolab` = false), `assets/<md5>` + `assets/<md5>.png`; portrait rides the null-key image only (no `<portraitImage>` element per the owner's export); FIPS-safe MD5 (`hashlib.new("md5", data, usedforsecurity=False)`); missing-portrait rules per the matrix
- [ ] `backend/tests/test_export_api.py` — maptool rows: zip structure + content.xml parse, element/value inventory vs the gold fixture's tag set, asset md5 consistency, notes grammar lines, gmNotes verbatim, escaping, missing/broken portrait, 404/422 shapes, FR18 event, byte-identical determinism (incl. re-mint scenario) — pins the live-verified contract
- [ ] `frontend/src/views/WorldView.vue` + test — add `'maptool'` to the union + the "MapTool (rptok)" link + hint text
- [ ] `frontend/src/api/schema.ts` — regen + commit

**Acceptance Criteria:**
- Given a committed NPC with stat_block and portrait, when the DM downloads the maptool export, then the file is `{stem}.rptok` (`application/zip`) whose content.xml element set is a subset of the gold fixture's, whose values match the committed data, and whose embedded PNG round-trips.
- Given the same entity exported twice, then both files are byte-identical (fixed zip timestamps, stable order, derived GUID — no wall clock).
- Given a renderer exception, then exactly one `export_failure` event is logged and the response is the generic 500 (FR18).
- Given an entity with no usable portrait, then content.xml has no image fields and notes carry the `[missing]` marker (or no marker when there is no media), and the zip still parses.
- Given the generated rptok dragged onto a MapTool 1.18.6 map (owner live check at acceptance), then the token lands with name, visible stat-block notes, GM-only lore, and the portrait image; the round-trip re-save is committed as the second fixture.
- Given an unknown entity or campaign, then the response is the single indistinguishable 404; `format=weird` is a 422.

## Spec Change Log

<!-- Append-only. Populated by step-04 during review loops. -->

- `[review loop 0]` Corrected GUID on-disk encoding: `<baGUID>` base64 of 16 bytes, NOT 32-char uppercase hex (owner's real 1.18.6 export) — determinism design note, Boundaries Always, and renderer task updated; pin from the fixture. _Avoids:_ malformed token id that MapTool cannot parse.
- `[review loop 0]` Corrected the "only campaign-defined propertyType" justification: per-token `propertyMapCI` persists through import (`imported()` does not null it — source + owner's export); campaign-dependence is display-only. `propertyMapCI` added as an explicit deferred/Ask-First surface. _Avoids:_ a false premise underlying the carrier decision.
- `[review loop 0]` Token macros: `macroPropertiesMap` persists through import (legacy `macroMap` is the nulled one — source + owner's export). Macros moved from implied-impossible to explicit deferred/Ask-First with derived-UUID + command-grammar sub-decisions. _Avoids:_ ruling out a real surface without a decision.
- `[review loop 0]` Added FIPS-safe MD5 (`hashlib.new("md5", …, usedforsecurity=False)`) and pinned `compress_type`/no-`extra`/fixed timestamps to the renderer task; determinism scoped to one Python/zlib build. _Avoids:_ environment-dependent failures and a false cross-version byte-identity claim.
- `[review loop 0]` Added `notesType`/`gmNotesType` (`text/plain`) as explicit emitted fields — the on-disk MIME fields that control notes rendering (owner's export). _Avoids:_ silent rendering-mode ambiguity.
- `[review loop 0]` Promoted the owner's real export to a committed provisional reference fixture (`fixture-maptool-token-1_18_6-owner-export/`); listed confirmed wrapper shapes (GUID/MD5Key/imageAssetMap/Asset/propertyMapCI/macroPropertiesMap) and the technical field set. Portrait carrier pinned: `imageAssetMap` null-key only, no `<portraitImage>`. _Avoids:_ guessing shapes the gold gate must not re-derive.

## Design Notes

**Why `.rptok`, not JSON.** The epic's "RPGToken JSON" is not a shipped MapTool format — the JSON serializations that exist are a campaign-level add-on data store and a network DTO, neither importable as a token file (research-5-4 §1, source-linked). The only token-file loader is `PersistenceUtil.loadToken` on the ZIP `PackedFile`, and the only DM surface is drag-drop of the `.rptok` (verified: `TransferableHelper.handleURLList`). Per the owner directive (no shapes from memory), the file layout, field inventory, and portrait constraints are all source-cited in research-5-4.

**Determinism mechanics.** The 5-1 byte-identical invariant forces two choices: (1) the token `id` GUID is derived — `base64(sha256(entity_id)[:16])` — serialized exactly as the owner's real file shows: `<id><baGUID>…</baGUID></id>` (XStream writes the `byte[]` field; NOT the 32-hex Java ctor form — that conflation was corrected in review). Unique per entity, deterministic across exports, byte-identical repeats. (2) The ZIP is written with fixed member timestamps, an explicit `compress_type`, no member `extra` fields, and a fixed entry order. Deflate is deterministic for identical inputs — scoped to one Python/zlib build; byte-identity across dependency upgrades is NOT guaranteed. No wall clock anywhere.

**Notes = the stat carrier.** MapTool has no built-in 5e schema. Per-token structured storage DOES exist — `propertyMapCI` persists through import (`Token.imported()` does not null it; confirmed by source + the owner's export: full AC/HP/abilities map at `propertyType=Basic`) — but its *display* is campaign-dependent (the `propertyType` set decides whether a stat tab renders; there is no built-in 5e set). `notes` is the default carrier for guaranteed visibility + FG paste-compat — a deliberate choice, not the only surface. GM-only lore goes in `gmNotes` (research-5-4 §2c). The notes text reuses the verified 2024-Core Import-Text grammar so the same text stays paste-able into FG's parser.

**Token type.** We always emit `<tokenType>NPC</tokenType>` — MapTool's `Token.Type` enum is only `{PC, NPC}`; there is no MONSTER/FACTION/PLACE kind. The D&D creature type stays in the notes text.

**Macros (deferred by default).** Per-token macros persist through import — `imported()` nulls the legacy `macroMap`, not the modern `macroPropertiesMap` (verified: the owner's export carries `label="attack macro example"`, `command="[4d6+5]"` under `<macroPropertiesMap>`). Emitting one button per attack/spell is a genuine scope tradeoff (functional clickable tokens), deferred to Ask First: it needs derived macro UUIDs for determinism (never random) and a command-grammar decision (bare roll vs full hit-check via `!tokenprop`).

**The gold-file gate.** Field names in `content.xml` are verified from Token.java; the exact XStream wrappers (`GUID`/`baGUID`, `MD5Key`, map entries) and the emitted default-valued technical field set are pinned by copy from a real save, never guessed. The owner's real 1.18.6 export is **already committed** as the provisional reference fixture (`fixture-maptool-token-1_18_6-owner-export/`): it supplies the wrapper shapes below. The formal gold `.rptok` + our round-trip re-save still land at acceptance (live drag-drop gate) — until then the implementer mirrors the provisional fixture's shapes and element set (a real save of a real token; the "subset of gold" contract).

Provisional wrapper shapes (verified against the owner's export): `GUID` → `<id><baGUID>base64-of-16-bytes</baGUID></id>` (same for `exposedAreaGUID`; FQCN `<net.rptools.maptool.model.GUID><baGUID>…` inside maps); `MD5Key` → `<net.rptools.lib.MD5Key><id>32-lowercase-hex</id></net.rptools.lib.MD5Key>`; `imageAssetMap` → `<entry><null/><net.rptools.lib.MD5Key>…</…></entry>`; Asset descriptor → `<net.rptools.maptool.model.Asset><id><id>32hex</id></id><name>…</name><extension>png</extension><type>IMAGE</type>`; `propertyMapCI`/`macroPropertiesMap` shapes exist in the fixture for the deferred surfaces (do not emit by default). The owner's file also shows the larger technical field set (`vblColorSensitivity`, `alwaysVisibleTolerance`, `isAlwaysVisible`, `tokenOpacity`, `speechName`, `terrainModifier*`, `terrainModifiersIgnored`, `uniqueLightSources`, `lightSourceList/`, `sightType`, `hasSight`, `hasImageTable`, `notesType`, `gmNotesType`, `allowURIAccess`, `beingImpersonated`, `exposedAreaGUID`) — mirror the real set, don't hand-pick; omitting non-essential members relies on `readResolve` defaults and is confirmed by the round-trip gate.

## Verification

**Commands:**
- `uv run --directory backend pytest -q` — expected: full suite green incl. new maptool rows
- `make lint && make typecheck` — expected: clean
- `uv run --directory backend pytest -q backend/tests/test_export_api.py -k maptool` — expected: maptool rows green
- `npm run test` — expected: frontend green incl. WorldView export-row test; `npm run gen:api` after backend restart

**Manual checks (owner, acceptance):**
- Own box (or dev box after approved install): MapTool 1.18.6 → open any campaign → drag the generated `.rptok` onto the map → confirm the token lands with the editable name, the full stat-block notes visible, GM Notes showing lore only to the GM view, and the portrait rendering on the token. Re-save via right-click → Save As… and commit the result as the round-trip fixture.