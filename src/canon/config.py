"""Canon configuration model.

User-facing entry point for "what does a canon pipeline need from me to run?"
v0.1 ships a flat Pydantic model with JSON/TOML load support.

v0.2 adds:
- ``output_paths`` — per-file-type path overrides, defaulting to mazeworld's tree
- ``llm_backend`` / ``image_backend`` / ``music_backend`` / ``sfx_backend`` — backend keys

# Requirement-schema TODO — progress:
#   DONE (minimal): generation scope is config-driven and game-agnostic.
#     ``counts`` (generic entity_type->count dict) and ``model`` live here;
#     packs read ``counts`` with their own defaults; the runner exposes CLI
#     flags + ``--config`` with precedence CLI > file > pack default. Core holds
#     no game-specific count fields.
#   STILL PENDING (full): ``canon init <pack>`` scaffold that writes a documented
#     starter config per game type; ``canon describe <pack>`` / JSON-Schema export
#     for introspection; and moving the remaining RPG-baked defaults out of core
#     into packs (``_default_output_paths`` dungeon tree, ClassPhase/SpellPoolPhase,
#     and the Bible's RPG-shaped fields) so shooters/platformers/fighters fit.
#     NEXT MULTI-GAME MILESTONE: let the spec itself declare the *pack/game type*,
#     *story premise*, and *output layout* — so one config fully defines "what game
#     to generate" (the runner becomes pack-agnostic instead of importing
#     mazeworld_pack directly). Best done when game #2 (a platformer) forces the
#     shape. See memory: feedback_canon_game_agnostic.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


def _default_output_paths() -> dict[str, str]:
    """Return the default output-paths dict matching mazeworld's data/ layout."""
    return {
        "story": "story/story.json",
        "world_bible": "world_bible.json",
        "narrative": "narrative.json",
        "manifest": "manifest.json",
        "generation_stats": "generation_stats.json",
        "classes": "classes/classes.json",
        "spell_pools": "classes/spell_pools.json",
        "npcs": "npcs/npcs.json",
        "items": "items/items.json",
        "monsters": "monsters/monsters.json",
        "events": "events/events.json",
        "quests": "quests/quests.json",
        "maze": "rooms/{map_id}/maze.json",
        "portraits_dir": "portraits",
        "music_dir": "music",
        "sfx_dir": "sfx",
    }


class CanonConfig(BaseModel):
    """Configuration for a canon pipeline run.

    Fields:
        seed: Required. Deterministic seed string used to drive RNG and bible identity.
        num_maps: Number of maps (primary spatial units) to generate.
        max_retries: Maximum retry attempts for retry_with_feedback wrappers.
        max_llm_workers: Concurrency cap for LLM calls in parallelizable phases.
        context_limit_chars: Character cap for assembled story/world context blobs.
        output_dir: Where bible JSON and any side artifacts should be written.
        output_paths: Per-file-type path overrides relative to output_dir.
            Defaults match mazeworld's data/ tree layout. Override individual
            keys (e.g. ``output_paths["narrative"] = "custom/narr.json"``) for
            games with different layout expectations.
        backend_llm: Registry key naming the LLM backend. (v0.1 compat alias)
        llm_backend: Registry key naming the LLM backend. (v0.2 canonical name)
        image_backend: Registry key for image generation backend (optional).
        music_backend: Registry key for music generation backend (optional).
        sfx_backend: Registry key for SFX generation backend (optional).
    """

    seed: str
    num_maps: int = 3
    max_retries: int = 3
    max_llm_workers: int = 8
    context_limit_chars: int = 20000
    # Orchestrator (PRD §7): DAG node concurrency cap. Default 1 =
    # sequential; raising it is opt-in (LLM rate limits are the user's
    # risk — this is the only throttle in v1).
    max_concurrency: int = 1
    # Human gates (invariant I4): auto-approve for unattended runs; set
    # False to make gate nodes pause the run cleanly for review.
    gates_auto_approve: bool = True
    output_dir: Path = Path("./canon_output")
    output_paths: dict[str, str] = Field(default_factory=_default_output_paths)
    # v0.1 compat alias kept; llm_backend is the v0.2 canonical name
    backend_llm: str = "anthropic"
    llm_backend: str = "anthropic"
    image_backend: str | None = None
    music_backend: str | None = None
    sfx_backend: str | None = None

    # Generic per-entity-type generation counts, keyed by the entity-type
    # strings a *pack* declares (e.g. mazeworld: "npc"/"item"/"monster"/
    # "event"/"quest"/"class"; a shooter pack: "weapon"/"enemy"/...). Core
    # canon never interprets these keys — packs map them onto their specs and
    # supply their own defaults. Empty = "use the pack's defaults". This keeps
    # core game-agnostic: no game-specific count fields baked into the schema.
    counts: dict[str, int] = Field(default_factory=dict)
    # Optional LLM model id override (e.g. "claude-haiku-4-5-20251001").
    # None = let the backend pick its default.
    model: str | None = None

    # --- Loaders ---

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CanonConfig:
        """Construct a CanonConfig from a plain dict."""
        return cls.model_validate(data)

    @classmethod
    def from_json(cls, path: str | Path) -> CanonConfig:
        """Load a CanonConfig from a JSON file."""
        path = Path(path)
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)

    @classmethod
    def from_toml(cls, path: str | Path) -> CanonConfig:
        """Load a CanonConfig from a TOML file.

        Accepts either a flat top-level table::

            seed = "abc"
            num_maps = 4

        or an explicit ``[canon]`` section::

            [canon]
            seed = "abc"
            num_maps = 4

        If a ``[canon]`` table is present, it takes precedence over top-level
        keys. This lets users embed canon config inside a larger project
        TOML file (e.g., ``pyproject.toml``-style) without collision.
        """
        path = Path(path)
        with path.open("rb") as f:
            raw = tomllib.load(f)
        data = raw.get("canon", raw)
        return cls.from_dict(data)

    # --- Writers ---

    def to_json(self, path: str | Path | None = None) -> str:
        """Serialize to JSON. Writes to ``path`` if provided; always returns the JSON string."""
        text = self.model_dump_json(indent=2)
        if path is not None:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return text
