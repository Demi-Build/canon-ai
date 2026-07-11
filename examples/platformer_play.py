"""THROWAWAY pygame review harness — filler code, not the game.

Exists solely to simulate what the generated databases look like in motion:
tiles resolve through the Tileset artifact (categories + params on slots),
enemy squares through EnemyDefinition placeholder colors + the manifest's
variant vocabulary, and enemy movement executes the rolled behavior params.
The real platformer ships in Godot (Phase 4); this file dies when that
lives. Do not polish it.

    uv run --extra platformer --extra play \
        python examples/platformer_play.py <data_dir> [level_id]

Controls: arrows/A-D move, space/up jumps or swims, R respawns, Esc quits.
Combat v1: integer HEARTS (manifest "combat" block) — enemy contact costs
stats.damage x variant mults, hazard tiles cost their params.damage,
damaging volumes drain hearts continuously; land on an enemy's head to
stomp it (hp x mults / stomp_damage stomps) and bounce. Zero hearts:
respawn at the last checkpoint, hearts refilled, killed enemies restored
(GameRules.checkpoint_enemy_reset). After any (re)spawn you blink,
untouchable, and chasers hold still until your first move
(GameRules.spawn_grace); that first move starts a timed spawn SHIELD
(combat spawn_grace_s) so a checkpoint-camping enemy can't hit you the
instant you act. Fall off: same respawn. Reach the exit column: level
complete.
"""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path


def _sign(v: float) -> float:
    """Mirror of GDScript ``signf``: 1.0 / -1.0 / 0.0 (0 for exactly 0)."""
    return 1.0 if v > 0 else (-1.0 if v < 0 else 0.0)

SCALE = 32
FPS = 60


def _stage_for(manifest: dict, level_id: str) -> str:
    """The biome stage owning ``level_id`` (manifest v2 "stages")."""
    for stage_entry in manifest.get("stages", []):
        if level_id in stage_entry.get("levels", []):
            return str(stage_entry["stage_id"])
    raise SystemExit(
        f"level {level_id!r} not in this world — levels: "
        f"{manifest.get('levels')}"
    )


def _load(data_dir: Path, level_id: str):
    import numpy as np

    manifest = json.loads((data_dir / "manifest.json").read_text())
    stage_id = _stage_for(manifest, level_id)
    level_dir = data_dir / "level" / stage_id / level_id
    level = json.loads((level_dir / "level.json").read_text())
    with np.load(level_dir / "collision.npz") as data:
        grid = data["collision"]
    enemies = {
        p.stem: json.loads(p.read_text())
        for p in (data_dir / "enemy").glob("*.json")
    }
    placements = json.loads((level_dir / "entities.json").read_text())
    tileset = json.loads(
        (data_dir / "tileset" / stage_id / "manifest.json").read_text()
    )
    return manifest, level, grid, enemies, placements, tileset


