"""THROWAWAY pygame review harness — filler code, not the game.

Exists solely to simulate what the generated databases look like in motion:
tiles resolve through the Tileset artifact (categories + params on slots),
enemy squares through EnemyDefinition placeholder colors + the manifest's
variant vocabulary, and enemy movement executes the rolled behavior params.
The real platformer ships in Godot (Phase 4); this file dies when that
lives. Do not polish it.

    uv run --extra platformer --extra play \
        python examples/platformer_play.py <data_dir> [level_id]

ANIMATION VIEWER: ``PLAT_ANIM=<enemy:id|item:id|player|all>`` boots a preview
of the actor's animation states instead of a level — every state side by side
on its own clock and loop mode, over a baseline the frames are anchored to. It
reuses the game's own loaders and frame selection, so what it shows is what the
game plays. SPACE replays the ``once`` states, arrows switch actors, and it
composes with PLAT_CAPTURE for a headless contact strip:

    PLAT_ANIM=enemy:ember_hopper PLAT_CAPTURE=shots \
        python examples/platformer_play.py <data_dir>

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
import re
import sys
from pathlib import Path


def _sign(v: float) -> float:
    """Mirror of GDScript ``signf``: 1.0 / -1.0 / 0.0 (0 for exactly 0)."""
    return 1.0 if v > 0 else (-1.0 if v < 0 else 0.0)

SCALE = 32
FPS = 60
#: PLAT_PLAIN=1 — play WITHOUT art: palette-role tile blocks (the editor's
#: Blocks view), placeholder shapes for sprites/items/props, no backdrop.
#: Physics identical; purely a presentation switch for layout feel-checks.
_PLAIN = bool(os.environ.get("PLAT_PLAIN"))
# Landing one-shot window (presentation only): armed on the airborne →
# grounded EDGE, read by the anim candidates alone. main.gd shares the value.
LAND_ANIM_S = 0.15


def _stage_for(manifest: dict, level_id: str) -> str:
    """The biome stage owning ``level_id`` (manifest v2 "stages"). A
    SECRET ROOM id (multi-room arc — ``l3r1``, never in any stage's
    levels list) resolves to its parent's stage."""
    for stage_entry in manifest.get("stages", []):
        if level_id in stage_entry.get("levels", []):
            return str(stage_entry["stage_id"])
    m = re.fullmatch(r"(.+\d)r\d+", level_id)
    if m:
        return _stage_for(manifest, m.group(1))
    raise SystemExit(
        f"level {level_id!r} not in this world — levels: "
        f"{manifest.get('levels')}"
    )


def _active_music(level: dict, player_cell: int, stage_music: str) -> str:
    """The output-relative music file to play at the player's cell along the
    level's layout axis (columns horizontal / rows vertical): an active
    user-authored ``music_sections`` entry wins (its ``music_path``, which may
    be ``""`` = a deliberate silent zone), else the level's own ``music_path``,
    else the ``stage_music`` default. Pure — main.gd ports this VERBATIM so
    music switches at identical cells on both surfaces."""
    cell = int(player_cell)
    for sec in level.get("music_sections") or []:
        if int(sec.get("start", 0)) <= cell < int(sec.get("end", 0)):
            return str(sec.get("music_path") or "")
    return str(level.get("music_path") or "") or (stage_music or "")


def _load(data_dir: Path, level_id: str):
    import numpy as np

    manifest = json.loads((data_dir / "manifest.json").read_text())
    stage_id = _stage_for(manifest, level_id)
    level_dir = data_dir / "level" / stage_id / level_id
    level = json.loads((level_dir / "level.json").read_text())
    with np.load(level_dir / "collision.npz") as data:
        grid = data["collision"].copy()  # writable — breakable floors mutate it
    enemies = {
        p.stem: json.loads(p.read_text())
        for p in (data_dir / "enemy").glob("*.json")
    }
    placements = json.loads((level_dir / "entities.json").read_text())
    item_defs = {
        p.stem: json.loads(p.read_text())
        for p in (data_dir / "item").glob("*.json")
    }
    items_path = level_dir / "items.json"
    item_placements = (
        json.loads(items_path.read_text()) if items_path.exists() else []
    )
    tileset = json.loads(
        (data_dir / "tileset" / stage_id / "manifest.json").read_text()
    )
    return (
        manifest, level, grid, enemies, placements, tileset,
        item_defs, item_placements,
    )


def _tile_colors(
    data_dir: Path, tileset: dict, manifest: dict | None = None
) -> dict[int, tuple]:
    """Resolve tile colors by sampling the tilesheet via the Tileset slots —
    same resolution path the renderer and Godot use. Each sample is the
    tile REGION's average, so generated textures resolve to their
    palette-conformed mean, not one arbitrary pixel.

    PLAT_PLAIN skips the sheet entirely: palette-ROLE colors per tile type
    (the editor's Blocks view), so layout reads as pure blocks."""
    if _PLAIN:
        def hex_rgb(h: str) -> tuple:
            h = str(h).lstrip("#")
            return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))

        palette = tileset.get("palette") or {}
        roles = {
            int(t["id"]): str(t.get("color_role", "ground"))
            for t in (manifest or {}).get("tiles", [])
        }
        return {
            int(slot["tile_type"]): hex_rgb(
                palette.get(roles.get(int(slot["tile_type"]), "ground"), "#888888")
            )
            for slot in tileset["slots"]
        }

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


def _tile_textures(sheet, tileset: dict, size: int) -> dict:
    """Real tilesheet crops per tile TYPE for the art view — the harness's
    lift above flat region-average blocks.

    One representative slot per type is cropped from the sheet and scaled to
    the cell: the fully-surrounded autotile variant (max ``autotile_mask``)
    when the sheet carries the 16-variant set, else the type's only slot.
    We DON'T per-cell autotile — Godot stays the autotiled art surface of
    record; a single faithful crop per type is all the review harness needs
    to read as its texture (a light palette's near-white floor stops
    rendering as a blank square). Keyed by tile_type so the draw loop, which
    owns the collision grid and every runtime mutation (crumbles, box
    overlays), blits a texture wherever it used to fill a flat rect.
    ``sheet`` is a display-converted pygame Surface; returns
    ``{tile_type: Surface}`` (empty when PLAT_PLAIN — blocks view stays
    flat palette colors)."""
    import pygame

    if _PLAIN:
        return {}
    best: dict[int, dict] = {}
    for slot in tileset["slots"]:
        tt = int(slot["tile_type"])
        if tt == 0:
            continue  # empty draws as the background fill, never a tile
        mask = int((slot.get("params") or {}).get("autotile_mask", 0))
        if tt not in best or mask > best[tt]["mask"]:
            best[tt] = {"mask": mask, "region": slot["px_region"]}
    surfs: dict = {}
    sw, sh = sheet.get_size()
    for tt, info in best.items():
        x, y, w, h = (int(v) for v in info["region"])
        if x < 0 or y < 0 or x + w > sw or y + h > sh or w <= 0 or h <= 0:
            continue  # region outside the sheet — flat-color fallback
        sub = sheet.subsurface(pygame.Rect(x, y, w, h))
        surfs[tt] = (
            sub.copy()
            if (w, h) == (size, size)
            else pygame.transform.smoothscale(sub, (size, size))
        )
    return surfs


def _walk_durs(t: float, durs: list) -> int:
    """Cumulative-duration walk: the frame whose window contains ``t``."""
    acc = 0.0
    for i, d in enumerate(durs):
        acc += d
        if t < acc:
            return i
    return len(durs) - 1


def _anim_index(t: float, durs: list, loop_mode: str) -> int:
    """Pure frame-index math for one state (mirror of main.gd's
    ``_anim_index``): per-frame durations walked cumulatively. "loop" (and
    any unknown mode) wraps — uniform lists keep the legacy
    ``int(t / dur) % n`` arithmetic bit-exact for old trees; "once" clamps
    at the last frame; "ping_pong" walks 0..n-1..1 on the mirrored cycle.
    The loader floors every duration at 1ms, so the sums never hit zero."""
    n = len(durs)
    if n <= 1:
        return 0
    if loop_mode == "once":
        if t >= sum(durs):
            return n - 1
        return _walk_durs(t, durs)
    if loop_mode == "ping_pong":
        cycle = list(durs) + durs[-2:0:-1]
        i = _walk_durs(t % sum(cycle), cycle)
        return i if i < n else 2 * (n - 1) - i
    if all(d == durs[0] for d in durs):
        return int(t / durs[0]) % n
    return _walk_durs(t % sum(durs), durs)


def _anim_timing(meta: dict, n: int) -> tuple[list, str]:
    """Per-frame durations (seconds, floored at 1ms) + loop mode for one
    frames.json / atlas.json state entry. A "durations_ms" list is honored
    only when its length matches the frame count (the writer guarantees it;
    a desynced hand-edit falls back loudly-uniform). Entries without the
    new keys keep today's behavior exactly: one scalar duration fanned out,
    modulo looping. Mirror of main.gd's ``_anim_durs``."""
    raw = meta.get("durations_ms")
    if isinstance(raw, list) and len(raw) == n:
        durs = [max(0.001, float(d) / 1000.0) for d in raw]
    else:
        durs = [max(0.001, float(meta.get("duration_ms", 120)) / 1000.0)] * n
    return durs, str(meta.get("loop", "loop"))


def pick_anim_frame(
    candidates: list, anim_t: float, states: dict
) -> tuple[str, int]:
    """Pure state + frame-index selection for sprite animation (enemy OR player).

    ``candidates`` is the state-priority list the caller builds from the tracked
    runtime signals — the first candidate that exists in ``states`` wins.
    ``states`` maps state → ``{"durs": [float_seconds, ...], "loop": str}``
    (per-frame durations + loop mode; extra keys like the frame surfaces ride
    along untouched). The index is a deterministic function of the accumulated
    clock — the CALLER owns the state-change latch that zeroes the clock so
    "once" states restart. Reference for main.gd's GDScript mirror
    (``_anim_pick`` + ``_anim_index``).
    """
    state = next((s for s in candidates if s in states), next(iter(states)))
    info = states[state]
    return state, _anim_index(anim_t, info["durs"], info.get("loop", "loop"))


