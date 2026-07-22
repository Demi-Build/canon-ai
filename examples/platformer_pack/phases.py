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
from examples.platformer_pack import color as color_math

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


def resolved_model(ctx: Any, label: str) -> str:
    """The model id that ACTUALLY serves calls under this phase label:
    the per-agent table's answer when the backend honors per-request
    models (AnthropicBackend), else the backend's constructed model
    ("fake" on $0 runs — a table never changes what fake stamps)."""
    llm = getattr(ctx, "llm", None)
    backend = getattr(llm, "backend", None)
    resolver = getattr(llm, "model_resolver", None)
    if resolver is not None and getattr(
        backend, "supports_request_model", False
    ):
        model = resolver(label)
        if model:
            return str(model)
    return str(getattr(backend, "model", "fake"))


def stamp_provenance(
    ctx: Any,
    entity: Any,
    content_hash: str,
    schema_version: str = "1",
    model_extra: str = "",
    label: str | None = None,
) -> None:
    """Fold the adapter's content hash + generation inputs into the entity's
    provenance hash (PRD §6.3). Stamped on the Bible entity only — the
    artifact file holds data, the Bible holds provenance.

    ``label`` names the phase-label whose (table-resolved) model authored
    this entity, so a per-task model change stamps truthfully; unlabeled
    stamps keep the backend's global model. ``model_extra`` folds further
    generators into the model input (an asset phase's image backend, a
    level's other task models) so any generator bump invalidates alike."""
    if label is not None:
        model = resolved_model(ctx, label)
    else:
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


#: Minimum |luminance| between an actor's placeholder color and every
#: stage background — the composite-readability floor (first paid run:
#: a dark-navy beetle vanished against a near-black backdrop). Mirrors
#: style.MIN_CONTRAST (not imported — cycle).
ACTOR_BG_MIN_LUMA = 40.0


def placeholder_color(
    index: int,
    reserved: tuple[tuple[float, float], ...] = DEFAULT_RESERVED_HUES,
    background_lums: tuple[float, ...] = (),
) -> str:
    """Deterministic, well-spaced enemy colors: golden-angle hue steps
    starting at green. ``reserved`` hue bands (derived from the game's
    ACTUAL hazard/volume palette hues since the style agent — red/blue
    only as the palette-less fallback) get nudged out so enemies never
    read as hazards or volumes. ``background_lums`` (each stage
    background's luminance) enforces ``ACTOR_BG_MIN_LUMA`` via a
    hue-preserving lightness shift — an HSV value walk can't reach the
    floor for every hue, the closed-form lerp always can."""
    hue = (140.0 + index * 137.508) % 360.0
    for _ in range(12):  # bounded: bands can't cover the whole wheel
        if not any(_in_band(hue, band) for band in reserved):
            break
        hue = (hue + 47.0) % 360.0
    r, g, b = colorsys.hsv_to_rgb(hue / 360.0, 0.78, 0.95)
    hex_color = f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"
    if not background_lums:
        return hex_color
    lum = color_math.luminance(hex_color)
    if min(abs(lum - bg) for bg in background_lums) >= ACTOR_BG_MIN_LUMA:
        return hex_color
    up = max(background_lums) + ACTOR_BG_MIN_LUMA + 2
    down = min(background_lums) - ACTOR_BG_MIN_LUMA - 2
    if up <= 255.0:
        target = up
    elif down >= 0.0:
        target = down
    else:  # backgrounds span the range: take the side with more room
        target = up if (255.0 - max(background_lums)) >= min(
            background_lums
        ) else down
        target = min(255.0, max(0.0, target))
    return color_math.shift_luminance(hex_color, target)


def _ctx_palettes(ctx: Any) -> dict[str, dict[str, str]]:
    """Every stage palette in ctx (tests/legacy: the single palette)."""
    palettes = ctx.artifacts.get("palettes", {})
    if not palettes:
        single = ctx.artifacts.get("palette")
        palettes = {"": single} if single else {}
    return palettes


