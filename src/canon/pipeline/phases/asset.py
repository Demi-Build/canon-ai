"""AssetPhase — generates portraits + music + SFX for all entities that need them.

Uses async fan-out via asyncio.gather() across all three backend protocols.
Stamps the path it actually wrote back onto the in-memory object that owns it
(a character, an archetype, an entity stub, a map, ``ctx.artifacts``), so the
write-back that flushes those paths into the emitted row files never has to
re-derive a filename.

This module is the ONE place the portrait naming convention lives:
``entity_portrait_rel`` / ``class_portrait_rel`` / ``environment_portrait_rel``
build it, this phase writes it, and a repair that adopts portraits already on
disk asks the same functions rather than guessing. Every recorded path is
POSIX and RELATIVE to the pack root — emitted pack content must not carry the
absolute path of the machine that generated it (it would differ between two
same-seed runs, and it would not resolve on anyone else's disk).

Per the plan A5: this phase NEVER bypasses backend protocols. All image
generation goes through ImageBackend.generate_and_save_async (or sync if
backend.prefers_serial). Music/SFX go through their respective protocols.

Failures are acceptable; silence is not. Every asset is an ``AssetJob`` (data:
family, target, file, prompt, params) run by ONE executor that retries a
RETRYABLE failure (``canon.backends.failures`` — the chat backends' taxonomy,
extended to the asset providers) up to ``ASSET_RETRIES`` times with backoff,
calls a non-retryable one exactly once, counts every call in the stats, and
records each final failure where a person can read it: ``ctx.stats.failures``
and an ``asset_failed`` step-log event. ``run_jobs`` runs an explicit job list
through that same executor — the repair verb's ``--target missing`` — so a
pack that lost assets is finished for the price of what is missing.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from canon.bible.models import BibleMetadata
from canon.pipeline.steplog import RunCancelled, step

if TYPE_CHECKING:
    from canon.backends.failures import AssetError

# ``canon.backends`` is imported lazily below: the package init pulls the
# chat protocol in through ``canon.llm``, and this module is imported by
# ``canon.__init__`` before ``canon.llm`` finishes — a module-level import
# here is a circular one.

logger = logging.getLogger(__name__)

#: Filename prefix per entity kind. An OPEN vocabulary: a kind this map does
#: not name uses its own name (``quest`` → ``quest_4000.png``), so a pack that
#: declares a new kind gets a portrait path without editing canon.
PORTRAIT_PREFIXES: dict[str, str] = {
    "npc": "npc",
    "item": "item",
    "monster": "mon",
    "event": "evt",
}

#: Kinds whose portrait filename is keyed by the row's NAME slug instead of its
#: id. This is the convention already on disk in every generated pack; making it
#: uniform would orphan every portrait a previous run wrote.
NAME_KEYED_PORTRAITS: frozenset[str] = frozenset({"monster", "item"})

#: Kinds whose portrait file is keyed by POSITION in its collection rather than
#: by anything on the row, mapped to the path template that names it. The class
#: list is positional on disk, so the i-th class owns ``classes/class_i.png``.
#: Data, like the two maps above: a pack that adds such a kind adds an entry.
POSITIONAL_PORTRAITS: dict[str, str] = {"class": "classes/class_{index}.png"}

#: The fixed portraits the manifest indexes by name — the image twins of the
#: fixed music tracks. ``(artifact_key, file stem, prompt)``; the artifact key
#: is the ``manifest.json`` field ManifestPhase reads back.
FIXED_PORTRAITS: tuple[tuple[str, str, str], ...] = (
    ("player_portrait", "player", "the player character, heroic portrait"),
    ("start_portrait", "start_screen", "a start screen, atmospheric"),
    ("gameover_portrait", "game_over", "a game over scene, somber"),
    ("victory_portrait", "victory", "a victory scene, triumphant"),
)


def portrait_slug(name: str) -> str:
    """The filename slug of a row NAME (``"Ash Golem"`` → ``"ash_golem"``)."""
    return str(name).lower().replace(" ", "_")


def entity_portrait_rel(
    entity_type: str, entity_id: Any, name: Any, *, portraits_dir: str = "portraits"
) -> str:
    """The pack-relative portrait path of one entity row."""
    prefix = PORTRAIT_PREFIXES.get(entity_type, entity_type)
    stem = portrait_slug(name) if entity_type in NAME_KEYED_PORTRAITS else str(entity_id)
    return f"{portraits_dir}/{entity_type}s/{prefix}_{stem}.png"


def row_portrait_rel(
    kind: str,
    *,
    index: int = 0,
    row_id: Any = None,
    name: Any = None,
    portraits_dir: str = "portraits",
) -> str:
    """The pack-relative portrait path of ONE ROW of *kind*.

    The single entry point shared by the phase that writes the file and by a
    repair that adopts one already on disk, so the two can never disagree
    about where a portrait lives. *index* is the row's position in its
    collection (only read for a positional kind); *row_id* and *name* are the
    row's own id and name.
    """
    template = POSITIONAL_PORTRAITS.get(kind)
    if template is not None:
        return f"{portraits_dir}/{template.format(index=index)}"
    return entity_portrait_rel(kind, row_id, name, portraits_dir=portraits_dir)


def class_portrait_rel(index: int, *, portraits_dir: str = "portraits") -> str:
    """The pack-relative portrait path of the *index*-th class archetype."""
    return row_portrait_rel("class", index=index, portraits_dir=portraits_dir)


def environment_portrait_rel(index: int, *, portraits_dir: str = "portraits") -> str:
    """The pack-relative portrait path of the *index*-th map's environment."""
    return f"{portraits_dir}/environment_{index}.png"


