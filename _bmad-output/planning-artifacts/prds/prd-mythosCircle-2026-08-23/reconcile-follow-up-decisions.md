# Reconcile — follow-up decisions vs. PRD

Input: `original-inputs/follow-up-decisions.md` (verbatim, all sections)
PRD: `prd.md` (2026-08-23)

## Coverage

| # | Verbatim decision (section) | Landed in PRD |
|---|---|---|
| 1 | "first mvp then graph then simulate and also ingestion from obsidioni worldanil, kanka etc" (Scope order) | §7 Scope — Phase 1 (MVP) → Phase 2 (relationship graph) → Phase 3 (Simulate History) → Later (import connectors Obsidian/World Anvil/Kanka). Order preserved. |
| 2 | "its made within the tool. There can be ingestion tools from obsidioni worldanil, kanka or her own random notes uploaded for llm to digest" (World import) | §7 Phase 1 — "world state created inside the tool (guided build + notes digested by the LLM)"; import connectors in §7 Later. |
| 3 | "first 5e because thats the biggest but then add the other games such as pathfinders" (System target) | §7 Phase 1 — "5e-first"; §7 Later — "Additional systems (Pathfinder, then others)." |
| 4 | "I don't use foundry nor I have the money to buy it right now. Lets start with markdown, owlbear, maptool, fantasygrounds" (Export targets) | §3 E — Phase 1 export targets are Markdown, Owlbear, MapTool, Fantasy Grounds. **Partial**: Foundry appears in §3 E / §7 as a *Phase 2* target; the cost/user-doesn't-use-it rationale is not reflected (see Gaps/Conflicts). |
| 5 | "add an exporter. so characters, places etc can be easily used in VTTs such as markdown foundry, roll20, owlbear, fantasy ground, maptool" (Export targets) | §3 E — exporter with VTT targets; Phase 1 set matches user's "start with" list. Roll20 named by user is deferred to Phase 2 without note (see Gaps). |
| 6 | "Relation graph that lorekeeper have is really cool but while making the character you can add the relations to other things such as places, factions." (Export targets) | §3 B — "Inline relation editing — while creating or editing a character, the DM adds, edits, or deletes relations to any entity (places, factions, other characters)." |
| 7 | "only relevant lore should get added" (NFR / infra) | §4 — "Only the lore relevant to the request enters generation — retrieval over the graph, never the whole world in context." |
| 8 | "every generation would be made on my local server for the mvp after the initial phase we will go to API route using probably openroute" (NFR / infra) | §4 — "MVP: all generation runs on the owner's local server (RTX 3090) — zero per-token cost at MVP; … After the initial phase: API route via OpenRouter." |
| 9 | "content safety we probably will be need to use abliterated llm so grimdark things can be added/made" (NFR / infra) | §4 — "MVP runs an abliterated (uncensored) local model so grimdark campaign content … generates without refusal"; OpenRouter migration must pick non-over-censored endpoints. |
| 10 | "first of I am heavily against of subscription if/when customers demands it then I may concider it" (Business model) | §5 — "No subscription by design — reconsider only if customers demand it." |
| 11 | "I am thinking of token based approach" (Business model) | §5 — "Token/credit model (post-beta)." |
| 12 | "give some free token weekly/monthly to let users start doing things" (Business model) | §5 — "Free tokens granted weekly/monthly so users can get started (enough for, e.g., one character plus one image)." |
| 13 | "wanna make a character some token, simulate history token, generate a image more token" (Business model) | §5 — relative token scale: "character generation (low) < faction/place generation (mid) < simulate history (mid–high) < image generation (high) < video generation (highest)." |
| 14 | "generate a video dud you just all your token (video is expensive as heck" (Business model) | §5 — video is the highest-cost operation. **Partial**: the explicit framing that a single video consumes the user's *entire* token balance is not reflected (see Gaps). |
| 15 | "maybe we can give layers on video cheaper video with worse but fast video gen and expensive video with good but expensive video gen" (Business model) | §5 — "Video layering — two tiers: fast & rough (lower quality, cheaper, faster) and premium (higher quality, expensive, slower)"; also §8 open items. |
| 16 | "Lifetime buy seems a bit excessive because of the llm usage" (Business model) | §5 — "No lifetime buy: LLM usage is ongoing, so pricing must be per-usage." |
| 17 | "Mvp would be a close beta so a few invited DMs" (Business model) | §5 — "Closed beta (Phase 1). Launch as a closed beta: a few invited DMs on the owner's local server." |
| 18 | "I don't know how much to cost token and I don't know which llm/generators to use rn" (Business model) | §5 open items — "per-operation token prices; LLM + image + video endpoint selection on OpenRouter (TBD)"; §8 open items repeat both. |

## Gaps

1. **Why Foundry is out.** User: "I don't use foundry nor I have the money to buy it right now" — the exclusion is cost-based (and the user doesn't run Foundry at all). PRD lists Foundry as a *Phase 2* export target with no rationale; the "can't afford it / don't use it" decision is dropped.
2. **"Video consumes the entire token balance."** User's explicit point ("dud you just all your token") is reduced to "video = highest cost" in §5; the consequence — a single video can burn a user's whole balance — is absent from the token model and counter-metrics.
3. **Roll20 explicitly named, silently deferred.** User lists Roll20 in the VTT export list; PRD defers Roll20 to Phase 2 alongside Foundry without noting the user explicitly requested it.

## Conflicts

1. **Foundry export planned despite explicit exclusion.** PRD §3 E / §7 Phase 2: "Export targets: Foundry, Roll20" vs. decision "I don't use foundry nor I have the money to buy it right now. Lets start with markdown, owlbear, maptool, fantasygrounds" — user excluded Foundry (cost), not merely sequenced it later; PRD commits to building a Foundry export the owner says he can't/doesn't use.
