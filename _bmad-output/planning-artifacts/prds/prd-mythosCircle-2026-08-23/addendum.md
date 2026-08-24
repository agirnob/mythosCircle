# Addendum — mythosCircle PRD

Supporting detail that earns its place downstream but doesn't belong in the PRD's main narrative.

## Competitive landscape (researched 2026-08-23)

Full evidence for the "What's different" positioning. The PRD carries the honest compact version (Section 1); this is the substrate.

### Landscape

| Tool | Category | LLM? | Interconnected/living world? | Reacts to player actions? | Relevance to wedge |
|---|---|---|---|---|---|
| World Anvil | World wiki/storage | AI assist only ($12+/mo) | Yes, manual (linked articles, relationship trees) | No | Saturated storage layer; no generation |
| Campfire | Writing/world suite | No | Yes (linked cards, timelines) | No | Saturated non-LLM |
| donjon / Perchance | One-shot generators | No | No | No | Commodity |
| Wonderdraft | Cartography | No | No | No | Dormant (last commit 2019) |
| LitRPG Adventures | AI NPC/content generator | Yes | No | No | Price anchor ~$6–10/mo |
| AI Dungeon | LLM interactive fiction | Yes | No (session-only) | Yes, narratively | Player game, not DM tool |
| KoboldAI | Open LLM writing service | Yes | No | No (text only) | Raw engine, no world model |
| ChatGPT / Claude | General LLM | Yes | No structure/persistence | No | Prompted substitute |
| LoreKeeper | AI-DM platform + world builder | Yes | Yes — factions/NPCs feed the AI session | Partially — AI uses world state in-play | **Closest commercial** |
| Tabletop Arc | Campaign memory layer + generators | Yes | Yes (Lore Wall: canon from play) | Partially — passively records play | Adjacent "world becomes canon" |
| Roll20 | VTT + marketplace | Partial (AI Assistant since 2023; LLM NPC chatbots in marketplace) | No | No | Saturated; marketplace NPC packs w/ chat access |
| Foundry VTT | Self-hosted VTT | Community modules (Legend Lore, LLM Lib, local-LLM RPGX Familiar) | No | No | Modules generate content; no simulation |
| open-tabletop-gm / neuralinitiative.ai | OSS GM framework + hosted top-up | Yes (BYO) | Yes (typed relationship graph, world generation) | Yes (GM improvises) | Closest OSS to graph model |
| NarrativeEngine-P | Self-hosted AI-DM engine (87★) | Yes | Yes (world "divergence register", NPC agency) | Yes (extracts world state each turn) | **Closest to post-MVP simulation** |
| llm_RPG (OSS) | Living-world game | Heuristic + optional LLM | Yes (nightly Lanchester faction sim, social graph, nemesis) | Yes (world advances off-screen) | Strongest prior art for time sim; player game |
| ChatTTRPG | AI D&D chat (Pixel Talks) | Yes | No | Yes (chat DM) | Adjacent, player-facing |

### State of the space

Three clusters. (1) Mature non-LLM world-building wikis/generators (World Anvil $0–$34/mo, Campfire ~$2/mo, donjon, Perchance, Wonderdraft) — cheap/free but manual. (2) LLM content generators (LitRPG ~$6–10/mo, ChatGPT/Claude $20, AI Dungeon $9.99, free KoboldAI) — fast NPCs but isolated; the generated tavern has no relationship to the faction system. (3) Emerging AI-DM platforms where structured world data feeds gameplay (LoreKeeper free tier, Tabletop Arc, open-tabletop-gm/neuralinitiative, NarrativeEngine-P) — the only cluster where the world shapes play. Monetization is freemium SaaS at $2–$12/mo (World Anvil $650 lifetime); sub-$10/mo is saturated and general LLMs are the main substitute.

### Wedge verdict

The absolute claim — "no tool combines NPC generation + world-building into an interconnected living world" — is already achieved commercially by **LoreKeeper**: its world builder produces "structured data — factions, locations, NPCs, lore entries — that the AI Dungeon Master reads and uses during gameplay," and rival factions "generate tension between their members during encounters." The claim "no tool simulates history over time" is also false in open source: **NarrativeEngine-P**'s divergence register auto-extracts "who is where, who holds what, alliances, deaths, promises, debts" every turn; **llm_RPG** runs a nightly faction sim (real Lanchester army model), a social graph of "friendships & feuds," and a champion you almost kill becomes "a named nemesis who flees, rises in title, and hunts you for the rest of the campaign."

The defensible wedge is narrower: **hosted, DM-first (not AI-DM), explicit relationship graph, a world that simulates history over time (revenge, succession, warrants), plus image/video generation** — no commercial occupant.

### Direct threats to the wedge

1. **LoreKeeper** (free tier) — closest commercial world→play integration. Defense: it's an AI DM (the AI runs the table), no graph UI, no time simulation; mythosCircle sells "tool for the DM, not an AI DM."
2. **NarrativeEngine-P** (MIT, 87★, active) — implements the post-MVP "world reacts" for free, self-hosted. Defense: no hosted web product, no DM-facing candidate/graph UI, no image/video gen.
3. **llm_RPG** — strongest prior art for "world lives without you"; if it ships a DM mode, the simulate-history moat shrinks.
4. **open-tabletop-gm + neuralinitiative.ai** — OSS GM framework with a typed relationship graph plus a hosted top-up version; fast-follower risk on the graph+hosted axis. Also watch **Tabletop Arc**'s Lore Wall ("every fact has a source" canon from play) — a passive version of player-reactivity.

### Sources

- https://lore-keeper.com/blog/ai-world-builder-rpg — LoreKeeper, MythWeaver, World Anil, ChatGPT/Claude comparison
- https://dmtoolsai.com/best-ai-npc-generators-for-tabletop-rpg-2026/ — LitRPG Adventures, AI Dungeon, Perchance, ChatGPT/Claude pricing
- https://dmtoolsai.com/world-anvil-review-is-it-worth-it-for-dungeon-masters/ — World Anvil pricing (free/$6.50/$12/$34/mo, $650 lifetime), AI assistant, relationship trees
- https://tabletoparc.com/resources/ai-worldbuilding-tools — Tabletop Arc Lore Wall, generators, "world becomes canon"
- https://github.com/Sagesheep/NarrativeEngine-P — divergence register, NPC agency, lossless memory
- https://github.com/gddickinson/llm_RPG — nightly faction sim, social graph, nemesis system
- https://github.com/Bobby-Gray/open-tabletop-gm — typed relationship graph, /gm world generation, neuralinitiative.ai
- https://foundryvtt.com/packages/legend-lore and https://foundryvtt.com/packages/llm-lib — Foundry AI modules (LLM Lib: "chat or generate NPCs")
- https://www.campfirewriting.com/write + https://play.google.com/store/apps/details?id=com.campfiremobile — Campfire linked world-building cards, TTRPG support
- https://podtail.com/podcast/pixel-talks/chatttrpg-ai-d-d-med-thue-ersted-rasmussen-david-b/ — ChatTTRPG (AI D&D chat, Pixel Talks)
- https://api.github.com/search/repositories?q=koboldai — KoboldAI-Client (3.9k★, AGPL), lite.koboldai.net (active, any-LLM backend)
- https://api.github.com/repos/Megasploot/Wonderdraft — Wonderdraft: fantasy map tool, last push 2019

## Technical candidates (from original input)

- **Graph visualizer (§3 D):** Cytoscape.js or Vue Flow — owner's suggestion from the brain dump; decide at Phase 2 build time.