class _Hooks:
    """Once-per-process verification-harness state (the PLAT_* env
    protocol). The traj file and frame counters OUTLIVE a level load —
    Godot's ``_traj_frame`` is monotonic across ``_load_level``, and the
    pygame mirror must be too (the multi-room arc switches sub-maps
    mid-run; a per-room reopen/reset would break traj diffing)."""

    def __init__(self) -> None:
        # Headless verification capture — the pygame analog of Godot's
        # PLAT_LEVEL + --write-movie. PLAT_CAPTURE=<dir> runs a FIXED-dt,
        # no-input session saving a frame every PLAT_CAPTURE_EVERY ticks
        # for PLAT_CAPTURE_TICKS ticks, then quits.
        self.cap_dir = os.environ.get("PLAT_CAPTURE", "")
        # PLAT_TRAJ=<path> dumps player + enemy world positions per tick
        # in the SAME format main.gd emits (movement diffable in world
        # space, rendering-independent).
        self.traj_path = os.environ.get("PLAT_TRAJ", "")
        # PLAT_HOLD drives a fixed input ("right"/"left"/"run_right"/
        # "run_left"); jumps every PLAT_HOLD_JUMP_EVERY ticks (0 = never).
        self.hold_mode = os.environ.get("PLAT_HOLD", "")
        self.hold_jump_every = int(os.environ.get("PLAT_HOLD_JUMP_EVERY", "0"))
        self.cap_ticks = int(os.environ.get("PLAT_CAPTURE_TICKS", "300"))
        self.cap_every = int(os.environ.get("PLAT_CAPTURE_EVERY", "30"))
        # PLAT_ACTIONS="<frame>:<down|up>,..." — scripted SINGLE-FRAME
        # inputs the PLAT_HOLD vocabulary can't express: "down" holds the
        # Down key for exactly that frame (pipe entry / drop-through),
        # "up" presses jump/Up (door entry). Frame numbers are the traj
        # line numbers, identical on both surfaces. main.gd mirrors this.
        self.actions: dict[int, str] = {}
        for token in filter(None, os.environ.get("PLAT_ACTIONS", "").split(",")):
            frame_s, _, act = token.partition(":")
            self.actions[int(frame_s)] = act.strip()
        # PLAT_ANIM=<enemy:id|item:id|player|all> boots the ANIMATION VIEWER
        # instead of the level: every state of an actor playing side by side in
        # the same surface that renders the game. Composes with PLAT_CAPTURE.
        self.anim_target = os.environ.get("PLAT_ANIM", "")
        self.headless = bool(self.cap_dir or self.traj_path)
        self.cap_i = 0
        self.frame_i = -1
        # The music track (output-relative path) currently loaded — a switch
        # that resolves to the SAME track (room in the same stage, or staying
        # inside a music section) must NOT restart it. None = nothing decided
        # yet, so the first resolve always applies.
        self.music_cur: str | None = None
        self.traj_file = (
            open(self.traj_path, "w") if self.traj_path else None  # noqa: SIM115
        )
        if self.cap_dir:
            Path(self.cap_dir).mkdir(parents=True, exist_ok=True)


def _slice_strip(path: Path, n: int, size: tuple) -> list:
    import pygame

    sheet = pygame.image.load(str(path)).convert_alpha()
    fw = sheet.get_width() // n
    return [
        pygame.transform.smoothscale(
            sheet.subsurface((i * fw, 0, fw, sheet.get_height())), size
        )
        for i in range(n)
    ]


def _atlas_frames(sheet, rects: list, fsize: tuple, size: tuple) -> list:
    import pygame

    # Reconstitute each UNTRIMMED frame: blit the trimmed crop at its
    # (ox, oy) offset on a transparent frame_size square (the inline
    # equivalent of tileset_art's reconstitute_frame), then scale.
    frames = []
    for r in rects:
        frame = pygame.Surface(fsize, pygame.SRCALPHA)
        frame.blit(
            sheet.subsurface(
                (
                    int(r.get("x", 0)), int(r.get("y", 0)),
                    int(r.get("w", 0)), int(r.get("h", 0)),
                )
            ),
            (int(r.get("ox", 0)), int(r.get("oy", 0))),
        )
        frames.append(pygame.transform.smoothscale(frame, size))
    return frames


def _load_atlas_anim(data_dir: Path, sprite_dir: str, size: tuple) -> dict | None:
    import pygame

    meta_path = data_dir / (sprite_dir + "/atlas.json")
    if not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text())
    except (OSError, ValueError):
        return None
    fsize = meta.get("frame_size") or []
    sheet_path = data_dir / str(meta.get("path", ""))
    if (
        not isinstance(meta.get("states"), dict)
        or len(fsize) != 2
        or not str(meta.get("path", ""))
        or not sheet_path.exists()
    ):
        return None
    try:
        sheet = pygame.image.load(str(sheet_path)).convert_alpha()
        fsize = (int(fsize[0]), int(fsize[1]))
        anim: dict = {}
        for state, m in meta["states"].items():
            rects = m.get("frames") or []
            if not rects:
                continue
            left_rects = m.get("frames_left") or []
            durs, loop = _anim_timing(m, len(rects))
            anim[state] = {
                "frames": _atlas_frames(sheet, rects, fsize, size),
                "frames_left": (
                    _atlas_frames(sheet, left_rects, fsize, size)
                    if len(left_rects) == len(rects)
                    else None
                ),
                "durs": durs,
                "loop": loop,
            }
    except (AttributeError, TypeError, ValueError, pygame.error):
        return None  # garbled states/rects → fall back to the strip path
    return anim or None


def _load_anim(data_dir: Path, sprite_rel: str, size: tuple) -> dict | None:
    if _PLAIN or not sprite_rel:
        return None
    sprite_dir = sprite_rel.rsplit("/", 1)[0]
    anim = _load_atlas_anim(data_dir, sprite_dir, size)
    if anim:
        return anim
    meta_path = data_dir / (sprite_dir + "/frames.json")
    if not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text())
    except (OSError, ValueError):
        return None
    anim = {}
    for state, m in meta.items():
        strip = data_dir / m.get("path", "")
        n = int(m.get("frames", 0))
        if n < 1 or not strip.exists():
            continue
        left_rel = str(m.get("path_left", "") or "")
        left = None
        if (
            left_rel
            and int(m.get("frames_left", 0)) == n
            and (data_dir / left_rel).exists()
        ):
            left = _slice_strip(data_dir / left_rel, n, size)
        durs, loop = _anim_timing(m, n)
        anim[state] = {
            "frames": _slice_strip(strip, n, size),
            "frames_left": left,
            "durs": durs,
            "loop": loop,
        }
    return anim or None


def _anim_preview_targets(data_dir: Path, target: str) -> list[tuple[str, str]]:
    """``(label, base sprite path)`` for a PLAT_ANIM target. ``all`` walks every
    animatable actor in the pack; otherwise ``enemy:<id>`` | ``item:<id>`` |
    ``player`` names one. Items are static but resolve so a preview of one says
    so rather than erroring."""
    if target in ("all", "*"):
        out = [("player", "sprite/player/base.png")]
        enemy_dir = data_dir / "sprite" / "enemy"
        if enemy_dir.is_dir():
            out += [
                (f"enemy:{d.name}", f"sprite/enemy/{d.name}/base.png")
                for d in sorted(enemy_dir.iterdir())
                if d.is_dir()
            ]
        return out
    kind, _, rest = target.partition(":")
    if kind == "player":
        return [("player", "sprite/player/base.png")]
    if kind in ("enemy", "item") and rest:
        return [(target, f"sprite/{kind}/{rest}/base.png")]
    raise SystemExit(
        f"PLAT_ANIM: unknown target {target!r} "
        "(enemy:<id> | item:<id> | player | all)"
    )


