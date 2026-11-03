"""Mazeworld NPC dialogue generation + inlining.

``MazeworldDialoguePhase`` is the pack's answer to Option A: NPCs are generated
once, by ``DatabasePhase(npc)`` into ``npcs.json``.  This phase then generates a
dialogue tree for each of those NPCs and writes it *inline* into the NPC entry,
which is the shape mazeworld loads.

Why this exists (and replaces canon's ``DialoguePhase`` in the mazeworld
pipeline): canon's ``DialoguePhase`` is ``Character``-based — it iterates
``bible.characters`` and stores trees in ``bible.dialogues`` keyed by
character_id.  The mazeworld NPCs live in ``npcs.json`` (DatabasePhase output),
not ``bible.characters``, and mazeworld wants the tree *inline* on each NPC, not
in a side collection.  So this phase adapts each NPC dict into a transient
``Character``, reuses canon's tree generation (``generate_for_character``), then
converts the resulting ``DialogueTree`` into mazeworld's
``{"nodes": {...}}`` shape and writes it onto the NPC.

In ``GAME_MODE == "offline_static"`` (canon's default), a populated
``dialogue_tree`` makes mazeworld traverse the tree locally instead of calling
an LLM at runtime — so NPCs talk without an API key.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from canon.bible.models import BibleMetadata, Character
from canon.pipeline.phases.dialogue import DialoguePhase
from canon.pipeline.steplog import step

logger = logging.getLogger(__name__)


def _as_int_id(entity_id: Any) -> int | None:
    try:
        return int(entity_id)
    except (TypeError, ValueError):
        return None


class MazeworldDialoguePhase:
    """Generate a dialogue tree per ``npcs.json`` NPC and inline it.

    Run order: AFTER ``DatabasePhase(npc)`` (npcs.json + bible npc stubs exist).
    Replaces canon's ``DialoguePhase`` in the mazeworld pipeline.
    """

    name: str = "mazeworld_dialogue"

    def __init__(self, nodes_per_tree: int | tuple[int, int] = (3, 6)) -> None:
        # Reuse canon's generation machinery; roles is irrelevant here because
        # we drive generation directly via generate_for_character().
        self._dp = DialoguePhase(roles=("npc",), nodes_per_tree=nodes_per_tree)

    def run(self, ctx: Any) -> None:
        output_dir = Path(getattr(ctx.config, "output_dir", "."))
        output_paths = getattr(ctx.config, "output_paths", {})
        npcs_rel = output_paths.get("npcs", "npcs/npcs.json")
        npcs_path = output_dir / npcs_rel

        if not npcs_path.exists():
            logger.warning("MazeworldDialoguePhase: %s not found; skipping.", npcs_path)
            self._stamp_metadata(ctx)
            return

        npcs = json.loads(npcs_path.read_text(encoding="utf-8"))
        if not isinstance(npcs, list) or not npcs:
            logger.warning("MazeworldDialoguePhase: no NPCs to process.")
            self._stamp_metadata(ctx)
            return

        # Map npc id -> map_id from bible stubs so each tree is generated with
        # that room's cumulative story context.
        npc_map_id: dict[int, str] = {}
        for map_id, map_obj in ctx.bible.maps.items():
            for stub in getattr(map_obj, "entities", []):
                if stub.entity_type != "npc":
                    continue
                nid = _as_int_id(stub.entity_id)
                if nid is not None:
                    npc_map_id[nid] = map_id

        # Quest-giver linkage: quests.json carries giver_npc_id (set by
        # parse_quest). A quest-giver NPC gets the 4-variant tree set and its
        # quest_id wired in so mazeworld's quest_manager can swap trees on
        # completion/failure.
        quest_by_giver = self._quest_by_giver(output_dir, output_paths)

        generated = 0
        # Row P0-10 (§3.0-E/D): one item per NPC tree — the longest per-item
        # leg of a paid dungeon run — through the ONE emitter, which is also
        # the A4.5 cancel boundary.
        for npc_index, npc in enumerate(npcs, start=1):
            npc_id = npc.get("id")
            step(ctx, self.name, str(npc.get("name") or f"npc {npc_id}"), npc_index, len(npcs))
            character = self._npc_to_character(npc, npc_map_id.get(npc_id))
            quest = quest_by_giver.get(npc_id)

            if quest is not None:
                self._dp.generate_for_character(ctx, character, quest_variants=True)
                self._inline_quest_variants(npc, character, ctx.bible.dialogues)
                npc["quest_id"] = quest.get("id")
                npc["quest_type"] = quest.get("type")
                npc["is_story_npc"] = bool(quest.get("is_story_quest", False))
            else:
                self._dp.generate_for_character(ctx, character)
                # Bible.add_dialogue keys the dialogues dict by character_id.
                tree = ctx.bible.dialogues.get(
                    character.character_id
                ) or ctx.bible.dialogues.get(character.dialogue_tree_id)
                if tree is not None:
                    npc["dialogue_tree"] = self._to_mazeworld_tree(tree)
            generated += 1

        ctx.adapter.write_json_array(npcs_rel, npcs)
        self._stamp_metadata(ctx)
        logger.info("MazeworldDialoguePhase processed %d NPCs.", generated)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _quest_by_giver(output_dir: Path, output_paths: dict) -> dict[int, dict]:
        """Return ``{giver_npc_id: quest}`` (first quest per giver) from
        quests.json.  Empty if the file is missing."""
        quests_path = output_dir / output_paths.get("quests", "quests/quests.json")
        if not quests_path.exists():
            return {}
        quests = json.loads(quests_path.read_text(encoding="utf-8"))
        by_giver: dict[int, dict] = {}
        for quest in quests if isinstance(quests, list) else []:
            giver = quest.get("giver_npc_id")
            if giver is not None and giver not in by_giver:
                by_giver[giver] = quest
        return by_giver

    def _inline_quest_variants(
        self, npc: dict, character: Any, dialogues: dict
    ) -> None:
        """Inline the 4-variant tree set onto a quest-giver NPC.

        ``_generate_quest_variants`` keys ``bible.dialogues`` by tree_id and
        records each tree_id on the character's ``dialogue_tree_*_id`` fields.
        Mazeworld shows ``dialogue_tree`` while a quest is incomplete and swaps
        to ``dialogue_tree_complete`` / ``_failed`` on resolution, so the base
        tree is set to the incomplete variant (matching mazeworld's pipeline).
        """
        base = dialogues.get(character.dialogue_tree_id)
        incomplete = dialogues.get(character.dialogue_tree_incomplete_id)
        complete = dialogues.get(character.dialogue_tree_complete_id)
        failed = dialogues.get(character.dialogue_tree_failed_id)

        shown = incomplete or base
        if shown is not None:
            npc["dialogue_tree"] = self._to_mazeworld_tree(shown)
        if incomplete is not None:
            npc["dialogue_tree_incomplete"] = self._to_mazeworld_tree(incomplete)
        if complete is not None:
            npc["dialogue_tree_complete"] = self._to_mazeworld_tree(complete)
        if failed is not None:
            npc["dialogue_tree_failed"] = self._to_mazeworld_tree(failed)

    @staticmethod
    def _npc_to_character(npc: dict, map_id: str | None) -> Character:
        """Adapt a mazeworld NPC dict into a transient canon Character.

        Not added to bible.characters — it exists only to drive dialogue
        generation, so it never inflates npc_count or world_bible stubs.
        """
        npc_id = npc.get("id", 0)
        return Character(
            character_id=str(npc_id),
            name=npc.get("name") or f"NPC {npc_id}",
            role="npc",
            primary_map_id=map_id,
            personality=npc.get("personality"),
            lore=npc.get("backstory") or "",
            job=npc.get("job"),
            hobby=npc.get("hobby"),
            opening_greeting=npc.get("opening_greeting"),
        )

    @staticmethod
    def _to_mazeworld_tree(tree: Any) -> dict:
        """Convert a canon DialogueTree into mazeworld's ``{"nodes": {...}}``.

        mazeworld's ``_static_dialogue_response`` reads ``tree["nodes"]`` and
        starts at the ``"start"`` node (``tree.get("_current", "start")``), so
        the entry node is exposed as ``"start"`` when it isn't already.
        """
        nodes: dict[str, dict] = {}
        for node_id, node in tree.nodes.items():
            nodes[node_id] = {
                "prompt": node.prompt,
                "choices": [
                    {"text": choice.text, "next_node_id": choice.next_node_id}
                    for choice in node.choices
                ],
            }
        # mazeworld assumes a "start" entry node; alias it if the tree's entry
        # node is named differently.
        entry = getattr(tree, "entry_node_id", "start")
        if entry != "start" and entry in nodes and "start" not in nodes:
            nodes["start"] = nodes[entry]
        return {"nodes": nodes}

    def _stamp_metadata(self, ctx: Any) -> None:
        if not isinstance(getattr(ctx.bible, "metadata", None), BibleMetadata):
            ctx.bible.metadata = BibleMetadata()
        ctx.bible.metadata.phases_run.append(self.name)
