# Character generation contract for fine-tuning

This describes the current implementation, with Character Forge as the complete
worked example. The example is synthetic and replayed through the real worker
with mocked HTTP responses. It is not a response obtained from Gemini or Gemma.

Start with `training/character/complete.jsonl`: it contains the actual system
message, full assembled user prompt, and a validated target assistant response.
The JSONL is a messages-based training example; adapt its packaging to your
trainer. It is one example, not a sufficient training dataset by itself.

## Files you can use directly

All paths below are relative to `docs/training/character/`.

| File | Contents |
| --- | --- |
| `forge-input.json` | Structured seed submitted by Forge as the job payload |
| `context.json` | Theme seed, roster, and context facts for this example |
| `system-prompt.txt` | Exact provider system message |
| `user-prompt.txt` | Complete, unabridged Forge prompt, including reference tables |
| `request.json` | Actual OpenAI-compatible request body, including schema and token budget |
| `expected-output.json` | Complete target model output, before server transformations |
| `committed-output.json` | Character after real validation, canonicalization, and storage |
| `complete.jsonl` | System + user + successful assistant training example |
| `record-repair.jsonl` | Separate missing-record-field repair training example |
| `stat-repair.jsonl` | Separate invalid-AC repair training example |
| `record-repair-exchange.json` / `stat-repair-exchange.json` | Actual repair request body and target response |
| `replay-traces.json` | Every initial and repair exchange, final records, and call telemetry |
| `conditional-examples.json` | Validator-checked BBEG and Monster output variants, showing boss/casting/CR shapes |
| `character-record-contract.txt` | All required character record sections and conditional boss rules |
| `stat-block-rules.txt` | Full mechanical instructions embedded in prompts |
| `schemas.json` | Actual wave and stat-repair response schemas |
| `vocabularies.json` | Actual race, class, alignment, skill, edge, and spell reference sets |
| `validation.json` | Actual validator verdicts and successful replay results |

Regenerate these files from the repository root:

```bash
backend/.venv/bin/python scripts/export_character_training_example.py
```

The exporter uses a temporary database and HTTP MockTransport. It does not read
your application's database or API keys, and does not call an external model.
Conditional examples are reference outputs, not additional prompt/completion
training rows; their prompts would need corresponding role and context changes.

## 1. What enters Forge

The browser's `CharacterForgeView.vue` calls `CharacterSheetEditor.buildFigure()`
and submits a `build_in` job through `POST /api/jobs`. A draft uses:

```json
{
  "campaign_id": "<your Generic library campaign ULID>",
  "kind": "build_in",
  "payload": {
    "theme": "High Fantasy",
    "key_figures": [{"name": "Ahmet the Davulcu", "role": "NPC"}]
  }
}
```

This API request is not the model's input format. Python expands it into the
prompt in `user-prompt.txt`. `forge-input.json` additionally pins the example's
level, race, class, and alignment through a partial authored stat block.

For a world-bound Forge, the payload instead supplies `places: []`,
`factions: []`, `key_figures: [...]`, and `notes: ""`; the world supplies its theme.
The seed can contain a name, role, description, partial record/stat block, and
declared relations. Authored values are ground truth, not suggestions: the
pipeline restores them after repairs. Blank/unprovided fields are generated.

The API verifies authentication and campaign access. The store validates payload
shape, role, supplied stat-block subsections, relations, Generic-library rules,
queue capacity, and budgets before enqueuing. The worker rechecks runtime state.
The job snapshots provider routing when it starts, including an optional backup.

## 2. Exactly what context the model sees

The provider supplies this system message:

```text
You are a world-building assistant for a TTRPG Game Master. Write concise, evocative, game-ready material. No preamble.
```

The user message is assembled in this order:

1. Task introduction and demand for one JSON object.
2. Campaign title, description, theme, and custom lore.
3. Submitted sections, positional entity refs, and verbatim DM-authored fields.
4. Any mandated missing relation targets, with names and kinds.
5. DM notes, when supplied: authoritative level/role/class/power directives.
6. Entity-kind, generation-dial, and pinned-seed contracts.
7. Stat-block rules and spell reference tables.
8. Full character-record requirements, including conditional boss sections.
9. Output envelope, ref discipline, edge vocabulary, direction guidance, counters.

For the worked example the theme context is:

```json
{
  "title": "Generic (High Fantasy)",
  "description": "A classic world of kingdoms, wilds, and old magic — quest-givers, rival houses, and ruins hiding older powers.",
  "theme": "High Fantasy",
  "custom_lore": "Magic is real but rarely trusted; oaths bind, debts outlive people, and every road owes a toll to somebody."
}
```