def background_luminances(
    palettes: dict[str, dict[str, str]], tiles: Any
) -> tuple[float, ...]:
    """Each stage background's luminance, sorted + deduped (deterministic)
    — the values actor placeholders must clear by ``ACTOR_BG_MIN_LUMA``.
    Degrades to () when no palette carries the background role."""
    empty = next((t for t in tiles.tiles if t.category == "empty"), None)
    if empty is None or not empty.color_role:
        return ()
    lums = {
        round(color_math.luminance(palette[empty.color_role]), 4)
        for palette in palettes.values()
        if empty.color_role in palette
    }
    return tuple(sorted(lums))


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
    """World plan: title + an ORDERED list of biome stages (easy → hard).
    ``World.edges`` records the linear play chain and ``unlock_rules``
    the policy — data the world map + branching graphs (later) read."""

    name = "plat:world"

    def __init__(
        self,
        pitch: str = "a small hand-crafted-feeling 2D platformer",
        num_stages: int = 1,
    ) -> None:
        self.pitch = pitch
        self.num_stages = num_stages

    def _validate(self, obj: dict) -> list[str]:
        stages = obj.get("stages")
        if not isinstance(stages, list) or len(stages) != self.num_stages:
            return [
                f'"stages" must be a list of exactly {self.num_stages} '
                "objects (in play order)."
            ]
        problems = []
        seen: set[str] = set()
        for i, entry in enumerate(stages):
            if not isinstance(entry, dict) or not all(
                isinstance(entry.get(k), str) and entry.get(k)
                for k in ("stage_id", "biome", "brief")
            ):
                problems.append(
                    f"stages[{i}] must be an object with non-empty string "
                    '"stage_id", "biome", and "brief".'
                )
                continue
            slug = slugify(str(entry["stage_id"]))
            if slug in seen:
                problems.append(
                    f"stages[{i}] stage_id {entry['stage_id']!r} repeats an "
                    "earlier stage — every stage needs a distinct id."
                )
            seen.add(slug)
        return problems

    def run(self, ctx: Any) -> None:
        seed = str(getattr(ctx.config, "seed", ""))
        fallback = {
            "title": "Untitled Hollows",
            "stages": [
                {
                    "stage_id": f"stage_{i + 1}",
                    "biome": "caverns",
                    "brief": "A quiet cavern stage.",
                }
                for i in range(self.num_stages)
            ],
        }
        data = llm_json(
            ctx,
            self.name,
            lambda fb: ctx.prompts.world_generation(
                self.pitch, seed, self.num_stages
            ),
            required_keys=("title", "stages"),
            fallback=fallback,
            validate_obj=self._validate,
        )
        stage_ids: list[str] = []
        briefs: dict[str, str] = {}
        biomes: dict[str, str] = {}
        for entry in data["stages"]:
            stage_id = slugify(str(entry["stage_id"]))
            base, counter = stage_id, 2
            while stage_id in briefs:  # fallback dedup, post-validation
                stage_id = f"{base}_{counter}"
                counter += 1
            stage_ids.append(stage_id)
            briefs[stage_id] = str(entry["brief"])
            biomes[stage_id] = slugify(str(entry["biome"])) or "wilds"
        world = World(
            artifact_id=make_artifact_id("world"),
            title=str(data["title"]),
            stage_ids=stage_ids,
            # Linear play chain — the data seam branching graphs reuse.
            edges=list(zip(stage_ids, stage_ids[1:])),
            unlock_rules={"type": "linear"},
        )
        content_hash = ctx.adapter.write_json_singleton(
            "world.json", world.model_dump(mode="json")
        )
        stamp_provenance(ctx, world, content_hash, label="plat:world")
        ctx.bible.world = world
        ctx.artifacts["stage_ids"] = stage_ids
        ctx.artifacts["stage_briefs"] = briefs
        ctx.artifacts["stage_biomes"] = biomes
        logger.info(
            "WorldPhase produced world %r with %d stage(s):",
            world.title, len(stage_ids),
        )
        for stage_id in stage_ids:
            logger.info(
                "  %s [%s]: %s", stage_id, biomes[stage_id], briefs[stage_id]
            )
        _stamp_metadata(ctx, self.name)


# ---------------------------------------------------------------------------
# StagePhase
# ---------------------------------------------------------------------------


