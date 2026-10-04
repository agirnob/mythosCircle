"""Snapshot-only Tonight detail projection, including open JSON session images."""

from typing import Any

import pytest

from app.api.campaigns import _detail_key, _event_details
from app.store import models


def event(kind: str, before: Any, after: Any, **payload: Any) -> models.Event:
    return models.Event(type=kind, payload={"before": before, "after": after, **payload})


@pytest.mark.parametrize(
    ("key", "enabled", "disabled"),
    [
        ("defeated", "Marked defeated", "Cleared defeated"),
        ("allegiance", "Flipped allegiance", "Restored allegiance"),
        ("thread", "Resolved thread", "Reopened thread"),
        ("item", "Spent item", "Restored item"),
    ],
)
def test_flag_transitions_and_removal(key: str, enabled: str, disabled: str) -> None:
    assert _event_details(event("session_state_created", None, {"data": {key: True}})) == [enabled]
    assert _event_details(
        event("session_state_updated", {"data": {key: True}}, {"data": {key: False}})
    ) == [disabled]
    assert _event_details(event("session_state_deleted", {"data": {key: False}}, None)) == [
        f"{key.capitalize()}: removed"
    ]
    assert (
        _event_details(event("session_state_updated", {"data": {key: True}}, {"data": {key: True}}))
        == []
    )


def test_open_image_values_missing_null_and_nested_types() -> None:
    before = {
        "hp": 20,
        "ally_of": "Captain",
        "supplies": {"count": True},
        "old_note": None,
        "nullable": "old",
        "flag": True,
        "same": {"a": 1, "b": 2},
    }
    after = {
        "hp": 8,
        "ally_of": "Admiral",
        "supplies": {"count": 1},
        "nullable": None,
        "new_null": None,
        "flag": 1,
        "same": {"b": 2, "a": 1},
    }
    assert _event_details(event("session_state_updated", {"data": before}, {"data": after})) == [
        'Ally of: "Admiral"',
        "Flag: 1",
        "HP: 8",
        "New null: null",
        "Nullable: null",
        "Old note: removed",
        'Supplies: {"count": 1}',
    ]


def test_hp_label_preserves_the_familiar_abbreviation() -> None:
    assert _detail_key("hp") == "HP"


@pytest.mark.parametrize("field", ["secret", "rumor", "party_hook"])
def test_knowledge_changes_and_timestamp_only_compensation(field: str) -> None:
    known = {"field": field, "known": True, "updated_at": "old"}
    hidden = {**known, "known": False}
    label = field.replace("_", " ")
    assert _event_details(event("knowledge_state_created", None, known, field=field)) == [
        f"Revealed {label} to party"
    ]
    assert _event_details(event("knowledge_state_created", None, hidden, field=field)) == [
        f"{label.capitalize()} hidden from party"
    ]
    assert _event_details(event("knowledge_state_updated", known, hidden, field=field)) == [
        f"{label.capitalize()} hidden from party"
    ]
    assert _event_details(event("knowledge_state_updated", hidden, known, field=field)) == [
        f"Revealed {label} to party"
    ]
    assert _event_details(event("knowledge_state_deleted", hidden, None, field=field)) == [
        f"Removed {label} knowledge marker"
    ]
    assert (
        _event_details(
            event(
                "knowledge_state_updated",
                known,
                {**known, "updated_at": "new"},
                field=field,
            )
        )
        == []
    )


@pytest.mark.parametrize("kind", ["entity_created", "entity_updated", "edge_deleted"])
def test_ordinary_events_have_no_additional_details(kind: str) -> None:
    assert _event_details(event(kind, {}, {"data": {"defeated": True}})) == []
