# Implementation parity checklist

Use each literal prototype and screenshot alongside this checklist. Record implemented, intentionally changed (with reason), or deferred for each item; an empty checkbox is unfinished scope. The backend handoff is mandatory.

## Guided Build and World workspace

- [ ] Initial form retains additional per-kind counts, named entries, pasted lists, shaped places/factions, partial authored figures and free-form build notes with saved seed context.
- [ ] Authored text survives section changes and validation/retry; full production shared character mechanics and relation-target controls replace prototype JSON demonstrations.
- [ ] Initial generation leads into actual committed world content. Existing campaigns can open the workspace directly. Empty and consumed-build states are explicit.
- [ ] Existing and Planned entities have textual labels and distinct border treatment. Outline, cards and graph share stable identities.
- [ ] Add one place/faction/character beneath the chosen existing or planned place; focus its name and preserve the exact parent assignment.
- [ ] Planned places show recursively nested children; rows start compact, and Move/connect/guidance expands the correct editor without losing other open edits.
- [ ] Moving preserves guidance and independent faction membership. Renames update graph, outline, placement text and target options without changing identity.
- [ ] Connected parent removal requires reassignment. Leaf removal Undo preserves additions/edits made after removal.
- [ ] Optional AI proposals and bulk counts/named lists preserve scope. Suggestions do not mutate canon; stale proposals cannot silently apply.
- [ ] Generation toolbar and full scope summary remain reachable. Existing-name collisions, kind rules, reference errors and containment cycles are handled before submission.
- [ ] Generate only unfinished additions; use durable draft-key/result mappings, refresh actual content, clear confirmed completion and keep unfinished items after partial failure.
- [ ] Persistent account/campaign draft isolation and idempotent retries work across reloads and interrupted jobs.
- [ ] Keyboard, focus, readable relation alternatives, 320/390px reflow, zoom, reduced motion, loading/errors and cancellation are verified in real application UI.

## Other proposals and references

- [ ] Your Worlds: searchable campaign cards, Continue action, Generic exclusion, and world creation with preserved free writing/optional lore chapters.
- [ ] Add Character: authored Identity/Story/Mechanics/Connections/Review groups, batch navigation, canonical required-field validation, retained input and zero-LLM save.
- [ ] Library: private Generic characters only, search/details, explicit destination/cancel, accurate move semantics and partial-failure handling; no unsupported keep-a-copy promise.
- [ ] Forge remains unchanged; World/Overview styling remains the reference for shared components and tokens.

## Sign-off evidence

Capture the implemented desktop/mobile states beside the archived references, run meaningful backend contract/pipeline tests and frontend checks with Node 22, and document intentional differences. Local prototype success does not certify production API/authentication/accessibility behavior. Require CI gate before merging the implementation into develop.
