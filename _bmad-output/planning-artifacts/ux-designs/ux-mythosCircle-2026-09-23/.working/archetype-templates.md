# Archetype templates — place & faction (v3, draft 2026-09-24)

Inspired by the Lore-Weaver Create-City wizard (owner reference 2026-09-24): Concept
form (type cards, size slider, environment cards, tag input, uniqueness, advanced
options collapse), What-will-be-generated chips, Live Preview rail, Suggested Next
Steps. Templated per archetype; each template = a form block + generation profile.

## Shared anatomy

Every place/faction creation runs through the same four-part form, in the same order
(the walk):

1. **Concept** — the archetype card picker + the kind's identity fields + tags +
   theme + uniqueness. This is what Capture seeds if the DM uses it.
2. **Details** — the archetype's signature fields (below).
3. **World & relations** — region/environment placement + relation drafts +
   offered companions (tick-to-include).
4. **Review** — dial + "what will be generated" chips + the rail mirror + submit.

Simple Mode shows Concept + Review only (dials default, companions listed as
one-tap "add", generation fills the rest). Advanced Mode shows all four steps.

## Place template (shared bones)

- **Environment** (card select): Coastal / River / Lake / Plains / Forest / Mountain /
  Desert / Other.
- **Climate** (dropdown, optional): Temperate, Arid, Cold, Tropical, Monsoon, Magic-twisted, Other.
- **Economy** (card select): Trade / Agriculture / Mining / Craft / Smuggling /
  Servitude / Faith / Knowledge / Mixed. When chosen, an **Exports & Imports** detail
  (tag input, optional): what the place sends out, what it takes in — the trade story
  that makes the economy a hook, not a label. Seeded per archetype (City: the good it
  is known for + the good it depends on; Fort: arms out, food in; Ruin: nothing but
  scavengers). Seeds/feeds: the What-will-be-generated Economy & Trade chip, and the
  uniqueness angle when the DM leaves it blank.
- **Key Characteristics** (tag input): free tags, seeded per archetype.
- **Short description / theme** (≤300) and **What makes this unique** (≤200).
- **What will be generated** (chips, tied to dial): Government & Law · Economy &
  Trade · Districts & Locations · Notable NPCs · Factions & Groups · History ·
  Rumors & Secrets · Points of Interest · (Map, optional, pillar only).
- **Advanced Options** (collapse): custom section overrides, seed note, offered
  companions list.

---

## 1. City
- Signature: Government & Law (ruler, council, law), Economy & Trade as first chip;
  the scale and density the archetype implies (population in tens of thousands+,
  trade web reach).
- Characters: cosmopolitan density; name recognizes the trade/web it sits on.
- Uniqueness angle: the *one thing* no neighbor has — source of the city's pride and
  its pressure.
- Seed tags: Busy Harbor · Diverse Culture · Old Walls · Corruption
- Dial: important. Companions: Mayor (controls), Watch captain (employs), Market
  master (employs).

## 2. Town
- Signature: one or two powers (a lord, a guild), local economy, single High Street /
  market square focus; the compact scale the archetype implies.
- Characters: self-contained; everyone knows everyone.
- Uniqueness angle: the odd local custom or burden it alone carries.
- Seed tags: Local Lore · Tradesfolk · One Big Festival
- Dial: simple. Companions: Mayor (controls), Constable (employs).

## 3. Village
- Signature: faces first, gossip economy; agriculture or one craft; a single notable
  landmark and one or two stories; the small scale the archetype implies.
- Characters: warmth and suspicion in equal measure; outsiders noticed instantly.
- Uniqueness angle: what it fears and what it protects.
- Seed tags: Small · Superstitious · Self-Reliant
- Dial: simple. Companions: Elder (controls). (Innkeeper only if Tavern archetype
  is also ticked — always DM's choice.)

## 4. Hamlet
- Signature: one notable thing (a mill, a shrine, a madwoman's tower, a crossroads
  inn); everything else minimal.
- Characters: a handful of faces; one thread of story.
- Uniqueness angle: the notable thing *is* the hamlet.
- Seed tags: Tiny · One Story · Out of the Way
- Dial: draft. Companions: none.

## 5. Tavern / Inn
- Signature: faces + secrets first; owners, regulars, and who they fear; the
  establishment's trade beyond lodging (stable, bathhouse, gambling den, meeting
  room).
