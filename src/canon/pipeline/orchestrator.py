"""The orchestrator ("Mayor", PRD §7) — per-node DAG scheduling with
bounded parallelism, resume, edit detection, and the stale-cascade.

Opt-in: ``run_pipeline`` remains the sequential legacy path, untouched.
State lives in the Bible (``metadata.phase_status`` coarse,
``metadata.node_status`` fine) — no separate state file; the Bible is
persisted after every node commit, which is the crash-safe resume point.

Threading rule: **workers compute, the scheduler commits.** Node bodies
run in a thread pool; all status transitions and Bible persistence happen
on the scheduler thread. Concurrency is opt-in (``max_concurrency``
defaults to 1) — a phase that mutates shared context state is only safe
above cap 1 if its nodes touch disjoint state.

Mixed pipelines degrade gracefully: a legacy ``Phase`` (no ``expand``)
wraps into one opaque node with a barrier edge to the previous pipeline
item, and the item after it barriers on it too (its outputs are
undeclared). DagPhase-to-DagPhase edges come only from declared
``requires``, so independent (step, level) nodes overlap.
"""

from __future__ import annotations

import hashlib
import logging
from collections import deque
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from canon.bible.artifacts import ArtifactStatus
from canon.bible.models import BibleMetadata

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Node + DagPhase protocol
# ---------------------------------------------------------------------------


@dataclass
class Node:
    """One schedulable unit — typically a (step, level) pair.

    ``requires`` names artifact IDs this node consumes; they resolve
    against other nodes' ``produces`` (default: the node's own id).
    Requires that nothing in the run produces are treated as satisfied
    externally (pre-existing Bible artifacts) and logged once.

    ``always`` marks a node that re-runs every orchestration even when
    recorded DONE — for cheap, deterministic derived outputs (review
    renders, the root manifest) that must stay fresh after any upstream
    regeneration without needing their own place in the stale cascade.

    ``owns`` names Bible artifact IDs this node stamps beyond its own
    node id. Per-step nodes don't need it (their node id IS the artifact
    id), but a legacy phase node is keyed ``phase:<name>`` while the
    entities it writes carry their own ids (``tileset:<stage>``) — the
    stale cascade marks those entity ids, and ``owns`` is how the mark
    reschedules the producing node. Owned ids marked STALE force a
    re-run; on completion they are cleared to DONE.
    """

    node_id: str
    run: Callable[[Any], None]
    requires: list[str] = field(default_factory=list)
    produces: str = ""  # defaults to node_id
    gate: bool = False  # human gate (I4): pause point unless auto-approved
    always: bool = False  # re-run even when DONE (cheap derived outputs)
    owns: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.produces:
            self.produces = self.node_id


@runtime_checkable
class DagPhase(Protocol):
    """A phase that expands into DAG nodes. Anything without ``expand``
    is treated as a legacy sequential ``Phase``."""

    name: str

    def expand(self, ctx: Any) -> list[Node]: ...


@dataclass
class OrchestratorReport:
    """What happened, for humans and for Cradle (structured JSON)."""

    done: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # already done (resume)
    escalated: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)  # descendants of failures
    paused_at: str | None = None  # gate awaiting review

    @property
    def ok(self) -> bool:
        return not self.escalated and not self.blocked and self.paused_at is None

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "done": self.done,
            "skipped": self.skipped,
            "escalated": self.escalated,
            "blocked": self.blocked,
            "paused_at": self.paused_at,
        }


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------


