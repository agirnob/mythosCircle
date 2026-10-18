---
status: required-for-implementation
updated: 2026-10-18
---

# Backend work required by the accepted World workspace direction

The owner accepted Guided Build / World workspace version 6 as good enough for now and explicitly confirmed that this work includes backend changes. Stop further visual exploration for this pass. This is acceptance of the design direction, not a claim that production behavior exists. Implementation must include the frontend, API, generation pipeline, store, recovery and contract tests together.

## Reuse versus required changes

Current keyed build-in seeds, explicit relation target tiers, registry kind rules, queue/provider machinery and store writers are useful foundations. They do not provide the complete workspace contract. The [source feasibility audit](nested-build-feasibility.md) records the earlier investigation; line numbers and behavior must be rechecked against the implementation branch.

| Work package | Required behavior / acceptance evidence |
|---|---|
| Typed structural planning | A validated input/output schema carries stable draft IDs, typed relationships, selected scope and source draft/world versions. AI suggests structure only. Propose/apply/discard creates no world entities or edges and triggers no content generation. Existing Ask/text jobs are not a complete substitute. Test invalid model output and stale responses. |
| Scoped expansion submission | Compile one or more existing/planned-place allocations into explicit keyed entities and typed links. Submit unfinished additions only; committed entities are targets/context. Generate four characters at Harbor and two at Market with exactly those assignments. Test named and model-named slots together, planned parents, faction membership and authored-field preservation. |
| Model-named draft slots | The prototype permits multiple unnamed planned places/factions/figures. Current structured seeds and figure validation impose naming constraints, including the single unnamed authored-figure rule in `backend/app/store/jobs.py`. Add a validated materialization/adapter step or extend the typed contract so every generated slot retains its draft key and scoped relationships. Do not simply send multiple blank named seed objects to the current build-in endpoint. |
| Whole-plan validation | Enforce unique keys, target existence and campaign ownership, allowed kind/direction pairs, valid counts, acyclic containment and supported registry archetypes/dials. Membership/social cycles are not containment cycles. Revalidate committed targets and world revisions when the job starts. Errors preserve editable work. |
| Additions-only identity rules | Use the backend's canonical name normalizer. Current place/faction name matching may update or fold entities; stable draft keys do not bypass it. An additions-only request must explicitly resolve collisions before enqueue and recheck them before commit. Do not silently overwrite existing content. Named mandate targets and notes-driven extra generation must be resolved into the reviewed scope; preserve the initial Guided Build's legacy behavior separately. |
| Results and reconciliation | Return a durable draft-key → committed ULID mapping plus per-item outcome and relationship results. Refetch actual content; promote only confirmed completed items. Test that renames, planned-parent links and faction memberships survive generation. Never clear the entire plan solely because a job becomes terminal. |
| Partial success, cancellation and retries | Existing generation commits waves directly. Report committed results accurately after failure/cancellation; retain unfinished items. Add idempotent submission/retry or equivalent deduplication with durable per-item completion. Retrying must not recreate completed characters or replay committed links. Test failures after an early commit and interrupted result delivery. Do not promise atomic rollback. |
| Persistent workspace state | Load actual committed content independently from unfinished expansion drafts. Persist/recover the agreed draft state scoped to the invited account/campaign, including identity, placement, membership, guidance and revision. Reload and retry must not mix the initial seed form with completed additions or another account's drafts. Decide and document client/server persistence ownership during implementation. |
| Compatibility and API types | Preserve existing first-build counts, named/list/shaped entries, hybrid authorship, lore/notes, job progress and failed-seed recovery. Keep Forge unchanged and Generic Library private/characters-only. Update API schemas, generated frontend types and UI error handling alongside the backend contract. |

All world-state mutation stays in `backend/app/store/`. Use ULIDs for entity, edge, job and event identities; distinguish temporary draft keys explicitly. Campaign APIs remain private to their invited user. Keep UTC timestamps, standard error envelopes and existing pagination conventions.

## Delivery sequence

1. Recheck current source and define the typed planning/expansion/result contracts, ownership boundaries and persistence decisions.
2. Implement validation, scoped generation, identity/result mapping and failure/retry behavior through the existing store boundary; add meaningful contract/pipeline tests.
3. Connect the accepted workspace UI and initial Guided Build to those contracts. Replace local sample generation and prototype mechanics controls with the real shared editor and registry.
4. Verify the end-to-end sequence: initial build → scoped additions under existing and planned places → generation → reload → second expansion, including partial failure/retry and account isolation.
5. Follow repository workflow: fresh feature branch from updated develop; relevant backend checks plus frontend tests/typecheck/build under Node 22; push and PR into develop with CI gate required.

No backend implementation or deployment occurred during the design pass. This handoff is required scope for the later implementation, not optional follow-up polish.
