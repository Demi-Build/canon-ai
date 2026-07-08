"""Macro phases: World → Stage → EnemyGenerator (PRD §5.1, slice-sized).

All LLM output is JSON validated through the existing retry-with-feedback
loop; all mechanics are skeleton-rolled from the pack's schema *files*
(loaded via canon.skeleton.loader — no Python-literal specs); all writes go
through ctx.adapter and their content hashes are folded into each entity's
provenance hash.
"""

from __future__ import annotations

import colorsys
import json
import logging
import re
from pathlib import Path
from typing import Any

from canon.bible.artifacts import compute_provenance_hash, make_artifact_id
from canon.bible.models import BibleMetadata
from canon.bible.platformer import EnemyDefinition, Stage, World
from canon.llm.parsing import extract_json_object
from canon.pipeline.retry import retry_with_feedback
from canon.pipeline.rng import derive_rng
from canon.skeleton.core import roll_skeleton
from canon.skeleton.loader import load_skeleton_spec

SCHEMAS_DIR = Path(__file__).parent / "schemas"
PROMPT_VERSION = "slice-1"

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _stamp_metadata(ctx: Any, name: str) -> None:
    if not isinstance(getattr(ctx.bible, "metadata", None), BibleMetadata):
        ctx.bible.metadata = BibleMetadata()
    ctx.bible.metadata.phases_run.append(name)


def warn(ctx: Any, message: str) -> None:
    """Record a generation warning where reviewers will actually see it:
    the log now, and ctx.artifacts["slice_warnings"] → manifest.json +
    end-of-run summary. Fallbacks must never be silent — a run that
    "succeeds" on fallback content is a failed generation wearing a suit."""
    logger.warning(message)
    ctx.artifacts.setdefault("slice_warnings", []).append(message)


def stamp_provenance(
    ctx: Any,
    entity: Any,
    content_hash: str,
    schema_version: str = "1",
    model_extra: str = "",
) -> None:
    """Fold the adapter's content hash + generation inputs into the entity's
    provenance hash (PRD §6.3). Stamped on the Bible entity only — the
    artifact file holds data, the Bible holds provenance.

    ``model_extra`` folds a second generator into the model input (an
    asset phase's image backend alongside the LLM) so an image-model bump
    invalidates like an LLM-model bump does."""
    model = str(getattr(getattr(ctx.llm, "backend", None), "model", "fake"))
    if model_extra:
        model = f"{model}+{model_extra}"
    entity.provenance_hash = compute_provenance_hash(
        content_hash,
        schema_version=schema_version,
        prompt_version=PROMPT_VERSION,
        model=model,
        seed=str(getattr(ctx.config, "seed", "")),
    )


def llm_json(
    ctx: Any,
    label: str,
    build_request,
    required_keys: tuple[str, ...],
    fallback: dict,
    validate_obj=None,
) -> dict:
    """Generate → parse → validate a JSON object with the standard retry loop.

    ``build_request(feedback)`` returns the LLMRequest for an attempt.
    ``validate_obj(obj) -> list[str]`` optionally adds semantic checks;
    returned problem strings become retry feedback.
    """

    def generate(feedback: list[str] | None = None, max_tokens: int | None = None) -> str:
        request = build_request(feedback)
        if max_tokens is not None:
            request.max_tokens = max_tokens
        return ctx.llm.generate(request, phase=label)

    def validate(content: str) -> tuple[bool, list[str]]:
        obj = extract_json_object(content)
        if obj is None:
            return False, ["Response must be a bare JSON object — no prose or fences."]
        missing = [k for k in required_keys if k not in obj]
        if missing:
            return False, [f"JSON object is missing required key(s): {missing!r}."]
        if validate_obj is not None:
            problems = validate_obj(obj)
            if problems:
                return False, problems
        return True, []

    result = retry_with_feedback(
        generate_fn=generate,
        validate_fn=validate,
        fallback=json.dumps(fallback),
        max_retries=getattr(ctx.config, "max_retries", 3),
        label=label,
    )
    return extract_json_object(result) or fallback


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "unnamed"


#: Fallback reserved hue bands: red (spike tiles) and blue (water tiles)
#: — used when no palette exists to derive the real reservations from.
DEFAULT_RESERVED_HUES: tuple[tuple[float, float], ...] = (
    (335.0, 25.0), (200.0, 250.0),
)


def _in_band(hue: float, band: tuple[float, float]) -> bool:
    lo, hi = band
    return (lo <= hue <= hi) if lo <= hi else (hue >= lo or hue <= hi)


