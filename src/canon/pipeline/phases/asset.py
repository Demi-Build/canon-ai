"""AssetPhase — generates portraits + music + SFX for all entities that need them.

Uses async fan-out via asyncio.gather() across all three backend protocols.
Stamps absolute paths back onto in-memory entities. ManifestPhase later
re-flushes affected entity files with the stamped paths.

Per the plan A5: this phase NEVER bypasses backend protocols. All image
generation goes through ImageBackend.generate_and_save_async (or sync if
backend.prefers_serial). Music/SFX go through their respective protocols.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from canon.bible.models import BibleMetadata
from canon.pipeline.steplog import RunCancelled, step

logger = logging.getLogger(__name__)



class _ItemProgress:
    """Row P0-10 (master §3.0-E): the shared ``node_item`` counter for
    AssetPhase's concurrent tasks. ``total`` is set once every task has been
    built (before any is awaited); ``announce`` goes through the ONE emitter,
    so a cancel file stops the phase at an asset boundary exactly the way it
    stops a serial item loop — nothing already written is undone."""

    def __init__(self, ctx: Any, node: str) -> None:
        self.ctx = ctx
        self.node = node
        self.total = 0
        self.done = 0

    def announce(self, item: str) -> None:
        self.done += 1
        step(self.ctx, self.node, item, self.done, self.total or None)


class AssetPhase:
    """Generates assets (portraits, music, SFX) for the bible.

    Args:
        image_concurrency: max concurrent image requests (default 15)
        music_concurrency: max concurrent music requests (default 8)
        sfx_concurrency: max concurrent SFX requests (default 10)
        skip_image: skip portrait generation entirely
        skip_music: skip music generation
        skip_sfx: skip SFX generation
        portrait_size: (width, height) for portraits (default 512×512)

    Reads from ctx:
        - ctx.image_backend (or ctx.config.image_backend → BackendRegistry lookup)
        - ctx.music_backend / ctx.sfx_backend similarly
        - ctx.bible.characters: list[Character] — each gets a portrait
        - ctx.bible.maps[*].entities: list[EntityLore] — each gets a portrait
        - ctx.bible.class_archetypes: dict[str, ClassArchetype] — each gets a portrait
        - ctx.bible.maps[*]: each gets an environment portrait
        - ctx.config.output_dir: base path
        - ctx.config.output_paths: subdir for portraits, music, sfx
    """

    name: str = "assets"

    def __init__(
        self,
        image_concurrency: int = 15,
        music_concurrency: int = 8,
        sfx_concurrency: int = 10,
        skip_image: bool = False,
        skip_music: bool = False,
        skip_sfx: bool = False,
        portrait_size: tuple[int, int] = (512, 512),
    ) -> None:
        self.image_concurrency = image_concurrency
        self.music_concurrency = music_concurrency
        self.sfx_concurrency = sfx_concurrency
        self.skip_image = skip_image
        self.skip_music = skip_music
        self.skip_sfx = skip_sfx
        self.portrait_size = portrait_size

    def run(self, ctx: Any) -> None:
        asyncio.run(self._run_async(ctx))
        self._stamp_metadata(ctx)
        logger.info("AssetPhase complete.")

    async def _run_async(self, ctx: Any) -> None:
        # 1. Resolve backends
        image_backend = self._resolve_backend(ctx, "image")
        music_backend = self._resolve_backend(ctx, "music")
        sfx_backend = self._resolve_backend(ctx, "sfx")

        tasks: list = []
        # Row P0-10 (§3.0-E/D): one shared ``node_item`` counter across the
        # three asset families — each task announces the file it is about to
        # write through the ONE emitter (progress + the A4.5 cancel boundary).
        # ``total`` is filled once every task exists, which is before any of
        # them is awaited.
        progress = _ItemProgress(ctx, self.name)

        # 2. Image tasks (portraits for characters, entities, archetypes, maps)
        if image_backend and not self.skip_image:
            tasks.extend(self._build_image_tasks(ctx, image_backend, progress))

        # 3. Music tasks (one per environment + fixed tracks)
        if music_backend and not self.skip_music:
            tasks.extend(self._build_music_tasks(ctx, music_backend, progress))

        # 4. SFX tasks (fixed catalog + per-env ambience)
        if sfx_backend and not self.skip_sfx:
            tasks.extend(self._build_sfx_tasks(ctx, sfx_backend, progress))

        if not tasks:
            logger.warning("AssetPhase: no asset backends registered; nothing to do.")
            return
        progress.total = len(tasks)

        # 5. Fire it all in one gather — a failed asset is captured, not
        # re-raised (one dead sprite must not lose the other forty).
        #
        # A ⏹ STOP is the exception to that (row P0-10, master §3.0-D): the
        # `node_item` emitter raises ``RunCancelled`` at the boundary, and
        # `return_exceptions=True` would swallow it — the phase would report
        # `node_done` and claim ``phase:assets`` in `kept` with zero files
        # written. Re-raise it so the scheduler emits `node_failed` and the
        # kept list stays true.
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for outcome in results:
            if isinstance(outcome, RunCancelled):
                raise outcome

    def _resolve_backend(self, ctx: Any, kind: str) -> Any:
        """Resolve a backend from ctx.{kind}_backend or ctx.config.{kind}_backend."""
        attr = f"{kind}_backend"
        # Try ctx attr first (explicit injection for tests and advanced users)
        backend = getattr(ctx, attr, None)
        if backend is not None:
            return backend
        # Fall back to ctx.config.{kind}_backend → BackendRegistry lookup
        backend_name = getattr(getattr(ctx, "config", None), attr, None)
        if backend_name:
            try:
                from canon.backends.registry import BackendRegistry

                if kind == "image":
                    return BackendRegistry.image(backend_name)
                if kind == "music":
                    return BackendRegistry.music(backend_name)
                if kind == "sfx":
                    return BackendRegistry.sfx(backend_name)
            except KeyError:
                logger.warning(
                    "AssetPhase: %s backend %r not registered", kind, backend_name
                )
        return None

    # ------------------------------------------------------------------
    # Image task builders
    # ------------------------------------------------------------------

    def _build_image_tasks(self, ctx: Any, backend: Any, progress: _ItemProgress) -> list:
        """Return a list of awaitable tasks for image generation."""
        portraits_dir = self._portraits_dir(ctx)
        sem = asyncio.Semaphore(self.image_concurrency)
        width, height = self.portrait_size
        tasks = []

        async def _bounded(
            prompt: str,
            filepath: Path,
            on_success_attr: tuple[Any, str] | None,
        ) -> None:
            async with sem:
                progress.announce(f"portrait · {filepath.stem}")
                filepath.parent.mkdir(parents=True, exist_ok=True)
                ok = await backend.generate_and_save_async(
                    prompt, str(filepath), width, height
                )
                if ok and on_success_attr is not None:
                    obj, attr = on_success_attr
                    setattr(obj, attr, str(filepath))
                stats = getattr(ctx, "stats", None)
                if stats is not None:
                    _inc(stats, "image_attempts", 1)
                    if ok:
                        _inc(stats, "image_successes", 1)
                        # Read last_cost HERE — no await between the generate
                        # call returning and this read, so under cooperative
                        # asyncio no other task can clobber the shared field.
                        # Backends that report a per-call cost (pixellab/retro)
                        # land real dollars; ones that don't (fal/local) add 0.
                        _add_cost(stats, "image_cost_usd", backend)

        # Characters
        for char in ctx.bible.characters or []:
            prompt = char.portrait_prompt or f"a character: {char.name}"
            fp = portraits_dir / "npcs" / f"npc_{char.character_id}.png"
            tasks.append(_bounded(prompt, fp, (char, "portrait_path")))

        # Entities (per map)
        for _map_id, m in (ctx.bible.maps or {}).items():
            for entity in m.entities or []:
                prompt = entity.lore or f"a {entity.entity_type}: {entity.name}"
                prefix = {"npc": "npc", "item": "item", "monster": "mon", "event": "evt"}.get(
                    entity.entity_type, entity.entity_type
                )
                if entity.entity_type in ("monster", "item"):
                    slug = entity.name.lower().replace(" ", "_")
                    fp = portraits_dir / f"{entity.entity_type}s" / f"{prefix}_{slug}.png"
                else:
                    fp = portraits_dir / f"{entity.entity_type}s" / f"{prefix}_{entity.entity_id}.png"
                tasks.append(_bounded(prompt, fp, None))

        # Class archetypes
        for i, (_arch_id, arch) in enumerate((ctx.bible.class_archetypes or {}).items()):
            prompt = getattr(arch, "portrait_prompt", None) or f"a {arch.name}"
            fp = portraits_dir / "classes" / f"class_{i}.png"
            tasks.append(_bounded(prompt, fp, (arch, "portrait_path")))

        # Per-map environment portraits
        for i, (_map_id, m) in enumerate((ctx.bible.maps or {}).items()):
            prompt = f"a {m.environment}: {m.name}"
            fp = portraits_dir / f"environment_{i}.png"
            tasks.append(_bounded(prompt, fp, None))

        return tasks

    # ------------------------------------------------------------------
    # Music task builders
    # ------------------------------------------------------------------

    def _build_music_tasks(self, ctx: Any, backend: Any, progress: _ItemProgress) -> list:
        """Return a list of awaitable tasks for music generation.

        Generates: combat, maze_<env> per unique env, puzzle_event,
        start_screen, victory, game_over.
        """
        music_dir = self._music_dir(ctx)
        sem = asyncio.Semaphore(self.music_concurrency)
        tasks = []

        async def _bounded(prompt: str, filepath: Path, duration: int) -> None:
            async with sem:
                progress.announce(f"music · {filepath.stem}")
                filepath.parent.mkdir(parents=True, exist_ok=True)
                ok = await backend.generate_and_save_async(prompt, str(filepath), duration)
                stats = getattr(ctx, "stats", None)
                if stats is not None:
                    _inc(stats, "music_attempted", 1)
                    if ok:
                        _inc(stats, "music_succeeded", 1)
                        _add_cost(stats, "audio_cost_usd", backend)

        environments = {
            m.environment
            for m in (ctx.bible.maps or {}).values()
            if getattr(m, "environment", None)
        }

        # Fixed tracks
        tasks.append(_bounded("combat music, intense", music_dir / "combat.mp3", 120))
        tasks.append(_bounded("puzzle / event tension", music_dir / "puzzle_event.mp3", 120))
        tasks.append(
            _bounded("start screen, atmospheric", music_dir / "start_screen.mp3", 120)
        )
        tasks.append(_bounded("victory fanfare", music_dir / "victory.mp3", 120))
        tasks.append(_bounded("game over, somber", music_dir / "game_over.mp3", 30))

        # Per-environment tracks
        for env in environments:
            tasks.append(
                _bounded(f"ambient {env} music", music_dir / f"maze_{env}.mp3", 120)
            )

        return tasks

    # ------------------------------------------------------------------
    # SFX task builders
    # ------------------------------------------------------------------

    def _build_sfx_tasks(self, ctx: Any, backend: Any, progress: _ItemProgress) -> list:
        """Fixed SFX catalog (matches mazeworld's standard effects) + per-env ambience."""
        sfx_dir = self._sfx_dir(ctx)
        sem = asyncio.Semaphore(self.sfx_concurrency)
        tasks = []

        async def _bounded(
            prompt: str, filepath: Path, duration: float, loop: bool
        ) -> None:
            async with sem:
                progress.announce(f"sfx · {filepath.stem}")
                filepath.parent.mkdir(parents=True, exist_ok=True)
                ok = await backend.generate_and_save_async(
                    prompt, str(filepath), duration, loop
                )
                stats = getattr(ctx, "stats", None)
                if stats is not None:
                    _inc(stats, "sfx_attempted", 1)
                    if ok:
                        _inc(stats, "sfx_succeeded", 1)
                        _add_cost(stats, "audio_cost_usd", backend)

        # Core fixed effects
        FIXED: list[tuple[str, str, float, bool]] = [
            ("weapon_light_swing", "a quick light weapon swing", 0.6, False),
            ("weapon_light_hit", "a light weapon impact", 0.4, False),
            ("weapon_heavy_swing", "a heavy weapon swing", 0.8, False),
            ("weapon_heavy_hit", "a heavy weapon impact", 0.6, False),
            ("spell_damage_single_cast", "a single-target spell cast", 0.8, False),
            ("spell_heal_cast", "a healing spell cast", 1.0, False),
            ("player_take_damage", "player taking damage", 0.5, False),
            ("player_death", "player dying", 1.5, False),
            ("item_pickup", "item pickup chime", 0.4, False),
            ("door_open", "door opening", 1.2, False),
            ("dice_roll", "dice rolling", 0.8, False),
            ("event_complete", "event resolved", 1.0, False),
        ]
        for name, prompt, dur, loop in FIXED:
            tasks.append(_bounded(prompt, sfx_dir / f"{name}.mp3", dur, loop))

        # Per-environment ambience (looped)
        environments = {
            m.environment
            for m in (ctx.bible.maps or {}).values()
            if getattr(m, "environment", None)
        }
        for env in environments:
            tasks.append(
                _bounded(
                    f"{env} ambience, looped",
                    sfx_dir / f"ambience_{env}.mp3",
                    5.0,
                    True,
                )
            )

        return tasks

    # ------------------------------------------------------------------
    # Directory helpers
    # ------------------------------------------------------------------

    def _portraits_dir(self, ctx: Any) -> Path:
        out = Path(getattr(ctx.config, "output_dir", "."))
        sub = _get_output_path(ctx, "portraits_dir", "portraits")
        return out / sub

    def _music_dir(self, ctx: Any) -> Path:
        out = Path(getattr(ctx.config, "output_dir", "."))
        sub = _get_output_path(ctx, "music_dir", "music")
        return out / sub

    def _sfx_dir(self, ctx: Any) -> Path:
        out = Path(getattr(ctx.config, "output_dir", "."))
        sub = _get_output_path(ctx, "sfx_dir", "sfx")
        return out / sub

    def _stamp_metadata(self, ctx: Any) -> None:
        if not isinstance(getattr(ctx.bible, "metadata", None), BibleMetadata):
            ctx.bible.metadata = BibleMetadata()
        ctx.bible.metadata.phases_run.append(self.name)


# ------------------------------------------------------------------
# Module-level helpers (not part of the public API)
# ------------------------------------------------------------------


def _get_output_path(ctx: Any, key: str, default: str) -> str:
    """Safely retrieve a subdir name from ctx.config.output_paths."""
    output_paths = getattr(getattr(ctx, "config", None), "output_paths", None)
    if output_paths is None:
        return default
    if isinstance(output_paths, dict):
        return output_paths.get(key, default)
    # Duck-typed object with .get()
    getter = getattr(output_paths, "get", None)
    if callable(getter):
        return getter(key, default)
    return default


def _inc(stats: Any, attr: str, delta: int | float) -> None:
    """Safely increment a stats attribute; creates it at 0 if absent."""
    current = getattr(stats, attr, None)
    if current is None:
        try:
            setattr(stats, attr, delta)
        except AttributeError:
            pass
    else:
        try:
            setattr(stats, attr, current + delta)
        except AttributeError:
            pass


def _add_cost(stats: Any, attr: str, backend: Any) -> None:
    """Add the backend's most-recent per-call cost to a stats total. Call ONLY
    on the line immediately after ``await backend.generate_and_save_async(...)``
    (no await in between) so the shared ``last_cost`` field is still this call's
    value — the AssetPhase gather makes reads at any other point racy."""
    _inc(stats, attr, float(getattr(backend, "last_cost", 0.0) or 0.0))
