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
    SOLID = {1, 2, 3}  # FLOOR, PLATFORM, WALL (PLATFORM treated solid-simple)
    SPIKE = 10

    spawn = next(t for t in level["triggers"] if t["type"] == "spawn")
    exit_ = next(t for t in level["triggers"] if t["type"] == "exit")

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
            self.color = hex_rgb(self.spec["stats"].get("placeholder_color", "#ff00ff"))

        def update(self, dt: float, player_x: float) -> None:
            behavior = self.spec["behavior"]
            speed = float(self.spec["stats"].get("speed", 0))
            archetype = self.spec.get("archetype", "sentry")
            if archetype == "patroller" and speed > 0:
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

    def solid_at(cx: float, cy: float) -> bool:
        ix, iy = int(cx), int(cy)
        if not (0 <= ix < width and 0 <= iy < height):
            return False
        return int(grid[iy, ix]) in SOLID

    def respawn() -> None:
        nonlocal px, py, vy
        px, py, vy = float(spawn["x"]), float(spawn["y"]), 0.0

    run_speed = float(movement["run_speed"])
    gravity = float(movement["gravity"])
    # Initial jump velocity to rise exactly jump_height cells: v = sqrt(2gh)
    jump_v = (2.0 * gravity * float(movement["jump_height"])) ** 0.5

    running = True
    while running:
        dt = clock.tick(FPS) / 1000.0
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_r:
                    respawn()
                    won = False
                elif event.key in (pygame.K_SPACE, pygame.K_UP, pygame.K_w) and on_ground:
                    vy = -jump_v

        keys = pygame.key.get_pressed()
        dx = (keys[pygame.K_RIGHT] or keys[pygame.K_d]) - (
            keys[pygame.K_LEFT] or keys[pygame.K_a]
        )
        new_x = px + dx * run_speed * dt
        if not solid_at(new_x, py):
            px = max(0.0, min(new_x, width - 1.0))

        vy += gravity * dt
        new_y = py + vy * dt
        if vy > 0 and solid_at(px, new_y + 0.99):
            py = float(int(new_y + 0.99) - 1)
            vy, on_ground = 0.0, True
        elif vy < 0 and solid_at(px, new_y):
            vy, on_ground = 0.0, False
        else:
            py, on_ground = new_y, False

        if py > height + 2:
            respawn()
        if int(grid[min(int(py), height - 1), int(px)]) == SPIKE:
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
        pygame.draw.rect(
            screen, (240, 240, 240),
            (px * SCALE + 4, py * SCALE + 4, SCALE - 8, SCALE - 8),
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
