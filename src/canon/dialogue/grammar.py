"""The ONE condition/effect token grammar — parse, format, scope legality,
operand vocabulary and engine-evaluability (Phase 0 §7.1, P0 paper P.2.1–P.2.4
and P.3.3; row P0-9).

What this extends, rather than re-implements: the ``DialogueSpec`` the pack
registry already carries (``canon.packs.spec.DEFAULT_DIALOGUE_DATA``, seeded
into the dungeon template at ``canon.packs.dungeon.spec.DIALOGUE``). Every
namespace name, operand vocabulary, scope name and effect name in this module
is READ FROM THAT SPEC — "vocabulary is pack-registry data; no component
builds tokens by concatenation" (§7.1). Nothing here is a ``Literal`` union:
a template that adds a namespace, a quest state or a selector axis adds DATA.

Grammar (P.2.1, C7/C8): a token is ``<namespace>:<operand>[:<operand>…]``.
The operand is the entity's ``id_field`` value **verbatim, stringified**
(``has_item:2000``, ``quest:4000:completed`` on the dungeon pack). Arity is
DERIVED from the namespace's operand descriptor rather than hardcoded:

===========================  ==========================================
descriptor keys              shape
===========================  ==========================================
``fields`` + ``ops``         ``<field>:<op>:<value>``     (``player``)
``windows``                  ``<window>``                 (``time``)
``entity`` [+ ``states``]    ``<id>``  /  ``<id>:<state>``
``keys`` [+ ``values``]      ``<key>`` / ``<key>:<bool>`` (``flag``, C7)
``values``                   ``<value>``                  (``segment``)
===========================  ==========================================

Effects (P.2.2) carry no descriptor of their own in ``DialogueSpec.operands``,
so ``EFFECT_OPERAND_OF`` joins each seeded effect to the CONDITION descriptor
whose vocabulary it shares (``gives_item`` → ``has_item``'s item table). That
join is a table, not a concatenation, and an effect the table does not name
validates as a free ``<name>:<operand>…`` token with a warning — doctrine 10:
an unknown token is a named diagnostic, never a crash and never a block.

Scope legality (§7.1): ``condition_namespaces`` are legal in every scope of
``DialogueSpec.scopes``; ``scene_only_namespaces`` (``actor``) are legal ONLY
in ``scene`` scope and are rejected elsewhere WITH THE REASON. Which
namespaces a ``music`` section may carry is W2.1's subset design (P.2.5) —
Phase 0 carries the scope name only, so ``music`` accepts the same set as
``tree`` here.

Deliberately absent, by row ownership: engine evaluation of any namespace
(its own arc — this module only REPORTS what the engine's
``evaluable_namespaces`` block claims); the ``music`` subset (W2.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from canon.packs.spec import DialogueSpec, default_dialogue

__all__ = [
    "EFFECT_OPERAND_OF",
    "OPTIONAL_TAIL_EFFECTS",
    "ParsedToken",
    "TokenError",
    "effect_descriptor",
    "engine_evaluable",
    "format_token",
    "legal_in",
    "namespace_shape",
    "parse_effect",
    "parse_token",
    "spec_of",
]

#: The effect → condition-descriptor join (P.2.2). Data: an effect names the
#: namespace whose operand vocabulary it writes to, so ``gives_item:2000``
#: validates against the same item table as ``has_item:2000``.
EFFECT_OPERAND_OF: dict[str, str] = {
    "gives_item": "has_item",
    "takes_item": "has_item",
    "gives_quest": "quest",
    "advance_quest": "quest",
    "set_flag": "flag",
}

#: P.9 C7 — the two effects whose trailing operand is OPTIONAL
#: (``advance_quest:<id>[:<state>]``, ``set_flag:<key>[:<bool>]``).
OPTIONAL_TAIL_EFFECTS: frozenset[str] = frozenset({"advance_quest", "set_flag"})


class TokenError(ValueError):
    """A malformed / illegal token. ``payload`` carries the structured
    diagnosis every surface renders (doctrine 4: the reason travels with the
    refusal, it is never left for the reader to guess)."""

    def __init__(self, message: str, **payload: Any) -> None:
        super().__init__(message)
        self.payload = {"error": message, **payload}


@dataclass
class ParsedToken:
    """A parsed token: its namespace, its positional operands, and the named
    slots the picker/evaluator read (``entity_id``, ``state``, ``field``,
    ``op``, ``value``, ``window``, ``key``)."""

    token: str
    namespace: str
    operands: list[str]
    kind: str = "condition"  # "condition" | "effect"
    slots: dict[str, str] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "namespace": self.namespace,
            "operands": list(self.operands),
            "kind": self.kind,
            "slots": dict(self.slots),
        }


def spec_of(spec: DialogueSpec | None) -> DialogueSpec:
    """The pack's ``DialogueSpec``, or the CORE seed when a caller has no
    pack (``dialogue test`` on a bare tree payload). Never "everything is
    legal": the core seed is the same grammar every template starts from."""
    return spec if spec is not None else default_dialogue()


