"""Conversation transcripts — ``<pack>/.canon/agent/<conversation>.jsonl`` (Phase 1 A2).

This is the "journaled" half of row A2's gate: every conversation the
service runs persists as an append-only JSON-lines file under the pack's
own ``.canon/`` (durable truth lives in the pack — I5), so a transcript
survives the sidecar, resumes with the pack, and reads like the log the
panel renders. "Conversation" is the word — never "session", which master
§3.0-D reserves for Phase 2's play processes (two id spaces).

Line shapes, one record per line, oldest first::

    {"type": "meta", "id", "pack", "backend", "model", "system", "created"}
    {"type": "user", "content": <str | list[dict]>, "ts"}
    {"type": "assistant", "content": [<canonical blocks>], "ts"}
    {"type": "tool_result", "content": [<tool_result blocks>], "ts"}
                                       # image blocks are stored as their
                                       # reference, never as bytes (A7, §3.4)
    {"type": "turn_end", "stop_reason", "usage": {<measured tokens>}, "ts"}
    {"type": "error", "message", "retryable", "ts"}   # a turn that died

``messages(id)`` rebuilds the resendable history from the ``user`` /
``assistant`` / ``tool_result`` lines (the exact message dicts the loop
appended — ``canon.llm.chat``'s canonical shapes, thinking blocks
included, so replay is verbatim); ``meta``, ``turn_end`` and ``error``
lines are bookkeeping and never reach a provider.

What is deliberately NOT here, by row ownership:

- The cost/journal events of ``<pack>/.canon/journal.jsonl`` — identity,
  ``costCents``, the ``measured|estimated`` flag — are row A6's (master
  §3.0-B). A transcript records *measured tokens* on ``turn_end`` and
  nothing priced.
- Pack-less storage (a conversation with no open pack) waits for P0-10's
  project store; until then the store REQUIRES a pack directory and the
  service checks the pack before creating a conversation.
- Compaction (Phase 1 §3.4's summary event) and resume-with-reprobe are
  later rows; ``messages`` today replays the whole transcript.
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Where transcripts live, relative to the pack root.
AGENT_DIR = Path(".canon") / "agent"

#: Conversation ids: ``conv_`` + 8 hex — a distinct id space from Phase 2's
#: play sessions (master §3.0-D).
ID_PREFIX = "conv_"

#: Record types that rebuild into resendable messages, with the role each
#: carries. ``tool_result`` is a *user* message by the provider contract
#: (``tool_result_message``), so the mapping is data here, not a branch.
_MESSAGE_ROLES: dict[str, str] = {"user": "user", "assistant": "assistant", "tool_result": "user"}

#: Row P1-A7 / Phase 1 §3.4: vision inputs are "referenced by path + hash and
#: re-attached only when the current question needs eyes". A tool result's
#: image blocks are therefore NOT stored in the transcript — the refs (path +
#: sha256 + bytes) already ride in the result's own summary text block, and
#: this line takes each image's place, so replaying a conversation never
#: re-sends a picture the model has already looked at. Calling the vision
#: tool again is what re-attaches.
ATTACHMENT_PLACEHOLDER = (
    "[image attachment omitted from the transcript — its path + sha256 are in this result's summary; "
    "call the tool again to look at it]"
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def dereference_attachments(content: Any) -> Any:
    """One tool_result block list with its image blocks replaced by
    ``ATTACHMENT_PLACEHOLDER`` text (§3.4). Structure-preserving and
    non-destructive: the caller's list is never mutated, a result with no
    images is returned unchanged (identity), and every non-image block —
    the summary text carrying the refs included — survives verbatim."""
    if not isinstance(content, list):
        return content
    out: list[Any] = []
    changed = False
    for block in content:
        if not isinstance(block, dict):
            out.append(block)
            continue
        if block.get("type") == "image":
            out.append({"type": "text", "text": ATTACHMENT_PLACEHOLDER})
            changed = True
            continue
        inner = block.get("content")
        if block.get("type") == "tool_result" and isinstance(inner, list):
            stripped = dereference_attachments(inner)
            if stripped is not inner:
                out.append({**block, "content": stripped})
                changed = True
                continue
        out.append(block)
    return out if changed else content


def record_for(message: dict) -> dict:
    """The transcript record for one loop message append — the inverse of
    ``messages``. A user message whose content is a block list is a
    ``tool_result`` line (the loop's ``tool_result_message``); anything
    else keeps its role as its type. Image attachments inside a tool result
    are recorded as references, never as bytes (row P1-A7, §3.4)."""
    role = message.get("role")
    content = message.get("content")
    if role == "user" and isinstance(content, list):
        return {"type": "tool_result", "content": dereference_attachments(content)}
    return {"type": role, "content": content}


class ConversationStore:
    """Append-only transcripts under ``<pack>/.canon/agent/``."""

    def __init__(self, pack_dir: str | Path) -> None:
        self.pack_dir = Path(pack_dir)
        self.dir = self.pack_dir / AGENT_DIR

    def path(self, conversation_id: str) -> Path:
        """``<pack>/.canon/agent/<conversation_id>.jsonl``."""
        return self.dir / f"{conversation_id}.jsonl"

    def _new_id(self) -> str:
        while True:
            conversation_id = ID_PREFIX + secrets.token_hex(4)
            if not self.path(conversation_id).exists():
                return conversation_id

    def create(self, backend: str, model: str | None, system: str | None) -> str:
        """Start a conversation: write the ``meta`` line, return the new id.

        ``backend`` / ``model`` are the ids the service was started with
        (data, recorded for the transcript's provenance); ``system`` is the
        system prompt every turn of this conversation resends.
        """
        self.dir.mkdir(parents=True, exist_ok=True)
        conversation_id = self._new_id()
        meta = {
            "type": "meta",
            "id": conversation_id,
            "pack": str(self.pack_dir),
            "backend": backend,
            "model": model,
            "system": system,
            "created": _now(),
        }
        with self.path(conversation_id).open("x", encoding="utf-8") as fh:
            fh.write(json.dumps(meta) + "\n")
        return conversation_id

    def append(self, conversation_id: str, record: dict) -> None:
        """Append one record (stamped ``ts`` when it has none). ``KeyError``
        when the conversation does not exist — a transcript is never
        created by a stray append."""
        path = self.path(conversation_id)
        if not path.is_file():
            raise KeyError(f"no conversation {conversation_id!r} under {self.dir}")
        line = dict(record)
        line.setdefault("ts", _now())
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line) + "\n")

    def load(self, conversation_id: str) -> list[dict]:
        """Every line of the transcript, parsed, in order. ``KeyError`` when
        there is no such conversation."""
        path = self.path(conversation_id)
        if not path.is_file():
            raise KeyError(f"no conversation {conversation_id!r} under {self.dir}")
        with path.open(encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def meta(self, conversation_id: str) -> dict:
        """The ``meta`` line (first line) of a transcript."""
        lines = self.load(conversation_id)
        if not lines or lines[0].get("type") != "meta":
            raise ValueError(f"transcript {self.path(conversation_id)} does not start with a meta line")
        return lines[0]

    def messages(self, conversation_id: str) -> list[dict]:
        """The resendable history — ``{"role", "content"}`` dicts rebuilt
        from the ``user`` / ``assistant`` / ``tool_result`` lines, which is
        exactly what ``run_conversation(history=...)`` takes."""
        return [
            {"role": _MESSAGE_ROLES[record["type"]], "content": record["content"]}
            for record in self.load(conversation_id)
            if record.get("type") in _MESSAGE_ROLES
        ]

    def list(self) -> list[dict]:
        """``[{"id", "created", "turns"}]`` for every transcript, oldest first
        (``turns`` = completed ``turn_end`` lines). A file that is not a
        transcript (no meta line) is skipped, not fatal."""
        if not self.dir.is_dir():
            return []
        out: list[dict] = []
        for path in sorted(self.dir.glob("*.jsonl")):
            try:
                lines = self.load(path.stem)
            except (OSError, ValueError):
                continue
            if not lines or lines[0].get("type") != "meta":
                continue
            meta = lines[0]
            turns = sum(1 for line in lines if line.get("type") == "turn_end")
            out.append({"id": meta.get("id", path.stem), "created": meta.get("created"), "turns": turns})
        out.sort(key=lambda row: (row["created"] or "", row["id"]))
        return out


__all__ = [
    "AGENT_DIR",
    "ATTACHMENT_PLACEHOLDER",
    "ID_PREFIX",
    "ConversationStore",
    "dereference_attachments",
    "record_for",
]
