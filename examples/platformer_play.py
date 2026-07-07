"""THROWAWAY pygame review harness — filler code, not the game.

Exists solely to simulate what the generated databases look like in motion:
tiles resolve through the Tileset artifact, enemy squares through
EnemyDefinition placeholder colors, and enemy movement executes the rolled
behavior params. The real platformer ships in Godot (Phase 4); this file
dies when that lives. Do not polish it.

    uv run --extra platformer --extra play \
        python examples/platformer_play.py <data_dir> [level_id]

Controls: arrows/A-D move, space/up jumps, R respawns, Esc quits.
Touch a spike or fall off: respawn. Reach the green exit: level complete.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCALE = 32
FPS = 60


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
    same resolution path the renderer and (later) Godot use."""
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

    # Physics semantics from the tileset manifest (Appendix E.3) — no
    # hardcoded tile IDs; real art later ships its own semantics here.
    def _types_with(collision: str) -> set:
        return {
            int(s["tile_type"]) for s in tileset["slots"]
            if s.get("collision") == collision
        }

    BLOCKING = _types_with("solid")
    ONE_WAY = _types_with("one_way")
    HAZARD = _types_with("hazard")
    WATER = _types_with("water")

    spawn = {"x": level["spawn"][0], "y": level["spawn"][1]}
    exit_ = {"x": level["exit"][0], "y": level["exit"][1]}

    def hex_rgb(h: str) -> tuple:
        h = h.lstrip("#")
        return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))

    class Enemy:
        def __init__(self, placement: dict) -> None:
            self.spec = enemies[placement["enemy_id"]]
            self.x = float(placement["x"])
            self.y = float(placement["y"])
            self.home = float(placement["x"])
            self.direction = 1.0
            self.elite = bool(placement.get("elite", False))
            self.color = hex_rgb(self.spec["stats"].get("placeholder_color", "#ff00ff"))

        def update(self, dt: float, player_x: float) -> None:
            behavior = self.spec["behavior"]
            speed = float(self.spec["stats"].get("speed", 0))
            archetype = self.spec.get("archetype", "sentry")
            # swimmers patrol their pool exactly like patrollers patrol land
            if archetype in ("patroller", "swimmer") and speed > 0:
                self.x += self.direction * speed * dt
                if abs(self.x - self.home) >= behavior.get("patrol_range", 4):
                    self.direction *= -1.0
            elif archetype == "chaser" and speed > 0:
                if abs(player_x - self.x) <= behavior.get("aggro_range", 6):
                    self.x += (1.0 if player_x > self.x else -1.0) * speed * dt
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
    live_enemies = [Enemy(p) for p in placements]

    def tile_at(cx: float, cy: float) -> int:
        ix, iy = int(cx), int(cy)
        if not (0 <= ix < width and 0 <= iy < height):
            return 0
        return int(grid[iy, ix])

    def blocked_at(x: float, y: float) -> bool:
        # Both body corners; PLATFORM is one-way and never blocks sideways.
        return tile_at(x + BODY_L, y) in BLOCKING or tile_at(x + BODY_R, y) in BLOCKING

    def landing_at(x: float, y: float, prev_bottom: float) -> bool:
        for cx in (x + BODY_L, x + BODY_R):
            tile = tile_at(cx, y)
            if tile in BLOCKING:
                return True
            if tile in ONE_WAY and prev_bottom <= float(int(y)):
                return True
        return False

    def in_water(x: float, y: float) -> bool:
        return tile_at(x + BODY_L, y) in WATER or tile_at(x + BODY_R, y) in WATER

    def respawn() -> None:
        nonlocal px, py, vy
        px, py, vy = float(spawn["x"]), float(spawn["y"]), 0.0

    run_speed = float(movement["run_speed"])
    gravity = float(movement["gravity"])
    water_factor = float(movement.get("water_speed_factor", 0.55))
    water_gravity = float(movement.get("water_gravity", 8.0))
    swim_impulse = float(movement.get("swim_impulse", 5.0))
    # Initial jump velocity to rise exactly jump_height cells: v = sqrt(2gh)
    jump_v = (2.0 * gravity * float(movement["jump_height"])) ** 0.5

    running = True
    while running:
        dt = clock.tick(FPS) / 1000.0
        swimming = in_water(px, py)
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
                    if swimming:
                        vy = -swim_impulse  # swim stroke, works anywhere in water
                    elif on_ground:
                        vy = -jump_v

        keys = pygame.key.get_pressed()
        dx = (keys[pygame.K_RIGHT] or keys[pygame.K_d]) - (
            keys[pygame.K_LEFT] or keys[pygame.K_a]
        )
        speed = run_speed * (water_factor if swimming else 1.0)
        new_x = px + dx * speed * dt
        if not blocked_at(new_x, py):
            px = max(0.0, min(new_x, width - 1.0))

        vy += (water_gravity if swimming else gravity) * dt
        if swimming:
            vy = min(vy, 3.0)  # terminal sink speed in water
        prev_bottom = py + 0.99
        new_y = py + vy * dt
        if vy > 0 and landing_at(px, new_y + 0.99, prev_bottom):
            py = float(int(new_y + 0.99) - 1)
            vy, on_ground = 0.0, True
        elif vy < 0 and blocked_at(px, new_y):
            vy, on_ground = 0.0, False
        else:
            py, on_ground = new_y, False

        if py > height + 2:
            respawn()
        if tile_at(px + BODY_L, py) in HAZARD or tile_at(px + BODY_R, py) in HAZARD:
            respawn()
        for enemy in live_enemies:
            enemy.update(dt, px)
            if abs(enemy.x - px) < 0.7 and abs(enemy.y - py) < 0.7:
                respawn()
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
        for enemy in live_enemies:
            pygame.draw.rect(
                screen, enemy.color,
                (enemy.x * SCALE + 2, enemy.y * SCALE + 2, SCALE - 4, SCALE - 4),
            )
            if enemy.elite:  # elite variation marker (§6.1 overrides)
                pygame.draw.rect(
                    screen, (255, 255, 255),
                    (enemy.x * SCALE, enemy.y * SCALE, SCALE, SCALE), 2,
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
