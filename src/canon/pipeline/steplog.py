"""Structured per-step JSONL log — `.canon/log.jsonl` (PRD §7.6).

Observability, not state: the scheduler's source of truth stays
``bible.metadata.node_status``; this log is an append-only event stream
for humans and Cradle. Events carry wall-clock timestamps, so the file
sits OUTSIDE the byte-determinism contract (alongside ``bible.json``'s
``generated_at``) — nothing in the pipeline may read it back.

Opt-in: attach a ``StepLog`` to ``PipelineContext.steplog`` and both
schedulers emit; contexts without one (MazeWorld, most tests) write
nothing.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

__all__ = ["StepLog"]


class StepLog:
    """Append-only JSONL event log under ``<output_dir>/.canon/log.jsonl``.

    One JSON object per line: ``{"ts": <UTC ISO-8601>, "event": <name>,
    ...fields}``. Appends are lock-serialized (the orchestrator completes
    nodes from a thread pool) and the file survives across resumes —
    it is a log, not a snapshot.
    """

    DIRNAME = ".canon"
    FILENAME = "log.jsonl"

    def __init__(self, output_dir: str | Path) -> None:
        self.path = Path(output_dir) / self.DIRNAME / self.FILENAME
        self._lock = threading.Lock()

    def emit(self, event: str, **fields: object) -> None:
        record: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "event": event,
        }
        record.update(fields)
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