def fixed_portrait_rel(stem: str, *, portraits_dir: str = "portraits") -> str:
    """The pack-relative path of a fixed (non-row) portrait."""
    return f"{portraits_dir}/{stem}.png"



# ---------------------------------------------------------------------------
# The asset job — one file a backend is asked for, as data
# ---------------------------------------------------------------------------

#: Concurrency per asset family — the ONE place these numbers live. ``sfx``
#: is 2: the first paid dungeon run fired 10 ElevenLabs requests at once on
#: the lowest tier and ~5 were dropped within 160 ms. A per-PROVIDER table
#: (``{"elevenlabs": 2, "fal": 15, …}``, keyed by registry id and read off the
#: backend the phase resolved) belongs beside ``canon.pricing`` — the module
#: that already holds every other per-provider figure — with these family
#: defaults as its fallback; until it exists a backend may declare its own
#: ``max_concurrency`` and the phase honours the smaller of the two.
ASSET_CONCURRENCY: dict[str, int] = {"image": 15, "music": 8, "sfx": 2}

#: Music the dungeon ships: ``(stem, prompt, seconds)``. The fixed tracks
#: first, then one ``maze_<environment>`` per environment (added at plan
#: time). Under 60 s stays on Lyria's clip model; these are full tracks.
MUSIC_CATALOG: tuple[tuple[str, str, int], ...] = (
    ("combat", "combat music, intense", 120),
    ("puzzle_event", "puzzle / event tension", 120),
    ("start_screen", "start screen, atmospheric", 120),
    ("victory", "victory fanfare", 120),
    ("game_over", "game over, somber", 30),
)

#: The music track length for per-environment ambience music.
MUSIC_ENV_SECONDS = 120

