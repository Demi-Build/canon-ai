"""Mazeworld-pack validators — referential-integrity + solvability checks.

These are the concrete, game-specific Stage-2 validators that turn canon's
``ValidationPhase`` from a no-op into a guard against the bug classes this pack
has hand-fixed: hallucinated entity ids, unsolvable puzzles, uncompletable
quests. They are PACK code (mazeworld rules: quest types, tool attributes,
the 5 item categories); canon core only provides the ``BaseValidator`` /
``ValidationResult`` framework.

Design: each validator reads the **persisted JSON** under ``output_dir`` — the
same files MazeWorld actually loads — so it validates the real artifact rather
than in-memory stubs (which carry only name/lore/tags, not referential fields).
ValidationPhase runs after every DatabasePhase has written its file, so the
output is complete by then. Each validator returns one aggregated
``ValidationResult`` (severity ``error`` if any issue is found).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from canon.validation.validator import BaseValidator, ValidationResult

# mazeworld puzzle-solve vocabulary (matches parsers._TOOL_ATTRIBUTES)
_TOOL_ATTRIBUTES = {"bludgeon", "cutting", "digging", "climbing"}


def _load(output_dir: str | Path, rel: str) -> Any:
    """Load a generated JSON file, or None if absent/unreadable."""
    path = Path(output_dir) / rel
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _values(data: Any) -> list:
    """Normalize an array-or-keyed-object JSON file to a list of entries."""
    if isinstance(data, dict):
        return list(data.values())
    if isinstance(data, list):
        return data
    return []


def _int_ids(entries: list, key: str = "id") -> set[int]:
    out: set[int] = set()
    for e in entries:
        v = e.get(key)
        if v is not None:
            try:
                out.add(int(v))
            except (ValueError, TypeError):
                pass
    return out


def _result(issues: list[str]) -> ValidationResult:
    return ValidationResult(
        passed=not issues,
        severity="error" if issues else "info",
        issues=issues,
    )


class LootIntegrityValidator(BaseValidator):
    """Every item reference resolves to a real generated item: event loot,
    quest reward, and merchant shop inventory."""

    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = output_dir

    def validate(self, data: Any, context: dict | None = None) -> ValidationResult:
        item_ids = _int_ids(_values(_load(self.output_dir, "items/items.json") or []))
        if not item_ids:
            return _result([])  # no items to reference — nothing to check
        issues: list[str] = []

        for e in _load(self.output_dir, "events/events.json") or []:
            for entry in e.get("loot_table") or []:
                iid = entry.get("item_id")
                if iid is not None and int(iid) not in item_ids:
                    issues.append(f"event {e.get('id')}: loot item_id {iid} does not exist")

        for q in _load(self.output_dir, "quests/quests.json") or []:
            rid = (q.get("reward") or {}).get("item_id")
            if rid is not None and int(rid) not in item_ids:
                issues.append(f"quest {q.get('id')}: reward.item_id {rid} does not exist")

        for n in _load(self.output_dir, "npcs/npcs.json") or []:
            for entry in n.get("shop_inventory") or []:
                iid = entry.get("item_id")
                if iid is not None and int(iid) not in item_ids:
                    issues.append(f"npc {n.get('id')}: shop item_id {iid} does not exist")

        return _result(issues)


class QuestCompletabilityValidator(BaseValidator):
    """Every quest's references resolve and its type-specific target is set, so
    the quest is actually completable in-game."""

    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = output_dir

    def validate(self, data: Any, context: dict | None = None) -> ValidationResult:
        out = self.output_dir
        npc_ids = _int_ids(_load(out, "npcs/npcs.json") or [])
        item_ids = _int_ids(_values(_load(out, "items/items.json") or []))
        event_ids = _int_ids(_load(out, "events/events.json") or [])
        monster_names = {
            v.get("name") for v in _values(_load(out, "monsters/monsters.json") or [])
        }
        issues: list[str] = []

        for q in _load(out, "quests/quests.json") or []:
            qid, qt = q.get("id"), q.get("type")
            giver = q.get("giver_npc_id")
            if giver is not None and npc_ids and int(giver) not in npc_ids:
                issues.append(f"quest {qid}: giver_npc_id {giver} is not a real NPC")
            if qt == "escort":
                esc = q.get("escort_npc_id")
                if esc and npc_ids and int(esc) not in npc_ids:
                    issues.append(f"quest {qid}: escort_npc_id {esc} is not a real NPC")
                if q.get("target_zone") in ([0, 0], None):
                    issues.append(f"quest {qid}: escort target_zone is unset/[0,0] (uncompletable)")
            elif qt == "fetch":
                targets = q.get("target_items") or []
                if not targets:
                    issues.append(f"quest {qid}: fetch quest has no target_items")
                for t in targets:
                    iid = t.get("item_id")
                    if iid is not None and item_ids and int(iid) not in item_ids:
                        issues.append(f"quest {qid}: fetch target item_id {iid} does not exist")
            elif qt in ("combat", "solve"):
                tev = q.get("target_event_id")
                if tev and event_ids and int(tev) not in event_ids:
                    issues.append(f"quest {qid}: target_event_id {tev} is not a real event")
                tmn = q.get("target_monster_name")
                if qt == "combat" and tmn and monster_names and tmn not in monster_names:
                    issues.append(f"quest {qid}: target_monster_name {tmn!r} is not a real monster")

        return _result(issues)


class PuzzleSolvabilityValidator(BaseValidator):
    """Every puzzle/event is solvable: it has a walk-away auto-success, and any
    ability/spell/tool solve hook references a real generated entity."""

    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = output_dir

    def validate(self, data: Any, context: dict | None = None) -> ValidationResult:
        out = self.output_dir
        ability_names: set[str] = set()
        spell_names: set[str] = set()
        for c in _load(out, "classes/classes.json") or []:
            for a in (c.get("abilities") or []) + (c.get("ability_pool") or []):
                ability_names.add(a.get("name"))
            for s in (c.get("spells") or []) + (c.get("spell_pool") or []):
                spell_names.add(s.get("name"))
        issues: list[str] = []

        for e in _load(out, "events/events.json") or []:
            if e.get("type") not in ("puzzle", "event"):
                continue
            eid = e.get("id")
            choices = e.get("choices") or []
            if not any(c.get("auto_success") for c in choices):
                issues.append(f"event {eid}: puzzle has no walk-away auto-success (unsolvable)")
            ca, cs, ct = e.get("correct_ability"), e.get("correct_spell"), e.get("correct_tool")
            if ca and ability_names and ca not in ability_names:
                issues.append(f"event {eid}: correct_ability {ca!r} is not a real ability")
            if cs and spell_names and cs not in spell_names:
                issues.append(f"event {eid}: correct_spell {cs!r} is not a real spell")
            if ct and ct not in _TOOL_ATTRIBUTES:
                issues.append(f"event {eid}: correct_tool {ct!r} is not a known tool attribute")

        return _result(issues)


def mazeworld_validators(output_dir: str | Path) -> list[BaseValidator]:
    """The pack's Stage-2 validators, bound to the run's output directory."""
    return [
        LootIntegrityValidator(output_dir),
        QuestCompletabilityValidator(output_dir),
        PuzzleSolvabilityValidator(output_dir),
    ]