- Characters: the keeper is the door to the world; regulars are the local chorus.
- Uniqueness angle: what the building hides — a tunnel, a ledger, a ghost, a debt.
- Seed tags: Good Ale · Worse Tales · Debt Collectors
- Dial: draft. Companions: Keeper (bases_at), Regular (bases_at), Patron (bases_at).

## 6. Temple / Sanctuary
- Signature: faith, ritual calendar, hierarchy, sacred geography; the faith's
  relationship to the rest of the world (shelter vs authority vs hidden hand).
- Characters: piety and politics; pilgrims as a constant current.
- Uniqueness angle: the relic, the miracle, or the vow the sanctuary was built on.
- Seed tags: Dedicated · Pilgrims · Old Rites
- Dial: important. Companions: High priest (controls), Acolyte (member_of), Pilgrim
  (member_of).

## 7. Ruin / Dungeon
- Signature: what's left and what's hidden; layers (surface remains vs intact
  depths); who currently occupies it (beasts, a cult, nothing yet).
- Characters: silence and hazard; the ruin speaks through what was left behind.
- Uniqueness angle: why it fell and what still works down there.
- Seed tags: Dangerous · Half-Buried · Something Wonders
- Dial: draft. Companions: Warden (protects), Guardian (protects) — only when the
  DM marks it occupied.

## 8. Fort / Garrison
- Signature: order, threat, gates; chain of command, supply, discipline; the area it
  patrols and the enemy it was built against.
- Characters: soldiers first; honor, boredom, resentment in the ranks.
- Uniqueness angle: the breach — moral, physical, or secret — in an otherwise total
  defense.
- Seed tags: Disciplined · Under Siege Mentality · Strict Ranks
- Dial: important. Companions: Commander (controls), Watch-captain (employs).

## 9. Market / Quarter
- Signature: commerce, deals, thieves; trading calendar, the square's geometry,
  guilds and fences sharing one street.
- Characters: merchants, porters, pickpockets; everything has a price and a rumor.
- Uniqueness angle: the one deal that could unbalance the whole district.
- Seed tags: Busy · Cutthroat · Seasonal Festivals
- Dial: simple. Companions: Guild factor (employs), Toll-keeper (employs), Smuggler
  (bases_at).

## 10. Wasteland / Frontier
- Signature: survival, lawless strips, scarce resources; what makes it hostile and
  what (or who) endures anyway.
- Characters: hard people with reasons to be far from everyone.
- Uniqueness angle: the bounty hidden in the waste that the law won't reach.
- Seed tags: Harsh · Unforgiving · Opportunists
- Dial: draft. Companions: Outlaw (bases_at), Hermit (bases_at), Caravan master
  (bases_at).

---

## Faction template (shared bones)

- **Wealth** (dropdown, optional): Poor · Rising · Comfortable · Wealthy · Vast.
- **Power base** (card select): Wealth / Arms / Faith / Knowledge / Blood / Numbers / Other.
- **Leadership style** (card select): Council / Autocrat / Cult of personality /
  Elected / Hidden hand.
- **Secrecy** (dropdown): Open · Discreet · Hidden · Unknown.
- **Key Characteristics** (tag input): seeded per archetype.
- **Short description / doctrine** (≤300) and **What makes this faction unique** (≤200).
- **What will be generated** (chips, tied to dial): Doctrine & Beliefs · Hierarchy &
  Leadership · Assets & Resources · Recruitment & Membership · Rituals & Practices ·
  Internal Factions · Rivals & Allies · Rumors & Secrets · Hooks & Quests.
- **Advanced Options** (collapse): custom section overrides, seed note, offered
  companions list.

## 1. Cult
- Signature: obsession, doctrine, secrecy; what it worships, why it recruits, what it
  promises initiates.
