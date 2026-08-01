"""Per-project job ledger — the durable record cradle's background worker
appends on each queued generation's completion (mirrors the spend ledger)."""

from __future__ import annotations

from pathlib import Path

from canon import jobs


def test_job_ledger_round_trip(tmp_path: Path):
    p = tmp_path / "jobs_pack"
    p.mkdir()
    jobs.record_job(p, {
        "job_id": "a1", "op": "improve", "target": "l1", "status": "ok",
        "changed": True, "changed_artifacts": ["level:s/l1/collision"],
        "duration_ms": 8200, "actual_usd": 0.03,
    })
    jobs.record_job(p, {
        "job_id": "a2", "op": "improve", "target": "l1", "status": "no_change",
        "changed": False, "duration_ms": 1200, "actual_usd": 0.0,
    })
    jobs.record_job(p, {
        "job_id": "a3", "op": "sprite", "target": "enemy:foo", "status": "failed",
        "error": "boom",
    })
    s = jobs.summarize(p)
    assert s["count"] == 3
    assert s["by_op"] == {"improve": 2, "sprite": 1}
    assert s["by_status"] == {"failed": 1, "no_change": 1, "ok": 1}
    # every stored line carries the authoritative schema + a ts
    assert all(e["schema"] == jobs.SCHEMA and e.get("ts") for e in s["entries"])
    # pass-through fields survive verbatim
    assert s["entries"][0]["changed_artifacts"] == ["level:s/l1/collision"]


def test_job_schema_is_authoritative(tmp_path: Path):
    """A caller can never override the ledger schema."""
    p = tmp_path / "jp"
    p.mkdir()
    stored = jobs.record_job(p, {"job_id": "x", "op": "improve", "schema": "evil"})
    assert stored["schema"] == jobs.SCHEMA


def test_job_read_skips_malformed_lines(tmp_path: Path):
    """A valid-JSON non-object line (or garbage) is skipped, not fatal —
    `canon jobs list` must survive a hand-tampered ledger."""
    p = tmp_path / "junk_jobs"
    p.mkdir()
    jobs.record_job(p, {"job_id": "ok1", "op": "improve", "status": "ok"})
    ledger = p / ".canon" / jobs.LEDGER_NAME
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write("42\n")  # valid JSON, not a dict
        fh.write('"nope"\n')
        fh.write("{not json\n")  # not JSON at all
        fh.write("\n")  # blank
    s = jobs.summarize(p)  # must not raise
    assert s["count"] == 1
    assert s["by_op"] == {"improve": 1}


def test_job_list_empty_when_no_ledger(tmp_path: Path):
    p = tmp_path / "fresh"
    p.mkdir()
    s = jobs.summarize(p)
    assert s == {"count": 0, "by_op": {}, "by_status": {}, "entries": []}
