# MythosCircle — To-do list

- [x] Forge destination stays visible and changeable after choosing a world (fixed; verified locally, 2026-10-17)
  Source: https://discord.com/channels/1556747585392353403/1556772952580816957/1560941617450324120
  Verified: the successful selection path hid the destination picker without a selected-world summary. Metadata/history failures also unmounted the sheet. Forge now keeps the editor and named destination controls mounted, supports world/library switching and inline retry, and gates unresolved or active-build destinations. Regression tests cover preserved authored fields, original-world relation drafts, stale responses, and Generic isolation.
- [ ] 1. custom lore, description and world name should be editable (open, normal)
  Source: https://discord.com/channels/1556747585392353403/1556747586130681912/1560777029832548385
- [ ] 2. descriptions per sections or more self explaniable or small examples  (open, normal)
- [ ] 3. theming changes for the user (open, normal)
- [ ] Character editing sometimes fails — investigate save failures and preserve edits for retry (open, high)
  Source: User report in Codex, 2026-10-17.
  Verified: conflict retries keep a stale base revision; role changes omit required boss additions/removal; the world profile editor loses new boss field values. Backend validation tests pass (2 targeted tests). Fix separately from Forge.
- [x] Redesign Forge with Quick generate as default and a Detailed builder sharing the same draft (implemented; verified 2026-10-17)
  Source: User request in Codex, 2026-10-17; inspiration: https://char-gen.com/character-generator
  Scope: persistent destination, optional identity and concept in Quick, Background-first Detailed builder, clearer Mechanics and spell descriptions, live authored/generated summary, and separate build history.
  Preview: https://p.superdesign.dev/draft/043058c8-074c-4f93-afb2-15f980728109
