"""``.canon/registry.json`` as a written artifact — synthesis on the first
registry-writing verb, ``canon registry set``, and capability enablement
(P0 paper P.4.1, P.4.2a, P.7.3, P.7.4; Phase 0 §5.1a; row P0-6).

- ``ensure_registry`` — P.4.1: no verb migrates. The pack resolves by the
  read-both shim until the FIRST registry-writing verb runs; that verb
  synthesizes the file from ``PackSpec.stamped()`` of the seed the shim
  picked (``template.version`` = sha256 over the stamped subset as canonical
  JSON — sorted keys, ``(",", ":")`` separators, ``ensure_ascii=False`` —
  excluding the ``template`` block), journals the synthesis as a ``create``
  on ``registry``, and from then on ``resolve_pack`` tier 1 answers
  ``registry`` and every verb reads the pack's own file. P0-10's create-time
  stamping writes the same document through the same function. Synthesis IS
  a write, so it runs in doctrine 1's order, after the wall: every verb here
  (and ``db define`` / ``db evolve``) resolves through the read-only
  ``registry_document`` and clears its whole payload first — a REFUSED verb
  leaves the pack exactly as it found it, resolving as it did before.
- ``registry_set`` — the ``db schema --set`` idiom against the registry
  (§3.0-A): JSON objects deep-merge to the leaf, a leaf replaces;
  ``capabilities`` (a stored list) takes the map form ``{"<id>": true}`` =
  append if absent (``false`` = disable, refused in v1 — P.9 R12);
  ``tuning.keys.<k>`` merges leaf-wise and only ``min`` / ``max`` /
  ``choices`` are user-writable (the block stays ``status: reserved`` — W2.1
  flips it; a set on it today is accepted as data with a warning that no
  verb reads it yet); ``entities.<kind>`` is ``db define`` / ``db evolve``
  territory; ``engines``, ``template``, ``pack_type``, ``schema`` are refused
  (W2.2 / identity). Fail-closed validation = the merged document rebuilds
  into an effective ``PackSpec`` (``canon.packs.effective_spec``).
- capability enablement (P.7.4, §5.1a): the id must have an implementing
  seed in core (``CAPABILITY_SEEDS``); ``dialogue`` copies
  ``default_dialogue()`` into the registry and gives every ``engines[]``
  entry an empty ``evaluable_namespaces`` block (P.2.4), and refuses unless
  an ``EntityKind`` named by ``storage.on`` (an npc-like kind) exists —
  ``db define`` one first. Journal ``artifact_id: registry``, ``detail.kind:
  capability_set``, ``changed: {"capabilities.<id>": {from: false, to:
  true}}`` (+ the seeded blocks).

Every registry event carries the ONE shape ``{"kind": registry_set |
capability_set | db_define | db_evolve, "changed": {"<dotted>": {from, to}}}``
— never a list (P.7.3). Mounted on ``canon.write_core.write_document``;
the registry's wall is a first-segment rule, so it rides the ``refuse``
hook while the shared leaf matcher stays parameterized.

Deliberately absent, by row ownership: create-time stamping and the
``pack templates`` metadata (P0-10); the tuning key vocabulary + bands
(W2.1); ``engine attach`` (W2.2); disabling a capability (v1.1, R12).
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from canon.packs import PACKS, REGISTRY_SCHEMA, ResolvedPack, effective_spec, resolve_pack
from canon.packs.spec import PackSpec, default_dialogue
from canon.write_core import commit_document, write_document

__all__ = [
    "CAPABILITY_SEEDS",
    "REGISTRY_REL",
    "ensure_registry",
    "registry_document",
    "registry_set",
    "synthesize_registry",
    "template_version",
    "validate_registry",
    "write_registry",
]

REGISTRY_REL = ".canon/registry.json"

#: P.4.2 top-level key order.
_KEY_ORDER = (
    "schema", "pack_type", "template", "label", "description", "vocab", "capabilities", "counts",
    "entities", "grids", "dialogue", "engines", "tuning", "world_fields", "phase_labels", "wizard",
)

#: Top-level keys ``registry set`` refuses, with the reason (doctrine 4).
_REFUSED_TOP: dict[str, str] = {
    "schema": "the registry schema id is identity",
    "pack_type": "pack_type is identity (the manifest mirrors it; a switch is a project fork)",
    "template": "template provenance is stamped at create, never edited",
    "engines": "engines are `engine attach` territory (W2.2)",
    "entities": "entities.<kind> is `db define` / `db evolve` territory",
}

_TUNING_USER_KEYS = ("min", "max", "choices")


def _canon_version() -> str:
    from canon.bible.models import Bible

    return str(Bible.model_fields["canon_version"].default)


def template_version(stamped: dict[str, Any]) -> str:
    """P.4.1's hash rule over the stamped subset (the ``template`` block
    itself excluded)."""
    subset = {k: v for k, v in stamped.items() if k != "template"}
    payload = json.dumps(subset, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def synthesize_registry(spec: PackSpec, pack_type: str, *, created_at: str | None = None) -> dict[str, Any]:
    """The P.4.2 document for *spec*: ``schema``, ``pack_type``, the
    ``template`` block, then the stamped subset in key order."""
    stamped = spec.stamped()
    # P.4.1's input set is the stamped subset MINUS the ``template`` block —
    # nothing else. Hash first, then drop ``pack_type`` from the ordering
    # loop only because the document already carries it at the top (P.4.2
    # key order), so the version stays recomputable from what was written.
    version = template_version(stamped)
    stamped.pop("pack_type", None)
    doc: dict[str, Any] = {
        "schema": REGISTRY_SCHEMA,
        "pack_type": pack_type,
        "template": {
            "id": pack_type,
            "version": version,
            "canon_version": _canon_version(),
            "created_at": created_at or datetime.now(UTC).isoformat(),
        },
    }
    for key in _KEY_ORDER:
        if key in stamped and key not in doc:
            doc[key] = stamped[key]
    for key, value in stamped.items():
        doc.setdefault(key, value)
    return doc


def validate_registry(doc: Any) -> PackSpec:
    """Fail-closed: the document must be a registry (``schema``, a
    registered ``pack_type``) that rebuilds into an effective spec."""
    if not isinstance(doc, dict) or doc.get("schema") != REGISTRY_SCHEMA:
        raise ValueError(f"registry must declare schema {REGISTRY_SCHEMA!r}")
    pack_type = doc.get("pack_type")
    seed = PACKS.get(pack_type) if isinstance(pack_type, str) else None
    if seed is None:
        raise ValueError(f"registry names pack_type {pack_type!r}, not a registered seed ({sorted(PACKS)})")
    return effective_spec(seed, doc)


def registry_document(pack_dir: str | Path) -> tuple[dict[str, Any], ResolvedPack, bool]:
    """``(document, resolved, needs_synthesis)`` — READ ONLY (doctrine: a read
    verb writes nothing). The pack's own registry when tier 1 answers, else
    the document synthesis WOULD write. ``registry set`` resolves through
    this and runs its wall + a dry apply against it, so a refused verb never
    leaves a synthesized file behind; ``ensure_registry`` is the same
    resolution followed by the write."""
    pack = Path(pack_dir)
    resolved = resolve_pack(pack)
    if resolved.source == "registry" and resolved.registry is not None:
        return resolved.registry, resolved, False
    return synthesize_registry(resolved.spec, resolved.pack_type), resolved, True


def ensure_registry(
    pack_dir: str | Path, *, actor: str = "user", session: str | None = None
) -> tuple[dict[str, Any], ResolvedPack, dict | None]:
    """``(document, resolved, synthesis event | None)`` — the registry as it
    stands, synthesized + journaled first when the pack still resolves by
    manifest stamp or shape (P.4.1). Call it only once the verb is going to
    write: it is itself a write."""
    pack = Path(pack_dir)
    doc, resolved, needs_synthesis = registry_document(pack)
    if not needs_synthesis:
        return doc, resolved, None
    committed = commit_document(
        pack,
        artifact_id="registry",
        rel_path=REGISTRY_REL,
        data=doc,
        actor=actor,
        session=session,
        detail={
            "kind": "registry_synthesize",
            "from": resolved.source,
            "template": dict(doc["template"]),
        },
        op="create",
        source="user",
    )
    return doc, resolve_pack(pack), committed["event"]


def write_registry(
    pack_dir: str | Path,
    document: dict[str, Any],
    changes: dict[str, Any],
    *,
    kind: str,
    actor: str = "user",
    session: str | None = None,
    apply: Callable[[Any, dict[str, Any]], dict[str, dict]] | None = None,
    refuse: Callable[[str], str | None] | None = None,
    warnings: list[str] | None = None,
    detail_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One registry event through the write core: ``artifact_id: registry``,
    ``detail.kind = kind``, fail-closed via ``validate_registry``."""
    return write_document(
        pack_dir,
        artifact_id="registry",
        rel_path=REGISTRY_REL,
        document=document,
        changes=changes,
        refuse=refuse,
        apply=apply,
        validate=lambda doc, _diff: (validate_registry(doc), None)[1],
        user_edited=False,
        actor=actor,
        session=session,
        detail={"kind": kind, **(detail_extra or {})},
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Capability seeds (P.7.4)
# ---------------------------------------------------------------------------


def _seed_dialogue(doc: dict[str, Any], spec: PackSpec, diff: dict[str, dict]) -> None:
    dialogue = default_dialogue()
    storage_on = str(dialogue.storage.get("on", "npc"))
    if storage_on not in (doc.get("entities") or {}):
        raise ValueError(
            f"enabling `dialogue` needs an EntityKind named {storage_on!r} (its `storage.on` — the npc-like kind "
            f"dialogue trees live on); this pack has {sorted(doc.get('entities') or {})} — `db define` one first"
        )
    if "dialogue" not in doc:
        block = dialogue.stamped()
        doc["dialogue"] = block
        diff["dialogue"] = {"from": None, "to": copy.deepcopy(block)}
    scopes = list((doc.get("dialogue") or {}).get("scopes") or dialogue.scopes)
    for entry in doc.get("engines") or []:
        if isinstance(entry, dict) and entry.get("evaluable_namespaces") is None:
            entry["evaluable_namespaces"] = {scope: {} for scope in scopes}
            diff[f"engines[id={entry.get('id')}].evaluable_namespaces"] = {
                "from": None, "to": copy.deepcopy(entry["evaluable_namespaces"]),
            }


def _seed_grid(doc: dict[str, Any], spec: PackSpec, diff: dict[str, dict]) -> None:
    if not doc.get("grids"):
        raise ValueError("enabling `grid` needs a GridKind in `grids` — this pack declares none")


#: capability id → the core's implementing seed (``None`` = a plain flag
#: with no block to copy). An id absent here has no implementing seed and
#: is refused (P.7.4). Values, never a union — a later phase adds entries.
CAPABILITY_SEEDS: dict[str, Callable[[dict[str, Any], PackSpec, dict[str, dict]], None] | None] = {
    "dialogue": _seed_dialogue,
    "grid": _seed_grid,
    "per_step_roll": None,
}


# ---------------------------------------------------------------------------
# registry set
# ---------------------------------------------------------------------------


def _flatten(payload: Any, prefix: str = "") -> dict[str, Any]:
    """Deep-merge-to-leaf semantics as addressed leaves: nested objects
    recurse, everything else (scalars, lists) replaces at its path."""
    out: dict[str, Any] = {}
    if isinstance(payload, dict) and payload:
        for key, value in payload.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict) and value:
                out.update(_flatten(value, path))
            else:
                out[path] = value
    elif prefix:
        out[prefix] = payload
    return out