#: The dungeon's fixed SFX catalog (matches mazeworld's standard effects):
#: ``(stem, prompt, seconds, loop)``. Every duration must sit inside the SFX
#: provider's bounds — ``validate_sfx_catalog`` refuses the catalog at
#: construction time otherwise, so an impossible entry is never sent.
#: ``weapon_light_hit`` and ``item_pickup`` were 0.4 s: two entries ElevenLabs
#: could never serve (its floor is 0.5 s), lost on every paid run. Raised to
#: the floor — the platformer's ``SFX_MIN_SECONDS`` clamp does the same.
SFX_CATALOG: tuple[tuple[str, str, float, bool], ...] = (
    ("weapon_light_swing", "a quick light weapon swing", 0.6, False),
    ("weapon_light_hit", "a light weapon impact", 0.5, False),
    ("weapon_heavy_swing", "a heavy weapon swing", 0.8, False),
    ("weapon_heavy_hit", "a heavy weapon impact", 0.6, False),
    ("spell_damage_single_cast", "a single-target spell cast", 0.8, False),
    ("spell_heal_cast", "a healing spell cast", 1.0, False),
    ("player_take_damage", "player taking damage", 0.5, False),
    ("player_death", "player dying", 1.5, False),
    ("item_pickup", "item pickup chime", 0.5, False),
    ("door_open", "door opening", 1.2, False),
    ("dice_roll", "dice rolling", 0.8, False),
    ("event_complete", "event resolved", 1.0, False),
)

#: Per-environment ambience loop length.
AMBIENCE_SECONDS = 5.0

#: ``family → (attempts field, successes field, cost field)`` on
#: ``GenerationStats``. The image lane keeps its v0.1 names, the audio lanes
#: their v0.2 ones, and both audio lanes share one cost bucket — exactly the
#: fields the estimator's per-unit calibration divides. An ATTEMPT is one
#: backend call: a retried asset counts every call it made, so ``attempts``
#: stays honest and ``successes`` keeps meaning files that landed.
STATS_FIELDS: dict[str, tuple[str, str, str]] = {
    "image": ("image_attempts", "image_successes", "image_cost_usd"),
    "music": ("music_attempted", "music_succeeded", "audio_cost_usd"),
    "sfx": ("sfx_attempted", "sfx_succeeded", "audio_cost_usd"),
}

#: The step-log event a FINAL asset failure emits (after its retries).
ASSET_FAILED_EVENT = "asset_failed"


@dataclass
class AssetJob:
    """One asset a backend is asked for — the unit the executor retries,
    counts and reports on.

    ``family`` picks the backend and the stats fields (``image`` / ``music``
    / ``sfx`` — the ``STATS_FIELDS`` keys); ``target`` is the pack address
    the failure list and the repair verb speak (``npc:1000``,
    ``class:warrior``, ``room:room_0``, ``portrait:player``, ``music:combat``,
    ``sfx:door_open``); ``rel`` is the pack-relative file; ``params`` are the
    backend call's remaining arguments (``width``/``height``, ``duration``,
    ``duration``+``loop``); ``slot`` is the in-memory owner the path is
    recorded on after a success (see ``_record``).
    """

    family: str
    target: str
    rel: str
    prompt: str
    params: dict[str, Any] = field(default_factory=dict)
    slot: tuple[Any, str] | None = None

    @property
    def label(self) -> str:
        """The progress line (``portrait · npc_1000``)."""
        family = "portrait" if self.family == "image" else self.family
        return f"{family} · {Path(self.rel).stem}"


def sfx_duration_bounds() -> tuple[float, float]:
    """The SFX provider's ``(min, max)`` seconds — ElevenLabs states its own
    limit once (``canon.backends.sfx_elevenlabs.SFX_DURATION_BOUNDS``) and
    every catalog check reads it from there."""
    from canon.backends.sfx_elevenlabs import SFX_DURATION_BOUNDS

    return SFX_DURATION_BOUNDS


