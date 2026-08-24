# Architecture Diagrams — mythosCircle

Derived from the final spine (`ARCHITECTURE-SPINE.md`, status: final). Diagrams are the load-bearing view of layering, deployment, and data model; prose rules live in the spine's ADs.

## Dependency direction (who may depend on whom)

Rule the diagram encodes: `api/` orchestrates; `pipeline/` proposes; `store/` commits; `providers/` are leaves (no inbound deps except from pipeline/media); nothing except `store/` writes world state.

```mermaid
graph TD
  UI[Vue SPA frontend/] --> API[API layer app/api/]
  API --> STORE[World-state store app/store/]
  API --> MEDIA[Media service app/media/]
  API --> PIPE[Generation pipeline app/pipeline/]
  PIPE --> STORE
  PIPE --> PROV[Provider adapters app/providers/]
  MEDIA --> PROV
  MEDIA --> STORE
  OPS[Ops deploy/ Caddy + backup] -.-> API
  OPS -.-> STORE
```

## Deployment (single machine — AD-8)

```mermaid
graph LR
  DM[Invited DM browser] -->|TLS| CADDY[Caddy]
  CADDY --> UV[uvicorn FastAPI, localhost]
  UV --> DB[(SQLite world-state + events + queue + media manifest)]
  UV --> MEDDIR[media/ directory]
  UV -->|OpenAI-compatible HTTP| LLM[llama-server, RTX 3090, abliterated 14-20B]
  UV -->|HTTP| IMG[local image server]
  CRON[nightly backup cron] --> DB
  CRON --> MEDDIR
```

## Core entities (names + relationships only)

```mermaid
erDiagram
  USER ||--o{ CAMPAIGN : owns
  CAMPAIGN ||--o{ ENTITY : contains
  CAMPAIGN ||--o{ REVISION : versions
  REVISION ||--o{ EVENT : owns
  ENTITY }o--o{ ENTITY : "typed directed edge (counter)"
  ENTITY ||--o{ MEDIA : has
  JOB }o--|| CAMPAIGN : runs_on
  JOB |o--o{ ENTITY : proposes
```

## Minimal structural tree

```text
mythosCircle/
  backend/
    app/
      api/        # routes: campaigns, entities, edges, jobs, media, exports, auth
      core/       # config, auth, errors, events
      store/      # revisions, event log, commit path, queue, media manifest
      pipeline/   # job runner, retrieval, generators (character/faction/place/simulate)
      providers/  # llm, image, video — OpenAI-compatible adapters
      media/      # media service (write/reclaim/validate)
    tests/        # store + pipeline unit tests
  frontend/
    src/          # Vue SPA: views per capability, Pinia world-state stores
  deploy/         # config.toml, caddy, systemd units, backup cron + restore script
```
