"""World store — the sole writer of world state (AD-1).

Every mutation of the world graph flows through a store commit: atomic
subgraph transactions, one revision per commit, per-transaction undo.
Nothing else in the codebase writes world state.

Public API:
    init_db / init_app_db / session_scope   -- engine & transactions (db)
    create_campaign / commit_subgraph       -- the commit path (commit)
    undo                                    -- compensating commit (undo)
    latest_revision / revision_chain / revision_events / world_state (read)
"""

from app.store.commit import (
    EDGE_TYPES,
    CorruptEventError,
    CrossCampaignConflictError,
    DanglingEdgeError,
    DuplicateEdgeError,
    DuplicateEntityError,
    EdgeRetargetError,
    EmptySubgraphError,
    InvalidEdgeTypeError,
    InvalidUlidError,
    StaleRevisionError,
    StoreError,
    UnknownCampaignError,
    commit_subgraph,
    create_campaign,
)
from app.store.db import (
    DB_ENV_VAR,
    DEFAULT_DB_URL,
    app_db_url,
    get_engine,
    init_app_db,
    init_db,
    session_scope,
)
from app.store.models import (
    Base,
    Campaign,
    Edge,
    EdgeInput,
    Entity,
    EntityInput,
    Event,
    Media,
    Revision,
)
from app.store.read import (
    latest_revision,
    revision_chain,
    revision_events,
    world_state,
)
from app.store.undo import undo

__all__ = [
    "Base",
    "Campaign",
    "DEFAULT_DB_URL",
    "DB_ENV_VAR",
    "EDGE_TYPES",
    "CorruptEventError",
    "CrossCampaignConflictError",
    "DanglingEdgeError",
    "DuplicateEdgeError",
    "DuplicateEntityError",
    "Edge",
    "EdgeInput",
    "EdgeRetargetError",
    "EmptySubgraphError",
    "Entity",
    "EntityInput",
    "Event",
    "InvalidEdgeTypeError",
    "InvalidUlidError",
    "Media",
    "Revision",
    "StaleRevisionError",
    "StoreError",
    "UnknownCampaignError",
    "app_db_url",
    "commit_subgraph",
    "create_campaign",
    "get_engine",
    "init_app_db",
    "init_db",
    "latest_revision",
    "revision_chain",
    "revision_events",
    "session_scope",
    "undo",
    "world_state",
]
