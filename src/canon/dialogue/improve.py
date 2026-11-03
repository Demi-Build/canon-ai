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

__all__ = ["FREE_BACKENDS", "IMPROVE_MAX_TOKENS", "OUTCOMES", "dialogue_estimate", "dialogue_improve"]

#: Output budget for the paid leg. The call streams, so a large ceiling costs
#: nothing unless used; 4096 cut real replies off mid-array (Pass 4), and
#: thinking tokens count against it.
IMPROVE_MAX_TOKENS = 16384

#: What the paid leg can come back with. Only ``proposed`` carries rows; the
#: rest are NAMED non-answers so the surface never shows "already clean" for a
#: reply that was cut off, refused, or not a list.
#:
#: - ``proposed``   — a parsed list with at least one usable row
#: - ``no_changes`` — a parsed, empty list on a normal stop
#: - ``clamped``    — a parsed list whose every row named a node/choice the
#:   tree does not carry (all dropped)
#: - ``truncated``  — the reply hit the token limit
#: - ``refused``    — the provider refused
#: - ``unparseable``— the reply held no JSON array
OUTCOMES: tuple[str, ...] = ("proposed", "no_changes", "clamped", "truncated", "refused", "unparseable")

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
        messages=[{"role": "user", "content": _user_content(payload, instruction)}],
        model=model,
        max_tokens=IMPROVE_MAX_TOKENS,
        effort="low",
    )
    response = collect(backend.stream(request))
    text = response.text
    proposed: Any = None
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        try:
            proposed = json.loads(text[start : end + 1])
        except ValueError:
            proposed = None
    parsed = isinstance(proposed, list)
    rows: list[dict[str, Any]] = []
    by_id = {str(t.get("tree_id")): t for t in payload}
    for entry in proposed if parsed else []:
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
    returned = len(proposed) if parsed else 0
    stop = str(response.stop_reason or "")
    if stop == "refusal":
        outcome = "refused"
    elif stop == "max_tokens" and not rows:
        outcome = "truncated"
    elif not parsed:
        outcome = "unparseable"
    elif rows:
        outcome = "proposed"
    elif returned:
        outcome = "clamped"
    else:
        outcome = "no_changes"
    usage = response.usage
    return rows, {
        "model": response.model,
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0),
        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0),
        "stop_reason": stop,
        "parsed": parsed,
        "returned": returned,
        "dropped": max(returned - len(rows), 0),
        "outcome": outcome,
    }


def _price(backend_id: str, gen: dict[str, Any]) -> dict[str, Any]:
    """The paid leg's measured cost, from canon.pricing (the one price
    source) via the same helper the agent service prices its turns with.
    A model with no price row is said so in ``error`` — never a silent $0.
    Prices only; writes no ledger (this verb is a pure read)."""
    from canon import pricing
    from canon.provenance import token_gen_block

    model = gen.get("model") or None
    priced = token_gen_block(backend_id, model, gen)
    if priced is None:
        return {"usd": 0.0, "paid": True, "accuracy": pricing.MEASURED, "note": "the call reported no token usage"}
    # Every backend that reaches this leg is a paid one (the free ids never
    # do), so a model with no price row is unpriced — not a measured $0.
    if model is None or pricing.llm(model) is None:
        return {
            "usd": None, "paid": True, "accuracy": pricing.ESTIMATED,
            "error": f"{backend_id}: no llm price row for {model!r} in canon.pricing",
        }
    return {"usd": priced["gen"]["cost_usd"], "paid": True, "accuracy": priced["accuracy"]}


def _user_content(payload: list[dict], instruction: str) -> str:
    """The one user message the paid leg sends — shared with the estimate so
    the forecast is sized off the real request."""
    return f"{instruction.strip() or 'Tighten the prose.'}\n\n" + json.dumps(payload, indent=None)[:120000]


def _scope(
    pack_dir: str | Path, npc_id: str, tree_id: str | None, scope: str
) -> tuple[Any, list[dict], str, list[str]]:
    """``(resolved npc, trees, source, scope tree ids)`` — the one scope rule
    both the proposal and its estimate use."""
    res = resolve_npc(pack_dir, npc_id)
    trees, source = npc_trees(res.row, res.npc_id, res.dialogue)
    if not trees:
        raise FileNotFoundError(f"npc {npc_id} has no dialogue to improve")
    if scope == "npc":
        return res, trees, source, [str(t.get("tree_id")) for t in trees]
    target = str(tree_id) if tree_id else str(trees[0].get("tree_id"))
    if target not in {str(t.get("tree_id")) for t in trees}:
        raise FileNotFoundError(
            f"npc {npc_id} has no tree {target!r} (have {[t.get('tree_id') for t in trees]})"
        )
    return res, trees, source, [target]


#: Characters per token for the size forecast — the same rough rule the
#: rest of the estimator's hand-sized tasks use. It is a forecast, labelled
#: ``estimated``; the measured figure arrives on the improve result.
_CHARS_PER_TOKEN = 4

#: The low end of the reply: a short list (or ``[]``) plus a little thinking.
_OUTPUT_LOW_TOKENS = 300


