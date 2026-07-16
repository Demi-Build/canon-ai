"""Artifact substrate — status enums, provenance base model, and the
global artifact-ID address space (PRD §6.1, §6.3, §7.3).

Phase 1 provides the *shapes*: the fields exist and validate, but nothing
walks ``parents`` or transitions ``status`` yet — that machinery is the
Phase 2 orchestrator. MazeWorld entities (``EntityLore``, ``Map``, …) do
NOT adopt this base; their map-scoped ``(map_id, entity_id)`` addressing
stays for back-compat.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import NamedTuple

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Status enums (PRD §7.3 lifecycle + §6.3 user_edited)
# ---------------------------------------------------------------------------


class ArtifactStatus(StrEnum):
    """Fine-grained per-artifact lifecycle state."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    RETRYING = "retrying"
    ESCALATED = "escalated"
    STALE = "stale"
    REGENERATING = "regenerating"
    AWAITING_REVIEW = "awaiting_review"
    USER_EDITED = "user_edited"


class PhaseStatus(StrEnum):
    """Coarse per-phase state, tracked in ``BibleMetadata.phase_status``."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# ArtifactMeta — the provenance base for Bible-complete entities
# ---------------------------------------------------------------------------


class ArtifactMeta(BaseModel):
    """Per-entity provenance additions (PRD §6.1).

    ``parents`` records the artifact IDs this artifact was derived from or
    references — the edge set the Phase 2 stale-cascade walks.
    """

    artifact_id: str = ""
    status: ArtifactStatus = ArtifactStatus.PENDING
    provenance_hash: str = ""
    parents: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Artifact-ID address space (PRD §6.1)
# ---------------------------------------------------------------------------

#: Level step artifacts, in pipeline order (PRD §6.1 / §6.2 invariant I5).
LEVEL_STEPS: tuple[str, ...] = (
    "layout",
    "collision",
    "terrain",
    "background",
    "hazards",
    "entities",
    "triggers",
    "items",
    "foreground",
)

#: namespace -> number of expected parts after the namespace.
#: ``world`` is a singleton; ``level`` is ``<stage_id>/<level_id>/<step>``.
_NAMESPACE_ARITY: dict[str, int] = {
    "world": 0,
    "stage": 1,
    "enemy": 1,
    "boss": 1,
    "tileset": 1,
    "backdrop": 1,
    "audio": 1,
    "props": 1,
    "item": 1,
    "level": 3,
}


class ArtifactId(NamedTuple):
    """Parsed form of a global artifact ID."""

    namespace: str
    parts: tuple[str, ...]

    def __str__(self) -> str:
        return make_artifact_id(self.namespace, *self.parts)


def make_artifact_id(namespace: str, *parts: str) -> str:
    """Build a validated artifact ID.

    Examples::

        make_artifact_id("world")                          # "world"
        make_artifact_id("enemy", "goblin_grunt")          # "enemy:goblin_grunt"
        make_artifact_id("level", "s1", "l3", "collision") # "level:s1/l3/collision"
    """
    _validate(namespace, parts)
    if not parts:
        return namespace
    return f"{namespace}:{'/'.join(parts)}"


def parse_artifact_id(artifact_id: str) -> ArtifactId:
    """Parse and validate a global artifact ID. Raises ValueError on any
    malformed input — unknown namespace, wrong arity, empty parts, or an
    unknown level step."""
    if not isinstance(artifact_id, str) or not artifact_id:
        raise ValueError(f"artifact_id must be a non-empty string; got {artifact_id!r}")

    namespace, sep, rest = artifact_id.partition(":")
    parts: tuple[str, ...] = tuple(rest.split("/")) if sep else ()
    _validate(namespace, parts)
    return ArtifactId(namespace=namespace, parts=parts)


def _validate(namespace: str, parts: tuple[str, ...]) -> None:
    arity = _NAMESPACE_ARITY.get(namespace)
    if arity is None:
        raise ValueError(
            f"Unknown artifact namespace {namespace!r}. "
            f"Known: {sorted(_NAMESPACE_ARITY)!r}."
        )
    if len(parts) != arity:
        raise ValueError(
            f"Namespace {namespace!r} takes {arity} part(s) "
            f"(e.g. {_example(namespace)!r}); got {len(parts)}: {parts!r}."
        )
    for part in parts:
        if not part:
            raise ValueError(f"Artifact ID for {namespace!r} has an empty part: {parts!r}.")
        if ":" in part or "/" in part:
            raise ValueError(
                f"Artifact ID part {part!r} may not contain ':' or '/' "
                "(reserved as namespace/part separators)."
            )
    if namespace == "level" and parts[2] not in LEVEL_STEPS:
        raise ValueError(
            f"Unknown level step {parts[2]!r}. Known steps: {list(LEVEL_STEPS)!r}."
        )


def _example(namespace: str) -> str:
    return {
        "world": "world",
        "stage": "stage:s1",
        "enemy": "enemy:goblin_grunt",
        "boss": "boss:ember_king",
        "tileset": "tileset:s1",
        "item": "item:rusty_key",
        "level": "level:s1/l3/collision",
    }[namespace]


# ---------------------------------------------------------------------------
# Provenance hash (PRD §6.3)
# ---------------------------------------------------------------------------


def compute_provenance_hash(
    content_hash: str,
    *,
    schema_version: str,
    prompt_version: str,
    model: str,
    seed: str,
) -> str:
    """Compose the §6.3 provenance hash from a content hash + generation
    inputs.

    The *content hash* is what the adapter returns at write time — a pure
    hash of the written bytes, recomputable from disk for edit detection.
    The *provenance hash* additionally folds in the generation inputs, so
    identical content produced by a different schema/prompt/model/seed
    hashes differently (a model or prompt bump invalidates correctly).
    Keep the two distinct: only the content hash can be compared against
    a recompute-from-disk.
    """
    key = "\n".join(
        [content_hash, str(schema_version), str(prompt_version), str(model), str(seed)]
    )
    return "sha256:" + hashlib.sha256(key.encode("utf-8")).hexdigest()
