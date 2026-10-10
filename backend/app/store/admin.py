"""Private account projections and atomic access transitions."""

from dataclasses import dataclass

from sqlalchemy import func, literal_column, select, update
from sqlalchemy.orm import Session

from app.core import time
from app.core.ids import is_valid_ulid
from app.core.pagination import InvalidCursorError, anchor_rowid, paging
from app.store import models
from app.store.commit import StoreError
from app.store.db import session_scope


class AccountNotFoundError(StoreError):
    """The target account does not exist."""


class ProtectedAdministratorError(StoreError):
    """Configured administrators cannot be disabled."""


@dataclass(frozen=True)
class AdminUser:
    id: str
    email: str
    is_admin: bool
    disabled_at: str | None
    created_at: str
    campaign_count: int


def _project(
    session: Session, accounts: list[models.Account], admin_ids: frozenset[str]
) -> list[AdminUser]:
    counts: dict[str, int] = {  # noqa: C416 - SQLAlchemy Rows are not typed tuples
        owner_id: count
        for owner_id, count in session.execute(
            select(models.Campaign.owner_id, func.count())
            .where(
                models.Campaign.owner_id.in_([a.id for a in accounts]),
                models.Campaign.is_generic.is_(False),
            )
            .group_by(models.Campaign.owner_id)
        ).all()
    }
    return [
        AdminUser(
            a.id, a.email, a.id in admin_ids, a.disabled_at, a.created_at, counts.get(a.id, 0)
        )
        for a in accounts
    ]


def list_users(
    *, admin_ids: frozenset[str], q: str = "", limit: int = 50, cursor: str | None = None
) -> tuple[list[AdminUser], str | None]:
    query = q.strip().lower()
    pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    matches = models.Account.email.like(pattern, escape="\\")
    with session_scope() as session:
        missing = InvalidCursorError("Cursor anchor does not exist or match the search.")
        anchor = anchor_rowid(session, models.Account, cursor, missing_error=missing)
        if (
            cursor is not None
            and session.scalar(
                select(models.Account.id).where(models.Account.id == cursor, matches)
            )
            is None
        ):
            raise missing
        accounts = list(
            session.scalars(
                select(models.Account)
                .where(matches, literal_column("rowid") > anchor)
                .order_by(literal_column("rowid"))
                .limit(limit + 1)
            )
        )
        page, next_cursor = paging(accounts, limit)
        return _project(session, list(page), admin_ids), next_cursor


def set_disabled(account_id: str, disabled: bool, *, admin_ids: frozenset[str]) -> AdminUser:
    if not is_valid_ulid(account_id) or account_id[0] not in "01234567":
        raise InvalidCursorError("Invalid account ID.")
    with session_scope() as session:
        account = session.get(models.Account, account_id)
        if account is None:
            raise AccountNotFoundError("Account not found.")
        if disabled and account_id in admin_ids:
            raise ProtectedAdministratorError("Configured administrators cannot be disabled.")
        if disabled:
            account.disabled_at = account.disabled_at or time.now()
            session.execute(
                update(models.LoginSession)
                .where(
                    models.LoginSession.account_id == account_id,
                    models.LoginSession.revoked_at.is_(None),
                )
                .values(revoked_at=time.now())
            )
        else:
            account.disabled_at = None
        session.flush()
        return _project(session, [account], admin_ids)[0]
