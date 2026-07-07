"""StoryPhase — generates Bible.story from the seed.

First phase in almost every pipeline, but technically optional — a user
could pre-build a StoryArc and skip this phase entirely.

v0.2: after generating the StoryArc, writes two files:
  - data/story/story.json  — full StoryArc dump
  - data/world_bible.json  — {"story": ..., "rooms": {...}} initial skeleton
    (ManifestPhase will overwrite/finalize at pipeline end)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from canon.bible.models import (
    BibleMetadata,
    Faction,
    GenerationTrail,
    StoryArc,
    StoryBeat,
)
from canon.llm.parsing import extract_json_object
from canon.pipeline.retry import default_token_escalation, retry_with_feedback

logger = logging.getLogger(__name__)


class StoryPhase:
    """Generates Bible.story from the seed.

    First phase in almost every pipeline, but technically optional — a
    user could pre-build a StoryArc and skip this phase entirely.

    On run:
    - Calls ctx.prompts.story_generation(seed=ctx.bible.seed, structure=story_structure)
    - Validates the LLM response parses as JSON with required fields
    - Retries up to ctx.config.max_retries with failure reasons fed back
    - On exhaustion: writes a minimal fallback StoryArc and logs a warning
    - Stamps GenerationTrail on the result
    - Updates Bible.metadata.phases_run
    """

    name: str = "story"

    def __init__(self, story_structure: dict | None = None) -> None:
        self.story_structure = story_structure

    def run(self, ctx: Any) -> None:
        max_retries = getattr(ctx.config, "max_retries", 3)
        seed = ctx.bible.seed
        # Bind story beats to the maps that actually exist (pre-created before
        # this phase), so the LLM emits exactly one beat per real room instead
        # of inventing an arbitrary arc length.
        map_ids = list(ctx.bible.maps.keys()) if getattr(ctx.bible, "maps", None) else None

        retry_count = [0]

        def generate(
            feedback: list[str] | None = None,
            max_tokens: int | None = None,
        ) -> str:
            retry_count[0] += 1
            if feedback:
                request = ctx.prompts.story_generation_with_feedback(
                    seed=seed, structure=self.story_structure, feedback=feedback,
                    map_ids=map_ids,
                )
            else:
                request = ctx.prompts.story_generation(
                    seed=seed, structure=self.story_structure, map_ids=map_ids,
                )
            if max_tokens is not None:
                request.max_tokens = max_tokens
            generate.last_prompt = request.user_message
            response = ctx.llm.generate(request, phase=self.name)
            generate.last_response = response
            return response

        generate.last_prompt = None
        generate.last_response = None

        def validate(content: str) -> tuple[bool, list[str]]:
            issues: list[str] = []
            data = extract_json_object(content)
            if data is None:
                return False, [
                    "Response did not contain a JSON object. Return ONLY a JSON "
                    "object, with no prose or markdown code fences."
                ]
            if not data.get("title"):
                issues.append("Missing required field 'title'.")
            if not data.get("synopsis"):
                issues.append("Missing required field 'synopsis'.")
            return len(issues) == 0, issues

        fallback_text = json.dumps({
            "title": f"Untitled World ({seed})",
            "synopsis": "An unwritten world.",
            "factions": [],
            "escalation_arc": [],
            "beats": [],
        })

        result_json = retry_with_feedback(
            generate_fn=generate,
            validate_fn=validate,
            fallback=fallback_text,
            max_retries=max_retries,
            label=self.name,
            token_escalation=default_token_escalation,
            initial_max_tokens=2000,
        )

        story = self._parse_story(result_json, seed)

        trail = GenerationTrail(
            prompt=generate.last_prompt or "",
            response=generate.last_response or "",
            # TODO(v0.2): record per-attempt validation history into GenerationTrail.validation_history
            validation_history=[],
            retry_count=max(retry_count[0] - 1, 0),
            cost=getattr(ctx.llm.backend, "last_cost", None),
            model=getattr(ctx.llm.backend, "model", None),
        )
        # TODO(v0.2): attach trail to StoryArc directly when StoryArc gains a generation_trail field
        # For now, trail is constructed but not attached to StoryArc (no field exists on the model).
        # It is available here for callers who inspect the phase object after run().
        self._last_trail = trail

        if not isinstance(ctx.bible.metadata, BibleMetadata):
            ctx.bible.metadata = BibleMetadata()
        ctx.bible.metadata.phases_run.append(self.name)

        ctx.bible.story = story
        logger.info("StoryPhase produced story: %s", story.title)

        # --- v0.2 persistence ---
        self._persist(ctx, story)

    def _persist(self, ctx: Any, story: StoryArc) -> None:
        """Write story.json and the initial world_bible.json skeleton."""
        output_paths = getattr(ctx.config, "output_paths", {})

        # 1. data/story/story.json — full StoryArc dump
        story_path = output_paths.get("story", "story/story.json")
        ctx.adapter.write_json_singleton(story_path, story.model_dump(mode="json"))

        # 2. data/world_bible.json — initial skeleton with empty room buckets.
        #    ManifestPhase overwrites this at pipeline end with the full version.
        rooms: dict = {}
        for map_id, m in ctx.bible.maps.items():
            rooms[map_id] = {
                "environment": m.environment,
                "environment_name": m.name,
                "level": m.level,
                "story_beat": m.story_beat,
                "boss_name": getattr(m, "boss_name", "") or "",
                "boss_lore": getattr(m, "boss_lore", "") or "",
                "maze_ref": "",
                "npcs": [],
                "items": [],
                "monsters": [],
                "events": [],
                "quests": [],
                "player_classes": [],
            }
        world_bible = {
            "story": story.model_dump(mode="json"),
            "rooms": rooms,
        }
        wb_path = output_paths.get("world_bible", "world_bible.json")
        ctx.adapter.write_json_singleton(wb_path, world_bible)

    @staticmethod
    def _parse_story(content: str, seed: str) -> StoryArc:
        """Convert LLM JSON to a StoryArc. Be liberal in accepting fields."""
        data = extract_json_object(content) or {}

        factions = []
        for f in data.get("factions") or []:
            try:
                factions.append(Faction(
                    faction_id=(
                        f.get("faction_id")
                        or f.get("id")
                        or f.get("name", "faction_0").lower().replace(" ", "_")
                    ),
                    name=f.get("name", ""),
                    description=f.get("description", ""),
                    history=f.get("history", ""),
                    leader=f.get("leader", ""),
                    threat_level=f.get("threat_level"),
                    aesthetic=f.get("aesthetic"),
                ))
            except Exception:
                continue

        beats = []
        for b in data.get("beats") or []:
            try:
                beats.append(StoryBeat(
                    map_id=b.get("map_id", ""),
                    beat=b.get("beat", ""),
                    boss_name=b.get("boss_name"),
                    boss_lore=b.get("boss_lore"),
                ))
            except Exception:
                continue

        return StoryArc(
            title=data.get("title", "Untitled World"),
            synopsis=data.get("synopsis", ""),
            seed=seed,
            factions=factions,
            primary_antagonist_faction_id=data.get("primary_antagonist_faction_id"),
            escalation_arc=data.get("escalation_arc") or [],
            climax=data.get("climax"),
            final_entity_id=data.get("final_entity_id"),
            final_entity_lore=data.get("final_entity_lore"),
            beats=beats,
            key_character_names=data.get("key_character_names") or [],
        )