def build_nodes(items: list[Any], ctx: Any) -> list[Node]:
    """Expand pipeline items into nodes, adding barrier edges around
    legacy phases (their data flow is undeclared)."""
    nodes: list[Node] = []
    prev_ids: list[str] = []
    prev_was_legacy = True  # treat the graph edge as strict until proven DAG
    for item in items:
        if hasattr(item, "expand"):
            expanded = list(item.expand(ctx))
            if prev_was_legacy and prev_ids:
                for node in expanded:
                    node.requires = list(node.requires) + prev_ids
            nodes.extend(expanded)
            prev_ids = [n.node_id for n in expanded]
            prev_was_legacy = False
        else:
            owns_hook = getattr(item, "owns", None)
            node = Node(
                node_id=f"phase:{item.name}",
                run=item.run,
                requires=list(prev_ids),
                owns=list(owns_hook(ctx)) if callable(owns_hook) else [],
            )
            nodes.append(node)
            prev_ids = [node.node_id]
            prev_was_legacy = True
    seen: set[str] = set()
    for node in nodes:
        if node.node_id in seen:
            raise ValueError(f"Duplicate node_id {node.node_id!r} in pipeline.")
        seen.add(node.node_id)
    return nodes


def _resolve_edges(nodes: list[Node]) -> dict[str, set[str]]:
    """node_id -> set of node_ids it depends on (unresolvable requires
    are treated as externally satisfied)."""
    producers = {n.produces: n.node_id for n in nodes}
    producers.update({n.node_id: n.node_id for n in nodes})
    deps: dict[str, set[str]] = {}
    external: set[str] = set()
    for node in nodes:
        resolved: set[str] = set()
        for req in node.requires:
            if req in producers:
                if producers[req] != node.node_id:
                    resolved.add(producers[req])
            else:
                external.add(req)
        deps[node.node_id] = resolved
    if external:
        logger.info(
            "Orchestrator: %d require(s) not produced this run, assumed "
            "pre-existing: %s", len(external), sorted(external),
        )
    return deps


def _check_cycles(deps: dict[str, set[str]]) -> None:
    """Kahn's algorithm; raises naming the cycle members."""
    remaining = {k: set(v) for k, v in deps.items()}
    queue = deque(k for k, v in remaining.items() if not v)
    seen = 0
    while queue:
        nid = queue.popleft()
        seen += 1
        for other, other_deps in remaining.items():
            if nid in other_deps:
                other_deps.discard(nid)
                if not other_deps:
                    queue.append(other)
        remaining[nid] = {None}  # mark processed (never re-queued)
    if seen != len(deps):
        cycle = sorted(k for k, v in remaining.items() if v and None not in v)
        raise ValueError(f"Dependency cycle among nodes: {cycle!r}")


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


def _ensure_metadata(ctx: Any) -> BibleMetadata:
    if not isinstance(getattr(ctx.bible, "metadata", None), BibleMetadata):
        ctx.bible.metadata = BibleMetadata()
    return ctx.bible.metadata