Generic is a storage library. Other characters in it never become narrative
context. A world-bound Forge wave 1 sees the campaign seed and submission; it
does not automatically receive a retrieved list of all existing world entities.
The runner still reads existing world state for relation resolution, mandates,
merging, and connectivity. Do not confuse that state with prompt contents.

Wave 2, when free-form build-in notes need digestion, receives bounded retrieved
world context: deterministic graph traversal, normally depth 1 and 24 detailed
entities, plus compact core anchors where required. Draft Forge does not need
wave 2. The separate Ask/generate flow also uses retrieved world context.

## 3. Model output: envelope and every character section

Forge/build-in expects:

```json
{
  "entities": [
    {
      "ref": "E0",
      "kind": "character",
      "name": "Ahmet the Davulcu",
      "text": "Optional short narrative",
      "data": {"name": "Ahmet the Davulcu", "role": "NPC"}
    }
  ],
  "edges": []
}
```

The abbreviated `data` above only illustrates placement. Use
`expected-output.json` for the complete valid output; it includes all required
character fields and structured attack damage. Do not put record fields beside
`data`, and do not nest narrative fields under `identity` or `lore`.

| `data` field | Requirement |
| --- | --- |
| `name` | Nonblank character name; match the entity name |
| `role` | `NPC`, `BBEG`, or `Monster` |
| `level_cr` | Display string: `level 5` or `CR 1/2`; stat identity is authoritative |
| `race_type` | Race/type display text |
| `class_profession` | Class/profession display text |
| `alignment` | Alignment display text |
| `personality` | Nonblank personality |
| `secret` | Specific concealed fact |
| `rumor` | Concrete in-world claim |
| `party_hook` | Concrete way the party engages the character |
| `appearance` | Face, body, clothing, scars, marks, and other visual detail |
| `background` | History |
| `goals` | Motivations/objectives |
| `relationships` | Narrative relationships; actual graph links are separate edges |
| `voice_style` | How the character speaks |
| `catchphrases` | Recognizable spoken phrases |
| `world_integration` | Object containing all five fields below |
| `stat_block` | Mechanics object described below |
| `boss` | Required for BBEG/Monster; entirely absent for NPC |

Every narrative/display field above must be a nonblank string. Required
`world_integration` fields, each also a nonblank string:

```json
{
  "reputation": "How people know the character",
  "factions": "Affiliations",
  "current_location": "Where the character can be found",
  "reaction_matrix": "C0: friendly; C2: suspicious — ONE string, never an object",
  "on_defeat": "What happens when defeated"
}
```

BBEG/Monster additionally requires all four `boss` strings. Use `"None."` for an
inapplicable field, rather than removing the field or leaving it blank:

```json
{
  "lair_actions": "...",
  "legendary_actions": "...",
  "immunities": "...",
  "vulnerabilities": "..."
}
```

### Stat block

| Field | Shape and rules |
| --- | --- |
| `identity` | Required object: `role`, `race`, and either `level` or `cr`; optional `class`, `alignment` |
| `attributes` | Required six integers: `str`, `dex`, `con`, `int`, `wis`, `cha`, each 1–30 |
| `combat` | Required positive integer `ac`, `hp`; optional `hit_dice` string |
| `skills` | Optional list of `{name, bonus}`; known skill and integer bonus |
| `actions` | Optional list of `{name, description, to_hit?, damage?}`; include applicable actions |
| `traits` | Optional list of `{name, description}` |
| `spells` | Optional list of known spell-name strings, with class membership and no repeats; forbidden for Monster |
| `saves` | Optional object keyed by ability, with integer bonuses |
| `features` | Optional list of nonblank feature-name strings, distinct from described traits |
| `initiative` | Optional integer, validated range −10…30 |
| `passive_perception` | Optional integer, validated range 1…40 |
| `proficiency_bonus` | Optional integer, validated range 1…12 |
| `spellcasting` | Optional object: `dc`/`attack_bonus` integers −5…40, and `slots` list of integers 0…20 |
| `resources` | Optional name → nonnegative integer object |
| `power` | Server-derived audit annotation. The model must not generate this. |

Use the reference file for current allowed classes, races, skills, alignments,
and spells. NPC/BBEG use integer `level` 1–20 and must not supply `cr`. Monster
uses integer `cr` 0–30 or `"1/8"`, `"1/4"`, `"1/2"`, and must not supply `level`.
NPC/BBEG race is from the supported race vocabulary; a Monster race is a nonblank
type string. Record role and stat identity role must agree.

A generated damaging action uses this machine-readable damage format:

```json
{
  "name": "Drumstick Strike",
  "to_hit": 5,
  "description": "Melee Weapon Attack: +5 to hit, reach 5 ft., one target. Hit: 5.5 (1d6 + 2) bludgeoning damage.",
  "damage": [{"dice": "1d6", "count": 1, "sides": 6, "bonus": 2, "average": 5.5, "type": "bludgeoning"}]
}
```