def _chat_default_model(backend_id: str) -> str | None:
    """The model a bare chat backend id actually runs — the chat backends'
    own defaults, NOT the pipeline's per-task table (those differ)."""
    defaults = {
        "anthropic": ("canon.backends.chat_anthropic", "DEFAULT_CHAT_MODEL"),
        "openai": ("canon.backends.chat_openai", "DEFAULT_OPENAI_CHAT_MODEL"),
        "kimi": ("canon.backends.chat_openai", "DEFAULT_KIMI_CHAT_MODEL"),
    }
    spec = defaults.get(backend_id)
    if spec is None:
        return None
    import importlib

    try:
        return str(getattr(importlib.import_module(spec[0]), spec[1]))
    except (ImportError, AttributeError):
        return None


def dialogue_estimate(
    pack_dir: str | Path,
    npc_id: str,
    *,
    instruction: str = "",
    tree_id: str | None = None,
    scope: str = "tree",
    backend: str = "none",
    model: str | None = None,
) -> dict[str, Any]:
    """Forecast what ``dialogue improve`` will cost with these arguments.

    Sized off the REAL request (system prompt + instruction + the scoped
    trees, exactly as the paid leg sends them) and priced at the model the
    call will run — the chat backend's default unless ``model`` names one —
    from ``canon.pricing``. One call, so no retry multiplier: ``low`` prices a
    short reply, ``high`` a reply that rewrites every line (capped at the
    call's token budget). The free backends forecast $0. Reads only.

    Returns the cradle estimate shape (``low``/``high``/``backend``/``model``/
    ``unitCount``/``accuracy``/``unitLabel`` + an ``llm`` block)."""
    from canon import pricing

    _res, trees, _source, scope_trees = _scope(pack_dir, npc_id, tree_id, scope)
    payload = [t for t in trees if str(t.get("tree_id")) in scope_trees]
    nodes = sum(len(t.get("nodes") or {}) for t in payload)
    choices = sum(
        len(n.get("choices") or []) for t in payload for n in (t.get("nodes") or {}).values() if isinstance(n, dict)
    )
    unit_label = f"{nodes} node{'s' if nodes != 1 else ''} · {choices} choice{'s' if choices != 1 else ''}"
    backend_id = (backend or "none").strip()
    warnings: list[str] = []
    input_tokens = (len(_SYSTEM) + len(_user_content(payload, instruction))) // _CHARS_PER_TOKEN
    prose_chars = sum(
        len(str(n.get("prompt", ""))) + sum(len(str(c.get("text", ""))) for c in (n.get("choices") or []))
        for t in payload for n in (t.get("nodes") or {}).values() if isinstance(n, dict)
    )
    # Every line rewritten, plus the JSON envelope per row, plus the thinking
    # the low effort still spends.
    output_high = min(IMPROVE_MAX_TOKENS, max(_OUTPUT_LOW_TOKENS * 2, (prose_chars * 3) // _CHARS_PER_TOKEN + 1024))
    if backend_id in FREE_BACKENDS:
        chosen_model, low, high = None, 0.0, 0.0
    else:
        chosen_model = (model or "").strip() or _chat_default_model(backend_id)
        row = pricing.price_for("llm", chosen_model, warnings) if chosen_model else None
        if chosen_model is None:
            warnings.append(f"no default chat model known for backend {backend_id!r} — pass --model to price it")
        rates = pricing.per_token(row) if row is not None else {"input": 0.0, "output": 0.0}
        low = round(input_tokens * rates["input"] + _OUTPUT_LOW_TOKENS * rates["output"], 4)
        high = round(input_tokens * rates["input"] + output_high * rates["output"], 4)
    task = {
        "calls": 1.0,
        "model": chosen_model or "",
        "input_tokens_per_call": input_tokens,
        "output_tokens_per_call": output_high,
        "usd": high,
    }
    return {
        "scope": scope,
        "backends": {"llm": backend_id or "none"},
        "llm": {"by_task": {"dialogue_improve": task}, "calls": 1.0, "usd": {"best": low, "worst": high}},
        "total_usd": {"best": low, "worst": high},
        "warnings": warnings,
        "low": low,
        "high": high,
        "backend": backend_id or "none",
        "model": chosen_model,
        "unitCount": 1,
        "accuracy": pricing.ESTIMATED,
        "calibration": "defaults",
        "unitLabel": unit_label,
        "trees": scope_trees,
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
    res, trees, source, scope_trees = _scope(pack_dir, npc_id, tree_id, scope)
    backend_id = (backend or "none").strip()
    if backend_id in FREE_BACKENDS:
        rows = _deterministic_rows(trees, scope_trees)
        gen: dict[str, Any] = {
            "backend": backend_id or "none", "model": None,
            "outcome": "proposed" if rows else "no_changes",
        }
        cost = {"usd": 0.0, "paid": False}
        note = (
            "no chat backend selected — this is the built-in deterministic copy pass "
            "(whitespace and sentence-ending marks only). Pick a backend for an LLM re-author."
        )
    else:
        rows, gen = _provider_rows(trees, scope_trees, instruction, backend_id, model)
        gen["backend"] = backend_id
        cost = _price(backend_id, gen)
        note = f"LLM re-author with {backend_id}"
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
        "outcome": gen["outcome"],
        "cost": cost,
        # The contract, stated in the payload as well as the docs: applying a
        # row is the surface's job, and it lands in the unsaved buffer.
        "wrote": False,
        "apply_with": "canon dialogue update --ops (node.prompt / choice.text)",
    }