def orchestrate(
    items: list[Any],
    ctx: Any,
    *,
    max_concurrency: int | None = None,
    persist_path: str | Path | None = None,
) -> OrchestratorReport:
    """Run pipeline items as a DAG. Resume-aware: nodes recorded DONE in
    ``bible.metadata.node_status`` are skipped unless marked STALE by
    :func:`detect_edits`. USER_EDITED nodes are also skipped — the edit
    is authoritative and is never regenerated (§6.3); only its
    descendants (stale) re-run. ``always`` nodes re-run every time."""
    cap = max_concurrency or int(getattr(ctx.config, "max_concurrency", 1))
    auto_gates = bool(getattr(ctx.config, "gates_auto_approve", True))
    metadata = _ensure_metadata(ctx)

    nodes = build_nodes(items, ctx)
    node_map = {n.node_id: n for n in nodes}
    deps = _resolve_edges(nodes)
    _check_cycles(deps)

    report = OrchestratorReport()
    status = metadata.node_status
    pinned = pinned_ids(ctx.bible)
    rerun = {
        nid for nid, s in status.items() if s is ArtifactStatus.STALE
    }
    completed: set[str] = set()
    for nid, node in node_map.items():
        node_status = status.get(nid)
        if nid in pinned:
            # Pinned content is deliberately protected — never scheduled,
            # even ahead of `always` (always node ids aren't pinnable, but
            # level-step nodes are node_id == artifact_id).
            completed.add(nid)
            report.skipped.append(nid)
        elif node_status == ArtifactStatus.USER_EDITED:
            # The user's edit is authoritative — NEVER regenerate it
            # (§6.3); its output satisfies dependents as-is.
            completed.add(nid)
            report.skipped.append(nid)
        elif (
            node_status == ArtifactStatus.DONE
            and nid not in rerun
            and not node.always
            and not any(aid in rerun for aid in node.owns)
        ):
            completed.add(nid)
            report.skipped.append(nid)

    def _persist() -> None:
        if persist_path is not None and hasattr(ctx.bible, "persist"):
            ctx.bible.persist(str(persist_path))

    def _commit(nid: str, new_status: ArtifactStatus) -> None:
        # Scheduler thread only — the single writer of status + Bible.
        status[nid] = new_status
        _persist()

    dead: set[str] = set()  # escalated nodes and their descendants
    pending = [n.node_id for n in nodes if n.node_id not in completed]
    in_flight: dict[Any, str] = {}
    pool = ThreadPoolExecutor(max_workers=max(cap, 1))
    try:
        while pending or in_flight:
            progressed = False
            for nid in list(pending):
                node = node_map[nid]
                if deps[nid] & dead:
                    continue  # blocked behind a failure
                if not deps[nid] <= completed:
                    continue
                if node.gate and not auto_gates:
                    _commit(nid, ArtifactStatus.AWAITING_REVIEW)
                    report.paused_at = nid
                    report.blocked = sorted(set(pending) - {nid})
                    logger.warning(
                        "Gate %r awaiting review — run stopped cleanly; "
                        "approve and `canon resume`.", nid,
                    )
                    return report
                pending.remove(nid)
                _commit(nid, ArtifactStatus.RUNNING)
                logger.info("=== Node: %s ===", nid)
                in_flight[pool.submit(node.run, ctx)] = nid
                progressed = True
                if len(in_flight) >= cap:
                    break
            if not in_flight:
                if not progressed:
                    break  # everything left is blocked
                continue
            finished, _ = wait(in_flight, return_when=FIRST_COMPLETED)
            for future in finished:
                nid = in_flight.pop(future)
                error = future.exception()
                if error is not None:
                    _commit(nid, ArtifactStatus.ESCALATED)
                    report.escalated.append(nid)
                    dead.add(nid)
                    logger.error("Node %s escalated: %s", nid, error)
                else:
                    for aid in node_map[nid].owns:
                        if status.get(aid) is ArtifactStatus.STALE:
                            status[aid] = ArtifactStatus.DONE
                    _commit(nid, ArtifactStatus.DONE)
                    completed.add(nid)
                    report.done.append(nid)
    finally:
        pool.shutdown(wait=True)

    # Transitively dead descendants stay pending → reported blocked.
    changed = True
    while changed:
        changed = False
        for nid in pending:
            if deps[nid] & dead and nid not in dead:
                dead.add(nid)
                changed = True
    report.blocked = sorted(set(pending))
    _persist()
    return report


# ---------------------------------------------------------------------------
# Edit detection + stale cascade (§6.3)
# ---------------------------------------------------------------------------


@dataclass
class EditReport:
    user_edited: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    #: Pinned artifacts whose on-disk bytes changed: the edit is adopted
    #: like any other, but reported separately so it's loud.
    pinned_edited: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "user_edited": self.user_edited,
            "stale": self.stale,
            "missing": self.missing,
            "pinned_edited": self.pinned_edited,
        }


def pinned_ids(bible: Any) -> set[str]:
    """The `canon pin` set — deliberately protected artifact ids. The
    cascade halts at a pin, the scheduler skips pinned node ids, and the
    art phase bodies skip pinned assets (status alone never gates a phase
    body, so the guards live at both layers)."""
    metadata = getattr(bible, "metadata", None)
    return set(getattr(metadata, "pinned", None) or ())


def pinnable_ids(bible: Any) -> set[str]:
    """What `canon pin` accepts: the hash-tracked, file-backed ART
    artifacts (tileset/enemy/backdrop/player). Level STEPS are excluded
    even though they're hash-tracked: a pinned step under a regenerating
    parent leaves the Bible claiming content its skipped node never
    restored (the collision step rebuilds the whole Level entity) —
    protecting level layers is USER_EDITED's job, not pin's."""
    return {
        aid
        for aid, _rel, _stored, _owner, _attr in _iter_hashed_files(bible)
        if not aid.startswith("level:")
    }


