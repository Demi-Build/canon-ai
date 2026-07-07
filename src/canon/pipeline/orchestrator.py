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
    """

    node_id: str
    run: Callable[[Any], None]
    requires: list[str] = field(default_factory=list)
    produces: str = ""  # defaults to node_id
    gate: bool = False  # human gate (I4): pause point unless auto-approved
    always: bool = False  # re-run even when DONE (cheap derived outputs)

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
            node = Node(
                node_id=f"phase:{item.name}",
                run=item.run,
                requires=list(prev_ids),
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
    rerun = {
        nid for nid, s in status.items() if s is ArtifactStatus.STALE
    }
    completed: set[str] = set()
    for nid, node in node_map.items():
        node_status = status.get(nid)
        if node_status == ArtifactStatus.USER_EDITED:
            # The user's edit is authoritative — NEVER regenerate it
            # (§6.3); its output satisfies dependents as-is.
            completed.add(nid)
            report.skipped.append(nid)
        elif (
            node_status == ArtifactStatus.DONE
            and nid not in rerun
            and not node.always
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

    def to_dict(self) -> dict:
        return {
            "user_edited": self.user_edited,
            "stale": self.stale,
            "missing": self.missing,
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
    ]
    world = getattr(bible, "world", None)
    if world is not None:
        entities.append(world)
    for entity in entities:
        aid = getattr(entity, "artifact_id", "")
        parents = list(getattr(entity, "parents", []) or [])
        if aid and parents:
            edges.setdefault(aid, set()).update(parents)
        for step, step_parents in getattr(entity, "step_parents", {}).items():
            prefix = f"level:{entity.stage_id}/{entity.level_id}"
            edges.setdefault(f"{prefix}/{step}", set()).update(step_parents)
    return edges


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

    for artifact_id, rel, stored, owner, hash_attr in _iter_hashed_files(bible):
        owners[artifact_id] = owner
        target = output_dir / rel
        if not target.exists():
            report.missing.append(artifact_id)
            continue
        actual = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != stored:
            report.user_edited.append(artifact_id)
            setattr(owner, hash_attr, actual)  # adopt: edit is authoritative

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
