"""Structured per-step JSONL log — `.canon/log.jsonl` (PRD §7.6).

Observability, not state: the scheduler's source of truth stays
``bible.metadata.node_status``; this log is an append-only event stream
for humans and Cradle. Events carry wall-clock timestamps, so the file
sits OUTSIDE the byte-determinism contract (alongside ``bible.json``'s
``generated_at``) — nothing in the pipeline may read it back.

Opt-in: attach a ``StepLog`` to ``PipelineContext.steplog`` and both
schedulers emit; contexts without one (MazeWorld, most tests) write
nothing.

Row P1-A4.5 (master §3.0-D, the ONE Stop contract — *start nothing new,
keep what landed, say what it cost*) extends this log with the cancel
hook for multi-item verbs:

- ``CANON_CANCEL_FILE`` (env) names a per-job cancel FILE (cradle's
  JobQueue writes ``<pack>/.canon/cancel/<job_id>`` and passes the path at
  spawn). ``check_cancel`` — called by ``canon.packs.platformer.phases.step``,
  the one ``node_item`` emitter every item loop goes through — raises
  ``RunCancelled`` when that file exists. Unset env = no-op, byte for byte
  (the A/B tree tests never see it).
- ``run_end`` is cancel-aware: once ``check_cancel`` has raised, the
  scheduler's own ``run_end`` (``ok=False``) gains ``cancelled: true`` and
  ``kept`` — the nodes that completed plus the items of the interrupted
  node that reached their boundary — so the relay can say what landed
  without a second ``run_end`` line. The runner (``run_slice``) exits 3.

Row P0-10 (master §3.0-E) moves the ONE ``node_item`` emitter — ``step`` —
from ``canon.packs.platformer.phases`` down here, unchanged. It was pack
code only by accident of birth: A4.5 put the cancel check inside it and
called it "the one emitter every item loop goes through", and P0-10's
dungeon runner needs exactly that emitter (so a dungeon create is
cancellable for free). ``canon.packs.platformer.phases.step`` re-exports
this function, so every platformer call site is byte-for-byte the same
call it was.

Deliberately absent, by row ownership: killing the process (cradle's
worker, after a 10 s grace), the play-session Stop (W2.0), pricing what a
cancelled run cost (A6 reads the ledger; this log carries no money).
"""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = ["CANCEL_FILE_ENV", "EXIT_CANCELLED", "RunCancelled", "StepLog", "step"]

#: The env var naming the per-job cancel file (cradle sets it at spawn).
CANCEL_FILE_ENV = "CANON_CANCEL_FILE"

#: The runner's exit status after a cancel (0 ok · 1 failed · 3 cancelled).
EXIT_CANCELLED = 3


class RunCancelled(RuntimeError):
    """Raised at a ``node_item`` boundary because the cancel file exists.

    Attributes:
        node: The node (``phase:<name>``) that was about to start an item.
        item: The item it was about to start (never started).
        kept: What had landed at that boundary — ``StepLog.kept()``.
        cancel_file: The file whose presence cancelled the run.
    """

    def __init__(self, node: str, item: str, kept: list[str], cancel_file: Path) -> None:
        self.node = node
        self.item = item
        self.kept = list(kept)
        self.cancel_file = cancel_file
        super().__init__(f"cancelled before {node} · {item} (cancel file {cancel_file}); kept {len(kept)} item(s)")


