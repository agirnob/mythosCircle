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


class Base(DeclarativeBase):
    """Declarative base for the world-store schema."""


class Campaign(Base):
    """A private world: one invited DM in Phase 1 (AD-9)."""

    __tablename__ = "campaign"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    name: Mapped[str] = mapped_column(String(500))
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
    Phase-1 vocabulary; ``counter`` semantics are per type
    (debt = amount, grudge/loyalty = score, ally/enemy = intensity).
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
            "kind IN ('text','image','video')",
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
    created_at: Mapped[str] = mapped_column(String(40))
    started_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    finished_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


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
    re-targeting is forbidden, AD-2).
    """

    src: str
    dst: str
    type: str
    counter: int = 1
    id: str | None = None