Use one damage part per damage type. Average, dice, and description should agree.
A Multiattack routine describes its attack count and does not carry its own
damage list. Non-damaging actions do not need damage. The manual Add Character
form can accept a dice-string shortcut; that is a different authoring format.

The prompt asks for deeper action lists at higher challenge: normally 2–3 actions
below level/CR 10 and 3–5 above, including distinct attacks and signature riders.
This is a quality instruction; action-count minimums are not hard validator gates.
Optional mechanics are included when appropriate, not populated with invented
numbers merely to make every possible key appear.

### Edges

Wave-1 refs are canonical positional `E0`, `E1`, etc.; not UUIDs, ULIDs, names,
or zero-padded `E01`. IDs are allocated by the server after parsing. Edges use
`{src, dst, type, counter?, reason}`; endpoints must exist and differ, type must
be in the closed vocabulary, counter is an integer within that type's allowed
range, and reason is a nonblank explanation. Direction must fit endpoint kinds
(for example `bases_at` targets a place).

A solitary character uses `"edges": []`. Never invent an absent `E1` to satisfy
a relationship. Dangling canonical wave-1 links are dropped with audit; malformed
refs, unsupported types, and invalid counters on retained links remain errors.
Wave 2 uses `N0` for newly generated entities and `C0` for context entities, and
its new entities must remain anchored to the core. Declared DM relations are
resolved and constructed by the pipeline; the model must not duplicate them.

## 4. Validation and repair order

The actual `run_build_in` ordering is:

1. Load campaign and world state, substitute the theme seed for Generic, validate
   sections, snapshot base revision, create the provider-call budget.
2. Expand requested name counts when present. Resolve declared relation targets;
   unresolved named targets join the roster as mandated entities.
3. Assemble the prompt and response schema. A single Forge character is one
   wave-1 call. Larger submissions use weighted chunks (weight 12, hard cap 16)
   and an additional best-effort edges-only wiring pass.
4. Receive the completion. Reject connection/HTTP failures, blank content, and
   incomplete completions. Eligible transport/429/5xx errors may try the configured
   backup once; this does not fix semantically bad JSON.
5. Strip an optional JSON code fence and parse the envelope. Invalid JSON gets
   one bounded corrective re-elicitation. Truncation can get one doubled-token
   retry within the operator ceiling. All physical attempts consume budget.
6. Drop dangling canonical wave-1 edge refs, recording the dropped rows. Normalize
   edge directions; kind-invalid edges receive one bounded edges-only repair and
   can be dropped with audit in wave 1. Validate entities, refs, edge types,
   counters, and graph semantics. Self-loops and duplicate handling are centralized.
7. Strip inappropriate stat blocks from noncharacters. Restore authored fields;
   reject an authored character replaced by a place/faction. Enforce flat
   place/faction records when relevant, then missing names and generated counts.
8. Check every character's complete narrative record. Missing or invalid required
   fields get a bounded record repair, in chunks of at most four characters.
   Record repair returns `records`, never `entities` or `stat_blocks`. Authored
   fields are restored again.
9. Merge partial authored stat blocks. Validate authored mechanics before any
   repair: invalid authored mechanics are rejected; complete authored blocks are
   frozen and preserved byte-for-byte.
10. Enforce generated stat blocks: canonicalize known formatting variants and
    damage, apply role pins, audit mechanics, and collect violations. Monster
    power-only violations may be conformed deterministically first. Shape defects
    get up to three bounded stat-repair passes, one character per call. Only
    permitted repair sections are merged; healthy identity values are protected.
    Recheck after each pass, then perform a final applicable power conform.
    Persistent violations fail before this wave commits.
11. Restore authored stat fields and role pins. Missing mandatory relation targets
    get one bounded re-emission and validation; a second miss fails.
12. Construct declared relations, fill or drop blank edge reasons, and check
    required connectivity for count-generated entities when applicable.
13. Commit through the store. Character creation uses fresh ULIDs. Wave 1 allows
    edgeless characters. Place/faction upserts, duplicate-edge audit, and a bounded
    stale-head rebase are handled by the store/pipeline.
14. If build-in notes require wave 2, retrieve core/world context and generate
    additional entities. Validate and repair records/stats, enforce core anchors
    with one bounded edges-only repair, and commit that wave separately.
15. Mark the job succeeded with revisions, counts, merge/drop audits, context
    summary, and per-label call telemetry. Emit job/queue updates. Cancellation
    checks occur between stages. A failed later wave does not undo wave 1.

The wave JSON schema deliberately leaves `data` open. It does not encode the
entire character contract; a schema-valid completion can still fail the Python
record/mechanics validators. Use both for evaluation.