class StagePhase:
    """Per-stage plans, one LLM call per stage. Level ids are allocated
    GLOBALLY across the world (stage k of M owns l{(k-1)*L+1}..l{kL}) so
    ``bible.levels`` keying, regen addressing, and PLAT_LEVEL all stay
    id-unique; the biome-local display name ("2-1") is derived from the
    world order by the manifest, never stored as an id."""

    name = "plat:stage"

    def __init__(self, num_levels: int = 3, num_enemies: int = 3) -> None:
        self.num_levels = num_levels
        self.num_enemies = num_enemies

    def run(self, ctx: Any) -> None:
        from examples.platformer_pack.effects import sanitize_effects

        stage_ids = ctx.artifacts["stage_ids"]
        world_title = ctx.bible.world.title if ctx.bible.world else ""
        ctx.artifacts.setdefault("level_briefs", {})
        ctx.artifacts.setdefault("level_views", {})
        ctx.artifacts.setdefault("level_rules", {})
        ctx.artifacts.setdefault("roster_briefs", {})
        for number, stage_id in enumerate(stage_ids, start=1):
            data = llm_json(
                ctx,
                f"{self.name}:{stage_id}",
                lambda fb, _sid=stage_id, _n=number: ctx.prompts.stage_generation(
                    world_title,
                    _sid,
                    ctx.artifacts.get("stage_briefs", {}).get(_sid, ""),
                    self.num_levels,
                    self.num_enemies,
                    stage_number=_n,
                    num_stages=len(stage_ids),
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
            # View hints are optional and lenient: unknown/missing →
            # standard (the game-global framing). Deliberate exceptions only.
            views = [str(v) for v in data.get("level_views") or []]
            views = (views + ["standard"] * self.num_levels)[: self.num_levels]
            # Per-level RULE-override proposals (combat/level-picks arc):
            # optional, one dict per level, most empty — validated
            # FAIL-CLOSED against the pack vocabulary at stamp time (a
            # design choice the LLM makes; the bounds are code's).
            rule_flags = [
                dict(r) if isinstance(r, dict) else {}
                for r in (data.get("level_rules") or [])
            ]
            rule_flags = (rule_flags + [{}] * self.num_levels)[
                : self.num_levels
            ]
            first = (number - 1) * self.num_levels
            level_ids = [f"l{first + i + 1}" for i in range(self.num_levels)]

            stage = Stage(
                artifact_id=make_artifact_id("stage", stage_id),
                stage_id=stage_id,
                theme=str(data["theme"]),
                biome=ctx.artifacts.get("stage_biomes", {}).get(stage_id, ""),
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
            stamp_provenance(
                ctx, stage, content_hash,
                label=f"plat:stage:{stage.stage_id}",
            )
            ctx.bible.stages[stage_id] = stage
            ctx.artifacts["level_briefs"].update(dict(zip(level_ids, briefs)))
            ctx.artifacts["level_views"].update(dict(zip(level_ids, views)))
            ctx.artifacts["level_rules"].update(dict(zip(level_ids, rule_flags)))
            ctx.artifacts["roster_briefs"][stage_id] = str(data["roster_brief"])
            logger.info(
                "StagePhase planned stage %r (%d/%d, theme %r): levels %s; "
                "roster: %s",
                stage_id, number, len(stage_ids), stage.theme,
                ", ".join(level_ids), ctx.artifacts["roster_briefs"][stage_id],
            )
            for level_id, brief in zip(level_ids, briefs):
                logger.info("  %s brief: %s", level_id, brief)
        _stamp_metadata(ctx, self.name)


# ---------------------------------------------------------------------------
# EnemyGeneratorPhase
# ---------------------------------------------------------------------------


#: Swim-style weights for swimmer definitions — rolled in CODE (the
#: skeleton lookup table is deterministic per key, so a weighted roll
#: that depends on archetype can't live in the schema yet). ``within`` =
#: the classic body-bound patroller; ``surface`` rides the water's top
#: row; ``float`` drifts diagonally through the body.
SWIM_STYLES: tuple[tuple[str, int], ...] = (
    ("within", 2), ("surface", 1), ("float", 1),
    # Unbounded cruiser (water arc): swims a straight line across the
    # whole body of water, flipping only at walls/water's edge — no
    # patrol tether (the Cheep-Cheep).
    ("cruise", 1),
)

#: Chance (out of the weights' total) that a COMMON / UNCOMMON enemy
#: roams every biome instead of a subset — the ecology knob that makes
#: commons feel world-wide and rares biome-bound. Tuned down after the
#: first real run rolled 5-of-7 worldwide and every biome played the
#: same roster (one native per biome is separately GUARANTEED).
_EVERYWHERE_WEIGHTS = {"common": (4, 10), "uncommon": (2, 10), "rare": (0, 10)}


def roll_habitats(rarity: str, biomes: list[str], rng: Any) -> list[str]:
    """The biome list one enemy inhabits — ``["*"]`` = the whole world.
    Deterministic (caller derives the rng); commons bias worldwide, rares
    always bind to exactly one biome."""
    if len(biomes) <= 1:
        return ["*"]
    hits, total = _EVERYWHERE_WEIGHTS.get(rarity, (0, 10))
    if rng.randrange(total) < hits:
        return ["*"]
    count = 1 if rarity == "rare" else rng.choice((1, 2))
    return sorted(rng.sample(biomes, min(count, len(biomes))))


class EnemyGeneratorPhase:
    """The WORLD enemy pool (ecology, not per-stage rosters): skeleton
    rolls mechanics + rarity from schemas/enemy.json (v5); habitats and
    swim styles roll in code; the LLM names and flavors each creature for
    its habitat. Placeholder color is assigned here, on the definition.
    Hue reservations come from the UNION of every stage palette (runs
    AFTER the style phase), so enemies never share a hue with any biome's
    hazards or volumes. Each stage's ``enemy_refs`` = the pool filtered
    by its biome, repaired (loudly) up to a minimum roster."""

    name = "plat:enemies"

    #: A stage roster below this gets the nearest pool enemies' habitats
    #: widened (code repair, loud) — a biome must have creatures to place.
    MIN_STAGE_ROSTER = 3

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

    def _reserved_bands(self, ctx: Any) -> tuple[tuple[float, float], ...]:
        """Union of every stage palette's hazard/volume hue bands (enemy
        colors must read against ALL biomes — commons travel)."""
        bands: list[tuple[float, float]] = []
        for palette in _ctx_palettes(ctx).values():
            bands.extend(reserved_hue_bands(palette, self.tiles))
        return tuple(dict.fromkeys(bands)) or DEFAULT_RESERVED_HUES

    def _habitat_desc(self, habitats: list[str]) -> str:
        if habitats == ["*"]:
            return "roams EVERY biome of the world"
        return f"native to the {', '.join(habitats)} biome(s) only"

    def run(self, ctx: Any) -> None:
        spec = load_skeleton_spec(self.schema_path)
        stages = list(ctx.bible.stages.values())
        biomes = [s.biome for s in stages if s.biome]
        roster_briefs = ctx.artifacts.get("roster_briefs", {})
        if not roster_briefs and "roster_brief" in ctx.artifacts:
            # Legacy single-stage artifacts shape (tests, old callers).
            roster_briefs = {
                s.stage_id: ctx.artifacts["roster_brief"] for s in stages
            }
        seed = str(getattr(ctx.config, "seed", ""))
        reserved = self._reserved_bands(ctx)
        bg_lums = background_luminances(_ctx_palettes(ctx), self.tiles)
        seen_ids: set[str] = set()
        used_names: list[str] = []

        for i in range(self.count):
            skeleton = roll_skeleton(spec, derive_rng(seed, self.name, i))
            rarity = str(skeleton.get("rarity", "common"))
            if i < len(biomes) and len(biomes) > 1:
                # GUARANTEED NATIVE: the first M pool slots bind one
                # creature to each biome in world order — every biome
                # gets fauna of its own before the weighted rolls run
                # (the first real run's forest and ruins played identical
                # rosters of meadow critters).
                habitats = [biomes[i]]
            elif i == len(biomes) and len(biomes) > 1:
                # GUARANTEED WORLDWIDE: one anchor creature the whole
                # kingdom shares ("some enemies really common across the
                # whole world" — user's ecology; the weighted rolls alone
                # can land all-bound OR all-worldwide on a small pool).
                habitats = ["*"]
            else:
                habitats = roll_habitats(
                    rarity, biomes,
                    derive_rng(seed, f"{self.name}:habitat", i),
                )
            swim_style = ""
            if skeleton["archetype"] == "swimmer":
                styles, weights = zip(*SWIM_STYLES)
                swim_style = derive_rng(
                    seed, f"{self.name}:swim", i
                ).choices(styles, weights=weights)[0]
            # Hop tuning (combat/level-picks arc): archetype-dependent
            # params can't live in the schema yet — rolled here like
            # swim_style, on an independent key.
            hop_height = 0
            hop_period_s = 0.0
            if skeleton["archetype"] == "hopper":
                hop_rng = derive_rng(seed, f"{self.name}:hop", i)
                hop_height = int(hop_rng.randint(2, 3))
                hop_period_s = round(0.8 + 0.8 * float(hop_rng.random()), 2)
            # Home = the first habitat biome's stage (theme + fauna brief
            # context for the prompt). Worldwide creatures are named for
            # the WORLD — no single biome's fauna brief (the first run
            # flavored the whole pool as meadow critters).
            if habitats == ["*"]:
                home = None
                theme = ctx.bible.world.title if ctx.bible.world else ""
                roster_brief = ""
            else:
                home = next(
                    (s for s in stages if s.biome in habitats),
                    stages[0] if stages else None,
                )
                theme = home.theme if home else ""
                roster_brief = (
                    roster_briefs.get(home.stage_id, "") if home else ""
                )

            data = llm_json(
                ctx,
                f"{self.name}:{i}",
                lambda fb, _skel=skeleton, _i=i, _t=theme, _rb=roster_brief,
                _r=rarity, _h=self._habitat_desc(habitats):
                    ctx.prompts.enemy_generation(
                        _skel, _t, _rb, _i,
                        used_names=list(used_names), feedback=fb,
                        rarity=_r, habitat_desc=_h,
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

            # Aggro is an ORTHOGONAL behavior tier (schemas/enemy.json),
            # rolled independently of locomotion: its aggro_mult/leash_mult
            # scale the enemy's own patrol_range into an eyesight radius and
            # a chase tether, so a big-territory enemy sees and chases
            # proportionally farther. passive -> both 0 (never pursues). A
            # negative leash_mult is the "no tether" sentinel (a 'hunter'
            # chases across the whole level) — stored as leash_range 0, which
            # the pursuit routine reads as unleashed. The multiply lives in
            # code; the mults + weights stay user-editable schema data.
            patrol_range = int(skeleton["patrol_range"])
            aggro_mult = float(skeleton.get("aggro_mult", 0) or 0)
            leash_mult = float(skeleton.get("leash_mult", 0) or 0)
            aggro_range = round(aggro_mult * patrol_range)
            leash_range = 0 if leash_mult < 0 else round(leash_mult * patrol_range)
            behavior = {
                "patrol_range": patrol_range,
                # Eyesight radius: how close the player gets before an
                # aggressive enemy commits (0 = passive, never pursues).
                "aggro_range": aggro_range,
                # Chase tether: how far from home it pursues before breaking
                # off and returning to its patrol (0 = no tether; the
                # 'relentless' VARIANT is the same idea as a placement
                # override).
                "leash_range": leash_range,
            }
            if swim_style:
                behavior["swim_style"] = swim_style
            if hop_height:
                behavior["hop_height"] = hop_height
                behavior["hop_period_s"] = hop_period_s
            enemy = EnemyDefinition(
                artifact_id=make_artifact_id("enemy", enemy_id),
                enemy_id=enemy_id,
                name=str(data["name"]),
                archetype=str(skeleton["archetype"]),
                # Typed, not a stat: placement footprints, touch boxes,
                # and render scale key off it (schema v4 rolls the
                # discrete tier; pre-v4 bibles default to 1.0).
                size=float(skeleton.get("size", 1.0)),
                rarity=rarity,
                habitats=habitats,
                stats={
                    "hp": skeleton["hp"],
                    "damage": skeleton["damage"],
                    "speed": skeleton["speed"],
                    "flavor": str(data.get("flavor", "")),
                    "placeholder_color": placeholder_color(
                        i, reserved, bg_lums
                    ),
                },
                behavior=behavior,
                # Ecology edges: the world, plus every habitat stage —
                # a habitat stage's regen re-flavors its natives.
                parents=[
                    make_artifact_id("world"),
                    *(
                        s.artifact_id
                        for s in stages
                        if habitats != ["*"] and s.biome in habitats
                    ),
                ],
            )
            content_hash = ctx.adapter.write_json_singleton(
                f"enemy/{enemy_id}.json", enemy.model_dump(mode="json")
            )
            stamp_provenance(
                ctx, enemy, content_hash, label=f"plat:enemies:{i}"
            )
            ctx.bible.enemy_definitions[enemy_id] = enemy
            logger.info(
                "Enemy %d/%d: %r (%s%s, size %.1f, %s, %s) — hp=%s dmg=%s "
                "spd=%s %s color=%s: %s",
                i + 1, self.count, enemy.name, enemy.archetype,
                f"/{swim_style}" if swim_style else "", enemy.size, rarity,
                self._habitat_desc(habitats),
                enemy.stats["hp"], enemy.stats["damage"], enemy.stats["speed"],
                " ".join(f"{k}={v}" for k, v in enemy.behavior.items()),
                enemy.stats["placeholder_color"], enemy.stats["flavor"],
            )

        self._assign_stage_rosters(ctx, stages)
        logger.info(
            "EnemyGeneratorPhase produced a %d-creature world pool: %s",
            len(seen_ids), ", ".join(sorted(seen_ids)),
        )
        _stamp_metadata(ctx, self.name)

    def _assign_stage_rosters(self, ctx: Any, stages: list) -> None:
        """Stage rosters = the pool filtered by biome. A roster below
        MIN_STAGE_ROSTER gets the nearest non-resident enemies' habitats
        widened — a code repair (recorded on the definitions, loud), not
        a re-roll."""
        pool = list(ctx.bible.enemy_definitions.values())
        for stage in stages:
            residents = [
                e for e in pool
                if e.habitats == ["*"] or stage.biome in e.habitats
            ]
            missing = self.MIN_STAGE_ROSTER - len(residents)
            if missing > 0:
                outsiders = [e for e in pool if e not in residents]
                for enemy in outsiders[:missing]:
                    enemy.habitats = sorted({*enemy.habitats, stage.biome})
                    content_hash = ctx.adapter.write_json_singleton(
                        f"enemy/{enemy.enemy_id}.json",
                        enemy.model_dump(mode="json"),
                    )
                    stamp_provenance(
                        ctx, enemy, content_hash, label="plat:enemies"
                    )
                    residents.append(enemy)
                    warn(
                        ctx,
                        f"ecology: stage {stage.stage_id!r} ({stage.biome}) "
                        f"had too few residents — widened "
                        f"{enemy.enemy_id!r}'s habitats to include it "
                        f"(now {enemy.habitats}).",
                    )
            stage.enemy_refs = [e.artifact_id for e in residents]
            # The roster is stage.json content — rewrite + re-stamp so the
            # on-disk plan matches the Bible after ecology assignment.
            content_hash = ctx.adapter.write_json_singleton(
                f"stage/{stage.stage_id}/stage.json",
                stage.model_dump(mode="json"),
            )
            stamp_provenance(
                ctx, stage, content_hash,
                label=f"plat:stage:{stage.stage_id}",
            )
            logger.info(
                "Stage %s (%s) roster: %s",
                stage.stage_id, stage.biome or "?",
                ", ".join(e.enemy_id for e in residents),
            )


class ItemGeneratorPhase:
    """The WORLD ITEM POOL (Arc 2): ``count`` ItemDefinitions rolled from
    ``schemas/item.json`` — the mechanical KIND set (coin/heal/shield/
    double_jump/run_boost) is closed in code, every number is a schema
    band, and the LLM authors only name + flavor (enemy-pool pattern).

    Slot GUARANTEES mirror the enemy ecology's: slot 0 is always a COIN
    and slot 1 a HEAL — the two kinds every world needs, which the
    weighted rolls alone can miss on a small pool. The guarantee pins the
    ``kind`` roll's choice list; every dependent field still rolls
    through the skeleton, so determinism per slot is untouched.
    """

    name = "plat:items"

    #: Kinds pinned to the first pool slots (world-critical coverage).
    GUARANTEED_KINDS = ("coin", "heal")

    def __init__(
        self,
        count: int = 5,
        schema_path: str | Path | None = None,
        tiles: Any = None,
    ) -> None:
        from examples.platformer_pack.tiles import DEFAULT_TILES

        self.count = count
        self.schema_path = Path(schema_path or SCHEMAS_DIR / "item.json")
        self.tiles = tiles or DEFAULT_TILES

    #: Rolled params that ride on the definition when non-zero.
    _PARAM_FIELDS = ("duration_s", "heal_amount", "coin_value", "boost_mult")

    def run(self, ctx: Any) -> None:
        from canon.bible.platformer import ItemDefinition

        spec_raw = json.loads(self.schema_path.read_text())
        world_title = ctx.bible.world.title if ctx.bible.world else ""
        seed = str(getattr(ctx.config, "seed", ""))
        bg_lums = background_luminances(_ctx_palettes(ctx), self.tiles)
        seen_ids: set[str] = set()
        used_names: list[str] = []

        for i in range(self.count):
            if i < len(self.GUARANTEED_KINDS):
                pinned = dict(spec_raw)
                pinned["fields"] = dict(spec_raw["fields"])
                pinned["fields"]["kind"] = {
                    "choices": [[self.GUARANTEED_KINDS[i], 1]]
                }
                spec = load_skeleton_spec(pinned)
            else:
                spec = load_skeleton_spec(spec_raw)
            skeleton = roll_skeleton(spec, derive_rng(seed, self.name, i))
            kind = str(skeleton["kind"])
            params = {
                key: skeleton[key]
                for key in self._PARAM_FIELDS
                if skeleton.get(key)
            }

            data = llm_json(
                ctx,
                f"{self.name}:{i}",
                lambda fb, _skel=skeleton, _i=i:
                    ctx.prompts.item_generation(
                        _skel, world_title, _i,
                        used_names=list(used_names), feedback=fb,
                    ),
                required_keys=("name",),
                fallback={"name": f"Item {i}", "flavor": ""},
                validate_obj=lambda obj: (
                    [
                        f"Name {obj.get('name')!r} is already taken; invent "
                        "a clearly different one."
                    ]
                    if str(obj.get("name", "")).strip().lower()
                    in {n.lower() for n in used_names}
                    else []
                ),
            )
            used_names.append(str(data["name"]))
            item_id = slugify(str(data["name"]))
            base, counter = item_id, 2
            while item_id in seen_ids:
                item_id = f"{base}_{counter}"
                counter += 1
            seen_ids.add(item_id)

            item = ItemDefinition(
                artifact_id=make_artifact_id("item", item_id),
                item_id=item_id,
                name=str(data["name"]),
                kind=kind,
                rarity=str(skeleton.get("rarity", "common")),
                params=params,
                stats={
                    "flavor": str(data.get("flavor", "")),
                    # Offset past the enemy pool's golden-angle walk so
                    # item swatches never collide with roster colors.
                    "placeholder_color": placeholder_color(
                        i + 40, DEFAULT_RESERVED_HUES, bg_lums
                    ),
                },
                parents=[make_artifact_id("world")],
            )
            content_hash = ctx.adapter.write_json_singleton(
                f"item/{item_id}.json", item.model_dump(mode="json")
            )
            stamp_provenance(ctx, item, content_hash, label=f"plat:items:{i}")
            ctx.bible.items[item_id] = item
            logger.info(
                "Item %d/%d: %r (%s, %s%s): %s",
                i + 1, self.count, item.name, kind, item.rarity,
                f", {params}" if params else "",
                item.stats["flavor"],
            )
        _stamp_metadata(ctx, self.name)
