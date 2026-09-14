---
title: '5.4 MapTool / RPGToken Export (Third Target)'
type: 'feature'
created: '2026-09-14'
status: 'done'
baseline_commit: '40946c0e4a1d294df8925b54b8e776a62a3f9b42'
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

**Always:** Export stays read-only — no revision, no event row, no store write (AD-1/AD-11); renderer pure of the snapshot. Repeated exports are **byte-identical**: ZIP with fixed member timestamps (e.g. 1980-01-01), pinned `compress_type`, no member `extra` fields, stable entry order, no wall clock; the token `id` GUID is **derived** from the entity id — `base64(sha256(entity_id)[:16])` serialized as `<id><baGUID>…</baGUID></id>`, the exact on-disk shape from the owner's real 1.18.6 export (never random). `notesType`/`gmNotesType` are emitted explicitly as `text/html` (owner decision 2026-09-14 — rich stat-block notes); HTML is conservative and attribute-free (`<p>`, `<b>`, `<br>`, `<hr>`, `<ul>`/`<li>` only), every text run still XML 1.0-filtered + fully escaped. Attack **macros are emitted** (owner decision 2026-09-14): `macroPropertiesMap` carries one **bare-roll** button per action with structured damage — label = action name, command `[1d20+N] [XdY+Z]…` (operators tight; `[1d20+N]` only when `to_hit` is stored), read from the committed structured fields (`to_hit` int, `damage` parts `{dice|count/sides, bonus}` — AR25 contract), never regex-parsed from prose; an action without structured damage parts gets **no button** (its text stays in the notes; spells are names-only → no spell buttons); macroUUIDs are **derived** (uuid5 of entity id + 1-based action index — determinism, never random); button shape copied from the fixture's MacroButtonProperties. `content.xml` is the **owner's proven Dragon template verbatim** (`_bmad-output/implementation-artifacts/maptool-token-template-dragon.xml` — the full technical field set: positions, `exposedAreaGUID`, `sizeMap` grid GUID, `beingImpersonated`, `isFlipped*`, `state`, lights, `ownerList`, `terrainModifier*`) with **only per-entity substitutions**: derived `<baGUID>`, `imageAssetMap` MD5, `name`, `notes`/`notesType` = `text/html`, `gmNotes`/`gmNotesType` = `text/html`, the twelve `propertyMapCI` values (abilities + AC/HP/movement from committed stats, `-` elsewhere — the template's own convention), and the `macroPropertiesMap` entries. Every other element stays byte-identical to the template. *The minimal-subset design (review loop 1) failed the live drop — superseded 2026-09-14.* Portrait embedding: newest AVAILABLE `kind=image` row (rowid order, `_hero_portrait_src` precedent) whose on-disk file starts with the PNG magic (no size cap — the .rptok image is a raw zip member, unlike the fg path's `MAX_INLINE_BYTES`); asset path = `assets/<md5>` (Asset descriptor XML: id/name/extension/type) + `assets/<md5>.png` where `<md5>` = 32-lowercase-hex MD5 of the PNG bytes (MD5Key.java). Broken/absent portrait → the **bundled default token image** is embedded instead (`backend/app/media/maptool_default_token.png` — a committed static PNG, a neutral NPC-bust token disc; replaceable by swapping the file): `imageAssetMap` null-key entry + `assets/<md5>` pair for the default's bytes, so a token without a portrait never lands empty; the honesty marker stays (`Portrait: <filename> [missing — default image used]`; no marker when the entity has no media at all). The generated portrait, when present, is the token image (null-key — unchanged). All emitted text is XML 1.0-legal (illegal control chars dropped, full `>` escaping); stat-block text uses the verified 2024-Core Import-Text grammar lines (research-5-3 §2d) — the FG paste path stays available by copy. `tokenType = NPC`; notes/GM-notes/lore content verbatim from committed state. Renderer failure → exactly one `export_failure` log event + generic 500 (FR18) — never validation in the export path.

**Ask First:** The gold gate runs on the **owner's machine** — no Java/MapTool install on the dev box; the owner performs the live import and produces the formal gold `.rptok` + round-trip fixture (FG precedent). **Import path is download → drag only** (no direct-input/HTTP transport — owner decision 2026-09-14); adding thumbnails/`charsheetImage`; anything beyond the gold-pinned shape (propertyMapCI now ships the Dragon-shaped Basic store with our stats — decided 2026-09-14 with the template).

**Never:** the JSON token format (does not exist — research-5-4 §1); embedding non-PNG or oversized images or a live signed URL (breaks determinism — the signed `portrait-url` route stays presentation-only); random GUIDs; validation/re-validation in the export path; VTT targets beyond MapTool; the 5-5 kill-criterion demo; media retention (KEEP-5 story); guessing XStream wrappers instead of pinning them from the gold file.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| HAPPY NPC | committed NPC, stat_block, portrait available | `{stem}.rptok` (`application/zip`) whose content.xml is the **Dragon template verbatim** with only the per-entity substitutions (`baGUID`, image MD5, `name`, `notes` = HTML stat block, `gmNotes` = HTML AR24 lore, `notesType`/`gmNotesType` = `text/html`, the 12 `propertyMapCI` values, `macroPropertiesMap` buttons); `assets/<md5>` + `.png` present and byte-consistent; every other element byte-identical to the template | N/A |
| PARTIAL PROPERTIES | entity with sparse stats | `propertyMapCI` ships the template's 12-key Basic store with `-` for missing values (the Dragon's own convention) | N/A |
| STRUCTURED ACTION ABSENT | action without `damage` parts (`to_hit`/`damage` missing — includes pre-2026-09-11 prose-only blocks) | that action gets **no macro button**; notes still carry its full text | N/A |
| BROKEN portrait | entity whose image rows are all unavailable | default token image embedded (`imageAssetMap` + `assets/<md5>` pair for the bundled default PNG); notes carry `Portrait: <filename> [missing — default image used]`; file imports with a visible image | N/A |
| NO media | entity with no media rows | default token image embedded; no marker line | N/A |
| NO stat_block | place/faction entity | minimal token: name + kind + gmNotes; notes carry only the identity header / nothing; empty notes/gmNotes emit `<notes></notes>` (pinned empty-element form, never `<notes/>`) | N/A |
| Monster | committed Monster, `cr "1/2"`, fraction→decimal precedent | Challenge line derives from `stat_block.identity` numerics (level for NPC/BBEG, CR decimal for Monster — same derivation as fg) | N/A |
| MISSING/FOREIGN | unknown entity or campaign id | identical 404 | 404 envelope |
| BAD FORMAT | `format=weird` | OpenAPI 422 before auth ordering (existing route pattern) | 422 envelope |
| RENDER FAILURE | renderer raises (simulated) | one `export_failure` log line, generic 500 | FR18 |
| ESCAPING | `&<>"` AND XML-illegal control chars (`\x00`–`\x08`, `\x0B`, `\x0C`, `\x0E`–`\x1F`) in name/text/traits | XML 1.0-legal filter (illegal chars dropped) + full `>` escaping (neutralizes `]]>`); values round-trip | N/A |
| DETERMINISM | same entity exported twice | byte-identical zips: fixed timestamps, stable order, derived GUID | N/A |
| RE-IMPORT | same entity exported and dragged in twice | derived GUID identical; replace-vs-duplicate behavior observed and pinned at acceptance | N/A |
| LIVE IMPORT | generated `.rptok` dragged onto a MapTool 1.18.6 map | token lands with name, notes, GM notes, portrait image (owner live check at acceptance; gold fixture committed) — **PROVEN 2026-09-14** (default-image token; lore confirmed under GM Notes) | N/A |

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
- [x] `backend/app/api/exports.py` — add `"maptool"` to the export_entity format Literal + `_attachment` branch (`{stem}.rptok`, `application/zip`) — single consumer point, mirrors `format == "fg"`
- [x] `_bmad-output/implementation-artifacts/` — mirror the committed provisional fixture (`fixture-maptool-token-1_18_6-owner-export/`) in the renderer FIRST: content.xml element set + wrapper shapes by copy, not guess; freeze the shape only after the acceptance round-trip — fixture-first, the 5-3 pattern
- [x] `backend/app/api/export_sheets.py` — implement `_rptok_notes_html(block, data) -> str` — 2024-Core lines as conservative attribute-free HTML (race/alignment header first; `<b>` labels + `<br>` separators, `text/html`) and `_rptok_gmnotes_html(data) -> str` — labelled AR24 lore paragraphs (`<p><b>…</b><br>…</p>`), from committed stat_block/identity
- [x] `backend/app/api/export_sheets.py` — implement `_rptok_attack_macros(block) -> list[dict]` — one MacroButtonProperties per action with structured damage: read the committed AR25 fields directly (`item["damage"]` = list of `{dice|count/sides, bonus}` parts; `item["to_hit"]` int, sign-aware); label = action name; command = `[1d20+N]` when `to_hit` is numeric, then one roll per damage part — `[{count}d{sides}{±bonus}]` (or the part's `dice` string), operators tight; multi-part damage chains rolls in one command (`[2d6+12] [3d10]`); `macroUUID` = `uuid5(namespace, entity_id + action index)`; 1-based `index` in action order; the rest of the button fields copied from the fixture (saveLocation=Token, colorKey=default, hotKey=None, group empty, autoExecute=true, includeLabel=false, applyToTokens=false, allowPlayerEdits=true, fontColorKey=default, fontSize=1.00em, displayHotKey=true, commonMacro=false, compare* true); an action without damage parts (or with unparseable dice) → skipped (no button) — never regex-parsed from prose
- [x] `backend/app/api/export_sheets.py` — implement `render_entity_maptool(export, entity_id) -> bytes` — deterministic ZIP (zipfile; fixed `date_time`, pinned `compress_type`, no `extra` fields, stable entry order): `content.xml` (Token XML = the committed **Dragon template** `maptool-token-template-dragon.xml` with ONLY per-entity substitutions: derived `<baGUID>`; `imageAssetMap` MD5; `name`; `notes` + `notesType` `text/html`; `gmNotes` + `gmNotesType` `text/html`; the 12 `propertyMapCI` values — ability scores/AC/HP/movement from stats, `-` fallback; `macroPropertiesMap` entry blocks per `_rptok_attack_macros` — all other elements byte-identical to the template: all emitted text filtered XML 1.0-legal with full `>` escaping), `properties.xml` (`version` = `1.18.6`, `herolab` = false), `assets/<md5>` + `assets/<md5>.png` + the `macroPropertiesMap` block (`<entry><int>N</int>` + MacroButtonProperties per `_rptok_attack_macros` result); portrait rides the null-key image only (no `<portraitImage>` element per the owner's export); FIPS-safe MD5 (`hashlib.new("md5", data, usedforsecurity=False)`); missing-portrait rules per the matrix — the no-portrait branch embeds the bundled default bytes (loaded once per render from `backend/app/media/maptool_default_token.png`, via `Path(__file__).parent / "../media/maptool_default_token.png"`; PNG magic checked; broken default → fall back to the old no-image behavior + marker)
- [x] `backend/tests/test_export_api.py` — maptool rows: zip structure + content.xml parse, template-fidelity (element inventory equals the Dragon template's, only the substitution values change + expected values), propertyMapCI values, asset md5 consistency, notes HTML structure + content, gmNotes HTML paragraphs with verbatim lore text, macro buttons (label/command/index/uuid determinism, action without structured damage → no button), escaping, missing/broken portrait, 404/422 shapes, FR18 event, byte-identical determinism (incl. re-mint scenario) — pins the live-verified contract
- [x] `frontend/src/views/WorldView.vue` + test — add `'maptool'` to the union + the "MapTool (rptok)" link + hint text
- [x] `frontend/src/api/schema.ts` — regen + commit

**Acceptance Criteria:**
- Given a committed NPC with stat_block and portrait, when the DM downloads the maptool export, then the file is `{stem}.rptok` (`application/zip`) whose content.xml element set equals the Dragon template's with only the per-entity substitutions holding committed values, and whose embedded PNG round-trips.
- Given the same entity exported twice, then both files are byte-identical (fixed zip timestamps, stable order, derived GUID — no wall clock).
- Given a renderer exception, then exactly one `export_failure` event is logged and the response is the generic 500 (FR18).
- Given an entity with no usable portrait, then the token embeds the bundled default image (`imageAssetMap` + `assets/<md5>` pair present, MD5 consistent with the default PNG bytes), notes carry the `[missing — default image used]` marker (no marker when there is no media), and the zip still parses.
- Given the generated rptok dragged onto a MapTool 1.18.6 map (owner live check at acceptance), then the token lands with name, visible stat-block notes, GM-only lore, and the portrait image; the round-trip re-save is committed as the second fixture.
- Given the same NPC imported twice (owner live check), then the replace-vs-duplicate behavior is recorded in this spec.
- Given the generated rptok dragged onto a MapTool 1.18.6 map (owner live check at acceptance), then each action with structured damage has a clickable macro button that rolls `1d20+N` (when `to_hit` is stored) and the action's dice in chat; actions without structured attack data show no button (their text stays in the notes).
- Given an unknown entity or campaign, then the response is the single indistinguishable 404; `format=weird` is a 422.

## Spec Change Log

<!-- Append-only. Populated by step-04 during review loops. -->

- `[review loop 0]` Corrected GUID on-disk encoding: `<baGUID>` base64 of 16 bytes, NOT 32-char uppercase hex (owner's real 1.18.6 export) — determinism design note, Boundaries Always, and renderer task updated; pin from the fixture. _Avoids:_ malformed token id that MapTool cannot parse.
- `[review loop 0]` Corrected the "only campaign-defined propertyType" justification: per-token `propertyMapCI` persists through import (`imported()` does not null it — source + owner's export); campaign-dependence is display-only. `propertyMapCI` added as an explicit deferred/Ask-First surface. _Avoids:_ a false premise underlying the carrier decision.
- `[review loop 0]` Token macros: `macroPropertiesMap` persists through import (legacy `macroMap` is the nulled one — source + owner's export). Macros moved from implied-impossible to explicit deferred/Ask-First with derived-UUID + command-grammar sub-decisions. _Avoids:_ ruling out a real surface without a decision.
- `[review loop 0]` Added FIPS-safe MD5 (`hashlib.new("md5", …, usedforsecurity=False)`) and pinned `compress_type`/no-`extra`/fixed timestamps to the renderer task; determinism scoped to one Python/zlib build. _Avoids:_ environment-dependent failures and a false cross-version byte-identity claim.
- `[review loop 0]` Added `notesType`/`gmNotesType` (`text/plain`) as explicit emitted fields — the on-disk MIME fields that control notes rendering (owner's export). _Avoids:_ silent rendering-mode ambiguity.
- `[review loop 0]` Promoted the owner's real export to a committed provisional reference fixture (`fixture-maptool-token-1_18_6-owner-export/`); listed confirmed wrapper shapes (GUID/MD5Key/imageAssetMap/Asset/propertyMapCI/macroPropertiesMap) and the technical field set. Portrait carrier pinned: `imageAssetMap` null-key only, no `<portraitImage>`. _Avoids:_ guessing shapes the gold gate must not re-derive.
- `[elicitation: gold-gate + direct input (owner, 2026-09-14)]` Gold gate re-sequenced: no Java/MapTool install on the dev box — the owner performs the live import on their machine (FG precedent) and produces the formal gold `.rptok` + round-trip fixture. Direct-input transport surfaced: MapTool *Macro Permissions → Enable External Macro Access* (`getRequest`/`exportData`) pulling the export from a signed TTL URL — sub-decisions: binary safety of the string-return round-trip (fallback = download → drag) and the signed-URL mint (media_url_secret precedent); server never connects out to MapTool. _Avoids:_ blocking acceptance on a dev-box install and an unreachable session-auth wall at macro-fetch time.
- `[elicitation: boundary sweep (owner, 2026-09-14)]` content.xml emission pinned to a **named stable subset** of the gold field set with an explicit omit list — campaign/runtime state (positions, `exposedAreaGUID`, `sizeMap` grid GUID, `beingImpersonated`, `isFlipped*`, `state`, lights, `ownerList`, `terrainModifier*`) — because copying the fixture's technical set verbatim would import a dead campaign's grid GUID and runtime flags; `layer`=TOKEN and `tokenShape`=CIRCLE pinned from the fixture (research §5.3 UNVERIFIED items settled by the owner's file); all emitted text filtered XML 1.0-legal with full `>` escaping (control chars dropped — `&<>"` escaping alone cannot legalize them; full `>` escaping also neutralizes `]]>`); empty notes/gmNotes emit `<notes></notes>` (pinned form, never `<notes/>`); embedded PNG intentionally uncapped (unlike the fg path's MAX_INLINE_BYTES); deterministic derived GUID means a twice-imported NPC carries the same id — replace-vs-duplicate observed and pinned at acceptance. _Avoids:_ unparseable content.xml from model/paste control chars, imported foreign-grid GUIDs, and an unbounded copy-the-fixture instruction.
- `[elicitation: transport (owner, 2026-09-14)]` SUPERSEDES the direct-input half of the gold-gate entry above: **download → drag is the ONLY import path**. No direct-input/HTTP transport, no signed export URL, no external-macro-access requirement; the server's signed-URL minting stays limited to the presentation-only `portrait-url` route. _Avoids:_ a second delivery surface (signed-URL mint + binary-safety unknowns) with zero DM-path benefit.
- `[elicitation: notes HTML (owner, 2026-09-14)]` `notesType`/`gmNotesType` decided: **`text/html`** — notes render the stat block as bold-labelled lines, gmNotes render the AR24 lore as labelled paragraphs; HTML is conservative and attribute-free (`<p>`, `<b>`, `<br>`, `<hr>`, `<ul>`/`<li>` only) with every text run XML 1.0-filtered + fully escaped; line content keeps the 2024-Core field order and labels. _Avoids:_ plain-text monochrome notes when MapTool renders rich notes (`text/html`) — the fixture's own demo note says "I can be html".
- `[elicitation: attack macros (owner, 2026-09-14)]` `macroPropertiesMap` ADOPTED at **bare-roll** level: one button per attack-shaped action — label = action name, command `[1d20+N] [XdY+Z]` (operators tight), numbers regex-parsed from committed 2024-Core action text; unparseable actions → no button (text stays in notes); `macroUUID` derived via uuid5 (entity id + action index), 1-based index in action order, button fields copied from the fixture's MacroButtonProperties. No `!tokenprop`, no propertyMapCI coupling; spells are names-only → no spell buttons. _Avoids:_ hand-rolled NPC dice at the table when the token can roll itself — with determinism intact and model-text variance bounded by parse-or-skip.
- `[elicitation: structured macro sources (owner, 2026-09-14)]` SUPERSEDES the prose-regex half of the attack-macros entry: the macro task reads **committed structured fields**, not prose — `action.to_hit` (int) for the attack roll, `action.damage` parts (`{dice|count/sides, bonus}`, AR25 contract) for damage rolls; multi-part damage chains rolls in one command; the `[1d20+N]` segment only when `to_hit` is stored (damage-only actions like smites still get a button); actions without damage parts — pre-2026-09-11 prose-only blocks included — get no button. _Avoids:_ regex drift against model-authored prose when the committed data is already structured.
- `[elicitation: default token image (owner, 2026-09-14)]` A token without a generated portrait gets the **bundled default token image** instead of an empty token: `backend/app/media/maptool_default_token.png` (committed 256×256 RGBA PNG — neutral NPC-bust token disc; a single swappable file) is embedded exactly like a portrait (`imageAssetMap` null-key + `assets/<md5>` pair, MD5 of its bytes); determinism holds (fixed asset → identical MD5 everywhere); honesty marker amended to `Portrait: <filename> [missing — default image used]` (no marker when the entity has no media); the generated portrait remains the token image when present. _Avoids:_ an empty/blank token on the map when the portrait pipeline produced nothing — MapTool's built-in fallback is a gray default with no visual identity.
- `[dogfood: Dragon template (owner, 2026-09-14)]` SUPERSEDES review loop 1's named-stable-subset / omit-list emission: the live MapTool 1.18.6 drop of a generated token was a silent no-op while the owner's own Dragon token imported fine, so the shape itself was the blocker, not layout semantics. content.xml is now the **owner's Dragon token verbatim** (`maptool-token-template-dragon.xml`, committed) with ONLY per-entity substitutions — derived baGUID, imageAssetMap MD5, name, notes/gmNotes + `text/html` types, the twelve propertyMapCI values (stats or `-`), macroPropertiesMap entries. `propertyMapCI` thus resolved: the Dragon Basic store ships with our stats (the display-dependence caveat stays, but the template makes the shape load-proven). Thumbnails/`charsheetImage` remain deferred. Live re-test is the acceptance gate. _Avoids:_ continued format-guessing against a client we cannot introspect — the proven file is the reference.
- `[dogfood acceptance (owner, 2026-09-14)]` **LIVE IMPORT PROVEN**: the template-fidelity token (`chaplain-brack-GXH1SXG1.rptok`) dropped onto MapTool 1.18.6 imports and lands correctly — name, HTML stat notes, the `[1d20+6] [4d6]` Guiding Bolt macro, and the AR24 lore visible under GM Notes (owner noted the lore "was under the gm notes" as expected). Portrait path still to prove visually once media exists. Remaining acceptance: re-import-twice behavior record + Save As round-trip fixture.
- `[review loop 1]` Step-4 review (3 layers; dedup → 12 patches, 0 intent/bad-spec, 1 defer): (1) `_rptok_token_image` scans `reversed(available)` for the FIRST row passing the PNG-magic check — default image only when NO candidate works (the "newest AVAILABLE row whose on-disk file starts with the PNG magic" contract, fg-path parity; was: newest-only + premature default); (2) honesty marker split by cause — `[missing — default image used]` when the newest row's file is absent, `[unusable — default image used]` when it exists but fails magic/read — and names the newest failed row in both branches; (3) macro dice rolls parse the part's `dice` string with the pipeline's clean-dice grammar (embedded bonus respected, never double-added with the `bonus` field; unparseable → that part doesn't roll); (4) notes show the dice text when `damage_parts_sentence` emits nothing (notes and macros agree — `<b>Hurl.</b> Hit: 1d12+1 damage.`); (5) `<b>Spells</b>` block (name-only, fg parity, skipped when empty); (6) `MOD SAVE` header emitted only when all six ability cells render; (7) BBEG challenge derivation pinned (identity.level → CR, e.g. 9); (8) broken-bundled-default → no-image shape tested; (9) `_XML10_ILLEGAL` also drops U+FFFE/U+FFFF (full XML 1.0 Char); (10) `count < 1 or sides < 1` parts never roll; (11) integral guards — non-integral `to_hit`/initiative/saves/perception omit the segment/cell, never truncate; (12) exports.py module docstring format inventory completed (json|markdown|html|owlbear|fg|maptool). Tests 5 → 10 maptool rows; full suite 1264. No frozen-block edits — every fix derives from the frozen contract + precedents. _Avoids:_ regressions past the gold gate — a token that can't load (U+FFFE), a silently-wrong default portrait, broken macro rolls, and unpinned sparse-data layout.

## Design Notes

**Why `.rptok`, not JSON.** The epic's "RPGToken JSON" is not a shipped MapTool format — the JSON serializations that exist are a campaign-level add-on data store and a network DTO, neither importable as a token file (research-5-4 §1, source-linked). The only token-file loader is `PersistenceUtil.loadToken` on the ZIP `PackedFile`, and the only DM surface is drag-drop of the `.rptok` (verified: `TransferableHelper.handleURLList`). Per the owner directive (no shapes from memory), the file layout, field inventory, and portrait constraints are all source-cited in research-5-4.

**Determinism mechanics.** The 5-1 byte-identical invariant forces two choices: (1) the token `id` GUID is derived — `base64(sha256(entity_id)[:16])` — serialized exactly as the owner's real file shows: `<id><baGUID>…</baGUID></id>` (XStream writes the `byte[]` field; NOT the 32-hex Java ctor form — that conflation was corrected in review). Unique per entity, deterministic across exports, byte-identical repeats. (2) The ZIP is written with fixed member timestamps, an explicit `compress_type`, no member `extra` fields, and a fixed entry order. Deflate is deterministic for identical inputs — scoped to one Python/zlib build; byte-identity across dependency upgrades is NOT guaranteed. No wall clock anywhere.

**Notes = the stat carrier.** MapTool has no built-in 5e schema. Per-token structured storage DOES exist — `propertyMapCI` persists through import (`Token.imported()` does not null it; confirmed by source + the owner's export: full AC/HP/abilities map at `propertyType=Basic`) — but its *display* is campaign-dependent (the `propertyType` set decides whether a stat tab renders; there is no built-in 5e set). `notes` is the default carrier for guaranteed visibility — a deliberate choice, not the only surface. Both notes and gmNotes are `text/html` (owner decision 2026-09-14): the stat block renders as bold-labelled lines (`Armor Class 16 (chain shirt)` etc.) and the AR24 lore as labelled paragraphs; line content still follows the 2024-Core grammar field order and labels (research-5-3 §2d), so the visible text stays FG-paste-shaped at the content level (the FG exporter builds its own text from the same fields — no cross-client HTML paste). GM-only lore goes in `gmNotes` (research-5-4 §2c).

**Token type.** We always emit `<tokenType>NPC</tokenType>` — MapTool's `Token.Type` enum is only `{PC, NPC}`; there is no MONSTER/FACTION/PLACE kind. The D&D creature type stays in the notes text.

**Attack macros (adopted).** Per-token macros persist through import — `imported()` nulls the legacy `macroMap`, not the modern `macroPropertiesMap` (verified: the owner's export carries `label="attack macro example"`, `command="[4d6+5]"` under `<macroPropertiesMap>`). We emit one **bare-roll** button per action with structured damage (owner decision 2026-09-14): label = action name; command = `[1d20+N] [XdY+Z]…` — attack roll + damage roll(s) in one chat message, the fixture's own grammar in spirit; numbers are read from the **committed structured fields** (`to_hit` int, `damage` parts `{dice|count/sides, bonus}` — the AR25 contract), never regex-parsed from prose (a later correction: committed data is structured, so the fragile prose-parse was dropped); multi-part damage chains rolls (`[2d6+12] [3d10]`); the `[1d20+N]` segment appears only when `to_hit` is stored (damage-only actions like smites still get a button); actions without structured damage parts get **no button** and stay in the notes only — pre-2026-09-11 prose-only blocks simply have no buttons; macroUUIDs are **derived** (uuid5 of entity id + action index — determinism, never random); index/order = stat-block action order; the button shape is copied from the fixture's MacroButtonProperties (saveLocation=Token, autoExecute=true, includeLabel=false, applyToTokens=false, allowPlayerEdits=true, compare* flags, fontSize 1.00em). Spells are names-only in committed data → no spell buttons.

**The gold-file gate.** Field names in `content.xml` are verified from Token.java; the exact XStream wrappers (`GUID`/`baGUID`, `MD5Key`, map entries) and the emitted default-valued technical field set are pinned by copy from a real save, never guessed. The owner's real 1.18.6 export is **already committed** as the provisional reference fixture (`fixture-maptool-token-1_18_6-owner-export/`): it supplies the wrapper shapes below. The formal gold `.rptok` + our round-trip re-save still land at acceptance (live drag-drop gate) — until then the implementer mirrors the provisional fixture's shapes and element set (a real save of a real token; the "subset of gold" contract).

Provisional wrapper shapes (verified against the owner's export): `GUID` → `<id><baGUID>base64-of-16-bytes</baGUID></id>` (same for `exposedAreaGUID`; FQCN `<net.rptools.maptool.model.GUID><baGUID>…` inside maps); `MD5Key` → `<net.rptools.lib.MD5Key><id>32-lowercase-hex</id></net.rptools.lib.MD5Key>`; `imageAssetMap` → `<entry><null/><net.rptools.lib.MD5Key>…</…></entry>`; Asset descriptor → `<net.rptools.maptool.model.Asset><id><id>32hex</id></id><name>…</name><extension>png</extension><type>IMAGE</type>`; `propertyMapCI`/`macroPropertiesMap` shapes exist in the fixture for the deferred surfaces (do not emit by default). The owner's file also shows the larger technical field set (`vblColorSensitivity`, `alwaysVisibleTolerance`, `isAlwaysVisible`, `tokenOpacity`, `speechName`, `terrainModifier*`, `terrainModifiersIgnored`, `uniqueLightSources`, `lightSourceList/`, `sightType`, `hasSight`, `hasImageTable`, `notesType`, `gmNotesType`, `allowURIAccess`, `beingImpersonated`, `exposedAreaGUID`) — mirror the real set, don't hand-pick; omitting non-essential members relies on `readResolve` defaults and is confirmed by the round-trip gate.

## Verification

**Commands:**
- `uv run --directory backend pytest -q` — expected: full suite green incl. new maptool rows
- `make lint && make typecheck` — expected: clean
- `uv run --directory backend pytest -q backend/tests/test_export_api.py -k maptool` — expected: maptool rows green
- `npm run test` — expected: frontend green incl. WorldView export-row test; `npm run gen:api` after backend restart

**Manual checks (owner, acceptance):**
- Owner's machine (no dev-box install): MapTool 1.18.6 → open any campaign → download the generated `.rptok` → drag it onto the map → confirm the token lands with the editable name, the full stat-block notes visible, GM Notes showing lore only to the GM view, and the portrait rendering on the token. Re-save via right-click → Save As… and commit the result as the round-trip fixture.

## Suggested Review Order

**Design intent — the deterministic .rptok assembly**

- Entry point: committed state → bytes — read-only, byte-identical, FR18 envelope
  [`export_sheets.py:1858`](../../backend/app/api/export_sheets.py#L1858)
- content.xml carries the frozen named stable subset, never runtime/campaign state
  [`export_sheets.py:1755`](../../backend/app/api/export_sheets.py#L1755)
- Fixed member timestamps, pinned compress_type, no extra fields, stable order
  [`export_sheets.py:1844`](../../backend/app/api/export_sheets.py#L1844)

**Text safety**

- XML 1.0-legal filter + full `>` escaping, one pass at the boundary (control chars, U+FFFE/U+FFFF)
  [`export_sheets.py:1254`](../../backend/app/api/export_sheets.py#L1254)

**Portrait and the default image**

- Newest-first scan for a PNG-magic portrait; bundled default only when none; honest `[missing]`/`[unusable]` marker
  [`export_sheets.py:1298`](../../backend/app/api/export_sheets.py#L1298)

**Stat carrier — notes, gmNotes, macros**

- 2024-Core lines as bold-labelled HTML, Spells block, sparse-safe layout
  [`export_sheets.py:1414`](../../backend/app/api/export_sheets.py#L1414)
- AR24 lore as labelled HTML paragraphs for gmNotes
  [`export_sheets.py:1575`](../../backend/app/api/export_sheets.py#L1575)
- Bare-roll buttons from structured AR25 fields; derived uuid5; integral-guarded to_hit
  [`export_sheets.py:1665`](../../backend/app/api/export_sheets.py#L1665)
- Fixture-shaped MacroButtonProperties XML (`<entry><int>N</int>`)
  [`export_sheets.py:1709`](../../backend/app/api/export_sheets.py#L1709)

**Route and API surface**

- `format=maptool` Literal + attachment branch with FR18 logging
  [`exports.py:366`](../../backend/app/api/exports.py#L366)

**Tests**

- Happy path: zip structure, subset inventory, exact notes/gmNotes, macros, determinism, read-only
  [`test_export_api.py:1679`](../../backend/tests/test_export_api.py#L1679)
- Older portrait wins over corrupt newer; corrupt vs missing marker branches; broken default
  [`test_export_api.py:1937`](../../backend/tests/test_export_api.py#L1937)
  [`test_export_api.py:1912`](../../backend/tests/test_export_api.py#L1912)
  [`test_export_api.py:2083`](../../backend/tests/test_export_api.py#L2083)
- Caster spells + BBEG challenge; sparse abilities + integral guards
  [`test_export_api.py:1966`](../../backend/tests/test_export_api.py#L1966)
  [`test_export_api.py:2019`](../../backend/tests/test_export_api.py#L2019)

**Frontend**

- MapTool export link + `maptool` format union
  [`../../frontend/src/views/WorldView.vue:1272`](../../frontend/src/views/WorldView.vue#L1272)
  [`../../frontend/src/views/WorldView.vue:439`](../../frontend/src/views/WorldView.vue#L439)
- Anchor row test + regenerated schema member
  [`../../frontend/src/views/WorldView.test.ts:1760`](../../frontend/src/views/WorldView.test.ts#L1760)
  [`../../frontend/src/api/schema.ts:1752`](../../frontend/src/api/schema.ts#L1752)

> Ctrl+click (Cmd+click on macOS) the links above to jump to each stop.