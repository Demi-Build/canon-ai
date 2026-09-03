"""``canon dialogue improve`` — a PROPOSAL, never a write (Phase 0 §7.2,
README Q10; row P0-9).

"An LLM re-author is never a write." The verb returns per-field before/after
rows; the surface lands accepted rows in the UNSAVED buffer, and ``⌘S`` —
``dialogue update`` — remains the only writer. Nothing in this module opens
a file for writing, and the result always carries ``wrote: false``.

Doctrine 3 (paid legs are user-run) shapes the backend seam:

- ``none`` — the built-in deterministic proposer. No provider, no key, $0.
  It is a *copy pass*, not an author: it proposes the mechanical text fixes
  a human would otherwise make by hand (trailing whitespace, doubled spaces,
  a missing sentence-ending mark, an untitled tree label). Deterministic, so
  the test suite exercises the whole verb — request shape, proposal shape,
  cost block, "wrote nothing" — at zero cost.
- ``fake`` — the same deterministic proposer, reached through the id the
  rest of canon uses for its $0 leg, so a caller can pass ``--backend fake``
  everywhere and get a $0 answer here too.
- any other id — resolved through ``canon.agent.providers.resolve_chat_backend``
  (the ONE provider map, ids as data) and called for real. That is the paid
  leg, and it is the user's to run: no test in this repo passes anything but
  ``none`` / ``fake``.

The proposal shape is the design's per-row diff (screen 04b): one row per
changed field, headed by its target, with ``before`` / ``after`` / ``why``,
so the modal can render ``Skip`` / ``Accept`` per card and the footer can
count what was accepted.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from canon.dialogue.storage import npc_trees, resolve_npc

__all__ = ["FREE_BACKENDS", "dialogue_improve"]

#: The $0 ids. Everything else is a real provider call the USER runs.
FREE_BACKENDS: frozenset[str] = frozenset({"none", "fake", ""})

_SYSTEM = (
    "You are a dialogue editor for a game. You are given a character's dialogue tree as JSON. "
    "Return ONLY a JSON array of proposed edits, each "
    '{"tree": id, "node_id": id, "field": "prompt"|"text", "choice": index or null, '
    '"after": "the rewritten text", "why": "one short sentence"}. '
    "Propose nothing structural: never add, remove or re-point a node or a choice, never touch "
    "conditions or effects. If nothing needs changing, return []."
)

_DOUBLE_SPACE = re.compile(r"[ \t]{2,}")


def _tidy(text: str) -> tuple[str, str] | None:
    """The deterministic proposer's one rule set: the mechanical copy fixes.
    Returns ``(after, why)`` or ``None`` when the text is already clean."""
    after = _DOUBLE_SPACE.sub(" ", str(text)).strip()
    reasons: list[str] = []
    if after != str(text):
        reasons.append("trimmed stray whitespace")
    if after and after[-1] not in ".!?…\"'”’)":
        after += "."
        reasons.append("added the missing sentence-ending mark")
    if after == str(text):
        return None
    return after, " and ".join(reasons)


def _deterministic_rows(trees: list[dict], scope_trees: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for tree in trees:
        tree_id = str(tree.get("tree_id"))
        if tree_id not in scope_trees:
            continue
        for node_id in sorted(tree.get("nodes") or {}):
            node = (tree.get("nodes") or {})[node_id]
            fix = _tidy(node.get("prompt", ""))
            if fix is not None:
                rows.append({
                    "target": f"tree:{tree_id}/node:{node_id}",
                    "tree": tree_id, "node_id": node_id, "choice": None, "field": "prompt",
                    "before": node.get("prompt", ""), "after": fix[0], "why": fix[1],
                })
            for index, choice in enumerate(node.get("choices") or []):
                fix = _tidy(choice.get("text", ""))
                if fix is None:
                    continue
                rows.append({
                    "target": f"tree:{tree_id}/node:{node_id}/choice:{index}",
                    "tree": tree_id, "node_id": node_id, "choice": index, "field": "text",
                    "before": choice.get("text", ""), "after": fix[0], "why": fix[1],
                })
    return rows


def _provider_rows(
    trees: list[dict],
    scope_trees: list[str],
    instruction: str,
    backend_id: str,
    model: str | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The paid leg. Builds one chat request, collects one reply, and CLAMPS
    every returned row to a field that actually exists — a model proposing a
    node the tree does not carry is dropped, not applied."""
    from canon.agent.providers import resolve_chat_backend
    from canon.llm.chat import ChatRequest, collect

    payload = [t for t in trees if str(t.get("tree_id")) in scope_trees]
    backend = resolve_chat_backend(backend_id, model)
    request = ChatRequest(
        system=_SYSTEM,
        messages=[{
            "role": "user",
            "content": (
                f"{instruction.strip() or 'Tighten the prose.'}\n\n"
                + json.dumps(payload, indent=None)[:120000]
            ),
        }],
        model=model,
        max_tokens=4096,
    )
    response = collect(backend.stream(request))
    try:
        proposed = json.loads(response.text[response.text.find("[") : response.text.rfind("]") + 1])
    except (ValueError, IndexError):
        proposed = []
    rows: list[dict[str, Any]] = []
    by_id = {str(t.get("tree_id")): t for t in payload}
    for entry in proposed if isinstance(proposed, list) else []:
        if not isinstance(entry, dict):
            continue
        tree = by_id.get(str(entry.get("tree")))
        node = (tree or {}).get("nodes", {}).get(str(entry.get("node_id")))
        if node is None:
            continue
        index = entry.get("choice")
        if index is None:
            before, field = node.get("prompt", ""), "prompt"
            target = f"tree:{entry['tree']}/node:{entry['node_id']}"
        else:
            choices = node.get("choices") or []
            if not isinstance(index, int) or not (0 <= index < len(choices)):
                continue
            before, field = choices[index].get("text", ""), "text"
            target = f"tree:{entry['tree']}/node:{entry['node_id']}/choice:{index}"
        after = str(entry.get("after", ""))
        if not after or after == before:
            continue
        rows.append({
            "target": target, "tree": str(entry["tree"]), "node_id": str(entry["node_id"]),
            "choice": index, "field": field, "before": before, "after": after,
            "why": str(entry.get("why") or ""),
        })
    usage = response.usage
    return rows, {
        "model": response.model,
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
    }