def placeholder_color(
    index: int,
    reserved: tuple[tuple[float, float], ...] = DEFAULT_RESERVED_HUES,
) -> str:
    """Deterministic, well-spaced enemy colors: golden-angle hue steps
    starting at green. ``reserved`` hue bands (derived from the game's
    ACTUAL hazard/volume palette hues since the style agent — red/blue
    only as the palette-less fallback) get nudged out so enemies never
    read as hazards or volumes."""
    hue = (140.0 + index * 137.508) % 360.0
    for _ in range(12):  # bounded: bands can't cover the whole wheel
        if not any(_in_band(hue, band) for band in reserved):
            break
        hue = (hue + 47.0) % 360.0
    r, g, b = colorsys.hsv_to_rgb(hue / 360.0, 0.78, 0.95)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def reserved_hue_bands(
    palette: dict[str, str], tiles: Any, half_width: float = 28.0
) -> tuple[tuple[float, float], ...]:
    """The hue bands enemies must avoid, derived from the palette hues
    actually used by this game's hazard and volume tiles (±half_width°).
    Palette-driven: a lava game reserves orange, not the default blue."""
    bands: list[tuple[float, float]] = []
    for tile in tiles.named("hazard", "volume"):
        hex_color = palette.get(tile.color_role)
        if not hex_color:
            continue
        raw = hex_color.lstrip("#")
        r, g, b = (int(raw[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
        hue = colorsys.rgb_to_hsv(r, g, b)[0] * 360.0
        bands.append(((hue - half_width) % 360.0, (hue + half_width) % 360.0))
    return tuple(bands) or DEFAULT_RESERVED_HUES


# ---------------------------------------------------------------------------
# WorldPhase
# ---------------------------------------------------------------------------


class WorldPhase:
    name = "plat:world"

    def __init__(self, pitch: str = "a small hand-crafted-feeling 2D platformer") -> None:
        self.pitch = pitch

    def run(self, ctx: Any) -> None:
        seed = str(getattr(ctx.config, "seed", ""))
        data = llm_json(
            ctx,
            self.name,
            lambda fb: ctx.prompts.world_generation(self.pitch, seed),
            required_keys=("title", "stage_id", "stage_brief"),
            fallback={
                "title": "Untitled Hollows",
                "stage_id": "stage_1",
                "stage_brief": "A quiet cavern stage.",
            },
        )
        stage_id = slugify(str(data["stage_id"]))
        world = World(
            artifact_id=make_artifact_id("world"),
            title=str(data["title"]),
            stage_ids=[stage_id],
        )
        content_hash = ctx.adapter.write_json_singleton(
            "world.json", world.model_dump(mode="json")
        )
        stamp_provenance(ctx, world, content_hash)
        ctx.bible.world = world
        ctx.artifacts["stage_id"] = stage_id
        ctx.artifacts["stage_brief"] = str(data["stage_brief"])
        logger.info(
            "WorldPhase produced world %r (stage %r): %s",
            world.title, stage_id, ctx.artifacts["stage_brief"],
        )
        _stamp_metadata(ctx, self.name)


# ---------------------------------------------------------------------------
# StagePhase
# ---------------------------------------------------------------------------


class StagePhase:
    name = "plat:stage"

    def __init__(self, num_levels: int = 3, num_enemies: int = 3) -> None:
        self.num_levels = num_levels
        self.num_enemies = num_enemies

    def run(self, ctx: Any) -> None:
        stage_id = ctx.artifacts["stage_id"]
        data = llm_json(
            ctx,
            self.name,
            lambda fb: ctx.prompts.stage_generation(
                ctx.bible.world.title if ctx.bible.world else "",
                ctx.artifacts.get("stage_brief", ""),
                self.num_levels,
                self.num_enemies,
            ),
            required_keys=("theme", "level_briefs", "roster_brief"),
            fallback={
                "theme": "caverns",
                "level_briefs": ["A level."] * self.num_levels,
                "roster_brief": "Cave creatures.",
            },
        )
        briefs = [str(b) for b in data.get("level_briefs") or []]
        briefs = (briefs + ["A level."] * self.num_levels)[: self.num_levels]
        # View hints are optional and lenient: unknown/missing → standard
        # (the game-global framing). Deliberate exceptions only.
        views = [str(v) for v in data.get("level_views") or []]
        views = (views + ["standard"] * self.num_levels)[: self.num_levels]
        level_ids = [f"l{i + 1}" for i in range(self.num_levels)]

        from examples.platformer_pack.effects import sanitize_effects

        stage = Stage(
            artifact_id=make_artifact_id("stage", stage_id),
            stage_id=stage_id,
            theme=str(data["theme"]),
            level_ids=level_ids,
            tileset_ref=make_artifact_id("tileset", stage_id),
            effects=sanitize_effects(
                data.get("effects"), warn=lambda m: warn(ctx, m)
            ),
            parents=[make_artifact_id("world")],
        )
        content_hash = ctx.adapter.write_json_singleton(
            f"stage/{stage_id}/stage.json", stage.model_dump(mode="json")
        )
        stamp_provenance(ctx, stage, content_hash)
        ctx.bible.stages[stage_id] = stage
        ctx.artifacts["level_briefs"] = dict(zip(level_ids, briefs))
        ctx.artifacts["level_views"] = dict(zip(level_ids, views))
        ctx.artifacts["roster_brief"] = str(data["roster_brief"])
        logger.info(
            "StagePhase planned stage %r (theme %r): %d levels; roster: %s",
            stage_id, stage.theme, len(level_ids), ctx.artifacts["roster_brief"],
        )
        for level_id, brief in ctx.artifacts["level_briefs"].items():
            logger.info("  %s brief: %s", level_id, brief)
        _stamp_metadata(ctx, self.name)


# ---------------------------------------------------------------------------
# EnemyGeneratorPhase
# ---------------------------------------------------------------------------


class EnemyGeneratorPhase:
    """Skeleton rolls mechanics from schemas/enemy.json; the LLM names and
    flavors. Placeholder color is assigned here, on the definition — every
    review surface resolves it from the database. Hue reservations come
    from the game's palette (runs AFTER the style phase), so enemies
    never share a hue with this game's hazards or volumes."""

    name = "plat:enemies"

    def __init__(
        self,
        count: int = 3,
        schema_path: Path | None = None,
        tiles: Any = None,
    ) -> None:
        from examples.platformer_pack.tiles import DEFAULT_TILES

        self.count = count
        self.schema_path = schema_path or (SCHEMAS_DIR / "enemy.json")
        self.tiles = tiles or DEFAULT_TILES

    def run(self, ctx: Any) -> None:
        spec = load_skeleton_spec(self.schema_path)
        stage_id = ctx.artifacts["stage_id"]
        stage = ctx.bible.stages[stage_id]
        theme = stage.theme
        roster_brief = ctx.artifacts.get("roster_brief", "")
        seed = str(getattr(ctx.config, "seed", ""))
        reserved = reserved_hue_bands(
            ctx.artifacts.get("palette", {}), self.tiles
        )
        seen_ids: set[str] = set()
        used_names: list[str] = []

        for i in range(self.count):
            skeleton = roll_skeleton(spec, derive_rng(seed, self.name, i))
            data = llm_json(
                ctx,
                f"{self.name}:{i}",
                lambda fb, _skel=skeleton, _i=i: ctx.prompts.enemy_generation(
                    _skel, theme, roster_brief, _i,
                    used_names=list(used_names), feedback=fb,
                ),
                required_keys=("name",),
                fallback={"name": f"Enemy {i}", "flavor": ""},
                validate_obj=lambda obj: (
                    [
                        f"Name {obj.get('name')!r} is already taken; invent a "
                        "clearly different one."
                    ]
                    if str(obj.get("name", "")).strip().lower()
                    in {n.lower() for n in used_names}
                    else []
                ),
            )
            used_names.append(str(data["name"]))
            enemy_id = slugify(str(data["name"]))
            # Numeric backstop — only reachable if the LLM ignored both the
            # used-names prompt and the retry feedback (or the fallback fired).
            base, counter = enemy_id, 2
            while enemy_id in seen_ids:
                enemy_id = f"{base}_{counter}"
                counter += 1
            seen_ids.add(enemy_id)

            enemy = EnemyDefinition(
                artifact_id=make_artifact_id("enemy", enemy_id),
                enemy_id=enemy_id,
                name=str(data["name"]),
                archetype=str(skeleton["archetype"]),
                stats={
                    "hp": skeleton["hp"],
                    "damage": skeleton["damage"],
                    "speed": skeleton["speed"],
                    "flavor": str(data.get("flavor", "")),
                    "placeholder_color": placeholder_color(i, reserved),
                },
                behavior={
                    "patrol_range": skeleton["patrol_range"],
                    "aggro_range": skeleton["aggro_range"],
                },
                parents=[stage.artifact_id],
            )
            content_hash = ctx.adapter.write_json_singleton(
                f"enemy/{enemy_id}.json", enemy.model_dump(mode="json")
            )
            stamp_provenance(ctx, enemy, content_hash)
            ctx.bible.enemy_definitions[enemy_id] = enemy
            stage.enemy_refs.append(enemy.artifact_id)
            logger.info(
                "Enemy %d/%d: %r (%s) — hp=%s dmg=%s spd=%s %s color=%s: %s",
                i + 1, self.count, enemy.name, enemy.archetype,
                enemy.stats["hp"], enemy.stats["damage"], enemy.stats["speed"],
                " ".join(f"{k}={v}" for k, v in enemy.behavior.items()),
                enemy.stats["placeholder_color"], enemy.stats["flavor"],
            )

        logger.info(
            "EnemyGeneratorPhase produced %d definitions: %s",
            len(seen_ids), ", ".join(sorted(seen_ids)),
        )
        _stamp_metadata(ctx, self.name)
