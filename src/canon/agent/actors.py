"""Actor strings — the ONE place canon builds and reads them (Phase 1 A4; master doctrine 8, I6).

Every journal event names who did it (``provenance.record(actor=…)``,
the §3.0-B ``identity``): ``user`` for a person at the CLI, or
``agent:<conversation>/<specialist>`` for the agent (Phase 1 §5.4 — a
conversation id and the specialist that fired the tool, so the ledger and
the journal filter by either). Nothing else in ``canon.agent`` spells the
prefix or the slash: ``agent_actor`` builds the string, ``parse_actor``
reads it back, and the grep test in ``tests/test_agent_permissions.py``
holds the line.

Cradle's own user string (``cradle:user``) is cradle's — ``src/lib/actor.ts``
and the Rust ``USER_ACTOR`` const own it there; canon never assumes a
cradle-shaped user actor, it just records whatever ``--actor`` says.

``bind_call`` / ``current_call`` are the thread of attribution from the
service to a write tool: ``ToolRegistry.execute`` knows the actor and the
conversation, ``Tool.run(input)`` does not, and the registry (A2) does not
change — so the service binds the call context around ``execute`` and
``tools_write`` reads it through ``actor_for``. A ``ContextVar`` keeps it
per worker thread, so two conversations' turns never see each other's
actor (A4.5's run manager inherits the same seam per delegation).

Deliberately absent: specialist threading beyond ``FOREMAN`` (A4.5 sets
the specialist per delegated run), any per-actor permission (grants
govern tool names, never actors — §5.4).
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

#: The identity the CLI's write verbs default to (``--actor user``); §3.0-B's ``user``.
USER_ACTOR = "user"

#: The agent identity prefix — ``agent:<conversation>/<specialist>``.
AGENT_PREFIX = "agent:"

#: The specialist every turn runs as until row A4.5 threads delegated runs.
FOREMAN = "foreman"


@dataclass(frozen=True)
class ActorRef:
    """A parsed actor string.

    Attributes:
        actor: The string as recorded.
        kind: ``"agent"`` for ``agent:…``, else ``"user"`` (any other string
            — ``user``, ``cradle:user`` — is a person; ids are data).
        conversation: The conversation id for an agent actor, else ``None``.
        specialist: The specialist for an agent actor, else ``None``.
    """

    actor: str
    kind: str
    conversation: str | None = None
    specialist: str | None = None


def user_actor() -> str:
    """The actor a canon-side user write records — ``"user"``."""
    return USER_ACTOR


def agent_actor(conversation: str, specialist: str = FOREMAN) -> str:
    """``agent:<conversation>/<specialist>`` — the only constructor.

    Raises ``ValueError`` for an empty part or a ``/`` inside the
    conversation id (it would make the string unparseable).
    """
    if not conversation or not specialist:
        raise ValueError("agent_actor needs a non-empty conversation and specialist")
    if "/" in conversation:
        raise ValueError(f"conversation id may not contain '/': {conversation!r}")
    if any(ch.isspace() for ch in conversation + specialist):
        raise ValueError("actor parts may not contain whitespace")
    return f"{AGENT_PREFIX}{conversation}/{specialist}"


def parse_actor(actor: str) -> ActorRef:
    """Read an actor string back. ``agent:<conv>/<spec>`` → kind ``agent``
    with both parts; ``agent:<conv>`` (row A2's pre-specialist shape) →
    specialist ``None``; anything else → kind ``user``."""
    if not isinstance(actor, str) or not actor.startswith(AGENT_PREFIX):
        return ActorRef(actor=actor, kind="user")
    rest = actor[len(AGENT_PREFIX) :]
    conversation, _, specialist = rest.partition("/")
    return ActorRef(actor=actor, kind="agent", conversation=conversation or None, specialist=specialist or None)


def is_agent(actor: str) -> bool:
    """Is ``actor`` an ``agent:…`` identity?"""
    return parse_actor(actor).kind == "agent"


# ---------------------------------------------------------------------------
# The call context: actor + conversation, bound around registry.execute
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CallContext:
    """Who is executing the current tool call and in which conversation.

    ``conversation`` is also the ``--session`` every write verb journals
    (the verbs' keyword predates master §3.0-D's naming; the value is the
    conversation id — never a Phase 2 play-session id).

    ``run_id`` names the delegated run this call belongs to (``None`` for
    the foreman's own calls) — the permission chip carries it so ⏹ on ONE
    run card wakes only that run's chips, which the actor string cannot
    tell apart (two parallel runs of one specialist share it).

    ``journal`` is the call's OWN provenance events, appended by
    ``tools_write.journal_window`` while the verb runs. It exists because
    the journal is a pack-global append log: slicing it by index around a
    call attributes a concurrent call's writes to this one (two turns on
    one pack, or two parallel delegations). The sink is per call, so the
    write card's ``journal`` and a run's ``artifacts_touched`` name only
    what this call wrote. Mutable by design; every other field is frozen.
    """

    actor: str
    conversation: str
    run_id: str | None = None
    journal: list[dict] = field(default_factory=list, compare=False, repr=False)


_CALL: contextvars.ContextVar[CallContext | None] = contextvars.ContextVar("canon_agent_call", default=None)


@contextmanager
def bind_call(actor: str, conversation: str, run_id: str | None = None) -> Iterator[CallContext]:
    """Bind the call context for the duration of one ``registry.execute``."""
    context = CallContext(actor=actor, conversation=conversation, run_id=run_id)
    token = _CALL.set(context)
    try:
        yield context
    finally:
        _CALL.reset(token)


def current_call() -> CallContext:
    """The bound call context, or ``LookupError`` when no turn bound one —
    a write tool run outside a turn has nobody to attribute to and must
    not fall back to a made-up actor."""
    context = _CALL.get()
    if context is None:
        raise LookupError("no call context is bound — write tools run inside a conversation turn (bind_call)")
    return context


__all__ = [
    "AGENT_PREFIX",
    "FOREMAN",
    "USER_ACTOR",
    "ActorRef",
    "CallContext",
    "agent_actor",
    "bind_call",
    "current_call",
    "is_agent",
    "parse_actor",
    "user_actor",
]
