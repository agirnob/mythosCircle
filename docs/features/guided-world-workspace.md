# Guided Build and persistent World workspace

The initial generator keeps its legacy seed, authored character editor, notes and generation behavior. Existing campaigns open the workspace; a successful first build opens it through either WebSocket completion or REST polling. Initial form drafts remain account/campaign scoped in browser storage, including raw shared-editor fields, incomplete controls and exact authored prose. Expansion drafts and completion receipts belong to the server.

## Wire contract

All endpoints require the campaign's invited account. Generic Library campaigns cannot use this workspace. Errors use the existing `{code, message, details?}` envelope.

| Endpoint | Body / result |
| --- | --- |
| `GET /api/campaigns/{id}/workspace` | `{draft_version, world_version, draft: {nodes}, receipts}` |
| `PUT /api/campaigns/{id}/workspace` | `{expected_draft_version, expected_world_version, draft: {nodes}}`; returns the updated snapshot |
| `POST /api/campaigns/{id}/workspace/plan` | `{request_id, expected_draft_version, expected_world_version, selected_ids, scope_id, guidance}`; returns a normal queued job |
| `POST /api/campaigns/{id}/workspace/generate` | Same submission shape; queues unfinished reviewed additions only |

`world_version` is the current revision ULID or null. `draft_version` starts at zero and increases on saved changes or confirmed completion. Node fields are closed: `id`, `kind` (`place`, `faction`, `character`), `name`, `parent_id`, `faction_id`, and `guidance`. Names may remain blank. Draft IDs are stable opaque keys, separate from canon ULIDs. Drafts permit 100 of each kind (300 total). Parent references must resolve to places; independent membership references resolve to factions. Containment must be acyclic. Place/faction names use the backend's canonical normalizer and cannot collide with existing content or another draft addition.

`request_id` must be a ULID. Identical retries return the original operation even after completion; changed input using the same key conflicts. `selected_ids` scopes generation and must include any planned dependencies. `scope_id` constrains new planning suggestions to a selected place and its proposed descendants. Planning returns a validated `proposal` with `source_draft_version` and `source_world_version`; applying it only saves the draft. Both source versions must remain current.

Expansion materializes blank names in a bounded model call, durably pins names to draft IDs on the request job, and uses the existing wave-one generation and content/stat repair machinery. Existing records and links are bounded context, never generation seeds. Context includes campaign theme/lore and submitted expansion guidance. Every reviewed existing target retains a compact identity even beyond the 24-record narrative window; individual structured excerpts are capped at 2,000 characters and prose at 3,000. Planning uses a closed provider schema with all properties required, including nullable fields. No unrestricted notes wave or upsert is used. Reviewed placement becomes `part_of` for places and `located_in` for other kinds; membership becomes `member_of` independently.

## Completion and recovery

The graph, per-item receipts, and removal of confirmed draft nodes commit together in a single store transaction. Surviving references to completed planned targets become canon ULIDs. Receipts contain `node_id`, `entity_id`, `kind`, `name`, `revision_id`, `edge_ids`, and `outcome: completed`.

This implementation uses a whole-selected-scope checkpoint: provider/validation failures before the checkpoint leave every selected draft item editable; cancellation after a checkpoint cannot erase its confirmed records. A restart after graph commit but before delivery finds receipts and cannot create the same characters or links again. Unselected drafts survive. Live references, collisions and expected versions are checked before enqueue, at execution, and before commit. Concurrent edits conflict rather than silently overwriting world or draft content.

The UI refreshes actual world content and draft receipts on terminal WebSocket frames and REST polling. Connected parent removal requires reassignment; leaf Undo restores that one removed node while preserving later changes. Outline, nested editor and accessible SVG graph use the same IDs. Move preserves guidance and independent membership. Completed selections and Undo references promote through receipts. A store-owned save loop drains edits made during an in-flight save after view unmount; unsaved browser exits trigger the standard browser prompt. Explicit reload fences older save responses. Only the latest planning operation is offered, and applied/discarded proposal identities persist per account/campaign in this browser. The graph also supplies a readable relationship list and keyboard selection.

## Database changes

Startup `create_all` adds `workspace`, `workspace_request` and `workspace_receipt`. Existing job-kind migration rebuilds the SQLite CHECK to include `plan_expansion` and `expand_world`, using the established migration path and retaining old jobs. Campaign deletion removes these new dependent rows before deleting jobs/campaigns. Back up the SQLite database using the normal deployment process before deploying schema changes. No deployment workflow or production stack configuration changes are required.

## Design parity

The accepted v6 nested rows, disclosures, optional bulk/AI tools, generation summary and outline/card layout are ported to production Vue components using the archived component styles. Sample content/controllers and prototype JSON mechanics are omitted; the initial form continues to use the production shared character editor. The graph is rendered from real records, planned nodes and typed relationships. Forge and the separate Your Worlds/Add Character/Library proposals remain outside this feature's scope.