# ---------------------------------------------------------------------------
# Scope legality
# ---------------------------------------------------------------------------


def legal_in(namespace: str, scope: str, spec: DialogueSpec | None = None) -> str | None:
    """``None`` when *namespace* is legal at *scope*, else THE REASON.

    Three named refusals, never a bare False: an unknown scope, a scene-only
    namespace outside ``scene`` scope (§7.1: "rejected in trees, with the
    reason"), and an unknown namespace (which lists the legal set).
    """
    spec = spec_of(spec)
    if scope not in spec.scopes:
        return f"unknown scope {scope!r} — this pack declares {list(spec.scopes)}"
    if namespace in spec.scene_only_namespaces:
        if scope == "scene":
            return None
        return (
            f"{namespace!r} is legal only in scene scope (a {scope} has no actor roster) — "
            f"scene-only namespaces are {list(spec.scene_only_namespaces)}"
        )
    if namespace in spec.condition_namespaces:
        return None
    return (
        f"unknown condition namespace {namespace!r} — this pack declares "
        f"{list(spec.condition_namespaces)} (+ scene-only {list(spec.scene_only_namespaces)})"
    )


# ---------------------------------------------------------------------------
# Operand descriptors → shape
# ---------------------------------------------------------------------------


def namespace_shape(namespace: str, spec: DialogueSpec | None = None) -> list[dict[str, Any]]:
    """The ordered operand slots for *namespace*, derived from its
    ``DialogueSpec.operands`` descriptor (the table in the module docstring).
    Each slot is ``{name, required, choices|entity|field|filter}`` — what
    the picker renders and ``parse_token`` validates against."""
    spec = spec_of(spec)
    descriptor = spec.operands.get(namespace)
    if descriptor is None:
        return [{"name": "operand", "required": True}]
    if "fields" in descriptor and "ops" in descriptor:
        return [
            {"name": "field", "required": True, "choices": list(descriptor["fields"])},
            {"name": "op", "required": True, "choices": list(descriptor["ops"])},
            {"name": "value", "required": True},
        ]
    if "windows" in descriptor:
        return [{"name": "window", "required": True, "choices": list(descriptor["windows"])}]
    if "entity" in descriptor:
        slots: list[dict[str, Any]] = [
            {
                "name": "entity_id",
                "required": True,
                "entity": descriptor["entity"],
                "field": descriptor.get("field", "id"),
                "filter": descriptor.get("filter"),
                "restrict_to": descriptor.get("restrict_to"),
            }
        ]
        if descriptor.get("states"):
            slots.append({"name": "state", "required": True, "choices": list(descriptor["states"])})
        return slots
    if "keys" in descriptor:
        slots = [{"name": "key", "required": True}]
        if descriptor.get("values"):
            slots.append({"name": "value", "required": False, "choices": list(descriptor["values"])})
        return slots
    if "values" in descriptor:
        # `segment` seeds an EMPTY value list (P.9 C4): legal namespace, no
        # operand vocabulary yet — so the slot never narrows on an empty list.
        choices = list(descriptor["values"])
        slot: dict[str, Any] = {"name": "value", "required": True}
        if choices:
            slot["choices"] = choices
        return [slot]
    return [{"name": "operand", "required": True}]


def effect_descriptor(effect: str, spec: DialogueSpec | None = None) -> list[dict[str, Any]]:
    """The operand slots of an EFFECT token, joined to the condition
    namespace whose vocabulary it writes (``EFFECT_OPERAND_OF``). An effect
    outside the table takes one free operand."""
    spec = spec_of(spec)
    source = EFFECT_OPERAND_OF.get(effect)
    if source is None:
        return [{"name": "operand", "required": True}]
    slots = [dict(slot) for slot in namespace_shape(source, spec)]
    if effect in OPTIONAL_TAIL_EFFECTS:
        # C7: the trailing operand is optional; `gives_quest` keeps only the id.
        if len(slots) > 1:
            slots[-1]["required"] = False
        elif source == "flag":
            values = (spec.operands.get("flag") or {}).get("values") or ["true", "false"]
            slots.append({"name": "value", "required": False, "choices": list(values)})
    elif effect == "gives_quest":
        slots = slots[:1]
    else:
        slots = [slot for slot in slots if slot.get("required", True)]
    return slots


# ---------------------------------------------------------------------------
# parse / format
# ---------------------------------------------------------------------------


