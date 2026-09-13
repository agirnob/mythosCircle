# Research: Fantasy Grounds NPC import — story 5-3

Research-first leg (owner directive 2026-09-08: no format shapes from memory).
Run by scout `FgFormatResearch`, 2026-09-13. All claims carry source URLs;
anything not confirmable from public sources is marked **UNVERIFIED**.
FG Unity is the current client (4.x).

## 1. Recommended target surface

**Target = FG Unity 5E ruleset: NPCs window → Import Text button, with the
stat block formatted as a 2024 / 2022 5e stat block.**

Official 5E NPC/Encounters wiki page documents the NPC list window's two
purple import controls:
https://fantasygroundsunity.atlassian.net/wiki/spaces/FGCP/pages/996641934/5E%20NPCs%20and%20Encounters

- **Import Text** — "can be used to create NPC Stat Blocks from other,
  differently-formatted stat blocks… there are currently three modes for
  import: *2022 - Monsters of the Multiverse*, *2024 - D&D Beyond*, and
  *2024 - D&D Core Rules*."
- **Import** — file explorer that loads an NPC "previously saved or exported
  into an `.XML` file".

For a single-entity "download and import" flow, **Import Text** is the right
mechanism: the DM downloads a plain `.txt` stat block from mythosCircle,
selects the matching import mode (2022-MM or 2024-Core), pastes, clicks
Import, and FG writes a fully parsed NPC record into the campaign. It needs a
desktop interactive step (same class as 5-2's Forge modal) and matches the
5-2 lesson: the real surface is what a DM actually does — paste text into the
built-in importer — rather than an undocumented bulk XML path.

Why not the other two surfaces:

- Raw `<npc>` XML (Import button): exists, but SmiteWorks deliberately does
  not document the per-ruleset record schema. Their Module page says: "The
  best way to see an example of the data file format for a specific data
  type is to create a record of the desired type within a campaign, and view
  the campaign db.xml file in a text editor"
  (https://fantasygroundsunity.atlassian.net/wiki/spaces/FGCP/pages/996644362/Module+-+Data+Files).
  Reverse-engineering from a live FG export required (see §5).
- `.mod` module: CAN carry many NPC records (CoreRPG record path `npc`;
  every module needs `definition.xml` + `db.xml`; same Module doc) but has a
  heavier Library/Module install-and-activate flow, is overkill for one NPC,
  and default CoreRPG modules place records under read-only `reference.npcs`.

## 2. Verified format shape

### 2a. The recommended surface: 2022/2024 stat-block TEXT (not XML)

The Import Text tool parses two accepted plain-text stat-block styles, both
documented by FG Academy (community, written against the live importer):

**2023-era / D&D Beyond / Monsters-of-the-Multiverse style** — worked example
*Grondar the Brawler*
(https://www.fantasygroundsacademy.com/post/mastering-dnd5e-npc-statblocks-a-community-guide-for-publishers-homebrew-dms-conversions-devs,
updated 2024-01-05):

```
Grondar the Brawler
Humanoid (Half-Orc) Medium, Lawful Neutral
Armor Class 14 (Studded Leather)
Hit Points 45 (6d10 +12)
Speed: 30 ft.
STR DEX CON INT WIS CHA
16 (+3) 12 (+1) 14 (+2) 8 (-1) 12 (+1) 10 (+0)
Saving Throws Str +5, Con +4
Skills: Athletics +5, Intimidation +2
Damage Vulnerabilities None
Damage Resistances None
Damage Immunities None
Condition Immunities None
Senses: Darkvision 60 ft., normal
Languages: Common, Orc
Challenge 2 (450 XP)
Traits
Brawler. Grondar has advantage on attacks made with unarmed strikes.
…
Actions
Club. Melee Weapon Attack +5 to hit, reach 5 ft., one target. Hit: 8 (1d8 +3) bludgeoning damage.
```

Exact-format requirements (same guide): no `Name:`/`Race:` labels, no
colons/bold/bullets/lists, `AC`/`HP` spelled out, ability scores as one
`STR DEX CON INT WIS CHA` header line followed by `16 (+3) …`, `Saving
Throws Str +5`, `Skills:`, damage/condition lines, `Senses:`, `Languages:`,
`Challenge N (XXX XP)`, `Traits`, `Actions`. The same article lists a
deliberately incorrect example showing what the parser rejects (labeled
keys, `80'` foot notation, bullets, no XP parenthetical in Challenge).

**2024 / 2025-SRD style** — worked example *Water Weird*
(https://www.fantasygroundsacademy.com/post/npc-stat-block-for-the-5e-2024-rule-set,
published 2025-07-01):

```
Water Weird
Large Elemental, Neutral
AC 13 Initiative +3 (16)
HP 65 (10d10 + 10)
Speed 5 ft., Swim 60 ft.
MOD SAVE MOD SAVE MOD SAVE
Str 17 +3 +3 Dex 16 +3 +3 Con 13 +1 +1
Int 11 +0 +0 Wis 10 +0 +0 Cha 10 +0 +0
Resistances Fire
Immunities Poison; Exhaustion, Grappled, Paralyzed, Petrified, Poisoned, Prone, Restrained, Unconscious
Senses Blindsight 30 ft.; Passive Perception 10
Languages Understands Primordial but can't speak
CR 3 (XP 700; PB +2)
Traits
Invisible in Water. …
Water Bound. …
Actions
Surge. Melee Attack Roll: +5, reach 10 ft. Hit: 13 (3d6 + 3) cold damage.
…
Information:
Appearance: …
Tactics: …
Habitat: …
Gear: …
Treasure (Loot): …
```

The 2024 format renames the ability line to `MOD SAVE` + `Str <score> +<mod>
+<save>`, adds `Initiative +mod (dex)`, `CR <x> (XP <x>; PB +x)`, and an
optional trailing `Information:` section (Appearance/Tactics/Habitat/Gear/
Treasure). These are the two exact shapes the exporter must emit; the DM
picks the matching mode in the Import Text dialog.

### 2b. The raw `<npc>` record (Import / export `.XML` / `.mod db.xml`)

Structure verified at CoreRPG level (Module - Data Files; FG modguide
https://www.fantasygrounds.com/modguide/database.xcp):

- Data is a tree under root; leaf nodes carry a `type` attribute ∈
  {`number`, `string`, `formattedtext`, `image`, `token`, `dice`,
  `windowreference`}; no-type tags are subtree containers.
- CoreRPG editable record path for NPCs is `npc`; read-only module path is
  `reference.npcs`.
- NPC records are identified by enumerated children (`npc.id-00001.…` style,
  e.g. `charsheet.id-00005.inventorylist.id-00001.name` in the modguide).
- Modules: every `.mod` needs root `definition.xml` (+ `db.xml` for GM
  data), `<root version=… release=…>` with `<name>`, `<displayname>`,
  `<category>`, `<author>`, `<ruleset>`, `<replaces>`.

**The exact 5E NPC record field-tag names (leaf tags under an `npc` node)
are UNVERIFIED** — not in any official public doc (SmiteWorks points users
at a saved campaign db.xml), and no community converter mirror publishes a
raw 5e NPC record (details in §5).

### Field list of the 5E NPC record (verified sheet fields)

From the official wiki (5E NPCs and Encounters, above): **Name**; **ID
toggle** (identified/non-ID name); **Pictures/Tokens** (Picture, Token-Flat,
Token-Camera); **Size** (Tiny/Small/Medium/Large/Huge/Gargantuan); **Type**
(MM creature types); **Alignment** (9 PHB alignments, may be abbreviated);
**Armor Class** (Legacy: number + extra box e.g. `13 (16 with Mage Armor)`;
2024: single number) + **Initiative** (2024 only); **Hit Points** (avg +
dice string, e.g. `16 (3d8 +3)`); **Damage Threshold**; **Speed**;
**Statistics** (STR/DEX/CON/INT/WIS/CHA, bonuses auto-calculated);
**Saving Throws** (e.g. `Dex +3, Con +2`); **Skills** (e.g. `Perception +7,
Stealth +5`); **Damage Vulnerabilities / Resistances / Immunities**;
**Condition Immunities**; **Senses**; **Languages**; **Challenge & XP**;
**Traits**; **Actions**; **Reactions / Legendary Actions / Lair Actions**;
**Innate Spells / Spells**; **Gear** (added to the 2024 NPC record, ruleset
patch 2025-04: "[5E][ADDED] Gear field added to 2024 version of NPC record" —
https://www.fantasygrounds.com/filelibrary/patchnotes_ruleset.html);
**Notes** tab (Habitat/Treasure/description). 2024 vs Legacy(2014) record
duality confirmed by patch notes (2024-09 "New D&D (2024) records data
support… new layouts and version setting available for (2024) vs. legacy
(2014) records (… NPC)"); record-based damage/condition fields confirmed
("[5E] NPC vulnerabilities, resistances and immunities calculated from
record fields").

## 3. Field mapping table (mythosCircle stat_block → FG 5E NPC)

Mapped via the **2024 stat-block Import Text** surface unless noted.
`[sheet]` = sheet field (official wiki); `[txt]` = stat-block text field
(FG Academy 2024 guide). **NO MAP** where FG has no receiver.

| mythosCircle stat_block field | FG 5E target | Notes |
| --- | --- | --- |
| attributes STR/DEX/CON/INT/WIS/CHA | `MOD SAVE` line scores `Str <score> +<mod> +<save>` `[txt]` / Statistics `[sheet]` | bonus auto-calculated; 2024 includes save per ability |
| saves | `+<save>` column in MOD SAVE line `[txt]` / Saving Throws `[sheet]` | abbreviated ability, e.g. `Dex +3` |
| skills (name + bonus) | `Skills:` list `[txt]` `[sheet]` | full skill names, e.g. `Perception +7, Stealth +5` |
| combat.AC | `AC <n>` `[txt]` / Armor Class `[sheet]` (2024 single value) | legacy sheet has extra "with X" text box |
| combat.HP (max + HD) | `HP <avg> (<hd> + <mod>)` `[txt]` / Hit Points `[sheet]` | second box holds dice string if random/max HP option used |
| combat initiative | `Initiative +<mod> (<dex>)` `[txt]` `[sheet]` | 2024 record only |
| speed | `Speed <n> ft.` (comma-separated speeds) `[txt]` `[sheet]` | e.g. `5 ft., Swim 60 ft.` |
| challenge rating (CR) | `CR <x> (XP <x>; PB +<x>)` `[txt]` / Challenge & XP `[sheet]` | 2022/MM style: `Challenge <x> (<x> XP)` |
| XP (derived) | inside CR line `[txt]` / sheet | derive like 5-2's level_cr looseness |
| alignment | name line `<size> <type>, <alignment>` `[txt]` / Alignment `[sheet]` | 2024 stat block carries alignment on the header line |
| size | `<size>` on header line `[txt]` / Size `[sheet]` | Tiny…Gargantuan; drives token size + some effects |
| type (creature kind) | `<type>` on header line `[txt]` / Type `[sheet]` | only MM types recognized for effects |
| senses | `Senses <list>` `[txt]` / Senses `[sheet]` | e.g. `Blindsight 30 ft.; Passive Perception 10` |
| languages | `Languages <list>` `[txt]` / Languages `[sheet]` | comma-separated |
| traits | `Traits` block `[txt]` / Traits `[sheet]` | parser keys off exact trait names (Regeneration, Magic Resistance, Improved/Superior Critical, Damage Threshold, Spellcasting, Innate Spellcasting) |
| actions/attacks (to-hit + damage) | `Actions` block `[txt]` / Actions `[sheet]` | exact attack phrasing: `Melee Attack Roll: +5, reach 10 ft. Hit: 13 (3d6 + 3) cold damage.` |
| reactions / legendary / lair actions | `Actions` block (named sections) `[txt]` / Reactions, Legendary Actions, Lair Actions `[sheet]` | parser drives CT effects |
| spells | `Spellcasting` / `Innate Spellcasting` trait text `[txt]` `[sheet]` | spells populate only if the source PHB module is open in campaign; exact slot/level format required |
| **NOT map-able** | — | FG has no remote-image field (see §4); legendary-"count" is expressed in action text, not a numeric field |

## 4. Portrait reality

FG's image value type is a **bitmap embedded in the database**, not a URL
("Image values contain a bitmap representation of a drawing or an image" —
The Data Base modguide); module images must be bundled files ("Any images
(handouts, maps) referenced by the XML data files will need to be included
as well" — Module - Data Files).

- The 5E NPC record has a **Pictures tab** holding **Picture**, **Token -
  Flat** (2D top-down), and **Token - Camera** (3D) image fields. FG 4.8
  patch note "PC portrait and NPC picture fields will fit the image to the
  full field space" confirms these are image fields.
- A signed HTTPS URL **cannot** be the value of these fields, and the
  stat-block text import has no image field at all. The portrait must
  therefore **not be embedded in the export** (consistent with the
  no-binary constraint).
- **Practical recommendation:** the Export Text artifact carries the signed
  portrait URL as a line in the NPC's `Information:` section (2024 format
  supports an `Information:`/`Appearance:` block; FG Academy shows Appearance
  under Information, separate from the parsed stat block). The DM opens that
  URL to download the PNG and drags it onto Picture/Token fields in the FG
  Pictures tab (unlock sheet → drag from Assets). As with 5-2, the signed
  URL's job is presentation/download, not programmatic image binding.
- Batch alternative (needs operator action + binary, not this story): a
  `.mod` may bundle portrait PNGs, but that violates mythosCircle's "never
  embeds binaries" rule — reject for 5-3.

## 5. Open questions / unknowns (live FG product check — 5-2 "spec-time operator action" precedent)

1. **Exact raw 5E `<npc>` record field tags** (needed only if 5-3 later
   targets the Import-XML button or `.mod` bulk export — NOT needed for the
   recommended Import-Text surface). **UNVERIFIED**; SmiteWorks does not
   publish them.
   *Check:* In FG Unity 5E, create a blank NPC, set HP/AC/stats/skills/
   Traits/Actions, save, close campaign, open the campaign folder's
   `db.xml`, record the leaf-tag names under the `npc.*` node. Then run
   **Export NPC** (record export button, added 2025-02 per patch notes) and
   diff the exported `.xml`.
2. **Which Import Text modes exist at the target version + exact parsing**
   (e.g. does `2024 - D&D Core Rules` require `MOD SAVE` + `Str … +mod
   +save` exactly as Academy shows; how are `Initiative`/`PB` parsed when
   absent). The two FG Academy guides agree but are community; the wiki page
   is "in the process of being updated".
   *Check:* open NPCs → Import Text, list the `mode` dropdown entries, paste
   the Water-Weird text verbatim under `2024 - D&D Core Rules`, confirm the
   field values on the NPC sheet; repeat with a partial stat block (missing
   PB) to see what is optional.
3. **Whether the parser auto-creates Combat Tracker effects for actions/
   traits** exactly as the wiki's wording table describes (Melee Attack / DC
   saves / Spellcasting wording) — affects how faithfully Actions must be
   phrased to be "table-ready" (auto-rolls) rather than text-only.
   *Check:* import the Water-Weird example, drag the NPC onto the Combat
   Tracker, verify Surge's attack/save/condition effects auto-build
   (https://fantasygroundsunity.atlassian.net/wiki/spaces/FGCP/pages/996641984/5E+Combat+Tracker).
4. **FG Classic vs Unity divergence.** Confirmed in direction: the
   modguide/database.xcp is the legacy FG (©2004-2010) doc; the current
   client is FG Unity (4.x) whose NPC tooling is documented on the modern
   Customer Portal wiki. The Import Text stat-block parser is a **Unity
   5E-only** feature; no evidence it exists in FG Classic. The exporter
   should target FG Unity 5E and state that requirement.
   *Check (low priority):* confirm in the targeted client's ruleset that the
   NPC list shows the purple Import Text + Import buttons (Unity) vs. a
   text-only import (Classic).

## Source index

- FG Customer Portal — 5E NPCs and Encounters
  (https://fantasygroundsunity.atlassian.net/wiki/spaces/FGCP/pages/996641934/5E%20NPCs%20and%20Encounters,
  updated 2025-04-02; official) — import buttons, sheet fields, trait/action
  wording, spellcasting format.
- FG Customer Portal — Module - Data Files
  (https://fantasygroundsunity.atlassian.net/wiki/spaces/FGCP/pages/996644362/Module+-+Data+Files,
  updated 2023-06-12; official) — module files (definition.xml, db.xml,
  client.xml), `<root>` element, node/data types, CoreRPG record paths incl.
  `npc` / `reference.npcs`.
- FG modguide — The Data Base (https://www.fantasygrounds.com/modguide/database.xcp;
  legacy FG doc; official) — db.xml tree model, node types, image bitmap
  semantics, `charsheet.id-…` id scheme.
- FG — Ruleset Updates / patch notes
  (https://www.fantasygrounds.com/filelibrary/patchnotes_ruleset.html;
  current to 2026-07; official) — 2024 NPC record, Gear field (2025-04), NPC
  record resistances, record export button (2025-02), NPC Pictures tab,
  portrait fit.
- FG Academy — Mastering DnD5e NPC Statblocks
  (https://www.fantasygroundsacademy.com/post/mastering-dnd5e-npc-statblocks-a-community-guide-for-publishers-homebrew-dms-conversions-devs;
  updated 2024-01; community) — Grondar worked example + rejected-format
  anti-example.
- FG Academy — NPC stat block for the 5e 2024 Rule Set
  (https://www.fantasygroundsacademy.com/post/npc-stat-block-for-the-5e-2024-rule-set;
  2025-07-01; community) — Water Weird 2024 worked example, MOD SAVE / CR/PB
  / Information sections.
- Nexus-Ruleset campaign/import_npc.xml
  (https://github.com/Night-Tech-Studios/Nexus-Ruleset-for-fantasy-grounds/blob/main/campaign/import_npc.xml;
  third-party ruleset; confirms the import window shell +
  `populateImportModes("npc", mode)` + a `statblock` text field + right-side
  `description` field).
- Communities checked as one-click NPC converters (no raw 5E NPC record:
  n/m): mjmcphee/dnd-npc-generator, nathenxbrewer/FG5E-Tools (player
  characters only), http://beyond2fantasygrounds.com/devel/index.html
  (legacy PC converter).