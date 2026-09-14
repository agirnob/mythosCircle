---
title: '5.3 Fantasy Grounds Export (Second Target)'
type: 'feature'
created: '2026-09-14'
status: 'ready-for-dev'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-5-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/research-5-3-fantasy-grounds.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Committed 5e characters cannot reach Fantasy Grounds — the DM retypes stat blocks by hand, and the VTT's import surfaces were undocumented (the record schema is unpublished; parser behaviour had to be read from the shipped ruleset).

**Approach:** A `format=fg` adapter over the 5-1 entity projection emitting the **verified** `<root><npc>` record XML (2024 record — the exact shape FG Unity's own Export NPC produced, fixture-pinned in this repo), with the 2024-Core Import-Text stat block embedded in the record's `text` element so paste-import stays available. The DM imports the downloaded `.xml` via NPCs → Import (file picker) — no retyping, no parser gymnastics.

## Boundaries & Constraints

**Always:** Export stays read-only — no revision, no event row, no store write (AD-1/AD-11); renderer pure of the snapshot, byte-identical across repeats. Ownership 404s stay the single indistinguishable shape. Wire shape = the verified 2024 record (`fixture-fg-npc-record-2024-export.xml`): root `version="5.1"`, one `npc` node, `version` = `2024`. Numeric fields (`ac`, `hp`, `damagethreshold`, `xp`, ability `score`/`savemodifier`) are numbers; `cr` is a STRING (2024 record) derived from `stat_block.identity` numerics (owner 5-2 verdict: derive from numerics, top-level string display-only); fraction→decimal for Monster (`cr "1/2"` → `0.5`). `spellslots` and `summon*` omitted when unknown — sparse payloads import validly (action/trait entries are `npc_power` = `name` + `desc`). The record's `text` notes carry the character's AR24 LORE sections (appearance, personality, background, goals, relationships, secret, rumor, party_hook, voice_style, catchphrases — owner ruling 2026-09-14: FG's sheet already shows the stats, so the text slot is the story, not a stat-block duplicate; an empty notes area still emits the `<p />` shape FG itself exports). Portrait: FG image fields
are embedded-bitmap only (no remote field), and embedding a LIVE signed
URL would break byte-identical determinism (the mint's ``exp`` is a
wall-clock read) — so the artifact NAMES the newest available portrait
file in the ``text`` block's ``Information:`` line, and the DM fetches
the actual signed URL from the existing portrait-url route (5-2 surface,
7-day TTL) and drags the PNG onto the Pictures tab; a ``[missing]``
marker stands in when only broken rows exist. Import requires FG Unity
5E with 2024-record support (current client); the 2022/2014 grammar is
not emitted.

**Ask First:** switching the primary artifact from record-XML to Import-Text (parser tolerances as the contract — currently the fallback, not the contract); emitting 2022-MM/Legacy variants; anything that embeds portrait binaries (violates the no-binary rule, AD-10/AD-11).

**Never:** validation in the export path (FR18 — a format assertion failing at export time is a commit-path regression, logged not blocked); re-validating the stat block; VTT targets beyond FG (MapTool = 5.4); the 5-5 kill-criterion demo (separate story); `level_cr` format enforcement; media retention (KEEP-5 story); guessing record tags not in the fixture (field-set extensions need a live export first).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| HAPPY NPC | committed NPC, stat_block, portrait available | `.xml` attachment (`text/xml`, `{stem}.xml`) with full 2024 record; `text` notes carry the AR24 lore sections + `Information: Portrait: <filename>` pointer; the live signed URL comes from the portrait-url route | N/A |
| HAPPY Monster | committed Monster, `cr "1/2"`, level absent | `cr` = `0.5` (fraction→decimal); numeric fields from `combat`/`attributes`; spells list → `spells.id-0000N.name` | N/A |
| NO portrait | entity with only broken/absent media | record exports fine; `text` carries `Information: Portrait: <filename> [missing]` when only unavailable rows exist, no line when there is no media at all | N/A |
| NO stat_block | entity with no stat_block (place/faction) | minimal record (name + type/size where derivable), no `abilities`/`combat` block — sparse is legal | N/A |
| MISSING/FOREIGN | unknown entity or campaign id | identical 404 | 404 envelope |
| BAD FORMAT | `format=weird` | OpenAPI 422 before auth ordering (existing route pattern) | 422 envelope |
| RENDER FAILURE | renderer raises (simulated) | one `export_failure` log line, generic 500 | FR18 |
| TEXT ESCAPING | `&<>` in name/trait/desc | valid XML (entities escaped) | N/A |
| IMPORT (live) | generated `.xml` in FG Unity 5E, NPCs → Import | record lands; sheet fields match; 2024 badge shown (owner live check at acceptance) — **RESOLVED 2026-09-14**: Harbormaster Ilsa Vane imported into FG Unity from the staged sample and re-exported; every emitted field survived typed correctly (see fixture-fg-npc-record-2024-import-roundtrip.xml). FG added only its own defaults (empty sections, level1-9 slots 0, summon\* 0, root updater attrs) | manual ✓ |

## Code Map

- `backend/app/api/exports.py` — `export_entity` route: `format: Literal[..., "fg"]` + one branch calling the new renderer into `_attachment(..., fmt="fg")` (event name rides the existing failure envelope)
- `backend/app/api/export_sheets.py` — owns the projection renderers: mirror `render_entity_owlbear`'s stat-block accessors; add `render_entity_fg(export, entity_id) -> str` emitting the `<root><npc>` XML (xml.etree for escaping, or a string template with `xml.sax.saxutils.escape` — escaping is load-bearing)
- `backend/app/api/media.py` — `_media_refs`-style helper in exports.py already yields available media for the signed-URL line (reuse `portraitFor`-newest-available semantics — the 5-1 `media` refs in the projection)
- `backend/tests/test_export_api.py` — 5-2's format tests were extended here; add the fg cases + golden tag-set parity against `_bmad-output/implementation-artifacts/fixture-fg-npc-record-2024-export.xml`
- `frontend/src/views/WorldView.vue` — `entityExportUrl(entityId, 'markdown' | 'html' | 'owlbear')` + the download link; add `'fg'` to the union and the link row (WorldView.test.ts pins the menu)
- `frontend/src/api/schema.ts` — regenerate via `npm run gen:api` once the backend declares the new format (its Literal enum shows up in the OpenAPI schema)

## Tasks & Acceptance

**Execution:**
- [ ] `backend/app/api/exports.py` -- add `"fg"` to the export_entity format Literal + `_attachment` branch -- single consumer point, mirrors `format == "owlbear"`
- [ ] `backend/app/api/export_sheets.py` -- implement `render_entity_fg` -- findable, testable, pure
- [ ] `backend/app/api/export_sheets.py` -- build the embedded 2024-Core stat-block text per research §2d -- the paste path needs the exact grammar (name, header line, AC/HP/Speed, MOD SAVE rows, optional lines, Traits/Actions)
- [ ] `backend/tests/test_export_api.py` -- golden XML parity (tag set + numeric types vs the committed fixture), field mapping rows (abilities scores, ac/hp/hd, cr/xp, skills, senses, languages), text grammar lines, escaping, 404 shape, FR18 failure event, attachment headers -- pins the live-verified contract
- [ ] `frontend/src/views/WorldView.vue` + test -- add `'fg'` link -- one-click escape hatch per epic UX
- [ ] `frontend/src/api/schema.ts` -- regen + commit -- freshness rule from the 2.6 deferral

**Acceptance Criteria:**
- Given a committed NPC with stat_block and portrait, when the DM downloads the fg export, then the file is `{label}-{id}.xml` (text/xml) whose `npc` node's tag set is a subset of the committed fixture's and whose values match the entity's committed data.
- Given the same entity exported twice, then both files are byte-identical (pure projection, no timestamps).
- Given an unknown entity or campaign, then the response is the single indistinguishable 404.
- Given a renderer exception, then exactly one `export_failure` event is logged and the response is the generic 500 (FR18).
- Given the generated xml in FG Unity 5E (NPCs → Import), then the NPC record lands with correct 2024 fields (owner live check at acceptance — RESOLVED 2026-09-14, round-trip fixture committed).
- Given an entity with AR24 lore sections committed, then the record's `text` notes contain those sections in AR24 order, labelled, with content verbatim — and no duplicate stat block (owner ruling 2026-09-14).

## Spec Change Log

<!-- Append-only. Populated by step-04 during review loops. -->

## Design Notes

**Wire shape.** The owner's live Export NPC (2026-09-13, FG Unity 5E, 2024 record) is the contract — committed as `fixture-fg-npc-record-2024-export.xml` (annotations: 444=str … 999=cha, spellslots 1-9). The generator writes the same root attrs (`version="5.1"`, no dataversion/release — those are updater metadata, not record data) and the same field set; `.id-0000N` enumeration restarts per subtree (actions/traits/etc.), starting at 1, in list order — deterministic.

**Ability saves.** The import parser stores `savemodifier` as the difference (save − bonus); the XML generator computes `savemodifier = save_bonus − ability_bonus` from our `saves`/`attributes` data so the sheet shows the committed save. `initiative.misc` = initiative bonus − Dex bonus (same derivation as the parser) — omit when either component is unknown.

**CR/XP.** 2024 record: `cr` string ← `identity.level` (NPC/BBEG) or `identity.cr` (Monster, fraction→decimal, 5-2 precedent); `xp` omitted (we do not track XP — sparse is legal; FG displays XP only when present).

**Actions.** Each `actions`/`traits`/etc. entry = `npc_power` (`name` string, `desc` string). Name = the attack/trait heading with the trailing period stripped (parser convention); desc = the committed text with the 2024 phrasing (`Melee Attack Roll: +x, reach … Hit: …`) so the CT effects parser can drive rolls — the "table-ready" claim.

**Why XML over Text.** The XML shape is fully verified and the Import button is the documented file-picker surface; the text parser's tolerances (research §2d) are known but the artifact no longer needs them — the stat block lives in FG's structured fields, and the `text` slot is the DM's lore notes (owner ruling 2026-09-14). A DM who still wants the paste path can build the stat-block text from the Markdown/HTML exports.

**Field-set provenance.** Every emitted tag is pinned by the owner's live Export NPC fixture EXCEPT `conditionimmunities`, which the fixture omits — it is confirmed by the ruleset's own parser/record (`record_npc.xml` label + `manager_import_npc.lua` keyword both write `conditionimmunities`), so it follows the same verified family.

## Verification

**Commands:**
- `uv run --directory backend pytest -q` -- expected: full suite green incl. new fg cases
- `make lint && make typecheck` -- expected: clean
- `uv run --directory backend pytest -q backend/tests/test_export_api.py` -- expected: fg rows green

**Manual checks (owner, acceptance):**
- FG Unity 5E (installed at `~/.smiteworks/fantasygrounds`) → open a campaign → NPCs → Import → select the generated `.xml` → confirm the 2024 NPC record lands with correct fields.
- Open the file, copy the embedded stat block, NPCs → Import Text → `2024 - D&D Core Rules` → paste → confirm the same record (fallback proof).

## Suggested Review Order

1. Wire-shape parity vs the committed fixture (tag set, types, enumeration).
2. The embedded text grammar vs research §2d (line contract).
3. I/O matrix rows (404 shape, FR18, escaping, determinism).
4. Frontend link + schema regen.