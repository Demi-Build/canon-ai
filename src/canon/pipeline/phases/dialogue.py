"""DialoguePhase — generates DialogueTree per matching Character.

For every Character in Bible.characters whose role matches `roles`, generate a
DialogueTree and store it in Bible.dialogues keyed by character_id (via
Bible.add_dialogue). Sets the character's `dialogue_tree_id` field. Skip for
dialogue-free games. Requires CharacterPhase to have run first (or characters
to exist).

v0.2: 4-variant tree generation for quest-bearing characters. When a character's
role is in `quest_bearing_roles`, four DialogueTree variants are generated:
  - base (`{char_id}_dialogue`)
  - `{char_id}_dialogue_complete`
  - `{char_id}_dialogue_failed`
  - `{char_id}_dialogue_incomplete`

The character's four `dialogue_tree_*_id` fields are set accordingly.
Non-quest characters still get a single base tree (backward-compatible).
The `quest_bearing_roles` set defaults to empty (opt-in feature).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any

from canon.bible.models import BibleMetadata, GenerationTrail
from canon.dialogue.models import DialogueChoice, DialogueNode, DialogueTree
from canon.pipeline.retry import retry_with_feedback

logger = logging.getLogger(__name__)


class DialoguePhase:
    """Generates DialogueTree per matching Character.

    Roles default to the typical conversational set; users can override.
    Skips characters that already have a dialogue_tree_id set (avoids
    overwriting hand-authored content). Requires CharacterPhase to have
    run first (or characters to exist).

    nodes_per_tree:
        - int: exactly this many nodes
        - tuple(min, max): randomly chosen per character via ctx.rng
    """

    name: str = "dialogue"

    def __init__(
        self,
        roles: Sequence[str] = ("npc", "merchant", "villager", "trainer"),
        nodes_per_tree: int | tuple[int, int] = (3, 8),
        quest_bearing_roles: Sequence[str] = (),
    ) -> None:
        self.roles = tuple(roles)
        self.nodes_per_tree = nodes_per_tree
        self.quest_bearing_roles = tuple(quest_bearing_roles)

    def run(self, ctx: Any) -> None:
        if not ctx.bible.characters:
            logger.warning("DialoguePhase: no characters in bible; nothing to do.")
            if not isinstance(ctx.bible.metadata, BibleMetadata):
                ctx.bible.metadata = BibleMetadata()
            ctx.bible.metadata.phases_run.append(self.name)
            return

        max_retries = getattr(ctx.config, "max_retries", 3)
        for character in ctx.bible.characters:
            if character.role not in self.roles:
                continue
            if character.dialogue_tree_id:
                logger.debug(
                    "DialoguePhase: skipping %s (already has dialogue_tree_id)",
                    character.character_id,
                )
                continue
            if self.quest_bearing_roles and character.role in self.quest_bearing_roles:
                self._generate_quest_variants(ctx, character, max_retries)
            else:
                self._generate_for(ctx, character, max_retries)

        if not isinstance(ctx.bible.metadata, BibleMetadata):
            ctx.bible.metadata = BibleMetadata()
        ctx.bible.metadata.phases_run.append(self.name)
        logger.info("DialoguePhase generated %d trees.", len(ctx.bible.dialogues))

    def _generate_for(self, ctx: Any, character: Any, max_retries: int) -> None:
        if isinstance(self.nodes_per_tree, tuple):
            lo, hi = self.nodes_per_tree
            nodes = ctx.rng.randint(lo, hi)
        else:
            nodes = int(self.nodes_per_tree)

        if character.primary_map_id:
            context_text = ctx.bible.get_cumulative_context(character.primary_map_id)
        else:
            context_text = ctx.bible.get_context("")

        captured: dict[str, str] = {"prompt": "", "response": ""}

        def generate(feedback: list[str] | None = None) -> str:
            if feedback:
                request = ctx.prompts.dialogue_tree_generation_with_feedback(
                    character=character,
                    context=context_text,
                    nodes_per_tree=nodes,
                    feedback=feedback,
                )
            else:
                request = ctx.prompts.dialogue_tree_generation(
                    character=character,
                    context=context_text,
                    nodes_per_tree=nodes,
                )
            captured["prompt"] = request.user_message
            response = ctx.llm.generate(request, phase=self.name)
            captured["response"] = response
            return response

        def validate(content: str) -> tuple[bool, list[str]]:
            try:
                data = json.loads(content)
            except json.JSONDecodeError as e:
                return False, [f"Not valid JSON: {e}"]
            if not isinstance(data, dict):
                return False, ["Response must be JSON object."]
            nodes_dict = data.get("nodes")
            if not isinstance(nodes_dict, dict) or not nodes_dict:
                return False, ["'nodes' must be a non-empty object."]
            entry_id = data.get("entry_node_id", "start")
            if entry_id not in nodes_dict:
                return False, [f"entry_node_id '{entry_id}' not in nodes."]
            return True, []

        fallback = json.dumps({
            "entry_node_id": "start",
            "nodes": {
                "start": {"prompt": "...", "choices": []},
            },
        })
        result = retry_with_feedback(
            generate_fn=generate,
            validate_fn=validate,
            fallback=fallback,
            max_retries=max_retries,
            label=f"{self.name}:{character.character_id}",
        )

        try:
            data = json.loads(result)
        except json.JSONDecodeError:
            return

        entry_id = data.get("entry_node_id", "start")
        nodes_dict: dict[str, DialogueNode] = {}
        for node_id, node_raw in (data.get("nodes") or {}).items():
            if not isinstance(node_raw, dict):
                continue
            choices: list[DialogueChoice] = []
            for choice_raw in node_raw.get("choices", []) or []:
                if not isinstance(choice_raw, dict):
                    continue
                choices.append(DialogueChoice(
                    text=choice_raw.get("text", ""),
                    next_node_id=choice_raw.get("next_node_id"),
                    conditions=choice_raw.get("conditions") or [],
                    effects=choice_raw.get("effects") or [],
                ))
            nodes_dict[node_id] = DialogueNode(
                node_id=node_id,
                speaker=node_raw.get("speaker"),
                prompt=node_raw.get("prompt", ""),
                choices=choices,
                tags=node_raw.get("tags") or [],
            )

        tree_id = f"{character.character_id}_dialogue"
        trail = GenerationTrail(
            prompt=captured["prompt"],
            response=captured["response"],
            validation_history=[],
            retry_count=0,
            cost=getattr(ctx.llm.backend, "last_cost", None),
            model=getattr(ctx.llm.backend, "model", None),
        )
        tree = DialogueTree(
            tree_id=tree_id,
            character_id=character.character_id,
            entry_node_id=entry_id,
            nodes=nodes_dict,
            generation_trail=trail,
        )
        ctx.bible.add_dialogue(tree)
        character.dialogue_tree_id = tree_id

    def _generate_quest_variants(
        self,
        ctx: Any,
        character: Any,
        max_retries: int,
    ) -> None:
        """Generate 4 dialogue tree variants for quest-bearing characters.

        Variants: base, _complete, _failed, _incomplete.
        Each is a separate LLM call producing an independent DialogueTree.
        The character's four dialogue_tree_*_id fields are set on success.
        """
        variants = [
            ("", None),                            # base → dialogue_tree_id
            ("_complete", "dialogue_tree_complete_id"),
            ("_failed",   "dialogue_tree_failed_id"),
            ("_incomplete", "dialogue_tree_incomplete_id"),
        ]

        for suffix, id_field in variants:
            tree_id = f"{character.character_id}_dialogue{suffix}"

            if isinstance(self.nodes_per_tree, tuple):
                lo, hi = self.nodes_per_tree
                nodes = ctx.rng.randint(lo, hi)
            else:
                nodes = int(self.nodes_per_tree)

            if character.primary_map_id:
                context_text = ctx.bible.get_cumulative_context(character.primary_map_id)
            else:
                context_text = ctx.bible.get_context("")

            # Augment context with the variant type so the LLM frames
            # the dialogue appropriately.
            variant_label = suffix.lstrip("_") or "base"
            variant_hint = (
                f"\n[Dialogue variant: {variant_label}. "
                "Frame the conversation for this quest outcome state.]"
            )
            augmented_context = context_text + variant_hint

            captured: dict[str, str] = {"prompt": "", "response": ""}

            def _make_generate(aug_ctx: str) -> Any:
                def generate(feedback: list[str] | None = None) -> str:
                    if feedback:
                        request = ctx.prompts.dialogue_tree_generation_with_feedback(
                            character=character,
                            context=aug_ctx,
                            nodes_per_tree=nodes,
                            feedback=feedback,
                        )
                    else:
                        request = ctx.prompts.dialogue_tree_generation(
                            character=character,
                            context=aug_ctx,
                            nodes_per_tree=nodes,
                        )
                    captured["prompt"] = request.user_message
                    response = ctx.llm.generate(request, phase=self.name)
                    captured["response"] = response
                    return response
                return generate

            def validate(content: str) -> tuple[bool, list[str]]:
                try:
                    data = json.loads(content)
                except json.JSONDecodeError as e:
                    return False, [f"Not valid JSON: {e}"]
                if not isinstance(data, dict):
                    return False, ["Response must be JSON object."]
                nodes_dict = data.get("nodes")
                if not isinstance(nodes_dict, dict) or not nodes_dict:
                    return False, ["'nodes' must be a non-empty object."]
                entry_id = data.get("entry_node_id", "start")
                if entry_id not in nodes_dict:
                    return False, [f"entry_node_id '{entry_id}' not in nodes."]
                return True, []

            fallback = json.dumps({
                "entry_node_id": "start",
                "nodes": {
                    "start": {"prompt": "...", "choices": []},
                },
            })
            result = retry_with_feedback(
                generate_fn=_make_generate(augmented_context),
                validate_fn=validate,
                fallback=fallback,
                max_retries=max_retries,
                label=f"{self.name}:{character.character_id}{suffix}",
            )

            try:
                data = json.loads(result)
            except json.JSONDecodeError:
                continue

            entry_id = data.get("entry_node_id", "start")
            nodes_dict: dict[str, DialogueNode] = {}
            for node_id, node_raw in (data.get("nodes") or {}).items():
                if not isinstance(node_raw, dict):
                    continue
                choices: list[DialogueChoice] = []
                for choice_raw in node_raw.get("choices", []) or []:
                    if not isinstance(choice_raw, dict):
                        continue
                    choices.append(DialogueChoice(
                        text=choice_raw.get("text", ""),
                        next_node_id=choice_raw.get("next_node_id"),
                        conditions=choice_raw.get("conditions") or [],
                        effects=choice_raw.get("effects") or [],
                    ))
                nodes_dict[node_id] = DialogueNode(
                    node_id=node_id,
                    speaker=node_raw.get("speaker"),
                    prompt=node_raw.get("prompt", ""),
                    choices=choices,
                    tags=node_raw.get("tags") or [],
                )

            trail = GenerationTrail(
                prompt=captured["prompt"],
                response=captured["response"],
                validation_history=[],
                retry_count=0,
                cost=getattr(ctx.llm.backend, "last_cost", None),
                model=getattr(ctx.llm.backend, "model", None),
            )
            tree = DialogueTree(
                tree_id=tree_id,
                character_id=character.character_id,
                entry_node_id=entry_id,
                nodes=nodes_dict,
                generation_trail=trail,
            )
            ctx.bible.dialogues[tree_id] = tree

            # Set the appropriate ID field on the character.
            if id_field is None:
                character.dialogue_tree_id = tree_id
            else:
                setattr(character, id_field, tree_id)