def validate_sfx_catalog(
    catalog: Sequence[tuple[str, str, float, bool]] | None = None,
    bounds: tuple[float, float] | None = None,
) -> list[str]:
    """Every catalog entry whose duration the SFX provider cannot serve, as
    ``"<stem>: <seconds>s is below/above the <lo>–<hi>s range"`` — empty when
    the catalog is sound. The bounds default to the SFX provider's own
    (``sfx_duration_bounds``): the provider states its limit once and the
    catalog is checked against it here, before a single request is made."""
    lo, hi = bounds if bounds is not None else sfx_duration_bounds()
    problems: list[str] = []
    for stem, _prompt, seconds, _loop in (SFX_CATALOG if catalog is None else catalog):
        if seconds < lo:
            problems.append(f"{stem}: {seconds}s is below the {lo}–{hi}s range")
        elif seconds > hi:
            problems.append(f"{stem}: {seconds}s is above the {lo}–{hi}s range")
    return problems


def check_sfx_catalog(
    catalog: Sequence[tuple[str, str, float, bool]] | None = None,
    bounds: tuple[float, float] | None = None,
) -> None:
    """Refuse an impossible SFX catalog with a ``ValueError`` naming every
    offending entry — called when the SFX lane is wired, before any spend."""
    problems = validate_sfx_catalog(catalog, bounds)
    if problems:
        raise ValueError(
            "SFX catalog has entries the sound-effects provider cannot serve — "
            "fix the durations before generating: " + "; ".join(problems)
        )


def record_asset_failure(
    ctx: Any, *, family: str, target: str, rel: str, error: AssetError,
    node: str = "phase:assets", repair: str | None = None,
) -> dict[str, Any]:
    """The FINAL word on one asset that did not land, in every place a person
    can read it: ``ctx.stats.failures`` (→ ``generation_stats.failures``),
    one ``asset_failed`` step-log event (→ ``.canon/log.jsonl``, which cradle
    tails), and the log. Returns the record. Shared with the platformer's
    art / audio phases so both templates report the same shape.

    ``repair`` is the clause the record's ``hint`` ends on — the command
    THIS pack's verbs accept for this asset. The pipeline's own jobs leave
    it unset (``asset generate --target missing`` is theirs); a template
    whose verbs speak another grammar passes its own, so a hint never
    names a target the pack would refuse."""
    if repair:
        error.repair = repair
    record = {"kind": family, "target": target, "path": rel, **error.to_record()}
    stats = getattr(ctx, "stats", None)
    if stats is not None:
        failures = getattr(stats, "failures", None)
        if failures is None:
            try:
                stats.failures = []
                failures = stats.failures
            except AttributeError:  # pragma: no cover — a frozen stats object
                failures = None
        if failures is not None:
            failures.append(record)
    steplog = getattr(ctx, "steplog", None)
    if steplog is not None:
        steplog.emit(ASSET_FAILED_EVENT, node=node, item=Path(rel).stem, **record)
    logger.warning(
        "asset failed: %s (%s) after %d attempt(s) — %s%s: %s",
        target, rel, error.attempts, error.kind,
        f" {error.status}" if error.status else "", error,
    )
    return record