- Characters: charisma and dependency; believers who could still walk away.
- Uniqueness angle: the doctrine's single unshakeable (and testable) claim.
- Seed tags: Secluded · Devout · Recruiting Quietly
- Dial: pillar. Companions: Leader (controls), Champion (member_of), Zealot
  (member_of).

## 2. Guild
- Signature: craft, standards, rivalry; the trade it guards, its monopoly's reach,
  apprenticeships and secret techniques.
- Characters: pride of craft; masters who care more about the work than the money.
- Uniqueness angle: the split inside the guild the leadership papers over.
- Seed tags: Skilled · Protective · Rival Shop Feuds
- Dial: important. Companions: Guildmaster (controls), Master-crafter (member_of),
  Apprentice (member_of).

## 3. Thieves' guild
- Signature: shadow, codes, scores; territory, informants, fences; the line between
  extortion and protection.
- Characters: pragmatists with a code among thieves; loyalty priced by the job.
- Uniqueness angle: the score it's preparing that will change the balance of power.
- Seed tags: Invisible · Territorial · Honest Among Themselves
- Dial: pillar. Companions: Master (controls), Lieutenant (member_of), Fence
  (employs).

## 4. Noble house
- Signature: blood, rivals, estates; the family tree, its debts, its marriages, its
  enemies; land and titles as currency.
- Characters: heirs and spares; the family is the unit, love is a foreign concept.
- Uniqueness angle: the secret that could undo the line (paternity, treason, curse).
- Seed tags: Old Money · Feuding · Marriages As Policy
- Dial: important. Companions: Head (controls), Heir (member_of), Spymaster
  (employs).

## 5. Military order
- Signature: discipline, campaigns, oaths; chain of command, victories and defeats
  honored or buried; the enemy it was sworn against.
- Characters: soldiers first, believers second; veterans who know the cost.
- Uniqueness angle: the order's founding vow and the current leader who quietly
  breaks it.
- Seed tags: Loyal · Disciplined · One Enemy
- Dial: important. Companions: Commander (controls), Sergeant (member_of), Chaplain
  (member_of).

## 6. Merchant cartel
- Signature: capital, routes, bargains; what it ships, who it squeezes, the
  contracts nobody reads.
- Characters: arithmetic souls; risk as a profession, ruin as a hobby.
- Uniqueness angle: the single dependency (a route, a commodity, a favor) it would
  kill to protect.
- Seed tags: Wealthy · Ruthless · Diversified
- Dial: simple. Companions: Factor (employs), Emissary (employs), Debt-collector
  (employs).

## 7. Religious order
- Signature: faith, charity, inquisition; the public mission vs the private
  discipline; how it polices its own.
- Characters: sincere conviction next to institutional coldness.
- Uniqueness angle: the heretical finding the order is sitting on.
- Seed tags: Charitable · Watchful · Doctrine Over People
- Dial: important. Companions: Abbot (controls), Confessor (member_of), Pilgrim
  (member_of).

## 8. Hermetic society
- Signature: knowledge, secrets, gatekeeping; what it knows, who may know, and the
  price of admission.
- Characters: scholars with a vault door; curiosity disciplined into silence.
- Uniqueness angle: the truth it protects that would change everything if loosed.
- Seed tags: Secretive · Select · Knowledge As Power
- Dial: draft. Companions: Archivist (controls), Loremaster (member_of), Apprentice
  (member_of).

---

## Registry integration

```
archetype.template = {
  form: { identity:[…], signature:[…], tags:[…], theme:≤300, unique:≤200,
          advanced:{overrides, seedNote} },
  generate: { chips:[…], map: optional, dialDefaults:{nothing.pillar} },
  companions: [{role, kind, edges, dial}],   # tick-to-include, never forced
}
```

The Dice/stat-block depth for characters is separate (Q4=B: dial = mechanics weight).
These templates are the place/faction analogue: dial = elaboration over the full
section set, chips = what each level promises, uniqueness/tags = the anti-generic
toolkit (C.2).