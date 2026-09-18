"""Regression corpus from REAL gemma-4-26B model output (2026-09-16..18).

The 2026-09-17/18 stat-block power saga (see
`_bmad-output/implementation-artifacts/handoff-statblock-power-saga-2026-09-18.md`)
exposed the suite's structural blind spot: every hand-written fixture
carried INTERNALLY CONSISTENT data, so ~1370 tests could not see five
"easy" bugs that all lived in CONTRADICTORY model output — a count field
contradicting the part's own dice string, prose stating more damage than
the parts, a multiattack routine named only by its prose. The antidote:
capture the live model output VERBATIM from the per-job LLM journal
(`data/llm-journal/`) and pin the gate against it — inconsistent fields
and all.

Each fixture file is
``{"journal_id": <job>, "kind": <wave1|regenerate>, "captured": <ts>,
"entity": <name>, "block": <the stat_block EXACTLY as the model returned
it>}``. The ``_raw_*`` assertions first pin that the captured fixture is
STILL contradictory (a guard against a future hand-edit of the corpus),
then assert what a DM actually sees after the canonicalization gate — the
committed sheet, not the fold internals.
"""

import json
from pathlib import Path

import pytest

from app.pipeline import combat
from app.pipeline.statblocks import canonicalize_stat_block

_FIXTURES = Path(__file__).parent / "fixtures" / "model_outputs"


def _load(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text())


def _action_parts(block: dict, name: str) -> list[dict]:
    for action in block["actions"]:
        if action["name"] == name:
            return action.get("damage") or []
    raise AssertionError(f"no action {name!r} in {[a['name'] for a in block['actions']]}")


def _action_part(block: dict, name: str, index: int = 0) -> dict:
    parts = _action_parts(block, name)
    return parts[index]


def _assert_raw_contradiction(part: dict, dice: str, count: int, average: int) -> None:
    """The VERBATIM model slip (the b99105f class): the dice string and
    average agree with each other — only the stated ``count`` is wrong.
    If this ever stops holding, the fixture was hand-cleaned and the
    regression is silent again."""
    assert part["dice"] == dice
    assert part["count"] == count
    assert part["average"] == average


def test_fasiha_original_void_collapse_count_healed() -> None:
    """The original committed fasiha block (wave1, 2026-09-17): Void
    Collapse shipped ``{"dice": "8d8", "count": 1, "sides": 8, "average":
    36}`` — dice and average agree, count lies. The gate must heal count
    from the dice string (8), never rewrite the part down to 1d8/4.5."""
    env = _load("fasiha-wave1-original-2026-09-17.json")
    _assert_raw_contradiction(_action_part(env["block"], "Void Collapse"), "8d8", 1, 36)
    canonical = canonicalize_stat_block(env["block"])
    part = _action_part(canonical, "Void Collapse")
    assert (part["dice"], part["count"], part["sides"], part["average"]) == ("8d8", 8, 8, 36)


def test_fasiha_accepted_regenerate_same_slip_healed() -> None:
    """The ACCEPTED re-roll (regenerate, 2026-09-18) still carries the
    count-1 slip verbatim in the raw response — the gate heals it on the
    way to the committed sheet (the live accept stored count 8 / avg 36)."""
    env = _load("fasiha-accepted-regenerate-2026-09-18.json")
    _assert_raw_contradiction(_action_part(env["block"], "Void Collapse"), "8d8", 1, 36)
    canonical = canonicalize_stat_block(env["block"])
    part = _action_part(canonical, "Void Collapse")
    assert (part["dice"], part["count"], part["sides"], part["average"]) == ("8d8", 8, 8, 36)
    # the healthy Eldritch Blast part passes through untouched
    blast = _action_part(canonical, "Eldritch Blast")
    assert (blast["dice"], blast["count"], blast["sides"], blast["bonus"]) == (
        "1d10",
        1,
        10,
        8,
    )


def test_fatima_arcane_bolt_count_healed() -> None:
    """Same slip, second live case (wave1, level-20 BBEG): Arcane Bolt
    4d6 with ``count: 1`` — the dice string carries the identity."""
    env = _load("fatima-wave1-arcane-bolt-2026-09-17.json")
    _assert_raw_contradiction(_action_part(env["block"], "Arcane Bolt"), "4d6", 1, 22)
    canonical = canonicalize_stat_block(env["block"])
    part = _action_part(canonical, "Arcane Bolt")
    assert (part["dice"], part["count"], part["average"]) == ("4d6", 4, 22)


def test_ashen_pilgrim_censer_count_healed() -> None:
    """Third live case (wave1, level-5 NPC): the Censer's necrotic part
    2d6 with ``count: 1`` sits beside a healthy 1d6 part in one action."""
    env = _load("ashen-pilgrim-censer-2026-09-16.json")
    raw = next(p for p in _action_parts(env["block"], "Censer of Ash") if p["type"] == "necrotic")
    _assert_raw_contradiction(raw, "2d6", 1, 7)
    canonical = canonicalize_stat_block(env["block"])
    fixed = next(p for p in _action_parts(canonical, "Censer of Ash") if p["type"] == "necrotic")
    assert (fixed["dice"], fixed["count"], fixed["average"]) == ("2d6", 2, 7)


def test_fasiha_power_accounting_ontarget_level20() -> None:
    """The end state of the whole saga on the ORIGINAL captured block:
    dice identity restores Void Collapse to 36, the routine NAMED ONLY BY
    ITS PROSE ("Multiattack: fasiha makes three Eldritch Blast attacks")
    multiplies exactly that named action (3 x 13.5), and the level-20 NPC
    lands on-target inside the class-grade band (25, 84) — the live case
    that went from under-powered DPR 13.5 to 40.5."""
    env = _load("fasiha-wave1-original-2026-09-17.json")
    canonical = canonicalize_stat_block(env["block"])
    audit = combat.audit_stat_block(canonical)
    assert audit.band == (25, 84)
    assert audit.dpr == pytest.approx(3 * 13.5)
    assert audit.verdict == combat.VERDICT_ONTARGET


def test_canonicalize_never_lowers_healthy_numbers() -> None:
    """The folds only raise or heal — the accepted re-roll's numbers on
    the committed sheet survive a re-canonicalization unchanged."""
    env = _load("fasiha-accepted-regenerate-2026-09-18.json")
    canonical = canonicalize_stat_block(env["block"])
    counts = {
        (part["dice"], part["count"])
        for action in canonical["actions"]
        for part in (action.get("damage") or [])
    }
    assert ("8d8", 8) in counts
    assert ("1d10", 1) in counts