class _ItemProgress:
    """The shared ``node_item`` counter for
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
        image_concurrency: max concurrent image requests (``ASSET_CONCURRENCY``)
        music_concurrency: max concurrent music requests
        sfx_concurrency: max concurrent SFX requests (2 — see the table)
        skip_image: skip portrait generation entirely
        skip_music: skip music generation
        skip_sfx: skip SFX generation
        portrait_size: (width, height) for portraits (default 512×512)
        retries: retries per RETRYABLE failure after the first call
            (``canon.backends.failures.ASSET_RETRIES``); a non-retryable
            failure — 401/403/400/404, a validation refusal — is called once
            and listed.
        retry_backoff: seconds between retries (the module default; tests
            pass zeros).

    Reads from ctx:
        - ctx.image_backend (or ctx.config.image_backend → BackendRegistry lookup)
        - ctx.music_backend / ctx.sfx_backend similarly
        - ctx.bible.characters: list[Character] — each gets a portrait
        - ctx.bible.maps[*].entities: list[EntityLore] — each gets a portrait
        - ctx.bible.class_archetypes: dict[str, ClassArchetype] — each gets a portrait
        - ctx.bible.maps[*]: each gets an environment portrait
        - ctx.config.output_dir: base path
        - ctx.config.output_paths: subdir for portraits, music, sfx

    Failures are never silent: every final failure lands on
    ``ctx.stats.failures`` and as an ``asset_failed`` step-log event
    (``record_asset_failure``); the phase itself never raises for one.
    """

    name: str = "assets"

    def __init__(
        self,
        image_concurrency: int | None = None,
        music_concurrency: int | None = None,
        sfx_concurrency: int | None = None,
        skip_image: bool = False,
        skip_music: bool = False,
        skip_sfx: bool = False,
        portrait_size: tuple[int, int] = (512, 512),
        retries: int | None = None,
        retry_backoff: Sequence[float] | None = None,
    ) -> None:
        self.image_concurrency = (
            ASSET_CONCURRENCY["image"] if image_concurrency is None else image_concurrency
        )
        self.music_concurrency = (
            ASSET_CONCURRENCY["music"] if music_concurrency is None else music_concurrency
        )
        self.sfx_concurrency = (
            ASSET_CONCURRENCY["sfx"] if sfx_concurrency is None else sfx_concurrency
        )
        self.skip_image = skip_image
        self.skip_music = skip_music
        self.skip_sfx = skip_sfx
        self.portrait_size = portrait_size
        self.retries = retries
        self.retry_backoff = retry_backoff
        # Skeleton-time refusal: a catalog entry the provider cannot serve is
        # a bug in the catalog, caught before any LLM money is spent — not
        # a per-asset failure discovered after it.
        if not skip_sfx:
            check_sfx_catalog()

    def run(self, ctx: Any) -> None:
        asyncio.run(self._run_async(ctx))
        self._stamp_metadata(ctx)
        logger.info("AssetPhase complete.")

    # ------------------------------------------------------------------
    # Plan (the bible → jobs) and execute (jobs → files, retried, counted)
    # ------------------------------------------------------------------

    def _backends(self, ctx: Any) -> dict[str, Any]:
        """``family → backend`` for every family that is neither skipped nor
        without a backend."""
        out: dict[str, Any] = {}
        for family, skipped in (
            ("image", self.skip_image), ("music", self.skip_music), ("sfx", self.skip_sfx),
        ):
            if skipped:
                continue
            backend = self._resolve_backend(ctx, family)
            if backend is not None:
                out[family] = backend
        return out

    def plan(self, ctx: Any) -> list[AssetJob]:
        """Every job the bible owns, for the families that have a backend —
        the same list ``run`` executes, exposed so a caller can inspect it."""
        backends = self._backends(ctx)
        jobs: list[AssetJob] = []
        if "image" in backends:
            jobs.extend(self.plan_image_jobs(ctx))
        if "music" in backends:
            jobs.extend(self.plan_music_jobs(ctx))
        if "sfx" in backends:
            jobs.extend(self.plan_sfx_jobs(ctx))
        return jobs

    async def _run_async(self, ctx: Any) -> None:
        backends = self._backends(ctx)
        jobs = self.plan(ctx)
        if not jobs:
            logger.warning("AssetPhase: no asset backends registered; nothing to do.")
            return
        await self._execute(ctx, jobs, backends)

    def run_jobs(self, ctx: Any, jobs: Sequence[AssetJob]) -> list[dict[str, Any]]:
        """Execute an EXPLICIT job list (the repair verb's entry point: the
        jobs a pack on disk is missing) through the same retrying, counting,
        reporting executor ``run`` uses. Families with no backend on ``ctx``
        are skipped and reported. Returns the failure records."""
        backends = self._backends(ctx)
        runnable = [job for job in jobs if job.family in backends]
        for job in jobs:
            if job.family not in backends:
                logger.warning(
                    "AssetPhase: %s skipped — no %s backend was given", job.target, job.family,
                )
        before = len(_failures_of(ctx))
        if runnable:
            asyncio.run(self._execute(ctx, runnable, backends))
        return _failures_of(ctx)[before:]

    async def _execute(self, ctx: Any, jobs: Sequence[AssetJob], backends: dict[str, Any]) -> None:
        # One shared ``node_item`` counter across the
        # three asset families — each job announces the file it is about to
        # write through the ONE emitter (progress + the A4.5 cancel boundary).
        progress = _ItemProgress(ctx, self.name)
        progress.total = len(jobs)
        limits = {
            "image": self.image_concurrency,
            "music": self.music_concurrency,
            "sfx": self.sfx_concurrency,
        }
        semaphores: dict[str, asyncio.Semaphore] = {}
        for family, backend in backends.items():
            declared = getattr(backend, "max_concurrency", None)
            limit = limits[family]
            if isinstance(declared, int) and declared > 0:
                limit = min(limit, declared)
            semaphores[family] = asyncio.Semaphore(max(1, limit))
        tasks = [
            self._run_job(ctx, job, backends[job.family], semaphores[job.family], progress)
            for job in jobs
        ]
        # Fire it all in one gather — a failed asset is captured, not
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
            if isinstance(outcome, BaseException):  # pragma: no cover — _run_job records its own
                logger.error("AssetPhase: a job escaped its failure record: %r", outcome)

    async def _run_job(
        self, ctx: Any, job: AssetJob, backend: Any, sem: asyncio.Semaphore, progress: _ItemProgress,
    ) -> None:
        """One job: announce, call (retrying a retryable failure), count
        every call, record the path on success and the reason on failure."""
        from canon.backends.failures import (
            AssetError,
            cancel_hook,
            classify_exception,
            retry_call_async,
        )

        out_dir = Path(getattr(ctx.config, "output_dir", "."))
        filepath = out_dir / job.rel
        stats = getattr(ctx, "stats", None)
        attempts_field, successes_field, cost_field = STATS_FIELDS[job.family]
        provider = provider_id(backend)

        async def attempt() -> None:
            if stats is not None:
                _inc(stats, attempts_field, 1)
            ok = await _call_backend(backend, job, str(filepath))
            if not ok:
                # The backend said no. Its own classified reason when it kept
                # one (the paid backends do); a foreign backend that returned
                # a bare False gets the owner's default — retry it.
                raise getattr(backend, "last_error", None) or AssetError(
                    "backend reported failure without a reason",
                    retryable=True, kind="UnknownFailure", provider=provider,
                )
            if stats is not None:
                _inc(stats, successes_field, 1)
                # Read last_cost HERE — no await between the generate call
                # returning and this read, so under cooperative asyncio no
                # other task can clobber the shared field. Backends that
                # report a per-call cost (pixellab/retro) land real dollars;
                # ones that don't (fal/local) add their list price.
                _add_cost(stats, cost_field, backend)

        async with sem:
            progress.announce(job.label)
            filepath.parent.mkdir(parents=True, exist_ok=True)
            try:
                # ``should_stop``: a ⏹ Stop between attempts ends the retries
                # — no paid call is made after the user asked for none.
                await retry_call_async(
                    attempt, provider=provider, label=job.target,
                    retries=self.retries, backoff=self.retry_backoff,
                    should_stop=cancel_hook(ctx),
                )
            except RunCancelled:
                raise
            except Exception as exc:  # noqa: BLE001 — the loop's own failure is a failure too
                # Almost always the classified ``AssetError`` the retry loop
                # raised; anything else (a bug in the counting above) is
                # classified here rather than vanishing into the gather.
                err = exc if isinstance(exc, AssetError) else classify_exception(exc, provider=provider)
                record_asset_failure(
                    ctx, family=job.family, target=job.target, rel=job.rel, error=err,
                    node=f"phase:{self.name}",
                )
                return
            # The RELATIVE path, and only on success: a slot that names a
            # file no backend wrote is the same lie as an empty one.
            if job.slot is not None:
                _record(job.slot, job.rel)

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
    # Image jobs
    # ------------------------------------------------------------------

    def plan_image_jobs(self, ctx: Any) -> list[AssetJob]:
        """One portrait job per character, entity, archetype and map, plus
        the fixed portraits the manifest indexes by name.

        Every job carries the slot that OWNS the file it writes, so a portrait
        that lands on disk is also recorded in memory. A slot is
        ``(target, key)`` where *target* is an object (``setattr``) or a dict
        (item assignment) — ``EntityLore.extra`` and ``Map.extra`` are the
        pack-shaped homes for keys the core models do not declare, and
        ``ctx.artifacts`` is where ManifestPhase already looks for the fixed
        portraits.
        """
        sub = _get_output_path(ctx, "portraits_dir", "portraits")
        width, height = self.portrait_size
        size = {"width": width, "height": height}
        jobs: list[AssetJob] = []

        # Characters
        for char in ctx.bible.characters or []:
            prompt = char.portrait_prompt or f"a character: {char.name}"
            rel = entity_portrait_rel("npc", char.character_id, char.name, portraits_dir=sub)
            jobs.append(AssetJob(
                "image", f"npc:{char.character_id}", rel, prompt, dict(size),
                (char, "portrait_path"),
            ))

        # Entities (per map)
        for _map_id, m in (ctx.bible.maps or {}).items():
            for entity in m.entities or []:
                prompt = entity.lore or f"a {entity.entity_type}: {entity.name}"
                rel = entity_portrait_rel(
                    entity.entity_type, entity.entity_id, entity.name, portraits_dir=sub
                )
                jobs.append(AssetJob(
                    "image", f"{entity.entity_type}:{entity.entity_id}", rel, prompt,
                    dict(size), (entity.extra, "portrait_path"),
                ))

        # Class archetypes
        for i, (arch_id, arch) in enumerate((ctx.bible.class_archetypes or {}).items()):
            prompt = getattr(arch, "portrait_prompt", None) or f"a {arch.name}"
            rel = class_portrait_rel(i, portraits_dir=sub)
            key = getattr(arch, "archetype", "") or arch_id
            jobs.append(AssetJob(
                "image", f"class:{key}", rel, prompt, dict(size), (arch, "portrait_path"),
            ))

        # Per-map environment portraits
        for i, (map_id, m) in enumerate((ctx.bible.maps or {}).items()):
            prompt = f"a {m.environment}: {m.name}"
            rel = environment_portrait_rel(i, portraits_dir=sub)
            jobs.append(AssetJob(
                "image", f"room:{map_id}", rel, prompt, dict(size),
                (m.extra, "environment_portrait"),
            ))

        # The fixed portraits the manifest indexes by name. Without these the
        # manifest advertises four portrait fields it can only ever leave empty.
        artifacts = getattr(ctx, "artifacts", None)
        for key, stem, prompt in FIXED_PORTRAITS:
            rel = fixed_portrait_rel(stem, portraits_dir=sub)
            slot = (artifacts, key) if isinstance(artifacts, dict) else None
            jobs.append(AssetJob("image", f"portrait:{stem}", rel, prompt, dict(size), slot))

        return jobs

    # ------------------------------------------------------------------
    # Music jobs
    # ------------------------------------------------------------------

    def plan_music_jobs(self, ctx: Any) -> list[AssetJob]:
        """The fixed tracks (``MUSIC_CATALOG``) plus ``maze_<env>`` per
        unique environment."""
        sub = _get_output_path(ctx, "music_dir", "music")
        environments = _environments(ctx)
        return music_jobs(environments, music_dir=sub)

    # ------------------------------------------------------------------
    # SFX jobs
    # ------------------------------------------------------------------

    def plan_sfx_jobs(self, ctx: Any) -> list[AssetJob]:
        """The fixed SFX catalog + per-environment ambience (looped)."""
        sub = _get_output_path(ctx, "sfx_dir", "sfx")
        environments = _environments(ctx)
        return sfx_jobs(environments, sfx_dir=sub)

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