def dialogue_improve(
    pack_dir: str | Path,
    npc_id: str,
    *,
    instruction: str = "",
    tree_id: str | None = None,
    scope: str = "tree",
    backend: str = "none",
    model: str | None = None,
    keep_structure: bool = True,
    actor: str = "user",
) -> dict[str, Any]:
    """Propose per-field rewrites for one NPC's dialogue. **Writes nothing.**

    ``scope`` is ``tree`` (the named tree, default: the first) or ``npc``
    (every tree the character has) — the design's scope pills. ``keep
    _structure`` is on by default and this verb honours it absolutely: no
    proposal row ever touches a node id, a choice target, a condition or an
    effect, on either backend path.

    ``actor`` journals nothing here (nothing is written) — it rides into the
    payload as ``requested_by`` so the caller threads ONE identity from the
    proposal through to the ``dialogue update`` that lands the accepted rows.
    """
    res = resolve_npc(pack_dir, npc_id)
    trees, source = npc_trees(res.row, res.npc_id, res.dialogue)
    if not trees:
        raise FileNotFoundError(f"npc {npc_id} has no dialogue to improve")
    if scope == "npc":
        scope_trees = [str(t.get("tree_id")) for t in trees]
    else:
        target = str(tree_id) if tree_id else str(trees[0].get("tree_id"))
        if target not in {str(t.get("tree_id")) for t in trees}:
            raise FileNotFoundError(
                f"npc {npc_id} has no tree {target!r} (have {[t.get('tree_id') for t in trees]})"
            )
        scope_trees = [target]
    backend_id = (backend or "none").strip()
    if backend_id in FREE_BACKENDS:
        rows = _deterministic_rows(trees, scope_trees)
        gen: dict[str, Any] = {"backend": backend_id or "none", "model": None}
        cost = {"usd": 0.0, "paid": False}
        note = (
            "no chat backend selected — this is the built-in deterministic copy pass "
            "(whitespace and sentence-ending marks only). Pick a backend for an LLM re-author."
        )
    else:
        rows, gen = _provider_rows(trees, scope_trees, instruction, backend_id, model)
        gen["backend"] = backend_id
        cost = {"usd": None, "paid": True, "note": "priced by the caller from the token usage"}
        note = f"LLM re-author on {backend_id} — a paid run, and still only a proposal"
    return {
        "npc": res.npc_id,
        "requested_by": actor,
        "backend_note": note,
        "source": source,
        "scope": scope,
        "trees": scope_trees,
        "instruction": instruction,
        "keep_structure": bool(keep_structure),
        "backend": backend_id or "none",
        "proposal": {"rows": rows, "count": len(rows)},
        "gen": gen,
        "cost": cost,
        # The contract, stated in the payload as well as the docs: applying a
        # row is the surface's job, and it lands in the unsaved buffer.
        "wrote": False,
        "apply_with": "canon dialogue update --ops (node.prompt / choice.text)",
    }
