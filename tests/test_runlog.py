"""The per-project run log (``canon.runlog``) and the debug report built from
it (``canon.debug_report``, ``canon debug runs|report``)."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from canon import runlog
from canon.backends import FakeLLMBackend
from canon.backends.failures import AssetError, retry_call
from canon.debug_report import build_report, list_runs, render_markdown
from canon.llm import LLMClient, LLMRequest
from canon.pipeline.stats import GenerationStats
from canon.pipeline.steplog import StepLog


@pytest.fixture(autouse=True)
def _clean_runlog(monkeypatch):
    monkeypatch.delenv(runlog.ENV_MODE, raising=False)
    monkeypatch.delenv(runlog.ENV_PARENT, raising=False)
    runlog.end()
    yield
    runlog.end()


@pytest.fixture
def pack(tmp_path: Path) -> Path:
    p = tmp_path / "pack"
    (p / ".canon").mkdir(parents=True)
    return p


def _records(pack: Path) -> list[dict]:
    return runlog.read_runlog(pack)


def test_a_quiet_run_writes_nothing(pack):
    runlog.start(["canon", "level", "export", str(pack)])
    runlog.end(0)
    assert not runlog.log_path(pack).exists()


def test_all_mode_writes_every_run(pack, monkeypatch):
    monkeypatch.setenv(runlog.ENV_MODE, "all")
    runlog.start(["canon", "level", "export", str(pack)])
    runlog.end(0)
    events = [r["event"] for r in _records(pack)]
    assert events == ["run_start", "run_end"]


def test_off_mode_writes_nothing(pack, monkeypatch):
    monkeypatch.setenv(runlog.ENV_MODE, "off")
    assert runlog.start(["canon", "x", str(pack)]) is None
    runlog.record_call(lane="llm", provider="anthropic", ok=False, error=RuntimeError("boom"))
    runlog.end(1)
    assert not runlog.log_path(pack).exists()


def test_a_failed_run_is_written_with_its_traceback(pack):
    runlog.start(["canon", "db", "new", str(pack)])
    try:
        raise ValueError("bad row")
    except ValueError as exc:
        runlog.end(1, exc)
    end = _records(pack)[-1]
    assert end["event"] == "run_end"
    assert end["ok"] is False and end["exit_code"] == 1
    assert "ValueError: bad row" in end["error"]
    assert "raise ValueError" in end["traceback"]


def test_the_first_call_flushes_and_later_events_stream(pack):
    runlog.start(["canon", "db", "complete", str(pack)])
    assert not runlog.log_path(pack).exists()
    runlog.record_call(lane="llm", provider="anthropic", label="npc", ok=True, duration_ms=12.5,
                       input_tokens=10, output_tokens=5, cost_usd=0.001)
    kinds = [r["event"] for r in _records(pack)]
    assert kinds == ["run_start", "env", "call"]  # on disk before the run ends
    runlog.end(0)
    end = _records(pack)[-1]
    assert end["summary"]["calls"] == 1
    assert end["summary"]["by_provider"]["anthropic"]["cost_usd"] == pytest.approx(0.001)


def test_a_steplog_binds_a_run_with_no_pack_on_its_command_line(tmp_path):
    out = tmp_path / "fresh"
    runlog.start(["python", "-m", "canon.packs.dungeon.run_world", "--output-dir", "elsewhere"])
    StepLog(out).emit("node_start", node="phase:story")
    runlog.record_call(lane="llm", provider="fake", ok=True)
    runlog.end(0)
    records = _records(out)
    steps = [r for r in records if r["event"] == "step"]
    assert steps and steps[0]["step"] == "node_start" and steps[0]["node"] == "phase:story"


def test_secrets_never_reach_the_file(pack, monkeypatch):
    monkeypatch.setenv("FAL_KEY", "fal-secret-value-123456")
    runlog.start(["canon", "x", str(pack), "--api-key", "sk-ant-abcdefghijklmnop", "--token=hunter22222"])
    runlog.record_call(
        lane="asset", provider="fal", ok=False,
        error=RuntimeError("401 for key fal-secret-value-123456 at https://x.test/?key=AIzaSyA1234567890123456789"),
    )
    runlog.end(1)
    text = runlog.log_path(pack).read_text()
    assert "fal-secret-value-123456" not in text
    assert "sk-ant-abcdefghijklmnop" not in text
    assert "hunter22222" not in text
    assert "AIzaSyA1234567890123456789" not in text
    assert runlog.REDACTED in text


def test_warnings_are_captured_and_info_from_canon_only(pack):
    runlog.start(["canon", "x", str(pack)])
    logging.getLogger("canon.test").info("canon info line")
    logging.getLogger("thirdparty").info("noise")
    logging.getLogger("thirdparty").warning("third-party warning")
    runlog.end(0)
    messages = [r["message"] for r in _records(pack) if r["event"] == "log"]
    assert "canon info line" in messages
    assert "third-party warning" in messages
    assert "noise" not in messages


def test_retry_call_records_every_attempt(pack):
    runlog.start(["canon", "x", str(pack)])
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise AssetError("busy", retryable=True, status=503, kind="HTTPError", provider="fal")
        return "ok"

    assert retry_call(flaky, provider="fal", label="npc:1", backoff=(0,), sleep=lambda _s: None) == "ok"
    runlog.end(0)
    attempts = [r for r in _records(pack) if r["event"] == "call"]
    assert [a["ok"] for a in attempts] == [False, False, True]
    assert [a.get("retrying") for a in attempts] == [True, True, None]
    assert attempts[0]["error"]["status"] == 503
    assert all(a["provider"] == "fal" and a["label"] == "npc:1" for a in attempts)


def test_llm_client_records_success_and_failure(pack):
    class Boom:
        def generate(self, request):
            raise ConnectionError("network down")

    runlog.start(["canon", "x", str(pack)])
    stats = GenerationStats()
    LLMClient(backend=FakeLLMBackend(["ok"]), stats=stats, phase="story").generate(
        LLMRequest(system="s", user_message="go"),
    )
    with pytest.raises(ConnectionError):
        LLMClient(backend=Boom(), phase="quest").generate(LLMRequest(system="s", user_message="go"))
    runlog.end(1)
    calls = [r for r in _records(pack) if r["event"] == "call"]
    assert [(c["label"], c["ok"]) for c in calls] == [("story", True), ("quest", False)]
    assert calls[1]["error"]["retryable"] is True
    assert "network down" in calls[1]["error"]["message"]


def test_env_file_keys_shadowed_by_the_shell_are_noted(pack, tmp_path, monkeypatch):
    from canon.cli.main import _load_env_file

    monkeypatch.setenv("ELEVENLABS_API_KEY", "old-shell-value-1")
    env_file = tmp_path / "keys.env"
    env_file.write_text("ELEVENLABS_API_KEY=new-file-value-2\n")
    runlog.start(["canon", "x", str(pack)])
    _load_env_file(env_file)
    runlog.end(0)
    assert os.environ["ELEVENLABS_API_KEY"] == "old-shell-value-1"
    shadow = [r for r in _records(pack) if r["event"] == "env_shadowed"]
    assert shadow and shadow[0]["names"] == ["ELEVENLABS_API_KEY"]
    text = runlog.log_path(pack).read_text()
    assert "old-shell-value-1" not in text and "new-file-value-2" not in text


def test_session_records_a_systemexit_code(pack):
    with pytest.raises(SystemExit):
        with runlog.session(["canon", "x", str(pack)]):
            runlog.record_call(lane="llm", provider="anthropic", ok=True)
            raise SystemExit(2)
    end = _records(pack)[-1]
    assert end["event"] == "run_end" and end["exit_code"] == 2 and end["ok"] is False


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def _paid_run(pack: Path) -> str:
    """A parent run that spawns a child; the child fails two assets."""
    parent = runlog.start(["canon", "world", "new", str(pack)])
    assert parent is not None
    runlog.record_call(lane="llm", provider="anthropic", label="story", ok=True, duration_ms=900,
                       input_tokens=100, output_tokens=50, cost_usd=0.01)
    # The child process inherits CANON_PARENT_RUN.
    child = runlog.RunLog(["python", "-m", "canon.packs.dungeon.run_world"], pack_dir=pack)
    assert child.parent == parent.run_id
    for label in ("music:combat", "music:town"):
        child.record_call(
            lane="asset", provider="lyria", label=label, ok=False, duration_ms=200,
            error={"error": "ClientError", "status": 403, "message": "PERMISSION_DENIED billing",
                   "retryable": False, "hint": "check the lyria key"},
        )
    child.note_cost(lane="image", usd=0.039, provider="fal")
    child.event("step", step="node_start", node="phase:assets")
    child.event("step", step="node_done", node="phase:assets")
    child.end(0)
    logging.getLogger("canon.x").warning("asset failed: music:combat")
    runlog.end(0)
    return parent.run_id


def test_report_folds_children_and_groups_failures(pack):
    run_id = _paid_run(pack)
    runs = list_runs(pack)
    assert [r["run"] for r in runs] == [run_id]
    assert len(runs[0]["children"]) == 1
    assert runs[0]["status"] == "warnings"

    report = build_report(pack)
    assert report["run"]["run"] == run_id
    totals = report["totals"]
    assert totals["calls"] == 3 and totals["final_failures"] == 2
    assert totals["cost_usd"] == pytest.approx(0.049)
    [group] = report["failures"]
    assert group["provider"] == "lyria" and group["status"] == 403 and group["count"] == 2
    assert group["labels"] == ["music:combat", "music:town"]
    assert any("lyria refused the credential" in s for s in report["suggestions"])
    assert report["phases"][0]["node"] == "phase:assets"
    assert report["phases"][0]["status"] == "done"

    md = render_markdown(report)
    assert "## Failures" in md and "lyria" in md and "## What to try" in md


def test_a_run_without_an_end_is_incomplete(pack):
    run = runlog.RunLog(["canon", "world", "new", str(pack)], pack_dir=pack)
    run.record_call(lane="llm", provider="anthropic", ok=True)
    # No end(): the process was killed.
    report = build_report(pack, run.run_id[-6:])
    assert report["run"]["status"] == "incomplete"
    assert any("killed" in s for s in report["suggestions"])
    assert "## Last events" in render_markdown(report)


def test_report_without_a_log_raises(pack):
    with pytest.raises(LookupError):
        build_report(pack)


def _cli(*args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in (runlog.ENV_PARENT, runlog.ENV_MODE)}
    return subprocess.run(
        [sys.executable, "-m", "canon.cli.main", *args], capture_output=True, text=True, env=env,
    )


def test_cli_debug_runs_and_report(pack, tmp_path):
    _paid_run(pack)
    result = _cli("debug", "runs", str(pack))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["runs"][0]["status"] == "warnings"

    out = tmp_path / "report.md"
    result = _cli("debug", "report", str(pack), "--out", str(out))
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["report"]["totals"]["final_failures"] == 2
    assert payload["markdown"].startswith("# Canon run report")
    assert out.read_text() == payload["markdown"]

    result = _cli("debug", "report", str(pack), "--markdown")
    assert result.returncode == 0 and result.stdout.startswith("# Canon run report")

    # The reads themselves are quiet: they add no run of their own.
    assert len(list_runs(pack)) == 1


def test_a_refused_read_writes_nothing(pack):
    """A clean error exit (bad input to a read) keeps the pack byte-quiet."""
    result = _cli("jobs", "record", str(pack), "--json", "not json")
    assert result.returncode == 1
    assert not runlog.log_path(pack).exists()


def test_a_refused_command_after_a_call_keeps_its_error(pack):
    runlog.start(["canon", "level", "generate", str(pack)])
    runlog.record_call(lane="llm", provider="anthropic", ok=True)
    runlog.event("cli_error", message="level l9 not found", exit_code=1)
    runlog.end(1)
    [run] = list_runs(pack)
    assert run["status"] == "failed" and run["exit_code"] == 1
    assert any(r.get("message") == "level l9 not found" for r in _records(pack))