### Combat checks versus quality guidance

All roles get structural, readable-damage, and optional-mechanics checks.
NPC/BBEG below their expected combat band are annotated for the DM, not inflated
by monster-grade repair. Monster underpowered DPR and frail HP can fail; HP is
frail below half the lower expected band. Overpowered characters are annotated,
not rejected. True noncombatants are exempt from damage-based grading. Merely
calling an attack “massive damage” without readable damage is invalid.

The auditor accounts for multiattack, save-for-half, area targets, limited-use
attacks, and legendary actions. `combat.py` and the exported rules are the source
of truth; do not teach a model to manufacture the server's `power` stamp.

## 5. Separate model tasks use separate envelopes

| Task | Model response | Where it goes |
| --- | --- | --- |
| Forge / build-in wave | `{"entities": [...], "edges": [...]}` | Validated graph wave, then store commit |
| Ask / generate | `{"candidates": [full record, ...]}` | Prompt asks for 3; 2–3 survivors are staged for DM selection |
| Regenerate | `{"candidates": [one full record]}` | Proposed replacement/section reroll; only requested fields change |
| Name repair | `{"names": [{"ref": "E0", "name": "..."}]}` | Missing names only |
| Record repair | `{"records": [{"ref": "E0", "data": {...}}]}` | Missing/wrong record fields; stat_block excluded |
| Stat repair | `{"stat_blocks": [{"ref": "E0", "stat_block": {...}}]}` | Flagged mechanics within edit scope |
| Edge repair/wiring | `{"edges": [...]}` | Frozen entities; only links can change |
| Manual Add Character | No model call | Fully authored sheets are validated and committed |

Generate candidates place the record directly in each candidate, with candidate
edges shaped as `{endpoint: "C0", direction: "outbound", type, counter?, reason}`.
They can also declare related entities with `N` refs. This differs from Forge's
outer `src`/`dst` edges. Generate uses an existing world and requires valid
connections; it does not silently substitute the solitary Forge rules.

Fine-tune on each intended task's exact prompt and envelope. Do not combine all
of these envelopes into one assistant response. The supplied three JSONL files
show complete Forge output, record repair, and stat repair as independent rows.
The deliberately invalid initial outputs in `replay-traces.json` are diagnostic
inputs to repairs, not successful assistant training targets.

## 6. Building and evaluating a larger dataset

Use diverse names, themes, roles, levels/CRs, classes, spellcasters, noncombatants,
authored partial fields, and world connections. Include solitary characters with
empty edges and multi-entity examples with real targets. Include BBEG/Monster
boss sections and caster spellcasting only where applicable. Keep authored values
identical in targets. Split evaluation by character/world, rather than allowing
near-duplicate prompts into both training and evaluation.

Check raw model outputs before repairs as well as final replay success: valid
JSON/envelope, complete required fields, correct refs/kinds, mechanical validity,
authored-field fidelity, and zero invented endpoints. Track repairs/calls needed;
a model needing multiple repairs should not look equivalent to a clean first pass.
Review narrative quality separately, since a validator cannot decide whether a
secret is interesting or a party hook is playable.

Existing real-call journals live beside the local database in
`backend/data/llm-journal/<job-id>.jsonl` by default. They contain prompt,
response, schema, settings, outcomes, usage, and labels such as `wave1`, `record`,
and `stat`. They can contain private world text and failed attempts. An HTTP
success alone does not prove a completion passed the downstream validators;
validate and review before treating it as a positive training target.

## Source map

- `frontend/src/views/CharacterForgeView.vue`: Forge submission and Generic mode.
- `frontend/src/components/CharacterSheetEditor.vue`: authored seed construction.
- `backend/app/api/jobs.py`, `backend/app/store/jobs.py`: auth, enqueue and payload checks.
- `backend/app/providers/llm.py`: exact system message and HTTP body.
- `backend/app/prompts.toml`: shared kind/dial/pinned-field instructions.
- `backend/app/pipeline/build_in.py`: prompts, wave execution, records, relations, repairs.
- `backend/app/pipeline/statblocks.py`: stat rules, repairs, canonicalization, power stamp.
- `backend/app/pipeline/knowledge.py`: supported vocabularies and mechanical validator.
- `backend/app/pipeline/combat.py`: DPR/HP grading and action analysis.
- `backend/app/store/candidates.py`: required character/world/boss fields.
- `backend/app/store/direct.py`: authored character and stat validation.
- `backend/app/pipeline/retrieval.py`: deterministic bounded graph context.
- `backend/app/pipeline/generate.py`, `regenerate.py`: other character-generation tasks.
- `backend/app/pipeline/wave.py`, `budget.py`: retries and call ceilings.
- `backend/app/core/journal.py`: actual-call transcription.