def music_jobs(environments: Iterable[str], *, music_dir: str = "music") -> list[AssetJob]:
    """The music jobs a pack with these environments owns — the catalog
    order, then one ``maze_<env>`` per environment in the order given. Shared
    by the phase (bible environments) and the repair (rows on disk)."""
    jobs = [
        AssetJob("music", f"music:{stem}", f"{music_dir}/{stem}.mp3", prompt, {"duration": seconds})
        for stem, prompt, seconds in MUSIC_CATALOG
    ]
    for env in environments:
        jobs.append(AssetJob(
            "music", f"music:maze_{env}", f"{music_dir}/maze_{env}.mp3",
            f"ambient {env} music", {"duration": MUSIC_ENV_SECONDS},
        ))
    return jobs


def sfx_jobs(environments: Iterable[str], *, sfx_dir: str = "sfx") -> list[AssetJob]:
    """The SFX jobs a pack with these environments owns — the fixed catalog,
    then one looped ``ambience_<env>`` per environment in the order given."""
    jobs = [
        AssetJob(
            "sfx", f"sfx:{stem}", f"{sfx_dir}/{stem}.mp3", prompt,
            {"duration": seconds, "loop": loop},
        )
        for stem, prompt, seconds, loop in SFX_CATALOG
    ]
    for env in environments:
        jobs.append(AssetJob(
            "sfx", f"sfx:ambience_{env}", f"{sfx_dir}/ambience_{env}.mp3",
            f"{env} ambience, looped", {"duration": AMBIENCE_SECONDS, "loop": True},
        ))
    return jobs