def _split(token: Any) -> tuple[str, list[str]]:
    if not isinstance(token, str) or not token.strip():
        raise TokenError(f"empty token {token!r}", token=str(token))
    parts = token.split(":")
    namespace = parts[0].strip()
    if not namespace:
        raise TokenError(f"token {token!r} has no namespace before the first ':'", token=token)
    return namespace, [p.strip() for p in parts[1:]]


def _fill(token: str, namespace: str, operands: list[str], slots: list[dict], kind: str) -> ParsedToken:
    required = [s for s in slots if s.get("required", True)]
    if len(operands) < len(required) or len(operands) > len(slots):
        want = "".join(
            f":<{s['name']}>" if s.get("required", True) else f"[:<{s['name']}>]" for s in slots
        )
        raise TokenError(
            f"{token!r}: {namespace} takes {len(required)}"
            + (f"–{len(slots)}" if len(slots) != len(required) else "")
            + f" operand(s) — {namespace}{want}",
            token=token,
            namespace=namespace,
        )
    named: dict[str, str] = {}
    for slot, value in zip(slots, operands, strict=False):
        choices = slot.get("choices")
        if choices and value not in choices:
            raise TokenError(
                f"{token!r}: {slot['name']} {value!r} is not in this pack's vocabulary {list(choices)}",
                token=token,
                namespace=namespace,
                slot=slot["name"],
            )
        named[str(slot["name"])] = value
    return ParsedToken(token=token, namespace=namespace, operands=list(operands), kind=kind, slots=named)


def parse_token(token: Any, *, scope: str = "tree", spec: DialogueSpec | None = None) -> ParsedToken:
    """Parse ONE condition token at *scope*. Raises ``TokenError`` naming the
    namespace, the slot and this pack's legal vocabulary — never a bare
    "invalid token"."""
    spec = spec_of(spec)
    namespace, operands = _split(token)
    reason = legal_in(namespace, scope, spec)
    if reason is not None:
        raise TokenError(reason, token=str(token), namespace=namespace, scope=scope)
    return _fill(str(token), namespace, operands, namespace_shape(namespace, spec), "condition")


def parse_effect(token: Any, *, spec: DialogueSpec | None = None) -> ParsedToken:
    """Parse ONE effect token. An effect the pack does not declare is a named
    error; a declared effect the ``EFFECT_OPERAND_OF`` table does not know
    parses with one free operand (doctrine 10)."""
    spec = spec_of(spec)
    namespace, operands = _split(token)
    if namespace not in spec.effects:
        raise TokenError(
            f"unknown effect {namespace!r} — this pack declares {list(spec.effects)}",
            token=str(token),
            namespace=namespace,
        )
    return _fill(str(token), namespace, operands, effect_descriptor(namespace, spec), "effect")


def format_token(namespace: str, *operands: Any) -> str:
    """``("quest", 4000, "active")`` → ``"quest:4000:active"`` — the ONE
    place a token is assembled (C8: operands are stringified verbatim)."""
    return ":".join([str(namespace), *(str(o) for o in operands)])


# ---------------------------------------------------------------------------
# Engine evaluability (P.2.4) — report, never enforce
# ---------------------------------------------------------------------------


def engine_evaluable(
    parsed: ParsedToken,
    scope: str,
    blocks: dict[str, Any] | None,
) -> tuple[bool, str | None]:
    """``(evaluable, reason)`` for *parsed* at *scope* against an engine's
    ``evaluable_namespaces`` block.

    P.2.4's rules, all three: a MISSING block resolves to the seed, never to
    "all supported" (the caller passes the seed — this function treats
    ``None`` as "nothing known", i.e. amber); a key present under the scope =
    evaluable; an optional narrowing object shrinks the honoured operand
    values, so ``quest:4000:active`` on a selector row goes amber against
    ``{"quest": {"states": ["completed", "failed"]}}``.

    Doctrine 10: this NEVER blocks. It is the engine-lag layer's data.
    """
    consequence = (
        "the effect never fires in game"
        if parsed.kind == "effect"
        else "the gate is ignored and the choice shows unconditionally in game"
    )
    if not isinstance(blocks, dict):
        return False, (
            f"the engine declares no evaluable namespaces for {scope} scope — {consequence}"
        )
    scoped = blocks.get(scope)
    if not isinstance(scoped, dict) or parsed.namespace not in scoped:
        return False, (
            f"the engine does not evaluate {parsed.namespace!r} at {scope} scope — {consequence}"
        )
    narrowing = scoped.get(parsed.namespace)
    if isinstance(narrowing, dict):
        for slot, value in parsed.slots.items():
            honoured = narrowing.get(f"{slot}s") or narrowing.get(slot)
            if isinstance(honoured, list) and honoured and value not in honoured:
                return False, (
                    f"the engine evaluates {parsed.namespace!r} at {scope} scope only for "
                    f"{slot} in {honoured} — {value!r} is outside that, so the gate is ignored"
                )
    return True, None