def _tile_colors(data_dir: Path, tileset: dict) -> dict[int, tuple]:
    """Resolve tile colors by sampling the tilesheet via the Tileset slots —
    same resolution path the renderer and Godot use. Each sample is the
    tile REGION's average, so generated textures resolve to their
    palette-conformed mean, not one arbitrary pixel."""
    from PIL import Image

    sheet = Image.open(data_dir / tileset["tilesheet_path"]).convert("RGB")
    colors = {}
    for slot in tileset["slots"]:
        x, y, w, h = slot["px_region"]
        region = sheet.crop((x, y, x + w, y + h))
        colors[int(slot["tile_type"])] = region.resize(
            (1, 1), Image.BILINEAR
        ).getpixel((0, 0))
    return colors


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    data_dir = Path(sys.argv[1])
    level_id = sys.argv[2] if len(sys.argv) > 2 else "l1"

    try:
        import pygame
    except ImportError:
        raise SystemExit(
            "pygame not installed — run with:  uv run --extra platformer "
            "--extra play python examples/platformer_play.py ..."
        ) from None

    manifest, level, grid, enemies, placements, tileset = _load(data_dir, level_id)
    movement = manifest["movement"]
    tile_colors = _tile_colors(data_dir, tileset)
    height, width = grid.shape
    BODY_L, BODY_R = 0.15, 0.85  # player body span — sample BOTH corners

    # Physics semantics from the tileset manifest (Appendix E.3 / 3b) — no
    # hardcoded tile IDs; categories + params ride on the slots.
    def _types_with(category: str) -> set:
        return {
            int(s["tile_type"]) for s in tileset["slots"]
            if s.get("collision") == category
        }

    BLOCKING = _types_with("solid")
    ONE_WAY = _types_with("one_way")
    #: tile id → hazard params ("damage" in hearts, default 1)
    HAZARDS = {
        int(s["tile_type"]): dict(s.get("params") or {})
        for s in tileset["slots"]
        if s.get("collision") == "hazard"
    }
    #: tile id → volume params (speed_factor/gravity/impulse/damage_per_second)
    VOLUMES = {
        int(s["tile_type"]): dict(s.get("params") or {})
        for s in tileset["slots"]
        if s.get("collision") == "volume"
    }

    # Combat tuning from the manifest (combat.json → "combat" block);
    # defaults mirror examples/platformer_pack/combat.py.
    combat = manifest.get("combat", {})
    MAX_HEARTS = int(combat.get("player_max_hearts", 3))
    STOMP_DAMAGE = int(combat.get("stomp_damage", 6))
    STOMP_BOUNCE = float(combat.get("stomp_bounce_factor", 0.7))
    IFRAMES_S = float(combat.get("hurt_iframes_s", 1.0))
    SPAWN_GRACE_S = float(combat.get("spawn_grace_s", 1.0))

    spawn = {"x": level["spawn"][0], "y": level["spawn"][1]}
    exit_ = {"x": level["exit"][0], "y": level["exit"][1]}
    # Checkpoints from the triggers layer (3b): crossing one moves the
    # respawn point there.
    checkpoints = [
        {"x": t["x"], "y": t["y"], "active": False}
        for t in level.get("triggers", [])
        if t.get("type") == "checkpoint"
    ]
    respawn_point = dict(spawn)

    def hex_rgb(h: str) -> tuple:
        h = h.lstrip("#")
        return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))

    rules = manifest.get("rules", {})
    water_policy = rules.get("enemy_water_policy", "swimmers_only")
    drop_through = bool(rules.get("platform_drop_through", True))
    enemy_reset = bool(rules.get("checkpoint_enemy_reset", True))
    spawn_grace = str(rules.get("spawn_grace", "until_move")) == "until_move"
    #: Variant vocabulary from the manifest — consumers never hardcode
    #: what "elite" or "champion" means.
    variant_defs = {v["name"]: v for v in manifest.get("variants", [])}

    class Enemy:
        def __init__(self, placement: dict) -> None:
            self.spec = enemies[placement["enemy_id"]]
            self.x = float(placement["x"])
            self.y = float(placement["y"])
            self.home = float(placement["x"])
            self.home_y = float(placement["y"])
            self.direction = 1.0
            self.dir_y = 1.0  # float-style swimmers drift diagonally
            # Aggro lock: set when an aggressive enemy first spots the
            # player, cleared on losing eyesight range or hitting the tether.
            self.alerted = False
            # Flyer clocks: vertical hover bob and the dive cycle, plus the
            # committed dive direction + depth (ephemeral, reset on respawn).
            self.bob_t = 0.0
            self.swoop_t = 0.0
            self.swoop_dir = 1.0
            self.swoop_dep = 0.0
            self.variant = variant_defs.get(str(placement.get("variant", "")))
            speed_mult = self.variant.get("speed_mult", 1.0) if self.variant else 1.0
            self.speed = float(self.spec["stats"].get("speed", 0)) * speed_mult
            # EFFECTIVE size everywhere: definition.size x variant.size
            # (combat.py contract). The body grows UP from its anchor
            # cell: feet at y+1, top at y+1-size.
            self.size = float(self.spec.get("size", 1.0)) * (
                float(self.variant.get("size", 1.0)) if self.variant else 1.0
            )
            # Dead stats live: hp x mults soaked by stomps, damage x
            # mults dealt in hearts (combat.py arithmetic, mirrored).
            mults = self.variant.get("stat_mults", {}) if self.variant else {}
            self.max_hp = float(self.spec["stats"].get("hp", 1)) * float(
                mults.get("hp", 1.0)
            )
            self.hp = self.max_hp
            self.damage_hearts = max(
                1,
                round(
                    float(self.spec["stats"].get("damage", 1))
                    * float(mults.get("damage", 1.0))
                ),
            )
            self.alive = True
            self.hurt_t = 0.0  # white flash after a surviving stomp
            visual = self.variant.get("visual", "outline") if self.variant else ""
            self.outlined = "outline" in visual
            self.color = hex_rgb(self.spec["stats"].get("placeholder_color", "#ff00ff"))
            behavior = dict(self.spec["behavior"])
            if self.variant:
                behavior.update(self.variant.get("behavior", {}))
            self.behavior = behavior
            # Swimmer sub-behavior (ecology): "surface" rides the water's
            # top row, "float" drifts diagonally, ""/"within" = classic.
            self.swim_style = str(behavior.get("swim_style", "") or "")

        def reset(self) -> None:
            """Checkpoint-respawn restore: killed enemies return, at
            their placement, full hp (GameRules.checkpoint_enemy_reset)."""
            self.x, self.y = self.home, self.home_y
            self.direction, self.dir_y = 1.0, 1.0
            self.alerted = False
            self.bob_t = self.swoop_t = 0.0
            self.swoop_dir, self.swoop_dep = 1.0, 0.0
            self.hp, self.alive, self.hurt_t = self.max_hp, True, 0.0

        def stomp(self) -> bool:
            """One stomp of damage; True if it killed."""
            self.hp -= STOMP_DAMAGE
            if self.hp <= 0:
                self.alive = False
                return True
            self.hurt_t = 0.25
            return False

        def _can_occupy(
            self, x: float, y: float | None = None, swim_style: str | None = None
        ) -> bool:
            """Terrain constraint for the NEXT step (GameRules-aware):
            swimmers stay in their volume (surface-riders on its TOP
            row); land enemies keep solid footing and — under
            swimmers_only/forbidden — never enter a volume. NO enemy
            walks into a hazard or clips through a solid — monsters
            respect the level (behavior doctrine; jumpers are v2). Pass
            ``swim_style=""`` to check plain in-water occupancy (a hunting
            swimmer ignores its passive surface/float drift rule)."""
            y = self.y if y is None else y
            style = self.swim_style if swim_style is None else swim_style
            cell = tile_at(x, y)
            below = tile_at(x, y + 1)
            if cell in HAZARDS:
                return False  # nobody strolls into spikes
            if self.spec.get("archetype") == "swimmer":
                if cell not in VOLUMES:
                    return False
                if style == "surface":
                    return tile_at(x, y - 1) not in VOLUMES
                return True
            if self.spec.get("archetype") == "flyer":
                # Airborne: any open-air cell — a flyer ignores ground and
                # flies over gaps, but never through walls or into
                # hazards/water.
                return not (cell in BLOCKING or cell in ONE_WAY or cell in VOLUMES)
            if cell in BLOCKING or cell in ONE_WAY:
                return False  # no clipping through terrain
            if cell in VOLUMES and water_policy != "amphibious":
                return False
            return below in BLOCKING or below in ONE_WAY  # no cliff-walking

        def _in_sight(
            self, rel_x: float, rel_y: float, aggro_range: float
        ) -> bool:
            """Is the player (rel = player - enemy) within eyesight RANGE and
            this locomotion's field of view? FOV shapes are data
            (rules.enemy_sight per archetype): "omni" 360, "hemisphere" the
            180 forward half-plane, "forward" a narrow cone in front within
            `vband` rows, "none" blind; absent archetype -> "omni". main.gd
            mirrors this — mechanics parity."""
            if math.hypot(rel_x, rel_y) > aggro_range:
                return False
            cfg = rules.get("enemy_sight", {}).get(
                self.spec.get("archetype", "sentry"), {}
            )
            fov = str(cfg.get("fov", "omni"))
            if fov == "none":
                return False
            if fov == "omni":
                return True
            if rel_x * self.direction < 0:
                return False  # behind the enemy's facing half-plane
            if fov == "forward":
                return abs(rel_y) <= float(cfg.get("vband", 2))
            return True  # "hemisphere": forward half-plane, any vertical

        def _aggro_mode(self, player_x: float, player_y: float) -> str:
            """Locomotion-agnostic aggro decision shared by ground/water/air:
            FOV-gated detection, then an `alerted` lock that commits the chase
            by RANGE until the player leaves eyesight range OR the tether
            (leash_range; <=0 = no tether) snaps. Returns "chase" | "return" |
            "patrol"; mutates self.alerted. Mirrored in main.gd."""
            aggro = float(self.behavior.get("aggro_range", 0) or 0)
            rel_x, rel_y = player_x - self.x, player_y - self.y
            leash = float(self.behavior.get("leash_range", 0) or 0)
            # A flyer's territory (leash + return threshold) is HORIZONTAL —
            # its dives dip in Y and must not count as straying from home.
            home_dist = (
                abs(self.x - self.home)
                if self.spec.get("archetype") == "flyer"
                else math.hypot(self.x - self.home, self.y - self.home_y)
            )
            if self.alerted:
                if math.hypot(rel_x, rel_y) > aggro or (
                    leash > 0 and home_dist >= leash
                ):
                    self.alerted = False
            elif self._in_sight(rel_x, rel_y, aggro):
                self.alerted = True
            if self.alerted:
                return "chase"
            if home_dist > float(self.behavior.get("patrol_range", 4)):
                return "return"  # chased out of its beat — walk home
            return "patrol"

        def _ground_toward(self, target_x: float, step: float) -> None:
            """Ground pursuit/return: step X toward a target, occupancy-gated
            (halts at cliffs, walls, hazards, water). Y is locked."""
            dir_to = _sign(target_x - self.x)
            if dir_to != 0:
                self.direction = dir_to
            nx = self.x + dir_to * step
            if abs(target_x - self.x) < step:
                nx = target_x
            if self._can_occupy(nx):
                self.x = nx

        def _swim_toward(
            self, target_x: float, target_y: float, step: float
        ) -> None:
            """Water pursuit/return: step X and Y (independently,
            occupancy-gated) toward a target, staying inside the volume. A
            hunting swimmer ignores its passive swim_style drift rule
            (swim_style "" occupancy)."""
            dir_x = _sign(target_x - self.x)
            if dir_x != 0:
                self.direction = dir_x
            nx = self.x + dir_x * step
            if abs(target_x - self.x) < step:
                nx = target_x
            if self._can_occupy(nx, self.y, swim_style=""):
                self.x = nx
            ny = self.y + _sign(target_y - self.y) * step
            if abs(target_y - self.y) < step:
                ny = target_y
            if self._can_occupy(self.x, ny, swim_style=""):
                self.y = ny

        def _fly_toward(
            self, target_x: float, target_y: float, step: float
        ) -> None:
            """Airborne pursuit/return: step X and Y (independently,
            occupancy-gated) toward a target through open air — the swoop and
            the climb home."""
            dir_x = _sign(target_x - self.x)
            if dir_x != 0:
                self.direction = dir_x
            nx = self.x + dir_x * step
            if abs(target_x - self.x) < step:
                nx = target_x
            if self._can_occupy(nx):
                self.x = nx
            ny = self.y + _sign(target_y - self.y) * step
            if abs(target_y - self.y) < step:
                ny = target_y
            if self._can_occupy(self.x, ny):
                self.y = ny

        def update(self, dt: float, player_x: float, player_y: float) -> None:
            # Aggro is an ORTHOGONAL tier layered on locomotion: an aggressive
            # enemy (aggro_range > 0) chases/returns via the shared decision,
            # otherwise runs its locomotion's patrol. main.gd mirrors every
            # branch — mechanics parity. No spawn-grace gate: the player is
            # untouchable during grace, so an aggressive enemy may close in
            # (shield + spawn-safety radius keep it fair, and it makes a
            # no-input frame capture actually show the chase).
            archetype = self.spec.get("archetype", "sentry")
            self.hurt_t = max(0.0, self.hurt_t - dt)
            patrol_range = float(self.behavior.get("patrol_range", 4))
            mode = "patrol"
            if float(self.behavior.get("aggro_range", 0) or 0) > 0 and self.speed > 0:
                mode = self._aggro_mode(player_x, player_y)
            chase = self.speed * float(rules.get("chase_speed_mult", 1.5)) * dt
            walk = self.speed * dt
            if archetype == "swimmer" and self.speed > 0:
                if mode == "chase":
                    self._swim_toward(player_x, player_y, chase)
                elif mode == "return":
                    self._swim_toward(self.home, self.home_y, walk)
                elif self.swim_style == "float":
                    # Passive floater: diagonal drift, each axis bouncing off
                    # the water's boundary independently.
                    step = self.speed * 0.7 * dt
                    new_x = self.x + self.direction * step
                    if abs(new_x - self.home) >= patrol_range or not self._can_occupy(
                        new_x
                    ):
                        self.direction *= -1.0
                    else:
                        self.x = new_x
                    new_y = self.y + self.dir_y * step
                    if tile_at(self.x, new_y) not in VOLUMES:
                        self.dir_y *= -1.0
                    else:
                        self.y = new_y
                else:
                    # Passive within/surface swimmer: x-bounce patrol.
                    new_x = self.x + self.direction * walk
                    if abs(new_x - self.home) >= patrol_range or not self._can_occupy(
                        new_x
                    ):
                        self.direction *= -1.0
                    else:
                        self.x = new_x
            elif archetype == "patroller" and self.speed > 0:
                if mode == "chase":
                    self._ground_toward(player_x, chase)
                elif mode == "return":
                    self._ground_toward(self.home, walk)
                else:
                    # Passive / not-alerted: x-bounce patrol within its beat.
                    new_x = self.x + self.direction * walk
                    if abs(new_x - self.home) >= patrol_range or not self._can_occupy(
                        new_x
                    ):
                        self.direction *= -1.0
                    else:
                        self.x = new_x
            elif archetype == "flyer" and self.speed > 0:
                fcfg = rules.get("flyer", {})
                # Flyer clocks advance EVERY frame (pure frame-count) so the
                # bob + dive phases stay deterministic across surfaces — parity.
                self.bob_t += dt
                self.swoop_t += dt
                bob = math.sin(
                    self.bob_t * float(fcfg.get("hover_freq", 3.0))
                ) * float(fcfg.get("hover_amp", 0.4))
                if mode == "chase":
                    # Dive-bomb from altitude, "hunt from above": RECOVER on
                    # the plane (bob + reposition toward the player, COMMIT the
                    # next dive's dir+depth), then a fixed-direction parabolic
                    # PLUNGE aimed where the player was, back up to the plane.
                    # Never descends to ground-chase. main.gd mirrors this.
                    period = float(fcfg.get("swoop_period", 3.0))
                    dur = float(fcfg.get("swoop_duration", 1.0))
                    phase = math.fmod(self.swoop_t, period)
                    if phase < dur:  # DIVE (committed dir + depth)
                        u = phase / dur
                        if self._can_occupy(self.x + self.swoop_dir * chase):
                            self.x += self.swoop_dir * chase
                        ny = self.home_y + self.swoop_dep * 4.0 * u * (1.0 - u)
                        if self._can_occupy(self.x, ny):
                            self.y = ny
                        if self.swoop_dir != 0:
                            self.direction = self.swoop_dir
                    else:  # RECOVER on the plane: track player, aim next dive
                        self.swoop_dir = _sign(player_x - self.x)
                        self.swoop_dep = max(0.0, player_y - self.home_y)
                        nx = self.x + _sign(player_x - self.x) * walk
                        if abs(player_x - self.x) < walk:
                            nx = player_x
                        if self._can_occupy(nx):
                            self.x = nx
                        by = self.home_y + bob
                        if self._can_occupy(self.x, by):
                            self.y = by
                        if player_x != self.x:
                            self.direction = _sign(player_x - self.x)
                elif mode == "return":
                    self._fly_toward(self.home, self.home_y, walk)  # climb home
                elif float(self.behavior.get("aggro_range", 0) or 0) > 0:
                    # AGGRESSIVE flyer idle: hover near spawn — a vertical bob
                    # plus a horizontal sway that scans its 180 cone both ways
                    # (and drifts back into the hover zone if it ended a chase
                    # outside it).
                    sway = float(fcfg.get("hover_sway", 2.0))
                    sway_speed = float(fcfg.get("sway_speed", 1.5))
                    nx = self.x + self.direction * sway_speed * dt
                    if nx > self.home + sway:
                        self.direction = -1.0
                    elif nx < self.home - sway:
                        self.direction = 1.0
                    nx = self.x + self.direction * sway_speed * dt
                    if self._can_occupy(nx):
                        self.x = nx
                    else:
                        self.direction *= -1.0
                    by = self.home_y + bob
                    if self._can_occupy(self.x, by):
                        self.y = by
                else:
                    # PASSIVE flyer: horizontal patrol at altitude + a periodic
                    # ambient dive (swoop) that returns to altitude.
                    new_x = self.x + self.direction * walk
                    if abs(new_x - self.home) >= patrol_range or not self._can_occupy(
                        new_x
                    ):
                        self.direction *= -1.0
                    else:
                        self.x = new_x
                    dur = float(fcfg.get("swoop_duration", 1.0))
                    phase = math.fmod(self.swoop_t, float(fcfg.get("swoop_period", 3.0)))
                    dip = (
                        float(fcfg.get("swoop_depth", 3.0)) * math.sin(math.pi * phase / dur)
                        if phase < dur
                        else 0.0
                    )
                    sy = self.home_y + dip
                    if self._can_occupy(self.x, sy):
                        self.y = sy
                    elif self._can_occupy(self.x, self.home_y):
                        self.y = self.home_y
            # sentry: stationary by definition

    pygame.init()
    screen = pygame.display.set_mode((width * SCALE, height * SCALE))
    pygame.display.set_caption(
        f"{manifest['world']} — {level_id}  [placeholder review build]"
    )
    clock = pygame.time.Clock()
    font = pygame.font.SysFont(None, 28)

    # Generated audio (manifest["audio"], late audio phase). Best-effort:
    # music loops, SFX keyed by event; ANY failure (no device, no files,
    # pre-audio manifest) leaves the game silent — this harness is the
    # quick pre-art surface, and music confirmation is one of its jobs.
    sounds: dict = {}
    try:
        # Audio is per-stage (levels in a biome share the theme).
        audio = (manifest.get("audio") or {}).get(
            _stage_for(manifest, level_id), {}
        )
        pygame.mixer.init()
        if audio.get("music"):
            pygame.mixer.music.load(str(data_dir / audio["music"]))
            pygame.mixer.music.play(-1)
        for sfx_event, rel in (audio.get("sfx") or {}).items():
            sounds[sfx_event] = pygame.mixer.Sound(str(data_dir / rel))
    except Exception as exc:  # noqa: BLE001 — silence is the fallback
        print(f"[audio] disabled: {exc}")

    def play_sfx(event_name: str) -> None:
        sound = sounds.get(event_name)
        if sound is not None:
            sound.play()

    # --- art track (all optional; the harness stays flat-color for tiles,
    # Godot is the art surface of record) ---
    def _sprite(rel: str, size: tuple) -> pygame.Surface | None:
        path = data_dir / rel
        if not rel or not path.exists():
            return None
        return pygame.transform.smoothscale(
            pygame.image.load(str(path)).convert_alpha(), size
        )

    stage_id = _stage_for(manifest, level_id)
    player_sprite = _sprite("sprite/player/base.png", (SCALE - 8, SCALE - 8))
    enemy_sprites = {
        eid: _sprite(spec.get("sprite_path", ""), (SCALE - 4, SCALE - 4))
        for eid, spec in enemies.items()
    }
    # Gameplay props (manifest "props", keyed per biome stage): sprite
    # when the art track made one, drawn placeholder shape otherwise.
    prop_paths = manifest.get("props", {}).get(stage_id, {})
    checkpoint_sprite = _sprite(
        prop_paths.get("checkpoint", ""),
        (int(SCALE * 1.5), int(SCALE * 1.5)),
    )
    exit_sprite = _sprite(prop_paths.get("exit", ""), (SCALE * 2, SCALE * 2))
    checkpoint_grey = None
    if checkpoint_sprite is not None:
        # Unclaimed flags render desaturated — claimed ones full color.
        checkpoint_grey = checkpoint_sprite.copy()
        checkpoint_grey.fill((150, 150, 160), special_flags=pygame.BLEND_RGB_MULT)
    backdrop_bands = []
    backdrop_manifest = data_dir / "backdrop" / stage_id / "manifest.json"
    if backdrop_manifest.exists():
        for rel in json.loads(backdrop_manifest.read_text()).get("band_paths", []):
            band = data_dir / rel
            if band.exists():
                raw = pygame.image.load(str(band)).convert()
                scale_f = (height * SCALE) / raw.get_height()
                backdrop_bands.append(
                    pygame.transform.smoothscale(
                        raw,
                        (int(raw.get_width() * scale_f), height * SCALE),
                    )
                )
    # Stage effects (values in stage.json; particles_falling is the v1
    # code interpreter — simple deterministic dots in the harness).
    import random as _random

    stage_json = data_dir / "stage" / stage_id / "stage.json"
    effect_dots: list = []
    if stage_json.exists():
        rng = _random.Random(0)
        for record in json.loads(stage_json.read_text()).get("effects", []):
            if record.get("name") != "particles_falling":
                continue
            params = record.get("params", {})
            effect_dots.append(
                {
                    "color": hex_rgb(str(params.get("color", "#e8e8f0"))),
                    "speed": float(params.get("speed", 80)),
                    "drift": float(params.get("drift", 0)),
                    "size": max(1, int(params.get("size", 2))),
                    "dots": [
                        [
                            rng.uniform(0, width * SCALE),
                            rng.uniform(0, height * SCALE),
                        ]
                        for _ in range(int(params.get("density", 40)))
                    ],
                }
            )

    px, py = float(spawn["x"]), float(spawn["y"])  # cell coords
    vy = 0.0
    on_ground = False
    won = False
    hearts = MAX_HEARTS
    iframes = 0.0  # post-hit invulnerability countdown
    damage_soaked = 0.0  # fractional volume drain toward the next heart
    moved = False  # first input after (re)spawn ends the spawn grace
    # Spawn SHIELD: full invincibility for a few seconds AFTER that
    # first input — enemies may legitimately camp a checkpoint; this
    # window is what keeps respawning next to one fair.
    spawn_shield = 0.0
    blink_t = 0.0  # deterministic blink clock (grace + shield + i-frames)
    live_enemies = [Enemy(p) for p in placements]

    def tile_at(cx: float, cy: float) -> int:
        ix, iy = int(cx), int(cy)
        if not (0 <= ix < width and 0 <= iy < height):
            return 0
        return int(grid[iy, ix])

    def blocked_at(x: float, y: float) -> bool:
        # Both body corners AND both body rows — mid-jump the body spans
        # two rows, and sampling only the head row let players clip
        # sideways through the first column of a wall or platform tier
        # (found in the first real-content play test).
        for cy in (y, y + 0.99):
            if (
                tile_at(x + BODY_L, cy) in BLOCKING
                or tile_at(x + BODY_R, cy) in BLOCKING
            ):
                return True
        return False

    def landing_at(x: float, y: float, prev_bottom: float) -> bool:
        for cx in (x + BODY_L, x + BODY_R):
            tile = tile_at(cx, y)
            if tile in BLOCKING:
                return True
            if tile in ONE_WAY and prev_bottom <= float(int(y)):
                return True
        return False

    def volume_params_at(x: float, y: float) -> dict | None:
        for cx in (x + BODY_L, x + BODY_R):
            params = VOLUMES.get(tile_at(cx, y))
            if params is not None:
                return params
        return None

    def respawn() -> None:
        nonlocal px, py, vy, damage_soaked, hearts, iframes, moved
        nonlocal spawn_shield
        px, py = float(respawn_point["x"]), float(respawn_point["y"])
        vy, damage_soaked = 0.0, 0.0
        hearts, iframes, moved, spawn_shield = MAX_HEARTS, 0.0, False, 0.0
        if enemy_reset:
            # Killed enemies come back on a checkpoint respawn — dying
            # never leaves a half-cleared level (GameRules kind).
            for other in live_enemies:
                other.reset()
        play_sfx("death")

    def note_move() -> None:
        """First input after a (re)spawn: the pre-move grace ends and
        the timed spawn SHIELD starts — the player can act (and be
        chased) but cannot be hurt until it runs out."""
        nonlocal moved, spawn_shield
        if not moved:
            moved = True
            if spawn_grace:
                spawn_shield = SPAWN_GRACE_S

    def hurt(cost: int) -> None:
        """One heart pool for contact and hazard hits — spawn grace,
        the spawn shield, and i-frames gate them (volume drain has its
        own path below)."""
        nonlocal hearts, iframes
        if iframes > 0.0 or spawn_shield > 0.0 or (spawn_grace and not moved):
            return
        hearts -= max(1, int(cost))
        iframes = IFRAMES_S
        if hearts <= 0:
            respawn()

    def drain(amount: float) -> None:
        """Continuous volume damage: accumulate fractions, convert each
        whole point into one heart — ignores hurt i-frames (lava keeps
        hurting) but respects spawn grace AND the spawn shield (full
        invincibility while it lasts)."""
        nonlocal damage_soaked, hearts
        if spawn_shield > 0.0 or (spawn_grace and not moved):
            return
        damage_soaked += amount
        while damage_soaked >= 1.0:
            damage_soaked -= 1.0
            hearts -= 1
            if hearts <= 0:
                respawn()
                return

    run_speed = float(movement["run_speed"])
    gravity = float(movement["gravity"])
    # Jump velocity for jump_height cells PLUS a headroom margin: discrete
    # frame integration undershoots the analytic apex, which made exactly-
    # jump_height platforms unlandable (feet never cleared the top).
    jump_v = (2.0 * gravity * (float(movement["jump_height"]) + 0.4)) ** 0.5

    # Headless verification capture — the pygame analog of Godot's
    # PLAT_LEVEL + --write-movie. PLAT_CAPTURE=<dir> runs a FIXED-dt, no-input
    # session (player holds still; spawn grace keeps it safe while aggressive
    # enemies close in — exactly what we want to SEE), saving a frame every
    # PLAT_CAPTURE_EVERY ticks for PLAT_CAPTURE_TICKS ticks, then quits. This
    # is what makes the pre-art surface frame-capturable for cross-surface
    # parity checks the same way Godot is.
    cap_dir = os.environ.get("PLAT_CAPTURE", "")
    # PLAT_TRAJ=<path> dumps every enemy's world position + alerted flag per
    # tick in the SAME format main.gd emits, so the two surfaces' movement is
    # diffable in world space (rendering-independent). Either hook runs a
    # deterministic FIXED-dt, no-input session.
    traj_path = os.environ.get("PLAT_TRAJ", "")
    headless = bool(cap_dir or traj_path)
    cap_ticks = int(os.environ.get("PLAT_CAPTURE_TICKS", "300"))
    cap_every = int(os.environ.get("PLAT_CAPTURE_EVERY", "30"))
    cap_i = 0
    if cap_dir:
        Path(cap_dir).mkdir(parents=True, exist_ok=True)
    traj_file = open(traj_path, "w") if traj_path else None  # noqa: SIM115

    running = True
    while running:
        dt = (1.0 / FPS) if headless else clock.tick(FPS) / 1000.0
        volume = volume_params_at(px, py)
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_r:
                    respawn()
                    won = False
                elif event.key in (pygame.K_SPACE, pygame.K_UP, pygame.K_w):
                    note_move()  # a jump ends the grace, starts the shield
                    down_held = (
                        pygame.key.get_pressed()[pygame.K_DOWN]
                        or pygame.key.get_pressed()[pygame.K_s]
                    )
                    if volume is not None:
                        # Submerged: small swim stroke. At the surface
                        # (open air above): a full jump — the reachability
                        # validator models volume exit by the normal jump
                        # rule, so the harness must actually deliver it.
                        if volume_params_at(px, py - 1.0) is None:
                            vy = -jump_v
                        else:
                            vy = -float(volume.get("impulse", 5.0))
                    elif on_ground and down_held and drop_through and (
                        tile_at(px + BODY_L, py + 1) in ONE_WAY
                        or tile_at(px + BODY_R, py + 1) in ONE_WAY
                    ):
                        py += 0.06  # drop through a one-way platform
                        vy, on_ground = 0.5, False
                    elif on_ground:
                        vy = -jump_v
                        play_sfx("jump")

        keys = pygame.key.get_pressed()
        dx = (keys[pygame.K_RIGHT] or keys[pygame.K_d]) - (
            keys[pygame.K_LEFT] or keys[pygame.K_a]
        )
        if dx:
            note_move()  # walking ends the grace, starts the shield
        grace = spawn_grace and not moved
        iframes = max(0.0, iframes - dt)
        spawn_shield = max(0.0, spawn_shield - dt)
        blink_t += dt
        speed = run_speed * (
            float(volume.get("speed_factor", 0.55)) if volume is not None else 1.0
        )
        new_x = px + dx * speed * dt
        if not blocked_at(new_x, py):
            px = max(0.0, min(new_x, width - 1.0))

        vy += (
            float(volume.get("gravity", 8.0)) if volume is not None else gravity
        ) * dt
        if volume is not None:
            vy = min(vy, 3.0)  # terminal sink speed in a volume
        prev_bottom = py + 0.99
        new_y = py + vy * dt
        if vy > 0 and landing_at(px, new_y + 0.99, prev_bottom):
            py = float(int(new_y + 0.99) - 1)
            vy, on_ground = 0.0, True
        elif vy < 0 and blocked_at(px, new_y):
            vy, on_ground = 0.0, False
        else:
            py, on_ground = new_y, False

        # Damaging volumes (swimmable lava): drain hearts continuously —
        # every accumulated point costs one heart; the fraction resets on
        # safe ground. (The old DAMAGE_BUDGET stand-in is gone: one heart
        # pool for everything.)
        if volume is not None:
            drain(float(volume.get("damage_per_second", 0.0)) * dt)
        else:
            damage_soaked = 0.0

        if py > height + 2:
            respawn()
        for corner in (px + BODY_L, px + BODY_R):
            hazard = HAZARDS.get(tile_at(corner, py))
            if hazard is not None:
                hurt(int(hazard.get("damage", 1)))
                break
        for enemy in live_enemies:
            enemy.update(dt, px, py)
            if not enemy.alive:
                continue
            # Size-aware touch AABB: the body is `size` cells square,
            # bottom-anchored — feet at y+1, top at y+1-size, centered
            # over its occupied columns (combat.py occupancy).
            cols = max(1, min(2, int(enemy.size)))
            e_cx = enemy.x + cols / 2.0
            e_top = enemy.y + 1.0 - enemy.size
            e_bottom = enemy.y + 1.0
            overlap_x = abs((px + 0.5) - e_cx) < 0.35 + enemy.size / 2.0
            overlap_y = py < e_bottom and py + 1.0 > e_top
            if not (overlap_x and overlap_y):
                continue
            feet = py + 1.0
            if vy > 0 and feet <= e_top + 0.35:
                # STOMP: falling onto the head band — damage the enemy,
                # bounce off it. No i-frames spent; stomping is safe.
                if enemy.stomp():
                    play_sfx("jump")  # bounce impulse; no stomp SFX in v1
                vy = -jump_v * STOMP_BOUNCE
            else:
                hurt(enemy.damage_hearts)
        if traj_file is not None:
            parts = [
                f"{e.spec.get('enemy_id', '')}:{e.x:.3f}:{e.y:.3f}:{1 if e.alerted else 0}"
                for e in live_enemies
            ]
            traj_file.write(f"{cap_i}|{','.join(parts)}\n")
        # Crossing a checkpoint moves the respawn point (3b triggers).
        for checkpoint in checkpoints:
            if (
                not checkpoint["active"]
                and int(px) == checkpoint["x"]
                and int(py) == checkpoint["y"]
            ):
                checkpoint["active"] = True
                respawn_point = {"x": checkpoint["x"], "y": checkpoint["y"]}
                play_sfx("checkpoint")
        # Exit zone: the exit's whole COLUMN, any height (leave right).
        if not won and int(px) == exit_["x"]:
            won = True
            play_sfx("win")

        screen.fill(tile_colors.get(0, (24, 24, 32)))
        for band in backdrop_bands:  # static scenery, far → near
            for bx in range(0, width * SCALE, band.get_width()):
                screen.blit(band, (bx, 0))
        for y in range(height):
            for x in range(width):
                t = int(grid[y, x])
                if t:
                    pygame.draw.rect(
                        screen,
                        tile_colors.get(t, (255, 0, 255)),
                        (x * SCALE, y * SCALE, SCALE, SCALE),
                    )
        # Exit GOAL object on the exit cell (the level visibly ends here;
        # the exit zone is still the whole column) — sprite or a drawn
        # green doorway. Mirrors main.gd's props.
        exit_foot = ((exit_["x"] + 0.5) * SCALE, (exit_["y"] + 1) * SCALE)
        if exit_sprite is not None:
            screen.blit(
                exit_sprite,
                (exit_foot[0] - SCALE, exit_foot[1] - SCALE * 2),
            )
        else:
            door = pygame.Rect(0, 0, int(SCALE * 1.1), int(SCALE * 1.8))
            door.midbottom = (int(exit_foot[0]), int(exit_foot[1]))
            glow = pygame.Surface(door.size, pygame.SRCALPHA)
            glow.fill((64, 255, 112, 90))
            screen.blit(glow, door.topleft)
            pygame.draw.rect(screen, (64, 255, 112), door, 2)
        # Checkpoint FLAGS: grey until claimed, colored after — sprite
        # (desaturated copy) or a drawn pole + pennant.
        for checkpoint in checkpoints:
            foot = (
                (checkpoint["x"] + 0.5) * SCALE,
                (checkpoint["y"] + 1) * SCALE,
            )
            if checkpoint_sprite is not None:
                image = (
                    checkpoint_sprite if checkpoint["active"] else checkpoint_grey
                )
                screen.blit(
                    image,
                    (foot[0] - SCALE * 0.75, foot[1] - SCALE * 1.5),
                )
                continue
            color = (
                (255, 210, 74) if checkpoint["active"] else (140, 140, 153)
            )
            pygame.draw.line(
                screen, (107, 87, 71),
                (foot[0], foot[1]), (foot[0], foot[1] - SCALE * 1.5), 2,
            )
            pygame.draw.polygon(
                screen, color,
                [
                    (foot[0], foot[1] - SCALE * 1.5),
                    (foot[0] + SCALE * 0.55, foot[1] - SCALE * 1.28),
                    (foot[0], foot[1] - SCALE * 1.06),
                ],
            )
        for enemy in live_enemies:
            if not enemy.alive:
                continue
            # Bottom-anchored, column-centered: a sized body grows UP
            # from its anchor cell (matches the touch AABB and the
            # placement footprint — never into the floor).
            cols = max(1, min(2, int(enemy.size)))
            side = (SCALE - 4) * enemy.size
            center_x = (enemy.x + cols / 2.0) * SCALE
            rect = (
                center_x - side / 2.0,
                (enemy.y + 1.0) * SCALE - 2 - side,
                side,
                side,
            )
            sprite = enemy_sprites.get(enemy.spec.get("enemy_id", ""))
            if enemy.hurt_t > 0 and int(enemy.hurt_t * 20) % 2 == 0:
                pygame.draw.rect(screen, (255, 255, 255), rect)  # stomp flash
            elif sprite is not None:
                image = sprite
                if enemy.size != 1.0:
                    image = pygame.transform.smoothscale(
                        sprite,
                        (int(rect[2]), int(rect[3])),
                    )
                if enemy.direction < 0:
                    image = pygame.transform.flip(image, True, False)
                screen.blit(image, (rect[0], rect[1]))
            else:
                pygame.draw.rect(screen, enemy.color, rect)
            if enemy.outlined:  # variant marker (§6.1 overrides)
                pygame.draw.rect(
                    screen, (255, 255, 255),
                    (
                        center_x - (SCALE * enemy.size) / 2.0,
                        (enemy.y + 1.0) * SCALE - SCALE * enemy.size,
                        SCALE * enemy.size,
                        SCALE * enemy.size,
                    ),
                    2,
                )
        # Spawn grace / shield / i-frames: the player BLINKS
        # (intermittently invisible) the whole time they are untouchable.
        blinking = (
            grace or spawn_shield > 0 or iframes > 0
        ) and int(blink_t * 8) % 2 == 0
        if not blinking:
            if player_sprite is not None:
                screen.blit(player_sprite, (px * SCALE + 4, py * SCALE + 4))
            else:
                pygame.draw.rect(
                    screen, (240, 240, 240),
                    (px * SCALE + 4, py * SCALE + 4, SCALE - 8, SCALE - 8),
                )
        # Foreground decor — drawn AFTER the player: in front, per §6.2.
        for decor in level.get("foreground", []):
            cx = decor["x"] * SCALE + SCALE // 2
            cy = decor["y"] * SCALE + SCALE // 2
            pygame.draw.polygon(
                screen, (185, 195, 205),
                [(cx, cy - 10), (cx + 8, cy), (cx, cy + 10), (cx - 8, cy)],
            )
        # Ambient stage effects on top of everything (screen-space).
        for effect in effect_dots:
            for dot in effect["dots"]:
                dot[0] = (dot[0] + effect["drift"] * dt * SCALE / 32.0) % (
                    width * SCALE
                )
                dot[1] = (dot[1] + effect["speed"] * dt * SCALE / 32.0) % (
                    height * SCALE
                )
                pygame.draw.circle(
                    screen, effect["color"],
                    (int(dot[0]), int(dot[1])), effect["size"],
                )
        # Hearts HUD (screen-space, top-left) — the one damage currency.
        for i in range(MAX_HEARTS):
            heart_rect = (16 + i * 24, 10, 18, 18)
            if i < hearts:
                pygame.draw.rect(screen, (214, 61, 74), heart_rect)
            else:
                pygame.draw.rect(screen, (110, 48, 56), heart_rect, 2)
        if won:
            screen.blit(
                font.render("LEVEL COMPLETE — R to reset", True, (64, 255, 112)),
                (16, 36),
            )
        pygame.display.flip()

        if headless:
            if cap_dir and cap_i % cap_every == 0:
                pygame.image.save(screen, f"{cap_dir}/frame_{cap_i:04d}.png")
            cap_i += 1
            if cap_i >= cap_ticks:
                running = False

    if traj_file is not None:
        traj_file.close()
    pygame.quit()


if __name__ == "__main__":
    main()
