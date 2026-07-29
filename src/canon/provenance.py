"""Content-provenance journal + content-addressed object store.

Canon's Bible captures *state + lineage* (provenance_hash, parents, status). This
module captures the *trajectory*: every generate / edit / import mutation as an
append-only event in ``.canon/journal.jsonl``, with the exact bytes of each
version preserved in a content-addressed store at ``.canon/objects/<sha256>``.

Together they are the training-data substrate — a (generated → human-edited) pair
is recoverable by replaying an ``edit`` event's ``before_hash``/``after_hash``
against the object store. Everything cradle does to a pack flows through the
canon write verbs, and each verb records here, so coverage is complete from one
choke point.

Local-first: a webapp flushes the same event shape to a remote sink. This log is
outside canon's byte-determinism contract (like the StepLog).

Event shape (one line of journal.jsonl):
    {schema, ts, artifact_id, op, source, actor, session?,
     detail?, before_hash?, after_hash?, gen?}
  op     : generate | edit | keep | delete | import | switch | regenerate
  source : llm | user | import
  gen    : {model?, prompt_hash?, input_tokens?, output_tokens?, cost?}  (generate/regenerate)
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def _canon_dir(pack: str | Path) -> Path:
    return Path(pack) / ".canon"


def _objects_dir(pack: str | Path) -> Path:
    return _canon_dir(pack) / "objects"


def journal_path(pack: str | Path) -> Path:
    return _canon_dir(pack) / "journal.jsonl"


def snapshot_bytes(pack: str | Path, data: bytes) -> str:
    """Store *data* in the content-addressed store; return ``sha256:<hex>``.

    Idempotent: identical content dedups to one object (the whole point of a CAS).
    """
    digest = hashlib.sha256(data).hexdigest()
    obj = _objects_dir(pack) / digest
    if not obj.exists():
        obj.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=obj.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, obj)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    return "sha256:" + digest


def snapshot_file(pack: str | Path, path: str | Path) -> str | None:
    """Snapshot a file's current bytes into the CAS. None if it doesn't exist."""
    p = Path(path)
    if not p.is_file():
        return None
    return snapshot_bytes(pack, p.read_bytes())


def read_object(pack: str | Path, content_hash: str) -> bytes:
    """Fetch a stored version's bytes by ``sha256:<hex>``."""
    digest = content_hash.split(":", 1)[1] if ":" in content_hash else content_hash
    return (_objects_dir(pack) / digest).read_bytes()


def already_recorded(pack: str | Path, artifact_id: str, after_hash: str) -> bool:
    """True if an (artifact_id, after_hash) event already exists.

    Lets ``baseline`` be called repeatedly (e.g. every time cradle opens a level)
    without duplicating generate events.
    """
    jp = journal_path(pack)
    if not jp.is_file():
        return False
    with jp.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("artifact_id") == artifact_id and e.get("after_hash") == after_hash:
                return True
    return False


def append_event(pack: str | Path, event: dict) -> dict:
    """Append one event (schema + ts stamped) to the journal."""
    jp = journal_path(pack)
    jp.parent.mkdir(parents=True, exist_ok=True)
    full = {"schema": SCHEMA_VERSION, "ts": datetime.now(timezone.utc).isoformat(), **event}
    with jp.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(full, default=str) + "\n")
    return full


def all_events(pack: str | Path) -> list[dict]:
    """Every parsed journal event, in append order. The lineage builder
    reads the WHOLE journal once — cross-artifact connections are found by
    shared content hashes, which no per-artifact filter can see."""
    jp = journal_path(pack)
    out: list[dict] = []
    if not jp.is_file():
        return out
    with jp.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def artifact_versions(pack: str | Path, artifact_id: str) -> list[dict]:
    """The ordered version chain for one artifact, from the journal.

    Each entry is a compact view of the event that produced a version:
    ``{ts, op, source, actor, hash}`` (hash = the resulting ``after_hash``).
    This is what a user-facing version-history / restore picker reads; the raw
    bytes of any entry are fetched via :func:`read_object`. Deleted versions
    stay in the chain — the object store is never pruned by user actions.
    """
    jp = journal_path(pack)
    out: list[dict] = []
    if not jp.is_file():
        return out
    with jp.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("artifact_id") != artifact_id or not e.get("after_hash"):
                continue
            out.append(
                {
                    "ts": e.get("ts"),
                    "op": e.get("op"),
                    "source": e.get("source"),
                    "actor": e.get("actor"),
                    "hash": e["after_hash"],
                }
            )
    return out


def record(
    pack: str | Path,
    *,
    artifact_id: str,
    op: str,
    source: str,
    actor: str = "user",
    session: str | None = None,
    detail: Any = None,
    before_hash: str | None = None,
    after_hash: str | None = None,
    gen: dict | None = None,
) -> dict:
    """Assemble and append a provenance event (see module docstring for shape)."""
    ev: dict[str, Any] = {"artifact_id": artifact_id, "op": op, "source": source, "actor": actor}
    if session:
        ev["session"] = session
    if detail is not None:
        ev["detail"] = detail
    if before_hash:
        ev["before_hash"] = before_hash
    if after_hash:
        ev["after_hash"] = after_hash
    if gen:
        ev["gen"] = gen
    return append_event(pack, ev)
