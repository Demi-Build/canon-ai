"""NarrativePhase — prose wrap-up after all entity-generating phases have run.

Produces:
  - A polished synopsis (overwrites the structural-stub synopsis from StoryPhase)
  - Per-map introductory text (overwrites the structural map descriptions)
  - Victory and defeat strings
  - data/narrative.json with mazeworld shape (v0.2)

Victory/defeat have no first-class field on Bible or BibleMetadata in v0.1.
They are stored in ``ctx.artifacts["narrative"]`` so existing schemas stay
unchanged.

Feedback handling: PromptSet defines no ``narrative_*_with_feedback`` variants
in v0.1.  On retry the phase uses ``_with_feedback`` to append failure reasons
to the base request's user_message so the same underlying prompt is reused
without needing separate methods.  Formalising dedicated feedback variants is
deferred to v0.2.

JSON extraction: DefaultPromptSet narrative methods ask the LLM for JSON with a
single prose field.  ``_extract_prose`` parses defensively — if the response is
valid JSON with the expected key, the inner string is returned; otherwise the
raw string is returned as-is.  This lets custom PromptSet subclasses return raw
prose without breaking the phase.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from canon.bible.models import BibleMetadata
from canon.llm.request import LLMRequest
from canon.pipeline.retry import retry_with_feedback
from canon.pipeline.steplog import step

logger = logging.getLogger(__name__)

# Map each narrative call to the JSON field its DefaultPromptSet method uses.
_NARRATIVE_JSON_KEYS: dict[str, str] = {
    "narrative:synopsis": "synopsis",
    "narrative:victory": "victory_text",
    "narrative:defeat": "defeat_text",
    # map intros use key "intro_text"; label is set dynamically below.
}
_MAP_INTRO_JSON_KEY = "intro_text"


def _extract_prose(text: str, json_key: str) -> str:
    """Return prose from a (possibly JSON-wrapped) LLM response.

    If *text* is valid JSON containing *json_key*, return that field's value.
    Otherwise return *text* unchanged — the PromptSet may return raw prose.
    """
    try:
        data = json.loads(text)
        if isinstance(data, dict) and json_key in data:
            return str(data[json_key]).strip()
    except (json.JSONDecodeError, ValueError):
        pass
    return text.strip()


class NarrativePhase:
    """Generates synopsis, map intros, and wrap-up text once all entities exist.

    Skip for games that don't need prose narration.

    Outputs:
        - Updates ``ctx.bible.story.synopsis`` with the LLM-generated synopsis.
        - Updates each ``Map.description`` with a per-map intro.
        - Stores victory/defeat strings in ``ctx.artifacts['narrative']`` dict.
          (v0.1 placement; preserved for backward compat.)
        - Writes ``data/narrative.json`` with mazeworld shape (v0.2):
          keys: synopsis, game_over, victory, room_intro_<map_id>
    """

    name: str = "narrative"

    def run(self, ctx: Any) -> None:  # noqa: PLR0912 (branchy but intentionally explicit)
        max_retries = getattr(ctx.config, "max_retries", 3)
        artifacts: dict[str, Any] = {}

        # ------------------------------------------------------------------
        # 1. Synopsis
        # ------------------------------------------------------------------
        synopsis_raw = self._generate_text(
            ctx,
            request_factory=lambda fb: (
                ctx.prompts.narrative_synopsis(ctx.bible)
                if not fb
                else self._with_feedback(ctx.prompts.narrative_synopsis(ctx.bible), fb)
            ),
            label="narrative:synopsis",
            fallback=ctx.bible.story.synopsis,
            max_retries=max_retries,
        )
        synopsis = _extract_prose(synopsis_raw, "synopsis")
        if synopsis:
            ctx.bible.story.synopsis = synopsis
        artifacts["synopsis"] = synopsis

        # ------------------------------------------------------------------
        # 2. Per-map intros
        # ------------------------------------------------------------------
        map_intros: dict[str, str] = {}
        # Row P0-10 (§3.0-E/D): one item per room intro, through the one emitter.
        for map_index, (map_id, map_obj) in enumerate(ctx.bible.maps.items(), start=1):
            step(ctx, self.name, f"intro · {map_id}", map_index, len(ctx.bible.maps))
            intro_raw = self._generate_text(
                ctx,
                request_factory=lambda fb, mid=map_id: (
                    ctx.prompts.map_intro(ctx.bible, mid)
                    if not fb
                    else self._with_feedback(ctx.prompts.map_intro(ctx.bible, mid), fb)
                ),
                label=f"narrative:intro:{map_id}",
                fallback=map_obj.description or "",
                max_retries=max_retries,
            )
            intro = _extract_prose(intro_raw, _MAP_INTRO_JSON_KEY)
            if intro:
                map_obj.description = intro
            map_intros[map_id] = intro
        artifacts["map_intros"] = map_intros

        # ------------------------------------------------------------------
        # 3. Victory text
        # ------------------------------------------------------------------
        victory_raw = self._generate_text(
            ctx,
            request_factory=lambda fb: (
                ctx.prompts.victory_text(ctx.bible)
                if not fb
                else self._with_feedback(ctx.prompts.victory_text(ctx.bible), fb)
            ),
            label="narrative:victory",
            fallback="You have triumphed.",
            max_retries=max_retries,
        )
        artifacts["victory_text"] = _extract_prose(victory_raw, "victory_text")

        # ------------------------------------------------------------------
        # 4. Defeat text
        # ------------------------------------------------------------------
        defeat_raw = self._generate_text(
            ctx,
            request_factory=lambda fb: (
                ctx.prompts.defeat_text(ctx.bible)
                if not fb
                else self._with_feedback(ctx.prompts.defeat_text(ctx.bible), fb)
            ),
            label="narrative:defeat",
            fallback="You have fallen.",
            max_retries=max_retries,
        )
        artifacts["defeat_text"] = _extract_prose(defeat_raw, "defeat_text")

        # ------------------------------------------------------------------
        # Commit artifacts (backward-compat: keep ctx.artifacts["narrative"])
        # ------------------------------------------------------------------
        ctx.artifacts["narrative"] = artifacts

        # ------------------------------------------------------------------
        # v0.2: Write narrative.json with mazeworld shape
        # ------------------------------------------------------------------
        narrative_path = getattr(
            ctx.config, "output_paths", {}
        ).get("narrative", "narrative.json")

        # Build mazeworld shape: synopsis, game_over, victory, room_intro_<map_id>
        narrative_json: dict[str, str] = {
            "synopsis": artifacts.get("synopsis", "") or "",
            "game_over": artifacts.get("defeat_text", "") or "",
            "victory": artifacts.get("victory_text", "") or "",
        }
        for map_id, intro in artifacts.get("map_intros", {}).items():
            narrative_json[f"room_intro_{map_id}"] = intro or ""

        ctx.adapter.write_json_singleton(narrative_path, narrative_json)

        # ------------------------------------------------------------------
        # Metadata stamp
        # ------------------------------------------------------------------
        if not isinstance(ctx.bible.metadata, BibleMetadata):
            ctx.bible.metadata = BibleMetadata()
        ctx.bible.metadata.phases_run.append(self.name)
        logger.info(
            "NarrativePhase produced synopsis + %d map intros + victory + defeat.",
            len(map_intros),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _generate_text(
        self,
        ctx: Any,
        request_factory: Any,
        label: str,
        fallback: str,
        max_retries: int,
    ) -> str:
        """Run retry_with_feedback for a single prose generation call."""

        def generate(feedback: list[str] | None = None) -> str:
            request = request_factory(feedback)
            return ctx.llm.generate(request, phase=self.name)

        def validate(content: str) -> tuple[bool, list[str]]:
            if not isinstance(content, str) or not content.strip():
                return False, ["Empty response."]
            return True, []

        return retry_with_feedback(
            generate_fn=generate,
            validate_fn=validate,
            fallback=fallback,
            max_retries=max_retries,
            label=label,
        )

    @staticmethod
    def _with_feedback(request: LLMRequest, feedback: list[str]) -> LLMRequest:
        """Append failure reasons to a base request's user_message.

        Used on retries because PromptSet has no dedicated
        ``narrative_*_with_feedback`` variants in v0.1.
        """
        return LLMRequest(
            system=request.system,
            user_message=(
                request.user_message
                + "\n\nA prior attempt failed: "
                + "; ".join(feedback)
                + "\nFix these issues."
            ),
            examples=request.examples,
            max_tokens=request.max_tokens,
        )
