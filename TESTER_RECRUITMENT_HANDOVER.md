# mythosCircle — tester recruitment handover

Updated 5 October 2026. Context for a new session about positioning, advertising, and finding the first testers.

## Product and audience

**mythosCircle helps TTRPG Dungeon Masters grow a connected campaign world and keep track of what happens in it.** AI drafts NPCs, factions, and places using existing world lore and relationships. The DM reviews, edits, and decides what becomes part of the world.

The original audience is busy but invested DMs running ongoing campaigns, initially D&D 5e. They want characters, secrets, rumors, and hooks without inventing every connection themselves. Positioning hypothesis: **less prep, connected NPCs, and campaign memory under the DM’s control**. Uniqueness and demand still need validating.

## Implemented capabilities

- **World building:** private campaigns, guided building from lore/notes, characters, factions, places, and editable relationships.
- **Ask the World:** plain-language requests produce staged proposals. The DM can review, accept, reject, edit, and regenerate; a proposal does not automatically overwrite the world.
- **Character tools:** manual character authoring, a guided Forge, lore details, secrets/rumors/party hooks, and 5e-oriented stat blocks.
- **Relationship graph:** browse connections around an entity or across the world.
- **Tonight:** named play sessions; separate Headline and Context; clickable `@entity` mentions; entity reference notes saved in SQL; explicit consequences such as defeated or allegiance changes; corrections that retain story context.
- **History:** a World timeline groups story events chronologically across sessions. A separate Changes tab records editorial activity. Selected historical actions can be copied into the journal without reapplying their state changes.
- **Media and export:** artwork/portrait and video generation infrastructure; JSON, Markdown, HTML, and entity export adapters for Owlbear, Fantasy Grounds, and MapTool. Actual provider availability and target-app imports need checking before a demo.

Picked `@mentions` also select Related entities after save/reopen; the owner confirmed the latest fix locally.

## Current stage and limits

Solo project in active development. Earlier plan: a small, free, invited beta using the owner’s inference hardware. Cohort size and operating capacity need confirming.

- The owner has used **world.miscco.uk**. Latest changes were verified locally at **localhost:5173** and pushed to `main`; the hosted site’s deployment parity has not been verified.
- Accounts and registration exist; campaigns are private per owner. Beta admission/onboarding needs a decision. There is no implemented public campaign gallery or shared player portal in this work.
- Automatic faction reactions/history simulation, session transcription, external world-import connectors, and broader game-system support remain future ideas. Today, journal prose records what the DM authors; it does not automatically simulate consequences or feed every note into generation.
- Earlier commercial intent was usage-based credits after beta, rather than a subscription. Pricing, allowances, and billing should not be advertised as available.
- Tonight browser checks, focused tests, typecheck, and build passed. Full suites still have 23 backend and 13 frontend baseline failures, including exports/media and legacy WorldView tests. Validate the chosen demo path before recruiting.

## What the first testers should help us learn

Learning goals: can a new DM bring in a world, generate a useful connected NPC, edit relationships, use it in prep/play, and return to record a session? Does this save effort compared with their current tools?

Candidate demo: create a town and guild → generate a connected blacksmith → inspect/edit the relationship graph → record the party’s actions in a named session → link the town with `@` → revisit the World timeline. Any later bounty or faction response in this example is authored by the DM.

Decide next: tester profile/count, budget, support time, generation capacity, signup route, and feedback collection. Proposed signals: first useful NPC, a relation edit, a session entry, repeat use, and friction reports. Analytics coverage needs checking.

## Technical context and source material

Python 3.12/FastAPI/SQLAlchemy/SQLite WAL backend; Vue 3/Vite/TypeScript/Pinia frontend. SQL stores entities, relationships, revisions, jobs, named sessions, and journal entries. Generation uses configurable inference providers. World writes belong only in `backend/app/store/`.

Checkpoints: `600a351` added the journal; `427d303` fixed mention selection. Sources: `README.md`, `AGENTS.md`, `frontend/src/views/`, `backend/app/api/`. Earlier intent: `_bmad-output/planning-artifacts/prds/prd-mythosCircle-2026-08-23/prd.md` (ignored local file; current code takes precedence over its older roadmap).

## Paste this into the next session

> Read TESTER_RECRUITMENT_HANDOVER.md. Help me brainstorm how to find the first useful testers for mythosCircle. First clarify my budget, support time, beta capacity, and hosted-site readiness. Then explore which DMs to approach, what problem/message would resonate, and suitable recruitment channels. Narrow this into a few small experiments with draft outreach copy, a short testing journey, feedback questions, and success criteria. Verify current channel rules when researching specific communities. Keep the claims grounded in implemented features and separate future world simulation from today’s DM-authored journal.