def _iter_hashed_files(bible: Any):
    """Yield (artifact_id, relative_path, stored_hash, owner, hash_attr)
    for every file-backed, hash-stamped artifact. Only Bible-complete
    entities participate (§8.5) — MazeWorld stubs never appear here.
    ``hash_attr`` names the owner's hash field so an authoritative user
    edit can be adopted (stored hash re-stamped from disk)."""
    for level in getattr(bible, "levels", {}).values():
        prefix = f"level:{level.stage_id}/{level.level_id}"
        base = f"level/{level.stage_id}/{level.level_id}"
        dense = (
            ("collision", level.collision, level.collision_hash),
            ("terrain", level.terrain, level.terrain_hash),
            ("background", level.background, level.background_hash),
        )
        for step, rel, stored in dense:
            if rel and stored:
                yield f"{prefix}/{step}", rel, stored, level, f"{step}_hash"
        sparse = (
            ("hazards", level.hazards_hash),
            ("triggers", level.triggers_hash),
            ("entities", level.entities_hash),
            ("foreground", level.foreground_hash),
        )
        for step, stored in sparse:
            if stored:
                yield (
                    f"{prefix}/{step}", f"{base}/{step}.json", stored,
                    level, f"{step}_hash",
                )
    for tileset in getattr(bible, "tilesets", {}).values():
        if tileset.tilesheet_path and tileset.tilesheet_hash:
            yield (
                tileset.artifact_id or f"tileset:{tileset.stage_id}",
                tileset.tilesheet_path,
                tileset.tilesheet_hash,
                tileset,
                "tilesheet_hash",
            )

    for enemy in getattr(bible, "enemy_definitions", {}).values():
        if enemy.sprite_path and enemy.sprite_hash:
            yield (
                enemy.artifact_id or f"enemy:{enemy.enemy_id}",
                enemy.sprite_path,
                enemy.sprite_hash,
                enemy,
                "sprite_hash",
            )

    for backdrop in getattr(bible, "backdrops", {}).values():
        aid = backdrop.artifact_id or f"backdrop:{backdrop.stage_id}"
        for rel in backdrop.band_paths:
            stored = backdrop.band_hashes.get(rel)
            if stored:
                # Dict-keyed hash field: "band_hashes:<path>" tells the
                # adopt step which entry to re-stamp (multi-file artifact).
                yield aid, rel, stored, backdrop, f"band_hashes:{rel}"

    for audio in getattr(bible, "audio", {}).values():
        aid = audio.artifact_id or f"audio:{audio.stage_id}"
        if audio.music_path and audio.music_hash:
            yield aid, audio.music_path, audio.music_hash, audio, "music_hash"
        for rel in audio.sfx_paths.values():
            stored = audio.sfx_hashes.get(rel)
            if stored:
                yield aid, rel, stored, audio, f"sfx_hashes:{rel}"

    for props in getattr(bible, "props", {}).values():
        aid = props.artifact_id or f"props:{props.stage_id}"
        for rel in props.prop_paths.values():
            stored = props.prop_hashes.get(rel)
            if stored:
                yield aid, rel, stored, props, f"prop_hashes:{rel}"

    player = getattr(bible, "player", None)
    if player is not None and player.sprite_path and player.sprite_hash:
        yield (
            player.artifact_id or "player",
            player.sprite_path,
            player.sprite_hash,
            player,
            "sprite_hash",
        )


