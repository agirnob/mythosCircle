---
title: 'Structured attack damage and the missing stat aspects (additive, prose fallback)'
type: 'feature'
created: '2026-09-11'
status: 'in-progress'
baseline_commit: '139ae7c'
context: []
---

## Intent

**Problem:** the DM's characters are saved with less than they know. Live
record shape (verified 2026-09-11 against the dev DB): flat AR24 prose +
`stat_block{identity, attributes{6}, combat{ac,hp}, skills[], traits[],
spells[], actions[{name, description}]}`. Everything else the
`prototype-2` builder design models — `stats.abilities/modifiers/saves/
initiative/passive_perception/spellcasting`, `combat.hit_dice`, structured
attack damage (`damage[{dice,count,sides,bonus,average,type}]`),
`features`, `resources` — is neither written by the model nor accepted,
and `canonicalize_action_damage` actively *folds* a volunteered `damage`
key into the description prose. Consequence: the auditor re-parses dice
out of prose, the Forge/Owlbear payload can only carry a text blob for an
attack, and the sheet cannot show saves, initiative, passive perception,
hit dice, spell DC/attack/slots or resources.

**Approach (owner decision 2026-09-11, option C):** accept and store the
richer aspects ADDITIVELY and OPTIONALLY — the prompt asks for them, the
validators accept them and check their shape, consumers prefer them and
fall back to today's prose when absent. No migration: entities are JSON,
existing worlds keep working byte-for-byte, and a block written before
this change validates exactly as it did.

## Boundaries & Constraints

**Always:** new fields are optional — absence is never a violation;
a present field must be well-formed or it is a violation naming the field;
the auditor/export/sheet prefer structured data and fall back to prose
(`actions[].damage[]` present ⇒ use it; absent ⇒ parse the description as
today); `actions[]` stays the ONE attack list (no parallel `attacks[]`/
`routine[]` array duplicating it — prototype-2's `routine`+`attacks` split
is a display shape, not a second source of truth); prompts stay pure
functions of the seed/record (AD-16).

**Ask First:** storing the derived `power` block (band/audited DPR/rank
totals) — it is computed by the auditor, and a stored copy can drift from
the committed numbers; the sheet/UI derive it. Also Ask First before
changing the Forge BID table (frozen from the live dictionary, spec-5-2).

