"""The tool registry — what the loop may call, and the one gate in front of it (Phase 1 A2).

A ``Tool`` is a ``ToolSpec`` (what the model sees) plus what the service
needs around it: its permission ``tier`` (``"auto"`` | ``"ask"`` | ``"paid"``
— a plain string, data), the ``run`` callable that does the work, and
``touches`` — a one-line statement of what it reads or writes, for the
chip copy and the transcript.

``ToolRegistry`` holds them in registration order (that order is the
``specs()`` order every request offers the model) and is the loop's
``tool_executor`` once the service binds ``actor`` and ``conversation``::

    registry = ToolRegistry()
    registry.register(Tool(spec=DESCRIBE_LEVEL, tier="auto", run=describe, touches="reads level/*.json"))
    run_conversation(..., tools=registry.specs(),
                     tool_executor=lambda name, i: registry.execute(name, i, actor=a, conversation=c))

Every failure reaches the model as data, never as a dead loop:
``execute`` raises ``UnknownTool`` (a JSON-bodied message naming the known
tools) or ``ToolRefused`` (the permission engine's reason), and
``run_conversation``'s ``_execute`` turns any exception into an
``is_error`` tool result.

Row A3 registers the real read tools here (its ``describe_level``,
windowed ``export_level``, ``db_row``, ``read_pack_file``, …) — this API is
the contract it builds against. Row A4 replaces ``PermissionEngine``'s
shell; the registry does not change. Row A4.5's run manager wraps
``execute`` per delegation for the write gate; it does not fork it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from canon.agent.permissions import PermissionEngine
from canon.llm.chat import ToolSpec


@dataclass
class Tool:
    """One registered tool.

    Attributes:
        spec: The provider-neutral ``ToolSpec`` offered to the model.
        tier: Permission tier — ``"auto"`` (reads, never ask), ``"ask"``
            (writes, chip first), ``"paid"`` (spend, estimate + confirm).
            A plain string; the engine decides what each means.
        run: ``(input) -> str | dict | Any``; the loop renders the return
            value as tool_result content. Raise to fail — the message
            becomes an ``is_error`` result.
        touches: What the tool reads/writes, one line, for chip copy and
            the transcript (``"reads level/<id>.json"``,
            ``"writes db/enemies.json via canon db update"``).
    """

    spec: ToolSpec
    tier: str
    run: Callable[[dict], Any]
    touches: str


class ToolRefused(PermissionError):
    """The permission engine said no; ``str(exc)`` is its reason."""


class UnknownTool(LookupError):
    """No tool is registered under that name; ``str(exc)`` is a JSON
    document ``{"error": "unknown_tool", "tool": ..., "known": [...]}`` —
    structured like every canon verb failure (the ``_emit_error`` contract),
    so the model can read the known names off it."""


class ToolRegistry:
    """Named tools in registration order + the permission check on execute."""

    def __init__(self, permissions: PermissionEngine | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        self._permissions = permissions if permissions is not None else PermissionEngine()

    @property
    def permissions(self) -> PermissionEngine:
        """The engine this registry consults (A4 swaps the shell for the real one)."""
        return self._permissions

    def register(self, tool: Tool) -> None:
        """Add ``tool``; a second tool under the same name is a ``ValueError``
        (a silent replacement would let a later import shadow a read tool
        with a write)."""
        name = tool.spec.name
        if name in self._tools:
            raise ValueError(f"tool {name!r} is already registered")
        self._tools[name] = tool

    def get(self, name: str) -> Tool:
        """The registered tool, or ``UnknownTool``."""
        tool = self._tools.get(name)
        if tool is None:
            raise UnknownTool(json.dumps({"error": "unknown_tool", "tool": name, "known": self.names()}))
        return tool

    def specs(self) -> list[ToolSpec]:
        """Every tool's ``ToolSpec`` in registration order — what each request offers."""
        return [tool.spec for tool in self._tools.values()]

    def names(self) -> list[str]:
        """Registered names in registration order."""
        return list(self._tools)

    def execute(self, name: str, input: dict, *, actor: str, conversation: str) -> Any:  # noqa: A002
        """Run ``name`` with ``input`` after the permission check.

        Returns the tool's own result. Raises ``UnknownTool`` for a name
        nothing registered, ``ToolRefused(reason)`` when the engine says no,
        and whatever the tool itself raises — the loop renders all three as
        ``is_error`` tool results.
        """
        tool = self.get(name)
        decision = self._permissions.check(tool, input, actor=actor, conversation=conversation)
        if not decision.allowed:
            raise ToolRefused(decision.reason)
        return tool.run(input)


__all__ = ["Tool", "ToolRefused", "ToolRegistry", "UnknownTool"]