class StepLog:
    """Append-only JSONL event log under ``<output_dir>/.canon/log.jsonl``.

    One JSON object per line: ``{"ts": <UTC ISO-8601>, "event": <name>,
    ...fields}``. Appends are lock-serialized (the orchestrator completes
    nodes from a thread pool) and the file survives across resumes —
    it is a log, not a snapshot.

    ``cancel_file`` pins the cancel file explicitly (tests); ``None`` reads
    ``CANON_CANCEL_FILE`` from the environment at every check, so an
    unset env is exactly the pre-A4.5 log.
    """

    DIRNAME = ".canon"
    FILENAME = "log.jsonl"

    def __init__(self, output_dir: str | Path, *, cancel_file: str | Path | None = None) -> None:
        self.path = Path(output_dir) / self.DIRNAME / self.FILENAME
        self._lock = threading.Lock()
        self._cancel_file = Path(cancel_file) if cancel_file is not None else None
        self._done: list[str] = []
        self._items: dict[str, list[str]] = {}
        #: The ``RunCancelled`` this log raised, once it has (``None`` before).
        self.cancelled: RunCancelled | None = None

    # -- the cancel hook ------------------------------------------------------

    @property
    def cancel_file(self) -> Path | None:
        """The cancel file in force: the pinned one, else the env's, else ``None``."""
        if self._cancel_file is not None:
            return self._cancel_file
        raw = os.environ.get(CANCEL_FILE_ENV, "")
        return Path(raw) if raw else None

    def cancel_requested(self) -> bool:
        """Does the cancel file exist right now? ``False`` when none is named."""
        path = self.cancel_file
        return path is not None and path.exists()

    def check_cancel(self, node: str, item: str) -> None:
        """The boundary check: raise ``RunCancelled`` (once, remembered on
        ``self.cancelled``) when the cancel file exists; otherwise nothing.
        Called BEFORE an item is announced, so every announced item of the
        interrupted node had reached its boundary — that is ``kept``."""
        if not self.cancel_requested():
            return
        cancel_file = self.cancel_file
        assert cancel_file is not None
        with self._lock:
            error = RunCancelled(node, item, self._kept_locked(), cancel_file)
            if self.cancelled is None:
                self.cancelled = error
        raise error

    def kept(self) -> list[str]:
        """What landed: every node that reached ``node_done``, then
        ``<node>:<item>`` for each item an unfinished node announced (items
        are announced at their boundary, so an announced item of a node the
        cancel interrupted had completed by the time the check ran)."""
        with self._lock:
            return self._kept_locked()

    def _kept_locked(self) -> list[str]:
        out = list(self._done)
        done = set(self._done)
        for node, items in self._items.items():
            if node in done:
                continue
            out.extend(f"{node}:{item}" for item in items)
        return out

    # -- the log ---------------------------------------------------------------

    def emit(self, event: str, **fields: object) -> None:
        record: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "event": event,
        }
        record.update(fields)
        with self._lock:
            self._note(event, fields)
            if event == "run_end" and self.cancelled is not None:
                record.setdefault("cancelled", True)
                record.setdefault("kept", self._kept_locked())
            line = json.dumps(record, ensure_ascii=False)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def _note(self, event: str, fields: dict[str, object]) -> None:
        node = fields.get("node")
        if not isinstance(node, str):
            return
        if event == "node_done":
            if node not in self._done:
                self._done.append(node)
        elif event == "node_item":
            item = fields.get("item")
            self._items.setdefault(node, []).append(str(item))


def step(
    ctx: Any,
    phase: str,
    item: str,
    index: int | None = None,
    total: int | None = None,
) -> None:
    """Emit one SUB-phase progress event on the run's StepLog.

    The schedulers already log ``node_start``/``node_done`` per phase, which
    is enough granularity for a $0 run (the whole thing is three seconds).
    A paid run is not: ``plat:sprite_art`` alone can sit on one node for
    minutes per asset, and a progress display frozen on "Sprite art" for ten
    minutes is indistinguishable from a crash — which is the entire problem
    a progress display exists to solve. So the phases that loop over PAID
    work announce each item as they start it.

    Observability only, exactly like the events the schedulers emit: a
    context with no ``steplog`` (most tests) writes nothing, and nothing in
    the pipeline may read these back.

    Row P1-A4.5 (master §3.0-D): this is ALSO the cancel boundary of every
    multi-item verb — the ONE emitter every item loop goes through, so the
    check lives here once. Before an item is announced,
    ``StepLog.check_cancel`` raises ``RunCancelled`` when the per-job
    cancel file (``CANON_CANCEL_FILE``) exists; the item never starts, the
    announced ones stay (keep what landed). With the env unset the check
    is a no-op and the emitted tree is byte-identical.

    Row P0-10 (master §3.0-E): moved here from the platformer pack so the
    dungeon's item loops (``db:<kind>``, dialogue, placement, portraits…)
    announce through the SAME emitter and inherit the same cancel boundary
    — no second cancel path, and cradle's progress relay is unchanged.
    """
    steplog = getattr(ctx, "steplog", None)
    if steplog is None:
        return
    node = f"phase:{phase}"
    check_cancel = getattr(steplog, "check_cancel", None)
    if check_cancel is not None:
        check_cancel(node, item)
    fields: dict[str, object] = {"node": node, "item": item}
    if index is not None:
        fields["index"] = index
    if total is not None:
        fields["total"] = total
    steplog.emit("node_item", **fields)
