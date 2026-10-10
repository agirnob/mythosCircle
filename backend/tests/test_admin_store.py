"""Account access, literal pagination and real additive migration coverage."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.ids import new_id
from app.core.pagination import InvalidCursorError
from app.core.settings import admin_account_ids
from app.store import app_db_url, create_campaign, init_db, models, session_scope
from app.store.admin import ProtectedAdministratorError, list_users, set_disabled
from app.store.auth import (
    InactiveAccountError,
    create_session,
    get_session_account,
    register_account,
    verify_login,
)


@pytest.fixture()
def db(tmp_path: Path) -> Iterator[None]:
    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'admin.db'}")
    try:
        yield
    finally:
        init_db(previous)


def test_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MYTHOSCIRCLE_ADMIN_ACCOUNT_IDS", raising=False)
    assert admin_account_ids() == frozenset()
    account_id = new_id()
    monkeypatch.setenv("MYTHOSCIRCLE_ADMIN_ACCOUNT_IDS", f" , {account_id}, {account_id}, ")
    assert admin_account_ids() == {account_id}
    monkeypatch.setenv("MYTHOSCIRCLE_ADMIN_ACCOUNT_IDS", "bad")
    with pytest.raises(ValueError):
        admin_account_ids()


def test_suspend_restore_revokes_every_session(db: None) -> None:
    account = register_account("dm@example.com", "password123")
    tokens = [create_session(account.id)[0] for _ in range(2)]
    disabled = set_disabled(account.id, True, admin_ids=frozenset())
    assert disabled.disabled_at
    assert set_disabled(account.id, True, admin_ids=frozenset()).disabled_at == disabled.disabled_at
    assert verify_login(account.email, "password123") is None
    assert all(get_session_account(token) is None for token in tokens)
    with pytest.raises(InactiveAccountError):
        create_session(account.id)
    assert set_disabled(account.id, False, admin_ids=frozenset()).disabled_at is None
    assert set_disabled(account.id, False, admin_ids=frozenset()).disabled_at is None
    assert verify_login(account.email, "password123") is not None
    assert all(get_session_account(token) is None for token in tokens)
    assert get_session_account(create_session(account.id)[0]) is not None
    with pytest.raises(ProtectedAdministratorError):
        set_disabled(account.id, True, admin_ids=frozenset({account.id}))


def test_projection_counts_and_literal_pagination(db: None) -> None:
    accounts = [
        register_account(email, "password123")
        for email in ["first%@example.com", "second_@example.com", "third@example.com"]
    ]
    create_campaign(
        accounts[0].id, title="Private", description="", theme="High Fantasy", custom_lore=""
    )
    with session_scope() as session:
        session.add(
            models.Campaign(
                id=new_id(),
                owner_id=accounts[0].id,
                title="Library",
                description="",
                theme="High Fantasy",
                custom_lore="",
                is_generic=True,
                created_at=accounts[0].created_at,
            )
        )
    page, cursor = list_users(admin_ids=frozenset({accounts[0].id}), limit=1)
    assert page[0].id == accounts[0].id and page[0].campaign_count == 1
    assert page[0].is_admin and cursor == accounts[0].id
    page2, cursor2 = list_users(admin_ids=frozenset(), limit=1, cursor=cursor)
    assert page2[0].id == accounts[1].id and cursor2 == accounts[1].id
    assert [u.id for u in list_users(admin_ids=frozenset(), q=" % ")[0]] == [accounts[0].id]
    assert [u.id for u in list_users(admin_ids=frozenset(), q="_")[0]] == [accounts[1].id]
    assert len(list_users(admin_ids=frozenset(), q=" EXAMPLE.COM ")[0]) == 3
    with pytest.raises(InvalidCursorError):
        list_users(admin_ids=frozenset(), q="third", cursor=cursor)
    with pytest.raises(InvalidCursorError):
        list_users(admin_ids=frozenset(), cursor=new_id())


def test_old_schema_reopens_twice_preserving_data(tmp_path: Path) -> None:
    previous = app_db_url()
    path = tmp_path / "legacy.db"
    url = f"sqlite:///{path}"
    other = f"sqlite:///{tmp_path / 'other.db'}"
    try:
        init_db(url)
        original = register_account("old@example.com", "password123")
        campaign = create_campaign(
            original.id, title="Existing", description="", theme="High Fantasy", custom_lore=""
        )
        token, _ = create_session(original.id)
        # Recreate the pre-feature shape with populated worlds and sessions.
        # Dispose the open URL before ALTER and before each genuine startup.
        init_db(other)
        with sqlite3.connect(path) as connection:
            connection.execute("ALTER TABLE account DROP COLUMN disabled_at")
        for _ in range(2):
            init_db(url)
            with session_scope() as session:
                account = session.get(models.Account, original.id)
                assert account is not None and account.disabled_at is None
                assert account.email == original.email
                assert session.scalar(select(models.Campaign.id)) == campaign.id
            assert get_session_account(token) is not None
            init_db(other)
    finally:
        init_db(previous)


def test_newly_configured_disabled_admin_can_be_restored(db: None) -> None:
    account = register_account("recover@example.com", "password123")
    set_disabled(account.id, True, admin_ids=frozenset())
    restored = set_disabled(account.id, False, admin_ids=frozenset({account.id}))
    assert restored.disabled_at is None and restored.is_admin


def test_startup_rejects_ulid_overflow(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import create_app

    monkeypatch.setenv("MYTHOSCIRCLE_ADMIN_ACCOUNT_IDS", "Z" * 26)
    with pytest.raises(ValueError, match="valid ULIDs"):
        create_app()
    monkeypatch.setenv("MYTHOSCIRCLE_ADMIN_ACCOUNT_IDS", "8" + "0" * 25)
    with pytest.raises(ValueError, match="valid ULIDs"):
        create_app()
    monkeypatch.setenv("MYTHOSCIRCLE_ADMIN_ACCOUNT_IDS", "7" + "Z" * 25)
    assert admin_account_ids() == {"7" + "Z" * 25}
