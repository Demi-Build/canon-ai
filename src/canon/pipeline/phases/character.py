"""CharacterPhase — generates named Characters and adds them to Bible.characters.

Skip for games without named NPCs (most tower defenses, some roguelikes).
Each character is generated via LLM with the character_generation prompt;
the role determines the prompt framing but is otherwise opaque to canon —
users define what roles mean.

v0.2 additions:
- Populates new Character fields: job, hobby, opening_greeting, portrait_prompt,
  personality_notes, exhausted_dialogue.
- Cross-room name deduplication: tracks existing_character_names across maps.
  When the LLM returns a duplicate name, a suffix is appended (soft collision).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from typing import Any

from canon.bible.models import BibleMetadata, Character, GenerationTrail
from canon.pipeline.retry import retry_with_feedback
from canon.skeleton.core import roll_skeleton

logger = logging.getLogger(__name__)


class CharacterPhase:
    """Generates named Characters.

    Skip for games without named NPCs. Characters' role affects the prompt
    framing but is otherwise opaque to canon — users define what roles mean.

    Args:
        per_map: If True, generate ``count`` characters per map. If False,
                 generate ``count`` total characters (global cast).
        count: Number of characters per map (or globally if per_map=False).
               Can be a callable ``lambda map_obj: int`` for per-map variation.
               When per_map=False and count is callable, it is invoked with
               ``None`` as the argument.
        roles: Sequence of role strings to cycle through. Each successive
               character generation uses ``roles[i % len(roles)]``.
               Users supply their own role strings — canon does not validate
               them against CharacterRole; validation is left to the user's
               checkers.
    """

    name: str = "characters"

    def __init__(
        self,
        per_map: bool = True,
        count: int | Callable[[Any], int] = 2,
        roles: Sequence[str] = ("npc",),
    ) -> None:
        self.per_map = per_map
        self.count = count
        self.roles = tuple(roles)

    def run(self, ctx: Any) -> None:
        max_retries = getattr(ctx.config, "max_retries", 3)
        # Cross-room name dedup: track generated names across all maps.
        existing_names: set[str] = set()

        if self.per_map:
            for map_id, map_obj in ctx.bible.maps.items():
                cnt = self._resolve_count(map_obj)
                for i in range(cnt):
                    role = self.roles[i % len(self.roles)]
                    self._generate_one(
                        ctx,
                        role=role,
                        map_id=map_id,
                        index=i,
                        max_retries=max_retries,
                        existing_names=existing_names,
                    )
        else:
            total = self._resolve_count(None)
            for i in range(total):
                role = self.roles[i % len(self.roles)]
                self._generate_one(
                    ctx,
                    role=role,
                    map_id=None,
                    index=i,
                    max_retries=max_retries,
                    existing_names=existing_names,
                )

        if not isinstance(ctx.bible.metadata, BibleMetadata):
            ctx.bible.metadata = BibleMetadata()
        ctx.bible.metadata.phases_run.append(self.name)
        logger.info(
            "CharacterPhase generated %d characters.", len(ctx.bible.characters)
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_count(self, map_obj: Any) -> int:
        """Return the concrete count for the given map object (or None for global)."""
        if callable(self.count):
            return int(self.count(map_obj))
        return int(self.count)

    def _generate_one(
        self,
        ctx: Any,
        role: str,
        map_id: str | None,
        index: int,
        max_retries: int,
        existing_names: set[str] | None = None,
    ) -> None:
        # Build context: cumulative per-map context or story-only if global.
        if map_id is not None:
            context_text = ctx.bible.get_cumulative_context(map_id)
        else:
            # story + factions context without a specific map
            context_text = ctx.bible.get_context("")

        # Roll a character skeleton if user registered one (key: "character").
        spec = ctx.schemas.get("character")
        skeleton = roll_skeleton(spec, ctx.rng) if spec is not None else {}

        # Capture the last prompt/response across retry attempts.
        captured: dict[str, str] = {"prompt": "", "response": ""}
        # Build existing_names list for LLM feedback (cross-room dedup hint).
        existing_names_list = sorted(existing_names) if existing_names else []

        def generate(feedback: list[str] | None = None) -> str:
            # Merge any explicit feedback with the dedup hint so the LLM
            # knows which names are already taken.
            combined_feedback: list[str] | None = feedback
            if existing_names_list:
                dedup_hint = (
                    f"Avoid these existing character names: {', '.join(existing_names_list)}."
                )
                combined_feedback = [dedup_hint] + (feedback or [])
            if combined_feedback:
                request = ctx.prompts.character_generation_with_feedback(
                    context=context_text,
                    skeleton=skeleton,
                    role=role,
                    feedback=combined_feedback,
                )
            else:
                request = ctx.prompts.character_generation(
                    context=context_text,
                    skeleton=skeleton,
                    role=role,
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
                return False, ["Response must be a JSON object."]
            issues: list[str] = []
            if not data.get("name"):
                issues.append("Missing 'name'.")
            if not data.get("lore") and not data.get("backstory"):
                issues.append("Missing 'lore'.")
            return len(issues) == 0, issues

        fallback = json.dumps(
            {"name": f"Character {index}", "lore": "An unwritten figure."}
        )
        result = retry_with_feedback(
            generate_fn=generate,
            validate_fn=validate,
            fallback=fallback,
            max_retries=max_retries,
            label=f"{self.name}:c{index}",
        )

        try:
            data = json.loads(result)
        except json.JSONDecodeError:
            # Fallback itself was invalid JSON — give up on this character.
            logger.warning(
                "CharacterPhase: could not parse result for character %d; skipping.",
                index,
            )
            return

        # Auto-allocate a character_id when the LLM doesn't supply one.
        # The zero-padded index is derived from the current length of
        # ctx.bible.characters so IDs are globally unique across both
        # per-map and global generation modes — two maps each generating
        # 2 characters will yield char_0000, char_0001, char_0002, char_0003.
        char_id = data.get("character_id") or f"char_{len(ctx.bible.characters):04d}"

        trail = GenerationTrail(
            prompt=captured["prompt"],
            response=captured["response"],
            validation_history=[],
            # TODO(v0.2): wire retry count back from retry_with_feedback
            retry_count=0,
            cost=getattr(ctx.llm.backend, "last_cost", None),
            model=getattr(ctx.llm.backend, "model", None),
        )

        # Resolve name and handle cross-room deduplication.
        name = data.get("name", f"Character {index}")
        if existing_names is not None and name in existing_names:
            # Soft-collision strategy: append a distinguishing suffix.
            name = f"{name} (the elder)"
            logger.debug(
                "CharacterPhase: name collision on %r; renamed to %r",
                data.get("name"),
                name,
            )

        # Coerce personality_notes to a list of strings.
        raw_notes = data.get("personality_notes") or []
        if isinstance(raw_notes, str):
            raw_notes = [raw_notes]
        personality_notes = [str(n) for n in raw_notes if n]

        character = Character(
            character_id=char_id,
            name=name,
            role=role,
            primary_map_id=map_id,
            lore=data.get("lore") or data.get("backstory") or "",
            personality=data.get("personality"),
            appearance=data.get("appearance"),
            faction_id=data.get("faction_id"),
            generation_trail=trail,
            # v0.2 new fields
            job=data.get("job"),
            hobby=data.get("hobby"),
            opening_greeting=data.get("opening_greeting"),
            portrait_prompt=data.get("portrait_prompt"),
            personality_notes=personality_notes,
            exhausted_dialogue=data.get("exhausted_dialogue"),
        )
        if existing_names is not None:
            existing_names.add(character.name)
        ctx.bible.add_character(character)