def _dependency_edges(bible: Any) -> dict[str, set[str]]:
    """artifact_id -> parent artifact_ids, from entity ``parents`` and
    per-step ``step_parents`` — the §6.1 edge set the cascade walks."""
    edges: dict[str, set[str]] = {}
    entities = [
        *getattr(bible, "levels", {}).values(),
        *getattr(bible, "stages", {}).values(),
        *getattr(bible, "enemy_definitions", {}).values(),
        *getattr(bible, "boss_definitions", {}).values(),
        *getattr(bible, "tilesets", {}).values(),
        *getattr(bible, "backdrops", {}).values(),
        *getattr(bible, "audio", {}).values(),
        *getattr(bible, "props", {}).values(),
    ]
    world = getattr(bible, "world", None)
    if world is not None:
        entities.append(world)
    player = getattr(bible, "player", None)
    if player is not None:
        entities.append(player)
    for entity in entities:
        aid = getattr(entity, "artifact_id", "")
        parents = list(getattr(entity, "parents", []) or [])
        if aid and parents:
            edges.setdefault(aid, set()).update(parents)
        for step, step_parents in getattr(entity, "step_parents", {}).items():
            prefix = f"level:{entity.stage_id}/{entity.level_id}"
            edges.setdefault(f"{prefix}/{step}", set()).update(step_parents)
    return edges


@dataclass
class RegenPlan:
    """What a regen request resolved to — marked ids re-run on the next
    orchestration; user-edited descendants are protected (§6.3); pinned
    artifacts halt the cascade entirely."""

    marked: list[str] = field(default_factory=list)
    kept_user_edited: list[str] = field(default_factory=list)
    kept_pinned: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "marked": self.marked,
            "kept_user_edited": self.kept_user_edited,
            "kept_pinned": self.kept_pinned,
        }


def mark_stale(bible: Any, targets: list[str]) -> RegenPlan:
    """Resolve regen targets and mark them (plus their §6.1 descendants)
    STALE, so the next orchestration re-runs exactly them — the substrate
    behind `canon regen`.

    Targets: a bare level id ("l2") expands to every step of that level;
    anything else is an artifact/node id used as-is ("level:<s>/<lid>/
    entities", "phase:plat:style"). Unknown targets raise, naming what IS
    addressable. EXPLICIT targets always mark (asking to regen a
    user-edited artifact discards the edit — deliberate); CASCADED
    descendants keep user_edited status (edits are never destroyed as a
    side effect). PINNED artifacts are stronger: an explicit pinned
    target is an error (unpin first — pin exists to protect paid art
    from exactly this), and the cascade HALTS at a pin (its content
    won't change, so nothing goes stale downstream because of it)."""
    if not isinstance(getattr(bible, "metadata", None), BibleMetadata):
        bible.metadata = BibleMetadata()
    metadata = bible.metadata
    pinned = pinned_ids(bible)
    edges = _dependency_edges(bible)
    known: set[str] = set(metadata.node_status)
    for child, parents in edges.items():
        known.add(child)
        known.update(parents)

    explicit: list[str] = []
    for target in targets:
        level = getattr(bible, "levels", {}).get(target)
        if level is not None:
            prefix = f"level:{level.stage_id}/{level.level_id}"
            explicit.extend(
                f"{prefix}/{step}" for step in sorted(level.step_parents)
            )
        elif target in known:
            explicit.append(target)
        else:
            raise KeyError(
                f"unknown regen target {target!r} — use a level id "
                f"({sorted(getattr(bible, 'levels', {}))}) or an "
                "artifact/node id from `canon status`."
            )
    explicit_pinned = sorted(set(explicit) & pinned)
    if explicit_pinned:
        # Before any mutation, like the unknown-target error above.
        raise KeyError(
            f"regen target(s) {explicit_pinned} are PINNED — pinned "
            "content is deliberately protected; `canon unpin` first if "
            "you really want to re-roll it."
        )

    children: dict[str, set[str]] = {}
    for child, parents in edges.items():
        for parent in parents:
            children.setdefault(parent, set()).add(child)
    plan = RegenPlan()
    queue = deque(explicit)
    seen: set[str] = set()
    while queue:
        current = queue.popleft()
        if current in seen:
            continue
        seen.add(current)
        is_explicit = current in explicit
        if not is_explicit and current in pinned:
            # Pin halts the cascade: the pinned content won't change, so
            # its descendants can't be invalidated through this path
            # (multi-parent children stay reachable via other paths).
            plan.kept_pinned.append(current)
            continue
        if (
            not is_explicit
            and metadata.node_status.get(current) == ArtifactStatus.USER_EDITED
        ):
            # Never destroy a user edit as a side effect of a cascade.
            plan.kept_user_edited.append(current)
        else:
            metadata.node_status[current] = ArtifactStatus.STALE
            plan.marked.append(current)
        queue.extend(children.get(current, ()))
    plan.marked.sort()
    plan.kept_user_edited.sort()
    plan.kept_pinned.sort()
    if plan.marked:
        logger.info(
            "Regen: %d node(s) marked stale (%d user-edited kept): %s",
            len(plan.marked), len(plan.kept_user_edited), plan.marked,
        )
    return plan


