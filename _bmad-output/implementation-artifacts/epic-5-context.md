# Epic 5 Context: Take It to the Table — Export & VTT

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

The DM takes a piece of the living world to the table: a character — or the whole world — out the door in under a minute per character, as Markdown or as a VTT artifact (Owlbear, Fantasy Grounds, MapTool/RPGToken), table-ready with stat block and portrait, still carrying the guild, grudge, and secret it was woven into. The export is a pure projection of committed state: it never mutates, never re-validates. Openness is the moat — the DM uses the tool, doesn't live in it; every entity leaves in seconds, to a VTT or a plain Markdown sheet. The epic ends at the kill-criterion demo: world→VTT in under ten minutes with a second human watching. If that fails, the "for online tables" claim is withdrawn and the epic moves to Phase 2.

## Stories

- Story 5.1: Export Engine — Pure Projection
- Story 5.2: Owlbear Export (First Target)
- Story 5.3: Fantasy Grounds Export (Second Target)
- Story 5.4: MapTool / RPGToken Export (Third Target)
- Story 5.5: World→VTT Kill-Criterion Demo

## Requirements & Constraints

- Export targets: Markdown, Owlbear, Fantasy Grounds (XML), MapTool (RPGToken JSON + portrait). Roll20 is Phase 2; Foundry is deferred (owner cost/usage constraint) — do not build them (FR16).
- Markdown is the DEFAULT export and must be a product artifact, not a dump (owner directive 2026-09-08): the file carries inline HTML + embedded CSS alongside the markdown — markdown pass-through renders it beautifully in a browser, and the same file converts to styled PDF.
- Research-before-spec (owner directive 2026-09-08): every VTT target's real format must be checked against online sources — official docs, the target's own public source code where it exists (e.g. MapTool), wikis, and community threads (Reddit). No format shape from memory alone.
- Every VTT artifact carries the generated 5e stat block and the generated portrait; the entity lands table-ready (FR17).
- The exportable invariant (SRD 5.1 field set + valid portrait reference) is enforced at commit — the export carries no validation gate of its own. A format assertion failing at export time is a commit-path regression: log an export-failure event, but the export still never mutates state and never re-validates (FR18).
- Export reads only the latest revision; stat blocks follow the SRD 5.1 field set — level for NPCs/BBEGs, CR for monsters (AR18).
- Owlbear's shape is fixed by the spine: media-service image URL + stat-block text in a JSON unit collection (Owlbear has no documented token-JSON import).
- Markdown reaches Obsidian-level completeness — full entities, typed edges, counters; the no-lock-in claim depends on it. Full-world JSON + Markdown must always be available.
- Timing contract: single-character export under a minute; the full world→VTT path under ten minutes, witnessed by a second human (two-eyes rule).
- Export is ownership, not backup — Epic 6's snapshot/restore semantics stay separate.

## Technical Decisions

- Export lives in the API's read-only layer: it reads committed state through the store and writes nothing (AD-1 single-writer; AD-11).
- Portraits in VTT artifacts are references served by the media service over the deployment's TLS origin — campaign-private files stay in the media directory; artifacts do not embed binaries (AD-10/AD-11).
- Staged candidates are invisible to export; only accepted, committed entities project (AD-15).
- Target order is locked: Owlbear first, Fantasy Grounds second, MapTool/RPGToken third. Each target is an adapter over the shared projection, never a second data path.
- The export snapshot shape (entity data incl. the AR24 sectioned record, edges with per-type counters, media refs with availability) is the single source every target consumes — the same snapshot the shipped JSON + Markdown world export renders.

## UX & Interaction Patterns

- Export is a one-click escape hatch (entity and world level) returning a download — no wizard, no ceremony.
- Broken media references are flagged in the artifact, never silently dropped (same honesty rule as the Markdown "file missing" marker).

## Cross-Story Dependencies

- 5.2–5.4 all consume 5.1's projection and its export-failure event hook; 5.5 exercises the whole chain and is the epic's acceptance gate.
- Portrait references ride Epic 4's manifest contract (EntityExport.media: id/kind/filename/available, spec-4.3) — VTT image URLs depend on the media service's serving path staying stable.
- The shipped export so far is whole-world JSON + Markdown (story 2.6); 5.1 adds the entity-level projection as the shared engine, 5.2–5.4 add targets.
- The deferred `level_cr` format enforcement (spec-3-1 review) explicitly lands "when the AR24 record gets a real consumer" — the Epic 5 export is that consumer; decide enforce-vs-loosen before the Owlbear sheet ships.
- The open Epic 4 retro item on media retention/pruning decides which portrait a fresh export references — land the ruling before 5.2's live demo.