**Never:** rewrite the stored record shape (no nesting the flat AR24 prose
under `narrative`/`identity`; no renaming `attributes`→`stats.abilities` —
prototype-2's nesting is a *design artifact*, not the storage contract);
drop a field the model wrote (canonicalize or validate it, never silently
discard); fail a job over a missing OPTIONAL field; store `power`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| STRUCTURED_DAMAGE | action carries `damage: [{dice,count,sides,bonus,average,type}]` | kept (canonicalized) as `actions[].damage`; description untouched; auditor uses the parts | malformed part ⇒ violation naming the index |
| PROSE_DAMAGE | action carries only `description` (today's shape) | unchanged behavior — auditor parses prose, export renders prose | N/A |
| STRING_DAMAGE | action carries `damage: "2d6+4 slashing"` | folded into the description (today's rule, unchanged) | N/A |
| NEW_STATS_PRESENT | `combat.hit_dice`, `saves{}`, `initiative`, `passive_perception`, `proficiency_bonus`, `spellcasting{dc,attack_bonus,slots}` | validated, stored, rendered on the sheet/UI; Forge ignores what has no BID | malformed ⇒ violation |
| NEW_STATS_ABSENT | a block written before this change | validates exactly as before — zero new violations | N/A |
| FEATURES_FIELD | block writes `features: ["Divine Smite", ...]` (prototype-2 naming) | validated and stored as its own list of feature NAMES — never folded into `traits` (whose entries are `{name, description}` objects; folding would invent descriptions) | a non-string/blank entry ⇒ violation |
| RESOURCES | `resources: {lay_on_hands: 85, ...}` | stored as a flat string→number map, rendered; no BID | non-numeric value ⇒ violation |
| STRUCTURED_AUDIT | a combat block whose damage is structured | DPR from the parts (same bands, same Multiattack rule), no prose parsing | N/A |
| STRUCTURED_CONFORM | the conform has to move DPR on a block with structured damage | structured parts move WITH the prose (both stay consistent) | N/A |

## Code Map

- `backend/app/pipeline/knowledge.py` -- `validate_stat_block`: accept + shape-check the new optional fields (hit_dice, saves, initiative, passive_perception, proficiency_bonus, spellcasting, resources, actions[].damage[]).
- `backend/app/pipeline/statblocks.py` -- `stat_block_rules_text`/`_character_record_lines` prompts ask for the new fields; `canonicalize_action_damage` keeps structured parts and folds only string damage; `conform_power`/`_append_damage_clause` keep structure and prose in step; `features`→`traits` canonicalization.
- `backend/app/pipeline/combat.py` -- `audit_stat_block`/action-damage parsing prefers structured parts, falls back to prose.
- `backend/app/api/export_sheets.py` -- Forge `Z035` action text rendered from structure when present (BID table unchanged); HTML sheet renders the new stats.
- `frontend/src/views/{WorldView,CandidatesView}.vue` (+ shared helpers) -- render the new fields on the entity card.
- `backend/tests/test_statblocks.py`, `test_combat.py`, `test_build_in_pipeline.py`, `test_export_api.py`, `frontend/src/views/*.test.ts`.

## Tasks & Acceptance

**Execution:**
- [x] `knowledge.validate_stat_block` -- accept + validate the optional fields -- a model that writes saves/hit dice/slots must not fail the job for it.
- [x] `statblocks` prompts + canonicalization -- ask for the structured shape, keep it, `features` as its own field (`canonicalize_stat_blocks`: `stats` nesting folded absent-only, damage parts completed from their dice, string damage still folded into prose).
- [x] `combat.audit_stat_block` -- structured-first DPR (`structured_damage_totals`), prose fallback on absent/unusable parts.
- [ ] `export_sheets` Forge + HTML sheet -- render attacks and the new stats -- the DM sees them where they export.
- [ ] Frontend entity card -- render the new stats -- the DM sees them in the app.
- [x] Tests -- matrix pins on the backend + one frontend pin per rendered group -- proven, not assumed.

**Acceptance Criteria:**
- Given an existing committed character (pre-change shape), when it is re-validated/exported, then nothing changes — no new violations, identical sheet/Forge output.
- Given an action with structured `damage[]`, when the block is validated and exported, then the auditor's DPR comes from the parts and the Forge `Z035` description carries the parts' numbers.
- Given a block with `saves`/`hit_dice`/`spellcasting`/`resources`, when the sheet renders, then those values appear.
- Given `features` instead of `traits`, when the block is canonicalized, then `traits` holds them.

## Spec Change Log

## Design Notes

Field names are chosen to match `prototype-2` where it names the same
thing (`saves`, `initiative`, `passive_perception`, `hit_dice`,
`spellcasting.dc/attack_bonus/slots`, `resources`, `damage[].{dice,count,
sides,bonus,average,type}`) without adopting its nesting or its derived
`power` block. Forge BIDs available for the new data: none beyond the
frozen table (Z023-Z028 are ability MODIFIERS, not save bonuses — a real
`saves` map must never be written there); the Forge gain is an accurate
`Z035` attack text. The HTML sheet and the app carry the rest.

## Verification

**Commands:**
- `uv run --directory backend pytest tests/test_statblocks.py tests/test_combat.py tests/test_build_in_pipeline.py tests/test_export_api.py tests/test_knowledge.py -q` -- expected: all pass.
- `uv run --directory backend pytest -q` -- expected: full suite green.
- `cd frontend && npx vitest run src/views` -- expected: green.
- `make lint && make typecheck` -- expected: clean.