def detect_edits(bible: Any, output_dir: str | Path) -> EditReport:
    """Recompute content hashes from disk; hash mismatch marks the artifact
    ``user_edited`` (the edit is authoritative — it is NEVER re-run) and
    every §6.1 descendant ``stale`` (schedulable for regeneration).
    Statuses land in ``bible.metadata.node_status`` and on owning entities;
    staleness is surfaced, not auto-regenerated.

    Because the edit is authoritative, it is ADOPTED: the stored content
    hash is re-stamped from disk, so the next detection pass is clean
    instead of re-cascading the same edit forever. The ``user_edited``
    status (and the untouched provenance hash) remain as the record that
    the artifact's content no longer traces to its generation inputs."""
    output_dir = Path(output_dir)
    report = EditReport()
    owners: dict[str, Any] = {}
    pinned = pinned_ids(bible)

    for artifact_id, rel, stored, owner, hash_attr in _iter_hashed_files(bible):
        owners[artifact_id] = owner
        target = output_dir / rel
        if not target.exists():
            # Multi-file artifacts (backdrop bands) yield one row per file
            # under the same id — report the id once.
            if artifact_id not in report.missing:
                report.missing.append(artifact_id)
            continue
        actual = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != stored:
            if artifact_id not in report.user_edited:
                report.user_edited.append(artifact_id)
                if artifact_id in pinned:
                    # The pin survives — the hand edit is adopted like any
                    # other, but reported separately so it's loud.
                    report.pinned_edited.append(artifact_id)
            # Adopt: the edit is authoritative. "field:key" addresses one
            # entry of a dict-keyed hash field (multi-file artifacts).
            if ":" in hash_attr:
                field_name, key = hash_attr.split(":", 1)
                getattr(owner, field_name)[key] = actual
            else:
                setattr(owner, hash_attr, actual)

    if report.user_edited:
        edges = _dependency_edges(bible)
        # Reverse: parent -> children, then BFS down from edited artifacts.
        children: dict[str, set[str]] = {}
        for child, parents in edges.items():
            for parent in parents:
                children.setdefault(parent, set()).add(child)
        queue = deque(report.user_edited)
        stale: set[str] = set()
        while queue:
            current = queue.popleft()
            for child in children.get(current, ()):
                if child in pinned:
                    # Cascade halts at a pin, same as mark_stale: the
                    # pinned content won't be regenerated, so nothing
                    # downstream goes stale through it.
                    continue
                if child not in stale and child not in report.user_edited:
                    stale.add(child)
                    queue.append(child)
        report.stale = sorted(stale)

    metadata = getattr(bible, "metadata", None)
    if isinstance(metadata, BibleMetadata):
        for aid in report.user_edited:
            metadata.node_status[aid] = ArtifactStatus.USER_EDITED
        for aid in report.stale:
            metadata.node_status[aid] = ArtifactStatus.STALE
    for aid in report.user_edited:
        if aid in owners:
            owners[aid].status = ArtifactStatus.USER_EDITED
    for aid in report.stale:
        owner = owners.get(aid)
        if owner is not None and owner.status != ArtifactStatus.USER_EDITED:
            owner.status = ArtifactStatus.STALE
    if report.user_edited:
        logger.warning(
            "Edit detection: %d user-edited artifact(s) %s; %d stale "
            "descendant(s).", len(report.user_edited), report.user_edited,
            len(report.stale),
        )
    return report
