"""Read helpers for the world store.

Reads the materialized latest-revision state and the append-only event
log. All reads go through the store (AD-13).
"""

from collections.abc import Sequence

from sqlalchemy import literal_column, select
from sqlalchemy.orm import Session

from app.store import models


def latest_revision(session: Session, campaign_id: str) -> models.Revision | None:
    """The head revision of a campaign (most recently committed).

    Ordered by SQLite ``rowid`` — monotonic per table under the single
    writer. ULID string order is not insertion order when revisions share
    a millisecond (ties break on the random suffix).
    """
    return session.scalars(
        select(models.Revision)
        .where(models.Revision.campaign_id == campaign_id)
        .order_by(literal_column("rowid").desc())
        .limit(1)
    ).first()


def revision_chain(session: Session, campaign_id: str) -> Sequence[models.Revision]:
    """All revisions of a campaign, oldest first."""
    return session.scalars(
        select(models.Revision)
        .where(models.Revision.campaign_id == campaign_id)
        .order_by(literal_column("rowid"))
    ).all()


def revision_events(session: Session, campaign_id: str, revision_id: str) -> Sequence[models.Event]:
    """The events owned by a revision, in commit order."""
    return session.scalars(
        select(models.Event)
        .where(
            models.Event.campaign_id == campaign_id,
            models.Event.revision_id == revision_id,
        )
        .order_by(literal_column("rowid"))
    ).all()


def world_state(
    session: Session, campaign_id: str
) -> tuple[Sequence[models.Entity], Sequence[models.Edge]]:
    """The materialized latest-revision entities and edges, in rowid
    (commit) order — deterministic retrieval (AD-16), matching the other
    read helpers' documented ordering."""
    entities = session.scalars(
        select(models.Entity)
        .where(models.Entity.campaign_id == campaign_id)
        .order_by(literal_column("rowid"))
    ).all()
    edges = session.scalars(
        select(models.Edge)
        .where(models.Edge.campaign_id == campaign_id)
        .order_by(literal_column("rowid"))
    ).all()
    return entities, edges