def run_anim_preview(data_dir: Path, target: str, hooks: _Hooks) -> None:
    """The animation VIEWER (PLAT_ANIM): play one actor's states side by side
    on a neutral field instead of playing the level, so an animation can be
    judged in the SAME surface that renders the game.

    Every state runs on its own clock at its own loop mode, so `once` states
    (jump/land/hurt/death) hold their last frame — press SPACE to restart them
    all. Reuses ``_load_anim`` (atlas → strips → static ladder) and
    ``_anim_index`` verbatim: what you see here is what the game selects.
    Composes with PLAT_CAPTURE for a headless contact strip; ← → switch actors
    when the target names several."""
    import pygame

    cell = SCALE * 3
    font = pygame.font.SysFont("monospace", 13)
    small = pygame.font.SysFont("monospace", 11)
    actors = _anim_preview_targets(data_dir, target)
    idx = 0
    clock = pygame.time.Clock()
    t = 0.0
    # A display mode must exist BEFORE any convert_alpha() in the loaders.
    screen = pygame.display.set_mode((360, cell + 112))
    while True:
        label, sprite_rel = actors[idx]
        anim = _load_anim(data_dir, sprite_rel, (cell - 16, cell - 16)) or {}
        states = sorted(anim)
        static = None
        if not states:  # static actor (items, un-animated enemies) — say so
            path = data_dir / sprite_rel
            if path.exists():
                static = pygame.transform.smoothscale(
                    pygame.image.load(str(path)).convert_alpha(), (cell - 16, cell - 16)
                )
        cols = max(1, len(states) or 1)
        width, height = max(360, cols * cell + 24), cell + 112
        if screen.get_size() != (width, height):
            screen = pygame.display.set_mode((width, height))
        pygame.display.set_caption(f"canon animation preview — {label}")
        switch = 0
        while not switch:
            dt = (
                1.0 / FPS if hooks.headless else clock.tick(FPS) / 1000.0
            )
            t += dt
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return
                if event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_ESCAPE, pygame.K_q):
                        return
                    if event.key == pygame.K_SPACE:
                        t = 0.0  # replay the `once` states
                    if event.key == pygame.K_RIGHT:
                        switch = 1
                    if event.key == pygame.K_LEFT:
                        switch = -1
            screen.fill((26, 22, 34))
            screen.blit(font.render(label, True, (236, 231, 245)), (12, 8))
            baseline = 32 + cell - 12
            for i, state in enumerate(states):
                meta = anim[state]
                x = 12 + i * cell
                pygame.draw.rect(
                    screen, (40, 34, 52), (x, 32, cell - 8, cell - 8), border_radius=6
                )
                # The floor line: frames are bottom-anchored, so a state whose
                # feet drift off it is mis-registered.
                pygame.draw.line(
                    screen, (86, 74, 104), (x + 6, baseline), (x + cell - 14, baseline)
                )
                frames = meta["frames"]
                f = _anim_index(t, meta["durs"], meta["loop"])
                frame = frames[min(f, len(frames) - 1)]
                screen.blit(
                    frame, (x + 8, baseline - frame.get_height())
                )
                screen.blit(
                    small.render(state, True, (236, 231, 245)),
                    (x + 6, 32 + cell - 4),
                )
                screen.blit(
                    small.render(
                        f"{f + 1}/{len(frames)} {meta['loop']}",
                        True, (150, 142, 168),
                    ),
                    (x + 6, 32 + cell + 9),
                )
            if static is not None:
                screen.blit(static, (12, 40))
                screen.blit(
                    small.render(
                        "static sprite — no animation", True, (216, 164, 65)
                    ),
                    (12, 40 + cell),
                )
            elif not states:
                screen.blit(
                    small.render(
                        f"no sprite at {sprite_rel}", True, (224, 69, 58)
                    ),
                    (12, 48),
                )
            screen.blit(
                small.render(
                    "SPACE replay  <- -> actor  ESC quit", True, (138, 131, 152)
                ),
                (12, height - 18),
            )
            pygame.display.flip()
            if hooks.cap_dir:
                if hooks.cap_i % hooks.cap_every == 0:
                    pygame.image.save(
                        screen,
                        f"{hooks.cap_dir}/anim_{hooks.cap_i:04d}.png",
                    )
                hooks.cap_i += 1
                if hooks.cap_i >= hooks.cap_ticks:
                    return
        idx = (idx + switch) % len(actors)
        t = 0.0


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

    pygame.init()
    hooks = _Hooks()
    if hooks.anim_target:
        # Animation viewer, not a play session: no level, no physics.
        pygame.font.init()
        run_anim_preview(data_dir, hooks.anim_target, hooks)
        pygame.quit()
        return
    # The ROOM-SWITCH loop (multi-room arc): run_level runs one map until
    # quit or a room transition; the player's CARRY state (hearts, coins,
    # held power-up) crosses the switch, per-level CACHES restore what a
    # map looked like when you left it (collected/spent/crumbled/dead/
    # checkpoints), and death inside a room EJECTS to the parent's
    # checkpoint with normal death semantics (user-locked doctrine).
    manifest0 = json.loads((data_dir / "manifest.json").read_text())
    enemy_reset0 = bool(
        manifest0.get("rules", {}).get("checkpoint_enemy_reset", True)
    )
    caches: dict[str, dict] = {}
    current, carry, arrive, in_room = level_id, None, None, False
    return_to: tuple[str, tuple[int, int]] | None = None
    while True:
        res = run_level(
            data_dir, current, hooks, carry=carry,
            cache=caches.setdefault(current, {}), arrive_at=arrive,
            in_room=in_room,
        )
        if hooks.headless and hooks.cap_i >= hooks.cap_ticks:
            break  # the scripted session's frame budget is spent
        action = str(res.get("action", "quit"))
        if action == "switch":
            return_to = (current, tuple(res["return_at"]))
            carry = res["carry"]
            current, arrive, in_room = str(res["room_id"]), None, True
        elif action == "return" and return_to is not None:
            current, arrive = return_to
            carry, in_room, return_to = res["carry"], False, None
        elif action == "eject" and return_to is not None:
            # Death in a room: back to the parent's checkpoint with
            # normal death semantics — hearts refill, held clears, coins
            # persist, killed enemies return everywhere (the rule).
            if enemy_reset0:
                for c in caches.values():
                    c.pop("dead", None)
            current, arrive = return_to[0], "respawn"
            carry = {"coins": res["carry"]["coins"]}
            in_room, return_to = False, None
        else:
            break
    if hooks.traj_file is not None:
        hooks.traj_file.close()
    pygame.quit()