def _environments(ctx: Any) -> list[str]:
    """The unique environments of the bible's maps, in the set order the
    phase has always iterated (kept so fake runs stay byte-identical)."""
    return list({
        m.environment
        for m in (ctx.bible.maps or {}).values()
        if getattr(m, "environment", None)
    })


async def _call_backend(backend: Any, job: AssetJob, filepath: str) -> bool:
    """The protocol call for the job's family (``generate_and_save_async``
    with that family's argument list)."""
    p = job.params
    if job.family == "image":
        return await backend.generate_and_save_async(job.prompt, filepath, p["width"], p["height"])
    if job.family == "music":
        return await backend.generate_and_save_async(job.prompt, filepath, p["duration"])
    if job.family == "sfx":
        return await backend.generate_and_save_async(
            job.prompt, filepath, p["duration"], bool(p.get("loop", False))
        )
    raise ValueError(f"unknown asset family {job.family!r}")


def provider_id(backend: Any) -> str:
    """The backend's registry id when its module declares one
    (``PROVIDER_ID``), else its class name — the ``provider`` a failure
    record names. A producer WRAPPER (the platformer's
    ``DiffusionSheetProducer``) answers with the backend it wraps."""
    inner = getattr(backend, "backend", None)
    if inner is not None and inner is not backend:
        return provider_id(inner)
    module = sys.modules.get(type(backend).__module__)
    declared = getattr(module, "PROVIDER_ID", None)
    return str(declared) if declared else type(backend).__name__


def _failures_of(ctx: Any) -> list[dict[str, Any]]:
    stats = getattr(ctx, "stats", None)
    failures = getattr(stats, "failures", None) if stats is not None else None
    return failures if isinstance(failures, list) else []


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


def _record(slot: tuple[Any, str], value: str) -> None:
    """Write *value* into ``slot`` — ``(target, key)`` where *target* is a dict
    (``EntityLore.extra``, ``ctx.artifacts``) or any object with that attribute
    (``Character.portrait_path``). A target that refuses the write is logged and
    skipped: one unrecordable portrait must not lose the other forty."""
    target, key = slot
    try:
        if isinstance(target, dict):
            target[key] = value
        else:
            setattr(target, key, value)
    except (AttributeError, TypeError, ValueError):  # pragma: no cover — defensive
        logger.warning("AssetPhase: could not record %s on %r", key, type(target).__name__)


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
