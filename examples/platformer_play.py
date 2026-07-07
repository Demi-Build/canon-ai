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
Touch a hazard or fall off: respawn (at the last checkpoint you crossed).
Linger in a damaging volume (lava): respawn. Reach the green exit: level
complete.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCALE = 32
FPS = 60
#: Damage a player can soak in a damaging volume before respawning —
#: crude stand-in for HP so lava reads "quick dips only" (throwaway).
DAMAGE_BUDGET = 3.0


def _load(data_dir: Path, level_id: str):
    import numpy as np

    manifest = json.loads((data_dir / "manifest.json").read_text())
    stage_id = manifest["stage_id"]
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
    same resolution path the renderer and Godot use."""
    from PIL import Image

    sheet = Image.open(data_dir / tileset["tilesheet_path"]).convert("RGB")
    colors = {}
    for slot in tileset["slots"]:
        x, y, _w, _h = slot["px_region"]
        colors[int(slot["tile_type"])] = sheet.getpixel((x + 1, y + 1))
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
    HAZARD = _types_with("hazard")
    #: tile id → volume params (speed_factor/gravity/impulse/damage_per_second)
    VOLUMES = {
        int(s["tile_type"]): dict(s.get("params") or {})
        for s in tileset["slots"]
        if s.get("collision") == "volume"
    }

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
    #: Variant vocabulary from the manifest — consumers never hardcode
    #: what "elite" or "champion" means.
    variant_defs = {v["name"]: v for v in manifest.get("variants", [])}

    class Enemy:
        def __init__(self, placement: dict) -> None:
            self.spec = enemies[placement["enemy_id"]]
            self.x = float(placement["x"])
            self.y = float(placement["y"])
            self.home = float(placement["x"])
            self.direction = 1.0
            self.variant = variant_defs.get(str(placement.get("variant", "")))
            speed_mult = self.variant.get("speed_mult", 1.0) if self.variant else 1.0
            self.speed = float(self.spec["stats"].get("speed", 0)) * speed_mult
            self.size = float(self.variant.get("size", 1.0)) if self.variant else 1.0
            visual = self.variant.get("visual", "outline") if self.variant else ""
            self.outlined = "outline" in visual
            self.color = hex_rgb(self.spec["stats"].get("placeholder_color", "#ff00ff"))
            behavior = dict(self.spec["behavior"])
            if self.variant:
                behavior.update(self.variant.get("behavior", {}))
            self.behavior = behavior

        def _can_occupy(self, x: float) -> bool:
            """Terrain constraint for the NEXT step (GameRules-aware):
            swimmers stay in their volume; land enemies keep solid footing
            and — under swimmers_only/forbidden — never enter a volume."""
            cell = tile_at(x, self.y)
            below = tile_at(x, self.y + 1)
            if self.spec.get("archetype") == "swimmer":
                return cell in VOLUMES
            if cell in VOLUMES and water_policy != "amphibious":
                return False
            return below in BLOCKING or below in ONE_WAY  # no cliff-walking

        def update(self, dt: float, player_x: float) -> None:
            archetype = self.spec.get("archetype", "sentry")
            if archetype in ("patroller", "swimmer") and self.speed > 0:
                new_x = self.x + self.direction * self.speed * dt
                if (
                    abs(new_x - self.home) >= self.behavior.get("patrol_range", 4)
                    or not self._can_occupy(new_x)
                ):
                    self.direction *= -1.0
                else:
                    self.x = new_x
            elif archetype == "chaser" and self.speed > 0:
                if abs(player_x - self.x) <= self.behavior.get("aggro_range", 6):
                    new_x = self.x + (1.0 if player_x > self.x else -1.0) * self.speed * dt
                    if self._can_occupy(new_x):  # halts at volume/cliff edges
                        self.x = new_x
            # sentry: stationary by definition

    pygame.init()
    screen = pygame.display.set_mode((width * SCALE, height * SCALE))
    pygame.display.set_caption(
        f"{manifest['world']} — {level_id}  [placeholder review build]"
    )
    clock = pygame.time.Clock()
    font = pygame.font.SysFont(None, 28)

    px, py = float(spawn["x"]), float(spawn["y"])  # cell coords
    vy = 0.0
    on_ground = False
    won = False
    damage_soaked = 0.0
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
        nonlocal px, py, vy, damage_soaked
        px, py = float(respawn_point["x"]), float(respawn_point["y"])
        vy, damage_soaked = 0.0, 0.0

    run_speed = float(movement["run_speed"])
    gravity = float(movement["gravity"])
    # Jump velocity for jump_height cells PLUS a headroom margin: discrete
    # frame integration undershoots the analytic apex, which made exactly-
    # jump_height platforms unlandable (feet never cleared the top).
    jump_v = (2.0 * gravity * (float(movement["jump_height"]) + 0.4)) ** 0.5

    running = True
    while running:
        dt = clock.tick(FPS) / 1000.0
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

        keys = pygame.key.get_pressed()
        dx = (keys[pygame.K_RIGHT] or keys[pygame.K_d]) - (
            keys[pygame.K_LEFT] or keys[pygame.K_a]
        )
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

        # Damaging volumes (swimmable lava): drain a small damage budget,
        # then respawn — reset when back on safe ground.
        if volume is not None:
            damage_soaked += float(volume.get("damage_per_second", 0.0)) * dt
            if damage_soaked >= DAMAGE_BUDGET:
                respawn()
        else:
            damage_soaked = 0.0

        if py > height + 2:
            respawn()
        if tile_at(px + BODY_L, py) in HAZARD or tile_at(px + BODY_R, py) in HAZARD:
            respawn()
        for enemy in live_enemies:
            enemy.update(dt, px)
            if abs(enemy.x - px) < 0.7 and abs(enemy.y - py) < 0.7:
                respawn()
        # Crossing a checkpoint moves the respawn point (3b triggers).
        for checkpoint in checkpoints:
            if (
                not checkpoint["active"]
                and int(px) == checkpoint["x"]
                and int(py) == checkpoint["y"]
            ):
                checkpoint["active"] = True
                respawn_point = {"x": checkpoint["x"], "y": checkpoint["y"]}
        if int(px) == exit_["x"] and int(py) == exit_["y"]:
            won = True

        screen.fill(tile_colors.get(0, (24, 24, 32)))
        for y in range(height):
            for x in range(width):
                t = int(grid[y, x])
                if t:
                    pygame.draw.rect(
                        screen,
                        tile_colors.get(t, (255, 0, 255)),
                        (x * SCALE, y * SCALE, SCALE, SCALE),
                    )
        pygame.draw.rect(
            screen, (64, 255, 112),
            (exit_["x"] * SCALE + 4, exit_["y"] * SCALE + 4, SCALE - 8, SCALE - 8), 3,
        )
        for checkpoint in checkpoints:
            pygame.draw.rect(
                screen, (255, 210, 74),
                (
                    checkpoint["x"] * SCALE + 4, checkpoint["y"] * SCALE + 4,
                    SCALE - 8, SCALE - 8,
                ),
                0 if checkpoint["active"] else 3,
            )
        for enemy in live_enemies:
            half_extra = (enemy.size - 1.0) * SCALE / 2.0
            rect = (
                enemy.x * SCALE + 2 - half_extra,
                enemy.y * SCALE + 2 - half_extra,
                (SCALE - 4) * enemy.size,
                (SCALE - 4) * enemy.size,
            )
            pygame.draw.rect(screen, enemy.color, rect)
            if enemy.outlined:  # variant marker (§6.1 overrides)
                pygame.draw.rect(
                    screen, (255, 255, 255),
                    (
                        enemy.x * SCALE - half_extra,
                        enemy.y * SCALE - half_extra,
                        SCALE * enemy.size,
                        SCALE * enemy.size,
                    ),
                    2,
                )
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
        if won:
            screen.blit(
                font.render("LEVEL COMPLETE — R to reset", True, (64, 255, 112)),
                (16, 12),
            )
        pygame.display.flip()

    pygame.quit()


if __name__ == "__main__":
    main()
