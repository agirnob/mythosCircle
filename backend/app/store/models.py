"""SQLAlchemy 2.0 models for the versioned world store (AD-13).

One SQLite database holds world state, the append-only event log, the
job queue, the media manifest, and accounts. The tables the store owns:
``campaign``, ``revision``, ``entity``, ``edge``, ``event``, ``media``,
and ``job`` — the persistent generation queue (AD-3).

The ``entity``/``edge`` rows are a materialized view of the latest
revision; the ``event`` log is the graph of record. World state is written
only through the commit path in this package — nothing outside
``app/store`` writes these tables (AD-1, AD-13). Jobs are **not** world
graph: their rows and transitions never touch the event log or revisions.
"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

#: The epic's proposal-kind contract (spec-3.1 Design Notes): a
#: ``generate`` job stages ``entity`` proposals. Defined here (not in
#: store.candidates) so the table CHECKs and the staging writer share one
#: definition; story 3.2 extends the lists in place.
PROPOSAL_KIND = "entity"
#: The closed proposal-status lifecycle (spec-3.2): staging writes only
#: ``proposed``; the DM's accept/reject transitions own the two terminal
#: states. Defined here (not in store.candidates) so the table CHECK,
#: the staging writer, and the lifecycle functions share one definition.
STATUS_PROPOSED = "proposed"
STATUS_ACCEPTED = "accepted"
STATUS_REJECTED = "rejected"
PROPOSAL_STATUS: tuple[str, ...] = (STATUS_PROPOSED, STATUS_ACCEPTED, STATUS_REJECTED)


class Base(DeclarativeBase):
    """Declarative base for the world-store schema."""


class Campaign(Base):
    """A private world: one invited DM in Phase 1 (AD-9, AR27).

    The world-seed fields (owner, title, description, theme, custom_lore)
    are the campaign's identity and config — they flow into the Epic 2
    build-in and generation prompts. Creating a campaign does NOT write a
    revision/event: the seed is not a graph delta (spec-1.6 Design Notes).
    """

    __tablename__ = "campaign"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("account.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text)
    theme: Mapped[str] = mapped_column(String(100))
    custom_lore: Mapped[str] = mapped_column(Text)
    # The Generic library flag (owner spec, 2026-09-17): a per-account
    # storage world for characters generated without a canon world. A
    # generic campaign's own entities and lore are NEVER generation
    # context — the runner substitutes the payload theme's defaults.
    is_generic: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String(40))


class Revision(Base):
    """One version of a campaign's world graph.

    Exactly one revision per commit (AD-1). A revision owns its events —
    every ``event`` row carries the ``revision_id`` that produced it.
    ``base_revision`` is the head the commit was validated against
    (``None`` for the first commit on an empty world); it carries the
    rebase-or-reject context (AD-2).
    """

    __tablename__ = "revision"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaign.id"), index=True)
    base_revision: Mapped[str | None] = mapped_column(String(26), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40))

    # Relationship (not just the FK) so the unit of work orders the
    # revision INSERT before its event INSERTs — flush order follows
    # mapped relationships, never bare ForeignKey columns.
    events: Mapped[list["Event"]] = relationship(back_populates="revision")


class Entity(Base):
    """Materialized entity of the latest revision (AD-23 hard truths).

    ``kind``: entity subtype (character, faction, place, ...).
    ``text``: generated narrative, if any. ``data``: structured hard truths.
    """

    __tablename__ = "entity"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaign.id"), index=True)
    kind: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(500))
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String(40))


class Edge(Base):
    """Typed, directed edge with a per-type counter (AD-5, AD-23).

    ``src``/``dst`` are entity ULIDs. ``type`` comes from the closed
    Phase-1 vocabulary; per-type ``counter`` semantics resolve from
    ``app.store.EDGE_COUNTER_SEMANTICS`` (the code contract, AD-23),
    and the commit path validates the counter's int shape, not its range.
    """

    __tablename__ = "edge"
    __table_args__ = (
        # AD-23: one row per relationship — the per-type counter is the
        # single source of truth for a (src, dst, type) pair. Counter
        # changes stage the edge's existing ULID.
        UniqueConstraint("campaign_id", "src", "dst", "type", name="uq_edge_relationship"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaign.id"), index=True)
    src: Mapped[str] = mapped_column(String(26), index=True)
    dst: Mapped[str] = mapped_column(String(26), index=True)
    type: Mapped[str] = mapped_column(String(64))
    counter: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[str] = mapped_column(String(40))


class Event(Base):
    """One append-only state delta; the log is the graph of record (AD-1).

    Event rows are never deleted or rewritten — undo is a compensating
    commit that appends the inverse deltas.
    """

    __tablename__ = "event"
    __table_args__ = (
        # The primary access pattern filters both columns (revision_events,
        # undo); SQLite uses at most one single-column index per query, so
        # the composite is the one that serves it.
        Index("ix_event_campaign_revision", "campaign_id", "revision_id"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaign.id"), index=True)
    revision_id: Mapped[str] = mapped_column(ForeignKey("revision.id"))
    type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(40))

    revision: Mapped["Revision"] = relationship(back_populates="events")


class Job(Base):
    """One persistent generation-queue entry (AD-3, AD-13).

    One FIFO across all campaigns; issuance order is SQLite ``rowid`` —
    the same monotonic clock as revisions and events (never ULID string
    order: ties on the random suffix). Jobs are NOT world graph: rows and
    transitions never touch the event log or revisions (AD-1 governs the
    graph only). Queue behavior lives in ``app.store.jobs``.
    """

    __tablename__ = "job"
    __table_args__ = (
        # DB-level enforcement of the closed state/kind sets and the
        # progress range — the store's pending/terminal logic assumes them.
        CheckConstraint(
            "state IN ('queued','running','succeeded','failed','cancelled')",
            name="ck_job_state",
        ),
        CheckConstraint(
            "kind IN ('text','image','video','build_in','generate','regenerate')",
            name="ck_job_kind",
        ),
        CheckConstraint("progress >= 0.0 AND progress <= 1.0", name="ck_job_progress"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaign.id"), index=True)
    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(32), index=True)
    progress: Mapped[float] = mapped_column(Float)
    max_llm_calls: Mapped[int] = mapped_column(Integer)
    max_media_calls: Mapped[int] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Generation output persisted by the worker on success (spec-1.4);
    #: jobs are not world graph, so this is NOT world state, never a revision.
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40))
    started_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    finished_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class ProposedCandidate(Base):
    """One staged candidate entity from a ``generate`` job (AR7, AR19).

    Staging rows live OUTSIDE the entity/edge tables (AR7): retrieval,
    export, and every world read are untouched until the DM accepts
    (story 3.2 owns the accept/reject lifecycle and the commit path; the
    generate runner never calls ``commit_subgraph``). ``payload`` is the
    candidate's structured AR24 sectioned record (spec-3.3): identity
    anchor (name, role, level/CR, race/type, class/profession,
    alignment), narrative-lore (appearance, personality, background,
    goals, relationships, the secret/rumor/party-hook triple, voice
    style, catchphrases), an AR25 stat block, a conditional boss section
    (role BBEG/Monster only), a world-integration block (reputation,
    factions, current location, reaction matrix, on_defeat), and typed
    edges into the committed world — tolerating extra keys (AR24
    forward compatibility). Written only through the store's staging
    function (AD-1) — one row per staged candidate, no revision, no
    event.
    """

    __tablename__ = "proposed_candidate"
    __table_args__ = (
        # DB-level enforcement of the closed proposal-kind and staging-
        # status sets, mirroring ck_job_kind/ck_job_state; story 3.2
        # extends these same lists (the status set to the full lifecycle).
        CheckConstraint(f"kind IN ('{PROPOSAL_KIND}')", name="ck_proposed_candidate_kind"),
        CheckConstraint(
            f"status IN ({', '.join(f"'{status}'" for status in PROPOSAL_STATUS)})",
            name="ck_proposed_candidate_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaign.id"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("job.id"), index=True)
    kind: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(40))
    #: The entity ULID committed when this candidate was accepted (null
    #: while proposed; never set for a rejected row). Durable audit
    #: provenance: row -> the entity it became (AD-15, spec-3.3).
    accepted_entity_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    #: The revision ULID created by the accept commit (null until it
    #: accepts). Lets a DM jump from the settled row to the accepting
    #: revision's events.
    accept_revision_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    #: For a regenerate-entity proposal (spec-3.5): the committed entity
    #: ULID this candidate was regenerated FROM. Null for every
    #: generate-staged row. Its accept is the ONLY legal regen-entity
    #: commit: the accept replaces the target entity in place (same ULID,
    #: existing edges preserved, one revision — AD-2/AR4) instead of
    #: minting a fresh ULID (which would orphan or duplicate edges).
    regenerates_entity_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    #: The regenerate target's committed ``data`` snapshotted at staging
    #: (spec-3.6, the accept-conflict guard; regenerate rows only, NULL
    #: for every generate-staged row). The DM's PATCH surface commits
    #: through the same store, so an accept whose target record no longer
    #: matches this snapshot would silently overwrite a hand edit — the
    #: accept compares ``target.data`` against it in the same BEGIN
    #: IMMEDIATE transaction and raises ``EntityEditConflictError`` (409)
    #: on mismatch (NULL fails closed: a pre-3.6 row cannot be verified).
    #: Refreshed to the current target by a re-roll's in-place payload
    #: replacement, so a re-rolled row is never stranded.
    entity_base_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class Media(Base):
    """Media manifest row (AD-10).

    Rows are written by the media stories (Epic 4); the table exists so
    media references are part of the versioned world and survive undo.
    """

    __tablename__ = "media"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaign.id"), index=True)
    entity_id: Mapped[str] = mapped_column(String(26), index=True)
    filename: Mapped[str] = mapped_column(String(500))
    kind: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(40))


class Account(Base):
    """One DM identity (AR14/AR29, spec-1.5).

    Email is normalized (lowercased/trimmed) before storage; the unique
    constraint is on the normalized value. Password is argon2id-hashed —
    the plaintext is never stored. Accounts are NOT world graph: no
    revisions, no events (AD-1 applies to world state only).
    """

    __tablename__ = "account"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[str] = mapped_column(String(40))


class Session(Base):
    """One login session (AR14/AR29, spec-1.5).

    The cookie carries an opaque random token; the DB stores only its
    SHA-256 hex (a leaked DB never yields usable tokens). ``expires_at``
    is the TTL boundary; ``revoked_at`` non-NULL means the session was
    logged out — revocation is immediate. Sessions are NOT world graph.
    """

    __tablename__ = "session"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("account.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[str] = mapped_column(String(40))
    expires_at: Mapped[str] = mapped_column(String(40))
    revoked_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


@dataclass(frozen=True)
class EntityInput:
    """One staged entity for ``commit_subgraph``.

    ``id=None``: the store assigns a new ULID (creation).
    ``id=<existing ULID>``: content replaced in place — inbound edges and
    media references survive (AD-2, AR4).
    """

    kind: str
    name: str
    text: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    id: str | None = None


@dataclass(frozen=True)
class EdgeInput:
    """One staged edge for ``commit_subgraph``.

    ``id=None``: new edge (store assigns a ULID). ``id=<existing ULID>``:
    counter update of that edge — src/dst/type are immutable (edge
    re-targeting is forbidden, AD-2) and the id MUST name an existing
    edge of the campaign: an explicit id never creates (spec-3.4 —
    creation under a caller-chosen id would let a stale update
    resurrect a concurrently deleted edge); an unknown id is an
    ``UnknownEdgeError``.
    """

    src: str
    dst: str
    type: str
    counter: int = 1
    id: str | None = None
