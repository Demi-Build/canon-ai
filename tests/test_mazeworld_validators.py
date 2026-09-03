"""Tests for the mazeworld pack's referential-integrity / solvability validators.

They read persisted JSON under output_dir, so each test writes tiny fixture
files into tmp_path and asserts the validator passes on clean data and flags
errors on broken data (the regression guard for the bug classes we hand-fixed).
"""

from __future__ import annotations

import json
from pathlib import Path

from canon.packs.dungeon.validators import (
    LootIntegrityValidator,
    PuzzleSolvabilityValidator,
    QuestCompletabilityValidator,
)


def _write(tmp: Path, rel: str, data) -> None:
    p = tmp / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# LootIntegrityValidator
# ---------------------------------------------------------------------------


def test_loot_integrity_passes_on_real_ids(tmp_path):
    _write(tmp_path, "items/items.json", {"2000": {"id": 2000, "name": "Sword"}})
    _write(tmp_path, "events/events.json", [{"id": 3000, "loot_table": [{"item_id": 2000}]}])
    _write(tmp_path, "quests/quests.json", [{"id": 4000, "reward": {"item_id": 2000}}])
    _write(tmp_path, "npcs/npcs.json", [])
    res = LootIntegrityValidator(tmp_path).validate(None)
    assert res.passed, res.issues


def test_loot_integrity_flags_hallucinated_ids(tmp_path):
    _write(tmp_path, "items/items.json", {"2000": {"id": 2000, "name": "Sword"}})
    _write(tmp_path, "events/events.json", [{"id": 3000, "loot_table": [{"item_id": 217}]}])
    _write(tmp_path, "quests/quests.json", [{"id": 4000, "reward": {"item_id": 999}}])
    _write(tmp_path, "npcs/npcs.json", [])
    res = LootIntegrityValidator(tmp_path).validate(None)
    assert not res.passed
    assert res.severity == "error"
    assert any("217" in i for i in res.issues)
    assert any("999" in i for i in res.issues)


# ---------------------------------------------------------------------------
# QuestCompletabilityValidator
# ---------------------------------------------------------------------------


def test_quest_completability_passes(tmp_path):
    _write(tmp_path, "npcs/npcs.json", [{"id": 1000}])
    _write(tmp_path, "items/items.json", {"2000": {"id": 2000}})
    _write(tmp_path, "events/events.json", [{"id": 3000}])
    _write(tmp_path, "monsters/monsters.json", {"5000": {"name": "Ghoul"}})
    _write(tmp_path, "quests/quests.json", [
        {"id": 4000, "type": "escort", "giver_npc_id": 1000, "escort_npc_id": 1000, "target_zone": [12, 7]},
    ])
    res = QuestCompletabilityValidator(tmp_path).validate(None)
    assert res.passed, res.issues


def test_quest_completability_flags_bad_giver_and_zero_zone(tmp_path):
    _write(tmp_path, "npcs/npcs.json", [{"id": 1000}])
    _write(tmp_path, "items/items.json", {})
    _write(tmp_path, "events/events.json", [])
    _write(tmp_path, "monsters/monsters.json", {})
    _write(tmp_path, "quests/quests.json", [
        {"id": 4000, "type": "escort", "giver_npc_id": 1000, "escort_npc_id": 9999, "target_zone": [0, 0]},
    ])
    res = QuestCompletabilityValidator(tmp_path).validate(None)
    assert not res.passed
    assert any("9999" in i for i in res.issues)        # bad escort npc
    assert any("[0,0]" in i.replace(" ", "") for i in res.issues)  # unset target zone


# ---------------------------------------------------------------------------
# PuzzleSolvabilityValidator
# ---------------------------------------------------------------------------


def test_puzzle_solvability_passes(tmp_path):
    _write(tmp_path, "classes/classes.json", [
        {"abilities": [{"name": "Smash"}], "spells": [{"name": "Ember"}]},
    ])
    _write(tmp_path, "events/events.json", [
        {"id": 3000, "type": "puzzle", "choices": [{"text": "Walk away", "auto_success": True}],
         "correct_ability": "Smash", "correct_spell": "Ember", "correct_tool": "cutting"},
    ])
    res = PuzzleSolvabilityValidator(tmp_path).validate(None)
    assert res.passed, res.issues


def test_puzzle_solvability_flags_no_walkaway_and_fake_refs(tmp_path):
    _write(tmp_path, "classes/classes.json", [{"abilities": [{"name": "Smash"}], "spells": []}])
    _write(tmp_path, "events/events.json", [
        {"id": 3000, "type": "puzzle",
         "choices": [{"text": "Force it", "stat_check": "STR"}],  # no walk-away
         "correct_ability": "Nonexistent Power", "correct_spell": None, "correct_tool": "lockpicking"},
    ])
    res = PuzzleSolvabilityValidator(tmp_path).validate(None)
    assert not res.passed
    assert any("walk-away" in i for i in res.issues)
    assert any("Nonexistent Power" in i for i in res.issues)
    assert any("lockpicking" in i for i in res.issues)
