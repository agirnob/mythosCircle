# Conventions — mythosCircle

Cross-cutting consistency conventions from the final spine. Applies everywhere.

| Concern | Convention |
| --- | --- |
| Naming | `snake_case` Python, `kebab-case` API routes, `camelCase` TS/Vue; entity/edge/job/event IDs are ULIDs |
| Data & formats | UTC ISO-8601 timestamps; error envelope `{code, message, details?}`; cursor pagination |
| State & cross-cutting | mutation only via store commit; structured JSON-lines logs to file; generation failures never mutate state; all jobs idempotent by job-id; each job declares a max LLM/media call budget from config — exceeding it fails the job |
| Testing | store + pipeline carry unit tests for commit/undo/retrieval (deterministic fixtures); no UI e2e in beta (owner dogfoods) |

Wire-contract specifics (AD-17):

- REST for CRUD + job submission; WebSocket for job progress/queue position.
- WebSocket message shape: `{type: job_progress|job_done|job_failed|queue_changed, job_id, state, queue_position?, progress?}`.
- IDs are ULID strings, prefixed in logs only (`entity:`, `edge:`, `job:`, `event:`).
- 4xx = user error (never a state change); 5xx = server error.