def run_level(
    data_dir: Path,
    level_id: str,
    hooks: _Hooks,
    carry: dict | None = None,
    cache: dict | None = None,
    arrive_at: object = None,
    in_room: bool = False,
) -> dict:
    """Load ONE level (or secret room) and run its game loop until quit
    or a room transition.

    Everything level-scoped — the grid, tile categories, enemies, items,
    sprites, the window itself — is constructed here; ``hooks`` carries
    the once-per-process verification state (traj file, monotonic frame
    counters, scripted input). ``carry`` overrides the player's portable
    state (hearts/coins/held); ``cache`` restores this map's persistent
    state (collected/spent/crumbled/dead/checkpoints) and is refilled on
    exit; ``arrive_at`` is a landing cell, the sentinel ``"respawn"``
    (the cached checkpoint), or None (the map's spawn). Returns
    ``{"action": "quit"|"switch"|"return"|"eject", ...}``."""
    import pygame

    (
        manifest, level, grid, enemies, placements, tileset,
        item_defs, item_placements,
    ) = _load(data_dir, level_id)
    # Per-level overrides (combat/level-picks arc): TWO fields onto TWO
    # targets — level.json's rules_overrides merge over the game-wide
    # rules, movement_overrides over the movement spec. Every derived
    # local below (drop_through, BREAK_DELAY_S, jump_v, accels...) is
    # computed AFTER these merges, per run_level — main.gd's
    # _load_level_by_id mirrors this (mechanics parity).
    movement = dict(manifest["movement"])
    movement.update(level.get("movement_overrides") or {})
    tile_colors = _tile_colors(data_dir, tileset, manifest)
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
    SUPPORT = BLOCKING | ONE_WAY  # anything the feet can rest on
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
    # Translucent water (RB2, presentation only): volume tiles draw from
    # pre-built SRCALPHA surfaces at the manifest's water_alpha so the
    # backdrop shows through — physics reads VOLUMES, never these.
    water_alpha = float(manifest.get("graphics", {}).get("water_alpha", 0.55))
    VOLUME_SURFS = {}
    for t in VOLUMES:
        surf = pygame.Surface((SCALE, SCALE), pygame.SRCALPHA)
        surf.fill((*tile_colors.get(t, (255, 0, 255))[:3], round(water_alpha * 255)))
        VOLUME_SURFS[t] = surf

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
    # A VERTICAL (climb) level is won by reaching the exit's ROW at the summit,
    # not its column — the exit sits at the TOP, not the right edge.
    layout_axis = level.get("layout_axis", "horizontal")
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

    rules = dict(manifest.get("rules", {}))
    # Per-level rule twists merge over the game-wide rules (the movement
    # half merged above); everything below derives from the merged dict.
    rules.update(level.get("rules_overrides") or {})
    water_policy = rules.get("enemy_water_policy", "swimmers_only")
    drop_through = bool(rules.get("platform_drop_through", True))
    enemy_reset = bool(rules.get("checkpoint_enemy_reset", True))
    # Death linger: a killed enemy stays frozen in place playing its DEATH
    # strip for this long before vanishing. 0 (old manifests) = vanish on
    # the kill frame. Mirrors main.gd.
    DEATH_LINGER_S = float(rules.get("death_linger_s", 0.0))
    spawn_grace = str(rules.get("spawn_grace", "until_move")) == "until_move"
    # Breakable floors (sectioned-levels D): tile ids whose slot params mark
    # them breakable, + the fuse length. The fuse mechanic below MUST match
    # main.gd byte-for-byte (parity), so both surfaces read these the same way.
    BREAKABLE = {
        int(s["tile_type"]) for s in tileset["slots"]
        if (s.get("params") or {}).get("breakable")
    }
    BREAK_DELAY_S = float(rules.get("break_delay_s", 0.6))
    # Items layer (Arc 2): definitions + placements. BOX placements OVERLAY
    # their solid container tile onto the grid at load — collision.npz
    # never carries boxes (the items step must not rewrite its parent), so
    # every consumer overlays identically. Boxed items stay inert inside
    # their box until the break mechanic lands; power-up pickups arrive
    # with the held-slot mechanic. main.gd mirrors all of it.
    box_tile = next(
        (
            int(s["tile_type"]) for s in tileset["slots"]
            if (s.get("params") or {}).get("container")
        ),
        None,
    )
    items = []
    for rec in item_placements:
        definition = item_defs.get(rec.get("item_id", ""), {})
        items.append(
            {
                "x": int(rec["x"]),
                "y": int(rec["y"]),
                "source": str(rec.get("source", "trail")),
                "kind": str(definition.get("kind", "coin")),
                "params": dict(definition.get("params", {})),
                "color": hex_rgb(
                    (definition.get("stats", {}) or {}).get(
                        "placeholder_color", "#ffd700"
                    )
                ),
                "sprite_path": str(definition.get("sprite_path", "") or ""),
                "sprite": None,  # loaded below once _sprite exists
                "collected": False,
            }
        )
        if items[-1]["source"] == "box" and box_tile is not None:
            grid[items[-1]["y"], items[-1]["x"]] = box_tile
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
            # Hopper state (combat/level-picks arc): the FIRST enemy with
            # real vertical physics — a vy, a grounded flag, and the hop
            # cadence clock (frame-count deterministic, the flyer idiom).
            self.vy = 0.0
            self.hop_t = 0.0
            self.e_grounded = True
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
            self.dying_t = 0.0  # death-linger countdown (frozen, no collision)
            visual = self.variant.get("visual", "outline") if self.variant else ""
            self.outlined = "outline" in visual
            self.color = hex_rgb(self.spec["stats"].get("placeholder_color", "#ff00ff"))
            behavior = dict(self.spec["behavior"])
            if self.variant:
                behavior.update(self.variant.get("behavior", {}))
            self.behavior = behavior
            # Swimmer sub-behavior (ecology): "surface" rides the water's
            # top row, "float" drifts diagonally, "cruise" roams unbounded
            # straight lines (water arc), ""/"within" = classic.
            self.swim_style = str(behavior.get("swim_style", "") or "")
            # WADING (water arc, "seabed" enemy_water_policy): a LAND
            # enemy posted on a submerged flat may occupy water cells —
            # computed ONCE from its placement cell (dry-posted enemies
            # never migrate into pools). main.gd mirrors this.
            hy, hx = int(self.home_y), int(self.home)
            self.amphibious = (
                0 <= hy < height
                and 0 <= hx < width
                and int(grid[hy, hx]) in VOLUMES
            )

        def reset(self) -> None:
            """Checkpoint-respawn restore: killed enemies return, at
            their placement, full hp (GameRules.checkpoint_enemy_reset)."""
            self.x, self.y = self.home, self.home_y
            self.direction, self.dir_y = 1.0, 1.0
            self.alerted = False
            self.bob_t = self.swoop_t = 0.0
            self.swoop_dir, self.swoop_dep = 1.0, 0.0
            self.vy, self.hop_t, self.e_grounded = 0.0, 0.0, True
            self.hp, self.alive, self.hurt_t = self.max_hp, True, 0.0
            self.dying_t = 0.0

        def stomp(self) -> bool:
            """One stomp of damage; True if it killed."""
            self.hp -= STOMP_DAMAGE
            if self.hp <= 0:
                self.alive = False
                # Death linger: freeze in place and play the death strip
                # from its first frame (main.gd mirrors both resets).
                self.dying_t = DEATH_LINGER_S
                self._anim_t = 0.0
                return True
            self.hurt_t = 0.25
            return False

        def _can_occupy(
            self,
            x: float,
            y: float | None = None,
            swim_style: str | None = None,
            airborne: bool = False,
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
            if airborne:
                # AIRBORNE occupancy (a hopper mid-arc — the 4th mirrored
                # predicate: main.gd + the test steppers): solids/one-ways/
                # volumes block the drift (bonk and flip at walls/water),
                # the hazard veto and the footing rule are SKIPPED — the
                # arc carries it over spike strips and gaps.
                return not (
                    cell in BLOCKING or cell in ONE_WAY or cell in VOLUMES
                )
            if cell in HAZARDS and not self.behavior.get("hazard_immune"):
                # Nobody strolls into spikes — except a hazard-immune
                # variant (emberborn, combat arc): it patrols ON them.
                return False
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
            if (
                cell in VOLUMES
                and water_policy != "amphibious"
                and not (water_policy == "seabed" and self.amphibious)
            ):
                # "seabed" (water arc): a land enemy POSTED in water wades
                # its submerged beat; dry-posted enemies stay out of pools.
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
            if not self.alive:
                # Dead: frozen in place (no patrol/chase), counting down the
                # death linger. main.gd's dead branch mirrors this.
                self.dying_t = max(0.0, self.dying_t - dt)
                return
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
                elif self.swim_style == "cruise":
                    # Unbounded cruiser (water arc): a straight line across
                    # the whole body of water, flipping only at walls/the
                    # water's edge — no patrol tether. main.gd mirrors this.
                    new_x = self.x + self.direction * walk
                    if self._can_occupy(new_x):
                        self.x = new_x
                    else:
                        self.direction *= -1.0
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
            elif archetype == "hopper" and self.speed > 0:
                # HOPPER (combat/level-picks arc): the first enemy with
                # real vertical physics. Grounded: the hop clock ticks;
                # at cadence it launches (hop_height via the ballistic
                # formula) facing its mode's target. Airborne: gravity
                # integrates, X drifts under the AIRBORNE occupancy mode
                # (arcs over gaps and hazards, flips at walls/water),
                # and it lands ANCHOR-ONLY on support below. Falling out
                # of the world vanishes it. main.gd + the test stepper
                # mirror every branch (mechanics parity).
                hop_h = float(self.behavior.get("hop_height", 2))
                period = float(self.behavior.get("hop_period_s", 1.0))
                grav = float(movement.get("gravity", 40.0))
                if self.e_grounded:
                    self.hop_t += dt
                    if mode == "chase":
                        self.direction = 1.0 if player_x >= self.x else -1.0
                    elif mode == "return":
                        self.direction = 1.0 if self.home >= self.x else -1.0
                    if self.hop_t >= period:
                        self.hop_t = 0.0
                        self.vy = -math.sqrt(2.0 * grav * (hop_h + 0.25))
                        self.e_grounded = False
                else:
                    # Positions QUANTIZED to the traj's 1e-3 lattice so
                    # tile-boundary decisions agree across surfaces (gd's
                    # float32 Vector2 vs float64 here flipped a wall test
                    # 2 cells apart mid-arc — the new mechanic holds a
                    # stronger parity line than the legacy drift class).
                    step = chase if mode == "chase" else walk
                    new_x = round(self.x + self.direction * step, 3)
                    if self._can_occupy(new_x, airborne=True):
                        self.x = new_x
                    else:
                        self.direction *= -1.0
                    self.vy += grav * dt
                    ny = round(self.y + self.vy * dt, 3)
                    if self.vy < 0 and tile_at(self.x, ny) in BLOCKING:
                        # Ceiling bonk: stop rising, fall from here.
                        self.vy = 0.0
                    elif self.vy > 0 and tile_at(self.x, ny + 1.0) in SUPPORT:
                        target = float(int(ny + 1.0) - 1)
                        if ny >= target:
                            self.y = target
                            self.vy = 0.0
                            self.e_grounded = True
                        else:
                            self.y = ny
                    else:
                        self.y = ny
                    if self.y > height + 2:
                        # Hopped off the world: gone (no death linger).
                        self.alive = False
                        self.dying_t = 0.0
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

    screen = pygame.display.set_mode((width * SCALE, height * SCALE))
    pygame.display.set_caption(
        f"{manifest['world']} — {level_id}  [placeholder review build]"
    )
    clock = pygame.time.Clock()
    font = pygame.font.SysFont(None, 28)

    # Real tile textures for the ART view (blocks view stays flat colors):
    # one representative crop per tile TYPE, blitted by the draw loop in
    # place of the region-average rect. Needs the display (convert_alpha),
    # so it's built here rather than beside tile_colors. An unreadable sheet
    # falls back to the flat colors — the harness never hard-fails on art.
    tile_surfs: dict = {}
    if not _PLAIN:
        try:
            _sheet = pygame.image.load(
                str(data_dir / tileset["tilesheet_path"])
            ).convert_alpha()
            tile_surfs = _tile_textures(_sheet, tileset, SCALE)
        except (pygame.error, OSError, KeyError, ValueError):
            tile_surfs = {}

    # Generated audio (manifest["audio"], late audio phase). Best-effort:
    # music loops, SFX keyed by event; ANY failure (no device, no files,
    # pre-audio manifest) leaves the game silent — this harness is the
    # quick pre-art surface, and music confirmation is one of its jobs.
    sounds: dict = {}
    audio_ok = False
    # Music resolves by POSITION: a user music_section > the level's own track
    # > the stage default theme. SFX stay per-stage (shared across a biome).
    audio_stage = _stage_for(manifest, level_id)
    stage_audio = (manifest.get("audio") or {}).get(audio_stage, {})
    stage_music = stage_audio.get("music") or ""
    try:
        pygame.mixer.init()
        audio_ok = True
        for sfx_event, rel in (stage_audio.get("sfx") or {}).items():
            sounds[sfx_event] = pygame.mixer.Sound(str(data_dir / rel))
    except Exception as exc:  # noqa: BLE001 — silence is the fallback
        print(f"[audio] disabled: {exc}")

    def apply_music(player_cell: int) -> None:
        """(Re)start the resolved track if it changed. Gated on
        ``hooks.music_cur`` so crossing INTO a section, or a room switch that
        keeps the same track, never re-triggers. Best-effort — silence on any
        failure. Called each frame (a no-op unless the resolved track flips)."""
        if not audio_ok:
            return
        resolved = _active_music(level, player_cell, stage_music)
        if resolved == hooks.music_cur:
            return
        try:
            if resolved:
                pygame.mixer.music.load(str(data_dir / resolved))
                pygame.mixer.music.play(-1)
            else:
                pygame.mixer.music.stop()
            hooks.music_cur = resolved
        except Exception as exc:  # noqa: BLE001 — silence is the fallback
            print(f"[audio] music switch failed: {exc}")

    def _music_cell(cx: float, cy: float) -> int:
        return int(cy if layout_axis == "vertical" else cx)

    apply_music(_music_cell(spawn["x"], spawn["y"]))

    def play_sfx(event_name: str) -> None:
        sound = sounds.get(event_name)
        if sound is not None:
            sound.play()

    # --- art track (all optional; the harness stays flat-color for tiles,
    # Godot is the art surface of record) ---
    def _sprite(rel: str, size: tuple) -> pygame.Surface | None:
        path = data_dir / rel
        if _PLAIN or not rel or not path.exists():
            return None  # placeholder shapes take over (pre-art rendering)
        return pygame.transform.smoothscale(
            pygame.image.load(str(path)).convert_alpha(), size
        )

    stage_id = _stage_for(manifest, level_id)
    player_sprite = _sprite("sprite/player/base.png", (SCALE - 8, SCALE - 8))
    enemy_sprites = {
        eid: _sprite(spec.get("sprite_path", ""), (SCALE - 4, SCALE - 4))
        for eid, spec in enemies.items()
    }

    # Per-state animation frames (art track, B4/G4) — see _load_anim.
    enemy_anims = {
        eid: a
        for eid, spec in enemies.items()
        if (a := _load_anim(data_dir, spec.get("sprite_path", ""), (SCALE - 4, SCALE - 4)))
    }
    # The player animates too (art track): idle/walk/JUMP, smoother (~9 frames).
    player_anim = _load_anim(
        data_dir, "sprite/player/base.png", (SCALE - 8, SCALE - 8)
    )
    # Item sprites (art track): drawn in place of the colored circle when
    # the art phase produced one (loud circle fallback otherwise).
    for item in items:
        if item["sprite_path"]:
            item["sprite"] = _sprite(
                item["sprite_path"], (SCALE - 10, SCALE - 10)
            )

    def _enemy_frame(enemy) -> tuple:
        """The current animation frame for an enemy's state plus whether the
        caller must mirror it — ``(None, flip)`` → the caller draws the
        static base sprite (loud fallback). Picks + LATCHES every frame: a
        state change restarts the clock so "once" states play from frame 0
        (main.gd mirrors the latch at each of its swap sites). Authored
        left-facing frames play UNFLIPPED."""
        if not enemy.alive:
            # Death linger: the death strip, falling back through hurt/idle
            # when no death state exists. Order mirrors main.gd.
            candidates = ["death", "hurt", "idle"]
        else:
            candidates = (
                (["hurt"] if enemy.hurt_t > 0 else [])
                # Hopper mid-hop (every other archetype keeps e_grounded
                # True, so the candidate is safely universal).
                + (["jump"] if not enemy.e_grounded else [])
                + (["walk"] if getattr(enemy, "_anim_moving", False) else [])
                + ["idle", "walk", "hurt", "death"]
            )
        anim = enemy_anims.get(enemy.spec.get("enemy_id", ""))
        if not anim:
            return None, enemy.direction < 0
        state, idx = pick_anim_frame(
            candidates, getattr(enemy, "_anim_t", 0.0), anim
        )
        if state != getattr(enemy, "_anim_state", ""):
            enemy._anim_state, enemy._anim_t, idx = state, 0.0, 0
        info = anim[state]
        if enemy.direction < 0 and info.get("frames_left"):
            return info["frames_left"][idx], False
        return info["frames"][idx], enemy.direction < 0
    # Gameplay props (manifest "props", keyed per biome stage): sprite
    # when the art track made one, drawn placeholder shape otherwise.
    prop_paths = manifest.get("props", {}).get(stage_id, {})
    checkpoint_sprite = _sprite(
        prop_paths.get("checkpoint", ""),
        (int(SCALE * 1.5), int(SCALE * 1.5)),
    )
    # Secret-room entrance props (multi-room arc): themed sprites when the
    # art track made them, drawn placeholder shapes otherwise.
    pipe_sprite = _sprite(
        prop_paths.get("pipe", ""), (int(SCALE * 1.5), int(SCALE * 1.5))
    )
    door_sprite = _sprite(
        prop_paths.get("door", ""), (int(SCALE * 1.5), int(SCALE * 1.5))
    )
    checkpoint_grey = None
    if checkpoint_sprite is not None:
        # Unclaimed flags render desaturated — claimed ones full color.
        checkpoint_grey = checkpoint_sprite.copy()
        checkpoint_grey.fill((150, 150, 160), special_flags=pygame.BLEND_RGB_MULT)
    # Depth SPLIT (graphics arc): bands <= 1.0 stay behind the tiles as
    # today; depth > 1.0 = FOREGROUND occluders, kept RGBA (convert_alpha)
    # and blitted AFTER the player — static tiling either way (this
    # harness has no camera; main.gd does the real parallax).
    backdrop_bands = []
    foreground_bands = []
    backdrop_manifest = data_dir / "backdrop" / stage_id / "manifest.json"
    if not _PLAIN and backdrop_manifest.exists():
        backdrop = json.loads(backdrop_manifest.read_text())
        depths = backdrop.get("depths", [])
        for i, rel in enumerate(backdrop.get("band_paths", [])):
            band = data_dir / rel
            if not band.exists():
                continue
            depth = float(depths[i]) if i < len(depths) else 0.5
            raw = pygame.image.load(str(band))
            raw = raw.convert_alpha() if depth > 1.0 else raw.convert()
            scale_f = (height * SCALE) / raw.get_height()
            scaled = pygame.transform.smoothscale(
                raw, (int(raw.get_width() * scale_f), height * SCALE)
            )
            (foreground_bands if depth > 1.0 else backdrop_bands).append(
                scaled
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
    vx = 0.0  # horizontal velocity (run-up momentum / slide)
    on_ground = False
    # Per-cell breakable-floor fuse: (col,row) -> seconds of hold LEFT. PERSIST
    # policy (counts down only while a foot rests on the cell, never resets).
    # Mirrors main.gd's tile_fuses byte-for-byte.
    break_fuses: dict[tuple[int, int], float] = {}
    won = False
    hearts = MAX_HEARTS
    coins = 0  # coin counter (score plumbing later); survives respawn
    # Held POWER-UP: one slot, a new pickup replaces it, death clears it.
    # Timed kinds (double_jump/run_boost) count DOWN in held_t (the
    # breakable-fuse arithmetic shape — parity); the shield has no timer
    # (held until it absorbs a hit). air_jump_ok re-arms on the same
    # deterministic foot probe as the coyote latch. Mirrors main.gd.
    held: dict | None = None
    held_t = 0.0
    air_jump_ok = False
    # Broken item boxes stay SOLID but flip SPENT (persists across respawn
    # like crumbled floors); pops are the brief cosmetic rise of a box's
    # item as it auto-collects. Mirrors main.gd.
    spent_boxes: set[tuple[int, int]] = set()
    pops: list[dict] = []
    iframes = 0.0  # post-hit invulnerability countdown
    # Coyote latch: seconds of jump-forgiveness LEFT after leaving a ledge.
    # Armed each tick from the deterministic foot probe (see the momentum
    # comment), consumed by any jump. Mirrors main.gd.
    coyote_s = float(movement.get("coyote_s", 0.0))
    coyote_t = 0.0
    damage_soaked = 0.0  # fractional volume drain toward the next heart
    moved = False  # first input after (re)spawn ends the spawn grace
    # Spawn SHIELD: full invincibility for a few seconds AFTER that
    # first input — enemies may legitimately camp a checkpoint; this
    # window is what keeps respawning next to one fair.
    spawn_shield = 0.0
    blink_t = 0.0  # deterministic blink clock (grace + shield + i-frames)
    player_anim_t = 0.0  # player animation clock (fixed-dt in capture)
    player_anim_state = ""  # last picked state — the "once"/clock latch
    player_facing = 1.0  # last horizontal facing (flip the sprite left/right)
    land_t = 0.0  # landing one-shot window (armed on the grounded edge)
    live_enemies = [Enemy(p) for p in placements]

    # Secret-room marks (multi-room arc): entrances on a main map, the
    # return portal inside a room — both stand-on-cell + verb-press
    # (pipe = Down alone, door = the jump/Up press). Unknown trigger
    # types still flow through inert.
    room_marks = [
        {
            "x": int(t["x"]),
            "y": int(t["y"]),
            "kind": str(t["type"]),
            "verb": str(t.get("params", {}).get("verb", "pipe")),
            "room_id": str(t.get("params", {}).get("room_id", "")),
            "return_x": int(t.get("params", {}).get("return_x", t["x"])),
            "return_y": int(t.get("params", {}).get("return_y", t["y"])),
        }
        for t in level.get("triggers", [])
        if t.get("type") in ("room_entrance", "room_portal")
    ]

    # Persistent per-map state (cache): what this map looked like when
    # you left it — collected/spent/crumbled/dead/claimed — restored on
    # re-entry, refilled at exit. Godot mirrors the same partition.
    crumbled_cells: set[tuple[int, int]] = set()
    cache = cache if cache is not None else {}
    for i in cache.get("collected", []):
        if 0 <= i < len(items):
            items[i]["collected"] = True
    spent_boxes |= {tuple(c) for c in cache.get("spent", [])}
    for key, remaining in (cache.get("fuses") or {}).items():
        break_fuses[tuple(key)] = float(remaining)
    for cx0, cy0 in cache.get("crumbled", []):
        grid[int(cy0), int(cx0)] = 0
        crumbled_cells.add((int(cx0), int(cy0)))
    for i in cache.get("checkpoints", []):
        if 0 <= i < len(checkpoints):
            checkpoints[i]["active"] = True
    if cache.get("respawn") is not None:
        rp = cache["respawn"]
        respawn_point = {"x": int(rp[0]), "y": int(rp[1])}
    for i in cache.get("dead", []):
        if 0 <= i < len(live_enemies):
            live_enemies[i].hp = 0.0
            live_enemies[i].alive = False
            live_enemies[i].dying_t = 0.0

    # Portable player state across a room switch (carry-everything).
    if carry is not None:
        hearts = int(carry.get("hearts", MAX_HEARTS))
        coins = int(carry.get("coins", 0))
        held = carry.get("held")
        held_t = float(carry.get("held_t", 0.0))
    # Arrival: a return cell, the cached checkpoint ("respawn"), or spawn.
    if arrive_at == "respawn":
        px, py = float(respawn_point["x"]), float(respawn_point["y"])
    elif arrive_at is not None:
        px, py = float(arrive_at[0]), float(arrive_at[1])
    result: dict = {"action": "quit"}
    switch_now: list[dict] = []
    pending_eject = [False]

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
        nonlocal px, py, vy, vx, damage_soaked, hearts, iframes, moved
        nonlocal spawn_shield, coyote_t, held, held_t, air_jump_ok
        if in_room:
            # Death (or R) inside a secret room EJECTS to the parent's
            # checkpoint (user-locked). The switch happens BETWEEN frames
            # — this frame keeps the dead pose (main.gd defers its eject
            # to the end of _process the same way, traj parity).
            if not pending_eject[0]:
                play_sfx("death")
            pending_eject[0] = True
            return
        px, py = float(respawn_point["x"]), float(respawn_point["y"])
        vy, vx, damage_soaked = 0.0, 0.0, 0.0
        hearts, iframes, moved, spawn_shield = MAX_HEARTS, 0.0, False, 0.0
        coyote_t = 0.0
        held, held_t, air_jump_ok = None, 0.0, False  # power-up dies with you
        if enemy_reset:
            # Killed enemies come back on a checkpoint respawn — dying
            # never leaves a half-cleared level (GameRules kind).
            for other in live_enemies:
                other.reset()
        play_sfx("death")

    def collect_item(item: dict) -> None:
        """Apply one item's effect (touch pickup AND box pops share it)."""
        nonlocal coins, hearts, held, held_t
        item["collected"] = True
        if item["kind"] == "coin":
            coins += int(item["params"].get("coin_value", 1))
        elif item["kind"] == "heal":
            hearts = min(
                MAX_HEARTS,
                hearts + int(item["params"].get("heal_amount", 1)),
            )
        else:
            # Power-up: ONE held slot, a new pickup replaces the old;
            # timed kinds carry duration_s, the shield has none.
            held = {
                "kind": item["kind"],
                "params": item["params"],
                "color": item["color"],
            }
            held_t = float(item["params"].get("duration_s", 0))
        play_sfx("checkpoint")  # closed SFX set — reuse (v1)

    def break_box(bx: int, by: int) -> None:
        """Bump/stomp opens an item box: the tile stays SOLID but flips
        SPENT, and its item pops out and auto-collects (brief rise)."""
        spent_boxes.add((bx, by))
        for item in items:
            if (
                item["source"] == "box" and not item["collected"]
                and item["x"] == bx and item["y"] == by
            ):
                collect_item(item)
                pops.append(
                    {"x": bx, "y": by, "color": item["color"], "t": 0.35}
                )

    def _mark_here(verb: str) -> dict | None:
        """The entrance/portal mark under the player's anchor cell whose
        entry verb is ``verb`` (checkpoint cell-equality convention)."""
        for m in room_marks:
            if m["verb"] == verb and int(px) == m["x"] and int(py) == m["y"]:
                return m
        return None

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
        own path below). A held SHIELD absorbs one hit of ANY size and
        breaks (still granting the i-frames window — no instant re-hit)."""
        nonlocal hearts, iframes, held
        if iframes > 0.0 or spawn_shield > 0.0 or (spawn_grace and not moved):
            return
        if held is not None and held["kind"] == "shield":
            held = None
            iframes = IFRAMES_S
            play_sfx("checkpoint")  # the ward shatters, no heart lost
            return
        hearts -= max(1, int(cost))
        iframes = IFRAMES_S
        if hearts <= 0:
            respawn()

    def drain(amount: float) -> None:
        """Continuous volume damage: accumulate fractions, convert each
        whole point into one heart — ignores hurt i-frames (lava keeps
        hurting) but respects spawn grace AND the spawn shield (full
        invincibility while it lasts). A held SHIELD soaks one whole
        heart of drain, then breaks."""
        nonlocal damage_soaked, hearts, held
        if spawn_shield > 0.0 or (spawn_grace and not moved):
            return
        damage_soaked += amount
        while damage_soaked >= 1.0:
            damage_soaked -= 1.0
            if held is not None and held["kind"] == "shield":
                held = None
                play_sfx("checkpoint")  # the ward boils away, heart kept
                continue
            hearts -= 1
            if hearts <= 0:
                respawn()
                return

    run_speed = float(movement["run_speed"])
    walk_speed = float(movement.get("walk_speed", run_speed))
    ground_accel = float(movement.get("ground_accel", 0.0))
    ground_friction = float(movement.get("ground_friction", 0.0))
    brake_accel = float(movement.get("brake_accel", ground_accel))
    air_accel = float(movement.get("air_accel", ground_accel))
    air_friction = float(movement.get("air_friction", 0.0))
    # No-accel manifests (ground_accel 0) fall back to the old snappy
    # instant-speed feel; a real spec drives run-up momentum.
    momentum = ground_accel > 0.0
    gravity = float(movement["gravity"])
    # Jump velocity for jump_height cells PLUS a headroom margin: discrete
    # frame integration undershoots the analytic apex, which made exactly-
    # jump_height platforms unlandable (feet never cleared the top).
    jump_v = (2.0 * gravity * (float(movement["jump_height"]) + 0.4)) ** 0.5

    # Verification-harness state lives on ``hooks`` (once-per-process:
    # the traj file + frame counters stay monotonic across level loads,
    # mirroring main.gd's _traj_frame/_play_frame).
    cap_dir = hooks.cap_dir
    hold_mode = hooks.hold_mode
    hold_jump_every = hooks.hold_jump_every
    headless = hooks.headless
    cap_ticks = hooks.cap_ticks
    cap_every = hooks.cap_every
    traj_file = hooks.traj_file

    running = True
    while running:
        hooks.frame_i += 1
        frame_i = hooks.frame_i
        dt = (1.0 / FPS) if headless else clock.tick(FPS) / 1000.0
        # Scripted single-frame input (PLAT_ACTIONS): the action for THIS
        # frame, or "" — "down" holds Down, "up" presses jump. Inert
        # unless the env is set. main.gd keys on the same frame numbers.
        script_act = hooks.actions.get(frame_i, "") if headless else ""
        jump_input = False  # any jump press this frame (pipe = down ALONE)
        # Live music switch: crossing into/out of a user music_section swaps
        # the track (gated, cosmetic — never touches physics/traj).
        apply_music(_music_cell(px, py))
        volume = volume_params_at(px, py)
        # Coyote latch (BEFORE input and movement, off last tick's state):
        # re-arm while the deterministic foot probe finds support (same
        # idiom as gmove and the breakable fuse — never the on_ground
        # flag, whose sub-cell flicker diverges between the surfaces),
        # otherwise count the forgiveness window down. main.gd mirrors
        # this at the same point in its tick order.
        foot_c = int(py + 1.01)
        grounded_probe = vy >= -0.01 and (
            tile_at(px + BODY_L, foot_c) in SUPPORT
            or tile_at(px + BODY_R, foot_c) in SUPPORT
        )
        if coyote_s > 0.0:
            if grounded_probe:
                coyote_t = coyote_s
            else:
                coyote_t = max(0.0, coyote_t - dt)
        if grounded_probe:
            air_jump_ok = True  # double-jump re-arms on the same probe
        # Timed power-ups count DOWN (the fuse arithmetic shape); the
        # shield has no timer (duration_s 0 = held until consumed).
        if held is not None and held_t > 0.0:
            held_t = max(0.0, held_t - dt)
            if held_t <= 0.0:
                held = None
        # Scripted-harness jump (PLAT_HOLD every-N, or a PLAT_ACTIONS
        # "up" on exactly this frame): the event loop below never sees a
        # KEYDOWN headless, so trigger the on-ground jump here, matching
        # the normal path (vy set before the horizontal step).
        scripted_jump = (
            headless and hold_mode and hold_jump_every
            and frame_i % hold_jump_every == 0
        ) or script_act == "up"
        if scripted_jump and volume is not None:
            # UNDERWATER a scripted press is the swim stroke the real
            # event path gives (full jump only with open air above) —
            # gated grounded/coyote/air-jump exactly like main.gd's
            # scripted gate, so mid-water drift never strokes (the gd
            # quirk is the parity reference; the old `volume is None`
            # outer gate silently killed ALL underwater scripted input
            # on this surface only — exposed by the first
            # grounded-on-seabed spawn, the low-gravity l9).
            if on_ground or coyote_t > 0.0 or (
                held is not None and held["kind"] == "double_jump"
                and air_jump_ok
            ):
                note_move()
                vy = (
                    -jump_v
                    if volume_params_at(px, py - 1.0) is None
                    else -float(volume["impulse"])
                )
        elif scripted_jump:
            jump_input = True
            # DOOR entry swallows the jump press (genre-standard): standing
            # grounded on a door mark, Up/jump enters instead of jumping
            # (never underwater — verb presses are dry-land interactions).
            door = _mark_here("door") if grounded_probe else None
            if door is not None:
                note_move()
                switch_now.append(door)
            elif on_ground or coyote_t > 0.0:
                note_move()
                vy = -jump_v
                coyote_t = 0.0
            elif (
                held is not None and held["kind"] == "double_jump"
                and air_jump_ok
            ):
                note_move()
                vy = -jump_v
                air_jump_ok = False
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
                    jump_input = True
                    down_held = (
                        pygame.key.get_pressed()[pygame.K_DOWN]
                        or pygame.key.get_pressed()[pygame.K_s]
                    )
                    # DOOR entry swallows the jump press (genre-standard).
                    door = (
                        _mark_here("door")
                        if grounded_probe and volume is None
                        else None
                    )
                    if door is not None:
                        switch_now.append(door)
                    elif volume is not None:
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
                        coyote_t = 0.0  # dropping is leaving, not a ledge slip
                    elif on_ground or coyote_t > 0.0:
                        vy = -jump_v
                        coyote_t = 0.0  # consumed — one forgiveness per slip
                        play_sfx("jump")
                    elif (
                        held is not None and held["kind"] == "double_jump"
                        and air_jump_ok
                    ):
                        # One mid-air jump while the power-up is held;
                        # re-arms on the grounded probe. The reachability
                        # sim never assumes it (base moveset only).
                        vy = -jump_v
                        air_jump_ok = False
                        play_sfx("jump")

        keys = pygame.key.get_pressed()
        dx = (keys[pygame.K_RIGHT] or keys[pygame.K_d]) - (
            keys[pygame.K_LEFT] or keys[pygame.K_a]
        )
        run_held = bool(keys[pygame.K_LSHIFT] or keys[pygame.K_RSHIFT])
        if headless and hold_mode:
            # Scripted run-up harness (PLAT_HOLD) — no-input can't show a
            # running jump, so drive a fixed direction (+ RUN) here.
            dx = 1 if hold_mode.endswith("right") else (
                -1 if hold_mode.endswith("left") else 0
            )
            run_held = hold_mode.startswith("run")
        if dx:
            note_move()  # walking ends the grace, starts the shield
            player_facing = dx  # face the direction of travel
        # PIPE entry: Down ALONE (no jump this frame — Down+jump stays the
        # drop-through gesture), grounded on a pipe mark. Godot checks the
        # same condition at the same pre-physics point (parity).
        down_now = bool(
            keys[pygame.K_DOWN] or keys[pygame.K_s]
        ) or script_act == "down"
        if (
            down_now and not jump_input and not switch_now
            and volume is None and grounded_probe
        ):
            pipe = _mark_here("pipe")
            if pipe is not None:
                note_move()
                switch_now.append(pipe)
        if switch_now:
            # A room transition ends the frame BEFORE physics: no traj
            # line, no frame-counter tick — the next line is the first
            # frame inside the other map, same frame number on both
            # surfaces (main.gd returns pre-traj the same way).
            break
        grace = spawn_grace and not moved
        iframes = max(0.0, iframes - dt)
        spawn_shield = max(0.0, spawn_shield - dt)
        blink_t += dt
        player_anim_t += dt
        vol_factor = (
            float(volume.get("speed_factor", 0.55)) if volume is not None else 1.0
        )
        if momentum:
            # Accelerate vx toward the held direction's target speed (walk, or
            # run_speed with RUN held); idle bleeds it off via friction (the
            # slide). Ground control is stronger than air control, so speed is
            # built on the GROUND and carried through the jump. vx is never
            # reset on jump — that IS the run-up momentum.
            #
            # "grounded for movement" is a DETERMINISTIC grid probe (support
            # directly under the feet, and not rising) — NOT the on_ground
            # flag, whose sub-cell landing flicker differs by a float epsilon
            # between the two surfaces and would desync run-up acceleration.
            foot = int(py + 1.01)
            gmove = vy >= -0.01 and (
                tile_at(px + BODY_L, foot) in SUPPORT
                or tile_at(px + BODY_R, foot) in SUPPORT
            )
            if dx:
                target = (run_speed if run_held else walk_speed) * vol_factor
                if held is not None and held["kind"] == "run_boost":
                    target *= float(held["params"].get("boost_mult", 1.5))
                desired = dx * target
                if vx * dx < 0.0:
                    # REVERSAL — the held direction opposes vx: a decisive
                    # ground brake, a WEAK air-brake (the ONLY thing that sheds
                    # air speed). Brakes toward `desired`, through 0.
                    rate = (brake_accel if gmove else 0.3 * brake_accel) * dt
                elif gmove:
                    rate = ground_accel * dt  # build up AND bleed overspeed
                elif abs(vx) < abs(desired):
                    rate = air_accel * dt  # air, same dir: build toward target
                else:
                    rate = 0.0  # air at/over target, same dir: hold (momentum)
                if rate:
                    vx = min(vx + rate, desired) if vx < desired else max(vx - rate, desired)
            else:
                fric = (ground_friction if gmove else air_friction) * dt
                vx = max(0.0, vx - fric) if vx > 0 else min(0.0, vx + fric)
        else:
            vx = dx * run_speed * vol_factor  # legacy instant-speed feel
        new_x = px + vx * dt
        if blocked_at(new_x, py):
            vx = 0.0  # ran into a wall — kill horizontal momentum
        else:
            px = max(0.0, min(new_x, width - 1.0))

        # Landing-edge capture (presentation): airborne state BEFORE the
        # vertical step — main.gd snapshots at the same point.
        was_airborne = not on_ground
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
            # Head-bump: a HEAD-row corner hitting an item BOX breaks it.
            # Deterministic which-box rule (mirrored in main.gd): the
            # column nearer player-center wins, ties break LEFT.
            if box_tile is not None:
                head = int(new_y)
                hits = [
                    c
                    for c in sorted({int(px + BODY_L), int(px + BODY_R)})
                    if 0 <= c < width and 0 <= head < height
                    and int(grid[head, c]) == box_tile
                    and (c, head) not in spent_boxes
                ]
                if hits:
                    col = min(
                        hits, key=lambda c: (abs(c - int(px)), c)
                    )
                    break_box(col, head)
            vy, on_ground = 0.0, False
        else:
            py, on_ground = new_y, False
        land_t = max(0.0, land_t - dt)
        if was_airborne and on_ground:
            # Airborne → grounded EDGE: arm the land one-shot window (read
            # by the anim candidates only; never by physics).
            land_t = LAND_ANIM_S

        # Breakable floors: a foot resting on a breakable cell burns its fuse;
        # at zero the tile is removed (collision cleared -> the player falls
        # next tick). Uses the SAME deterministic foot probe as gmove (never
        # the on_ground flag), the SAME countdown arithmetic, and a sorted
        # deduped column list — all to stay byte-identical to main.gd.
        if BREAKABLE:
            bfoot = int(py + 1.01)
            if vy >= -0.01:
                for bx in sorted({int(px + BODY_L), int(px + BODY_R)}):
                    if not (0 <= bx < width and 0 <= bfoot < height):
                        continue
                    if int(grid[bfoot, bx]) not in BREAKABLE:
                        continue
                    rem = break_fuses.get((bx, bfoot), BREAK_DELAY_S) - dt
                    if rem <= 0.0:
                        grid[bfoot, bx] = 0  # crumble — empty, player falls
                        crumbled_cells.add((bx, bfoot))  # cache survives trips
                        break_fuses.pop((bx, bfoot), None)
                    else:
                        break_fuses[(bx, bfoot)] = rem

        # Item boxes break under a STOMP too ("either works"): standing on
        # an unspent box opens it — same deterministic foot probe and
        # sorted-deduped columns as the fuse (byte-parity with main.gd).
        if box_tile is not None:
            sfoot = int(py + 1.01)
            if vy >= -0.01:
                for bx in sorted({int(px + BODY_L), int(px + BODY_R)}):
                    if not (0 <= bx < width and 0 <= sfoot < height):
                        continue
                    if (
                        int(grid[sfoot, bx]) == box_tile
                        and (bx, sfoot) not in spent_boxes
                    ):
                        break_box(bx, sfoot)

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
            # Animation state (presentation): moving = it changed position
            # this tick (patrol/chase/swoop → walk; a still sentry → idle).
            # The clock advances on the fixed-dt capture too → deterministic.
            enemy._anim_moving = (
                abs(enemy.x - getattr(enemy, "_anim_px", enemy.x))
                + abs(enemy.y - getattr(enemy, "_anim_py", enemy.y))
            ) > 1e-4
            enemy._anim_px, enemy._anim_py = enemy.x, enemy.y
            enemy._anim_t = getattr(enemy, "_anim_t", 0.0) + dt
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
                # BOUNCE V2 (combat arc): jump HELD at the stomp = a full
                # jump off the enemy's head (chainable by skill); not held
                # = the damped hop. jump_held is a DEDICATED signal (real
                # held poll OR the scripted "jumphold" action), feeding
                # ONLY this bounce — main.gd mirrors exactly.
                if enemy.stomp():
                    play_sfx("jump")  # bounce impulse; no stomp SFX in v1
                jump_held = (
                    keys[pygame.K_SPACE]
                    or keys[pygame.K_UP]
                    or keys[pygame.K_w]
                    or script_act == "jumphold"
                )
                vy = -jump_v * (1.0 if jump_held else STOMP_BOUNCE)
            else:
                hurt(enemy.damage_hearts)
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
        # Item pickup — the checkpoint anchor-cell convention, mirrored in
        # main.gd byte-for-byte. Collected STAYS collected across respawn
        # (respawn() never touches it — the break_fuses precedent). Boxed
        # items wait for the break mechanic; power-up kinds wait for the
        # held-slot mechanic (visible but inert until then).
        for item in items:
            if (
                not item["collected"]
                and item["source"] != "box"
                and int(px) == item["x"]
                and int(py) == item["y"]
            ):
                collect_item(item)
        if traj_file is not None:
            parts = [
                f"{e.spec.get('enemy_id', '')}:{e.x:.3f}:{e.y:.3f}:{1 if e.alerted else 0}"
                for e in live_enemies
            ]
            # PLAYER token first (P:px:py:vx:coins) so player-movement AND
            # pickup parity are diffable across surfaces. Written AFTER the
            # pickup/checkpoint block — main.gd dumps at the same point in
            # its tick, so a pickup lands on the SAME traj line on both.
            traj_file.write(
                f"{hooks.cap_i}|P:{px:.3f}:{py:.3f}:{vx:.3f}:{coins}|"
                f"{','.join(parts)}\n"
            )
        # Exit zone: horizontal → the exit's whole COLUMN (leave right);
        # vertical → the exit's ROW at the summit, any column (climb to the
        # top). Same reach model both surfaces (parity with main.gd).
        reached_exit = (
            int(py) <= exit_["y"]
            if layout_axis == "vertical"
            else int(px) == exit_["x"]
        )
        # WIN SUPPRESSION inside a secret room: its exit cell is the
        # return PORTAL, not a goal — nearing it must not complete the
        # level (main.gd gates identically, or the PARENT gets beaten).
        if not won and reached_exit and not in_room:
            won = True
            play_sfx("win")

        screen.fill(tile_colors.get(0, (24, 24, 32)))
        for band in backdrop_bands:  # static scenery, far → near
            for bx in range(0, width * SCALE, band.get_width()):
                screen.blit(band, (bx, 0))
        for y in range(height):
            for x in range(width):
                t = int(grid[y, x])
                if t in VOLUME_SURFS:  # translucent water over the backdrop
                    screen.blit(VOLUME_SURFS[t], (x * SCALE, y * SCALE))
                elif t in tile_surfs:  # real tilesheet crop (art view)
                    screen.blit(tile_surfs[t], (x * SCALE, y * SCALE))
                elif t:  # blocks view, or a type without a usable crop
                    pygame.draw.rect(
                        screen,
                        tile_colors.get(t, (255, 0, 255)),
                        (x * SCALE, y * SCALE, SCALE, SCALE),
                    )
        # No exit-goal doorway is drawn anymore (postmortem ticket 3): the win
        # zone is the whole right edge / summit, so the single-cell doorway was
        # a false affordance. Win logic (exit_) is untouched; a full-height
        # finish-line spanning the exit column is the future replacement.
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
        # Secret-room entrances/portals (multi-room arc): a green PIPE
        # (enter with Down) or a brown DOOR (enter with Up) standing on
        # its cell — prop sprite when the art track made one, drawn
        # placeholder otherwise. Mirrors main.gd's marks.
        for mark in room_marks:
            m_foot = ((mark["x"] + 0.5) * SCALE, (mark["y"] + 1) * SCALE)
            sprite = pipe_sprite if mark["verb"] == "pipe" else door_sprite
            if sprite is not None:
                screen.blit(
                    sprite, (m_foot[0] - SCALE * 0.75, m_foot[1] - SCALE * 1.5)
                )
            elif mark["verb"] == "pipe":
                body = pygame.Rect(0, 0, int(SCALE * 0.9), int(SCALE * 1.1))
                body.midbottom = (int(m_foot[0]), int(m_foot[1]))
                lip = pygame.Rect(0, 0, int(SCALE * 1.2), int(SCALE * 0.4))
                lip.midbottom = (int(m_foot[0]), body.top + 2)
                pygame.draw.rect(screen, (46, 160, 92), body)
                pygame.draw.rect(screen, (58, 190, 110), lip)
            else:
                door = pygame.Rect(0, 0, int(SCALE * 0.9), int(SCALE * 1.5))
                door.midbottom = (int(m_foot[0]), int(m_foot[1]))
                pygame.draw.rect(screen, (122, 82, 46), door)
                pygame.draw.circle(
                    screen, (220, 190, 90),
                    (door.right - 6, door.centery), 3,
                )
        # Items: uncollected trail/reward items as colored circles (boxed
        # items hide inside their box tile until it breaks).
        for item in items:
            if item["collected"] or item["source"] == "box":
                continue
            if item["sprite"] is not None:
                screen.blit(
                    item["sprite"],
                    (item["x"] * SCALE + 5, item["y"] * SCALE + 5),
                )
            else:
                pygame.draw.circle(
                    screen, item["color"],
                    (
                        int(item["x"] * SCALE + SCALE / 2),
                        int(item["y"] * SCALE + SCALE / 2),
                    ),
                    SCALE // 3,
                )
        # Spent boxes darken (still solid); a popped item rises briefly
        # as it auto-collects (cosmetic, fixed-dt deterministic).
        for (sbx, sby) in spent_boxes:
            pygame.draw.rect(
                screen, (48, 36, 24),
                (sbx * SCALE + 4, sby * SCALE + 4, SCALE - 8, SCALE - 8),
            )
        for pop in list(pops):
            pop["t"] -= dt
            if pop["t"] <= 0.0:
                pops.remove(pop)
                continue
            rise = (0.35 - pop["t"]) * 2.5
            pygame.draw.circle(
                screen, pop["color"],
                (
                    int(pop["x"] * SCALE + SCALE / 2),
                    int((pop["y"] - rise) * SCALE + SCALE / 2),
                ),
                SCALE // 3,
            )
        for enemy in live_enemies:
            if not enemy.alive and enemy.dying_t <= 0.0:
                continue  # linger over → vanish (dead never collide either way)
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
            # Animated frame + flip flag, or (None, flip) → base sprite. The
            # pick/latch runs on flash frames too — main.gd picks every
            # frame with visibility toggled separately.
            frame, frame_flip = _enemy_frame(enemy)
            if enemy.hurt_t > 0 and int(enemy.hurt_t * 20) % 2 == 0:
                pygame.draw.rect(screen, (255, 255, 255), rect)  # stomp flash
            elif (image := frame if frame is not None else sprite) is not None:
                if enemy.size != 1.0:
                    image = pygame.transform.smoothscale(
                        image, (int(rect[2]), int(rect[3]))
                    )
                if frame_flip:
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
        # Candidate build + pick + latch run EVERY frame (blink only hides
        # the draw) so the "once"/clock semantics stay in step with main.gd,
        # which picks while invisible too. Airborne → fall past the peak,
        # jump on the rise; grounded → skid (braking against carried
        # momentum, inert without accel specs) > land (the one-shot window)
        # > walk; idle/walk tail (loud fallback to the static base sprite
        # when there's no animation).
        if not on_ground:
            p_candidates = ["fall", "jump"] if vy > 0 else ["jump"]
        else:
            p_candidates = (
                (["skid"] if dx and _sign(vx) == -dx and abs(vx) > 0.5 else [])
                + (["land"] if land_t > 0.0 else [])
                + (["walk"] if dx else [])
            )
        p_candidates += ["idle", "walk"]
        p_frame, p_flip = None, player_facing < 0
        if player_anim:
            pstate, pidx = pick_anim_frame(
                p_candidates, player_anim_t, player_anim
            )
            if pstate != player_anim_state:
                player_anim_state, player_anim_t, pidx = pstate, 0.0, 0
            info = player_anim[pstate]
            if player_facing < 0 and info.get("frames_left"):
                # Authored left-facing frames play UNFLIPPED (asymmetric art).
                p_frame, p_flip = info["frames_left"][pidx], False
            else:
                p_frame = info["frames"][pidx]
        if not blinking:
            image = p_frame if p_frame is not None else player_sprite
            if image is not None:
                if p_flip:
                    image = pygame.transform.flip(image, True, False)
                screen.blit(image, (px * SCALE + 4, py * SCALE + 4))
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
        # Foreground occlusion bands (depth > 1.0): in front of the player
        # and decor, behind the screen-space ambience/HUD — mirrors
        # main.gd's foreground parallax layering.
        for band in foreground_bands:
            for bx in range(0, width * SCALE, band.get_width()):
                screen.blit(band, (bx, 0))
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
        # Coin counter beside the hearts (score plumbing comes later).
        coin_x = 16 + MAX_HEARTS * 24 + 12
        pygame.draw.circle(screen, (255, 208, 64), (coin_x + 9, 19), 9)
        screen.blit(
            font.render(f"x {coins}", True, (255, 224, 128)),
            (coin_x + 24, 10),
        )
        # Held power-up: its color swatch + the seconds left (shield has
        # no timer — held until it absorbs a hit).
        if held is not None:
            slot_x = coin_x + 80
            pygame.draw.rect(screen, held["color"], (slot_x, 10, 18, 18))
            label = (
                "ward" if held["kind"] == "shield" else f"{held_t:.0f}s"
            )
            screen.blit(
                font.render(label, True, (220, 220, 230)), (slot_x + 24, 10)
            )
        if won:
            screen.blit(
                font.render("LEVEL COMPLETE — R to reset", True, (64, 255, 112)),
                (16, 36),
            )
        pygame.display.flip()

        if headless:
            if cap_dir and hooks.cap_i % cap_every == 0:
                pygame.image.save(
                    screen, f"{cap_dir}/frame_{hooks.cap_i:04d}.png"
                )
            hooks.cap_i += 1
            if hooks.cap_i >= cap_ticks:
                running = False
        # A death-in-room eject switches maps BETWEEN frames — this frame
        # already traj'd the dead pose (main.gd ejects at the end of
        # _process the same way).
        if pending_eject[0]:
            running = False

    # Exit: report the transition (if any) + the portable player state,
    # and refill this map's cache so a return trip restores it.
    if switch_now:
        mark = switch_now[0]
        if mark["kind"] == "room_entrance":
            result = {
                "action": "switch",
                "room_id": mark["room_id"],
                "return_at": (mark["return_x"], mark["return_y"]),
            }
        else:
            result = {"action": "return"}
    elif pending_eject[0]:
        result = {"action": "eject"}
    result["carry"] = {
        "hearts": hearts, "coins": coins, "held": held, "held_t": held_t,
    }
    cache.clear()
    cache.update(
        {
            "collected": [
                i for i, it in enumerate(items) if it["collected"]
            ],
            "spent": sorted(spent_boxes),
            "fuses": dict(break_fuses),
            "crumbled": sorted(crumbled_cells),
            "dead": [
                i for i, e in enumerate(live_enemies) if not e.alive
            ],
            "checkpoints": [
                i for i, c in enumerate(checkpoints) if c["active"]
            ],
            "respawn": (respawn_point["x"], respawn_point["y"]),
        }
    )
    return result


if __name__ == "__main__":
    main()