def _refuse_registry_path(name: str) -> str | None:
    head, _, rest = name.partition(".")
    if head in _REFUSED_TOP:
        return f"{name!r} is refused: {_REFUSED_TOP[head]}"
    if head == "capabilities":
        return None
    if head == "tuning":
        parts = rest.split(".") if rest else []
        if not parts or parts[0] in ("schema", "status"):
            return f"{name!r} is refused: the tuning block stays `status: reserved` until W2.1 flips it"
        if parts[0] != "keys":
            return f"{name!r} is refused: only tuning.keys.<key>.{{min,max,choices}} is user-writable"
        if len(parts) >= 3 and parts[2] not in _TUNING_USER_KEYS:
            return (
                f"{name!r} is refused: tuning.keys.<key>.{parts[2]} is template-owned "
                f"(only {list(_TUNING_USER_KEYS)} are user-writable)"
            )
    return None


def registry_set(
    pack_dir: str | Path,
    changes: dict[str, Any],
    *,
    actor: str = "user",
    session: str | None = None,
) -> dict[str, Any]:
    """``canon registry set <pack> --set '<json>'`` per P.7.4 (see the module
    docstring for the merge rule and the refusals)."""
    if not isinstance(changes, dict) or not changes:
        raise ValueError("--set needs a non-empty JSON object")
    pack = Path(pack_dir)
    payload = copy.deepcopy(changes)
    capabilities = payload.pop("capabilities", None)
    leaves = _flatten(payload)
    warnings: list[str] = []
    enable: list[str] = []
    if capabilities is not None:
        if not isinstance(capabilities, dict):
            raise ValueError('capabilities takes the map form {"<id>": true} (a stored list, P.4.2)')
        for cap_id, flag in capabilities.items():
            if flag is not True:
                raise ValueError(
                    f"capabilities.{cap_id}={flag!r}: only enabling (true) is supported in v1 — "
                    "disabling a capability is v1.1 (P.9 R12)"
                )
            if cap_id not in CAPABILITY_SEEDS:
                raise ValueError(
                    f"capability {cap_id!r} has no implementing seed in core (known: {sorted(CAPABILITY_SEEDS)})"
                )
            leaves[f"capabilities.{cap_id}"] = True
            enable.append(cap_id)
    # Doctrine 1's order is resolve → wall → … → write. ``ensure_registry``
    # IS a write (it synthesizes + journals ``.canon/registry.json`` and
    # flips the pack to tier-1 resolution), so the wall runs against the
    # read-only resolution FIRST — a refused `registry set` must leave the
    # pack exactly as it found it. The core re-applies the same ``refuse``
    # hook below; this is the same rule, moved ahead of the synthesis.
    for name in leaves:
        why = _refuse_registry_path(name)
        if why:
            raise ValueError(why)
        if name.startswith("tuning.keys."):
            warnings.append(f"{name}: accepted as registry data — no verb reads tuning.keys yet (W2.1 flips the block)")
    doc, resolved, _needs = registry_document(pack)

    def apply(target: dict[str, Any], addressed: dict[str, Any]) -> dict[str, dict]:
        from canon.write_core import set_path

        diff: dict[str, dict] = {}
        present = list(target.get("capabilities") or [])
        for name, value in addressed.items():
            if name.startswith("capabilities."):
                cap_id = name.partition(".")[2]
                if cap_id in present:
                    continue
                present.append(cap_id)
                target["capabilities"] = present
                diff[name] = {"from": False, "to": True}
                seed = CAPABILITY_SEEDS[cap_id]
                if seed is not None:
                    seed(target, resolved.spec, diff)
                continue
            old, new = set_path(target, name, value)
            if old != new:
                diff[name] = {"from": old, "to": new}
        return diff

    # The last refusal a capability can raise lives inside its seed (dialogue
    # needs an npc-like kind) — run the apply against a throwaway copy so it
    # answers BEFORE the synthesis writes, then let the real pass repeat it.
    apply(copy.deepcopy(doc), leaves)
    doc, resolved, synthesis = ensure_registry(pack, actor=actor, session=session)

    kind = "capability_set" if enable and all(n.startswith("capabilities.") for n in leaves) else "registry_set"
    result = write_registry(
        pack, doc, leaves, kind=kind, actor=actor, session=session,
        apply=apply, refuse=_refuse_registry_path, warnings=warnings,
    )
    return {
        "artifact_id": "registry",
        "changed": result["changed"],
        "no_change": bool(result.get("no_change")),
        "warnings": result["warnings"],
        "synthesized": synthesis is not None,
        "registry": result["document"],
    }
