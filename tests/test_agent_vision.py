"""Tests for row P1-A7's vision tools — ``capture_frames`` / ``run_trajectory``
/ ``view_asset`` (Phase 1 §4.C/§4.D, ASSUMPTION-6a).

Hermetic + $0: the pack is a real generated platformer tree (``run_slice
--orchestrate`` on the fake backends, module-scoped, copied per test) and the
headless legs spawn the REAL harness (``canon.packs.platformer.play``, the
module cradle's ▶ Play spawns). No provider is ever called.

The load-bearing claims each class pins:

- the tools write NOTHING into the pack (every file's sha256 before and
  after);
- the child's environment carries only the ``PLAT_*`` the tool set;
- exit 0 is not evidence — a harness that produced no output is a NAMED
  failure, never a silent pass;
- the demote-to-ask escape hatch is registry DATA, not a code change;
- images are referenced (path + sha256) in the transcript and re-attached
  only by calling the tool again — never re-sent on replay.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from canon.agent.conversations import ATTACHMENT_PLACEHOLDER, ConversationStore, dereference_attachments, record_for
from canon.agent.loop import _render_result
from canon.agent.permissions import PermissionEngine
from canon.agent.registry import ToolRegistry
from canon.agent.tools_read import ToolInputError, register_read_tools
from canon.agent.tools_vision import (
    AGENT_SETTINGS_FILE,
    DEFAULT_TICKS,
    HARNESS_MODULE,
    MAX_FRAMES,
    MAX_TICKS,
    VISION_TIER,
    VISION_TOOL_NAMES,
    HeadlessError,
    attachment_refs,
    harness_launch,
    register_vision_tools,
    registry_tier,
    scrubbed_env,
    summarize_trajectory,
    vision_tool_specs,
)

REPO = Path(__file__).resolve().parents[1]

# A 1x1 PNG the stub harness writes when a test does not want real pygame.
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360606060000000050001a5f645400000000049454e44ae426082"
)


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def generated_tree(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("a7_tree")
    subprocess.run(
        [
            sys.executable, "-m", "canon.packs.platformer.run_slice",
            "--backend", "fake", "--engine", "json", "--image-backend", "fake",
            "--music-backend", "none", "--sfx-backend", "none",
            "--num-stages", "1", "--num-levels", "2", "--num-enemies", "2", "--num-items", "2",
            "--seed", "a7-vision", "--orchestrate", "--output-dir", str(out),
        ],
        check=True,
        capture_output=True,
        cwd=REPO,
    )
    return out


@pytest.fixture
def pack(generated_tree: Path, tmp_path: Path) -> Path:
    dst = tmp_path / "pack"
    shutil.copytree(generated_tree, dst)
    return dst


def fingerprint(root: Path) -> list[tuple[str, str]]:
    """Every file's path + sha256 — the "writes nothing" assertion."""
    return sorted(
        (str(p.relative_to(root)), hashlib.sha256(p.read_bytes()).hexdigest())
        for p in root.rglob("*")
        if p.is_file()
    )


def vision_registry(pack: Path) -> ToolRegistry:
    registry = ToolRegistry(PermissionEngine(pack))
    register_read_tools(registry, pack)
    register_vision_tools(registry, pack)
    return registry


def summary_of(result) -> dict:
    """The summary text block of a block-list tool result."""
    assert isinstance(result, list) and result[0]["type"] == "text"
    return json.loads(result[0]["text"])


def stage_of(pack: Path) -> str:
    return sorted(p.name for p in (pack / "level").iterdir() if p.is_dir() and (p / "l1").is_dir())[0]


def stub_harness(monkeypatch, script: str) -> list[dict]:
    """Replace the harness command with ``python -c <script>`` and record the
    environment every spawn received. Used where the point of the test is the
    CONTRACT around the process, not pygame."""
    seen: list[dict] = []
    real_run = subprocess.run

    def fake_launch(pack: Path, level_id: str) -> list[str]:
        return [sys.executable, "-c", script]

    def recording_run(argv, **kwargs):
        seen.append(dict(kwargs.get("env") or {}))
        return real_run(argv, **kwargs)

    monkeypatch.setattr("canon.agent.tools_vision.harness_launch", fake_launch)
    monkeypatch.setattr("canon.agent.tools_vision.subprocess.run", recording_run)
    return seen


#: A stub that behaves like the harness: writes N frames into PLAT_CAPTURE and
#: a traj file at PLAT_TRAJ, then exits 0.
_STUB_OK = (
    "import os,pathlib;"
    "d=os.environ.get('PLAT_CAPTURE');"
    "p=os.environ.get('PLAT_TRAJ');"
    f"png=bytes.fromhex('{_PNG.hex()}');"
    "[pathlib.Path(d,'frame_%04d.png'%i).write_bytes(png) for i in (0,30,60)] if d else None;"
    "pathlib.Path(p).write_text('0|P:1.0:2.0:0.0:0|\\n1|P:2.0:2.0:1.0:1|goblin:5.0:5.0:1\\n') if p else None"
)

#: A stub that exits 0 having written nothing at all — the Godot lesson (a
#: wrong --path boots the editor and exits clean).
_STUB_SILENT = "import sys; sys.exit(0)"


# ---------------------------------------------------------------------------
# Registration + tiers
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_the_three_tools_register_auto_tier_in_order(self, pack: Path) -> None:
        registry = ToolRegistry(PermissionEngine(pack))
        assert register_vision_tools(registry, pack) == list(VISION_TOOL_NAMES)
        for name in VISION_TOOL_NAMES:
            assert registry.get(name).tier == VISION_TIER == "auto"
        assert [spec.name for spec in vision_tool_specs()] == list(VISION_TOOL_NAMES)

    def test_registration_reads_nothing(self, tmp_path: Path) -> None:
        """A stub directory registers fine — every tool re-probes when it runs."""
        registry = ToolRegistry(PermissionEngine(tmp_path))
        assert register_vision_tools(registry, tmp_path) == list(VISION_TOOL_NAMES)

    def test_the_settings_flag_demotes_the_headless_pair_to_ask(self, pack: Path) -> None:
        """ASSUMPTION-6a's escape hatch is DATA: a line in
        ``.canon/agent/settings.json``, re-read per call, no restart and no
        code change."""
        registry = vision_registry(pack)
        engine = registry.permissions
        capture = registry.get("capture_frames")
        assert engine.classify(capture, {"level_id": "l1"}, actor="u", conversation="c").outcome == "allow"

        path = pack / AGENT_SETTINGS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"tool_tiers": {"capture_frames": "ask", "run_trajectory": "ask"}}),
            encoding="utf-8",
        )
        assert registry_tier(pack, "capture_frames") == "ask"
        for name in ("capture_frames", "run_trajectory"):
            decision = engine.classify(registry.get(name), {"level_id": "l1"}, actor="u", conversation="c")
            assert decision.outcome == "ask", name
        # view_asset is a plain read and is NOT demotable by this flag.
        assert engine.classify(registry.get("view_asset"), {"target": "player"},
                               actor="u", conversation="c").outcome == "allow"

    def test_the_registry_file_is_still_read_as_the_secondary_source(self, pack: Path) -> None:
        """A flag set in ``.canon/registry.json`` before the settings file
        existed keeps working; the settings file wins when both name a tool."""
        path = pack / ".canon" / "registry.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"agent": {"tool_tiers": {"capture_frames": "ask"}}}), encoding="utf-8")
        assert registry_tier(pack, "capture_frames") == "ask"

        settings = pack / AGENT_SETTINGS_FILE
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps({"tool_tiers": {"capture_frames": "auto"}}), encoding="utf-8")
        assert registry_tier(pack, "capture_frames") == "auto"

    def test_the_demote_survives_the_first_registry_write(self, pack: Path) -> None:
        """The reason the flag does not live in ``.canon/registry.json``: a
        generated pack has none, so the first write verb synthesizes one over
        whatever was there. A safety setting may never revert in silence."""
        from canon.registry_ops import ensure_registry

        settings = pack / AGENT_SETTINGS_FILE
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps({"tool_tiers": {"capture_frames": "ask"}}), encoding="utf-8")
        assert not (pack / ".canon" / "registry.json").exists()
        assert registry_tier(pack, "capture_frames") == "ask"

        ensure_registry(pack, actor="user")  # what `db define` / `registry set` do first
        assert (pack / ".canon" / "registry.json").is_file()
        assert registry_tier(pack, "capture_frames") == "ask"

    def test_a_malformed_settings_file_keeps_the_registered_tier(self, pack: Path) -> None:
        path = pack / AGENT_SETTINGS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        registry = vision_registry(pack)
        assert registry_tier(pack, "capture_frames") is None
        decision = registry.permissions.classify(
            registry.get("capture_frames"), {"level_id": "l1"}, actor="u", conversation="c"
        )
        assert decision.outcome == "allow", "fail closed to the REGISTERED tier, never to a guessed one"


# ---------------------------------------------------------------------------
# The headless pair against the real harness
# ---------------------------------------------------------------------------


class TestHeadlessRuns:
    """The only class that spawns the REAL harness, so the only one that needs
    pygame — the optional ``play`` extra, not a dev dependency. Without it the
    harness exits with its install hint and ``run_harness`` reports
    ``harness_failed``, which would read as a harness bug (and the missing-level
    test would pass for the wrong reason). Skip instead, the way
    ``tests/test_anim_frames.py`` already does."""

    @pytest.fixture(autouse=True)
    def _needs_pygame(self) -> None:
        pytest.importorskip("pygame", reason="the real harness needs the optional [play] extra")

    def test_capture_frames_attaches_images_and_writes_nothing(self, pack: Path) -> None:
        registry = vision_registry(pack)
        before = fingerprint(pack)
        result = registry.get("capture_frames").run(
            {"level_id": "l1", "ticks": 60, "every": 20, "script": {"hold": "right", "jump_every": 45}}
        )
        assert fingerprint(pack) == before, "an auto-tier vision tool must not touch the pack"

        summary = summary_of(result)
        images = [block for block in result if block["type"] == "image"]
        assert summary["tool"] == "capture_frames" and summary["level_id"] == "l1"
        assert summary["frames_captured"] == 3 and summary["frames_attached"] == 3
        assert summary["wrote_to_pack"] is False
        assert len(images) == 3
        for block in images:
            assert block["source"]["type"] == "base64" and block["source"]["media_type"] == "image/png"
            assert len(block["source"]["data"]) > 100
        refs = summary["attachments"]
        assert [ref["tick"] for ref in refs] == [0, 20, 40]
        assert all(ref["sha256"].startswith("sha256:") and ref["bytes"] > 0 for ref in refs)
        assert all(ref["path"] is None for ref in refs), "a frame is not a pack file and must not claim to be"
        # No capture directory survives anywhere near the pack.
        assert not list(pack.rglob("frame_*.png"))

    def test_run_trajectory_summarizes_and_never_returns_the_raw_file(self, pack: Path) -> None:
        registry = vision_registry(pack)
        before = fingerprint(pack)
        raw = registry.get("run_trajectory").run(
            {"level_id": "l1", "inputs": {"hold": "right", "jump_every": 45, "ticks": 90}}
        )
        assert fingerprint(pack) == before
        report = json.loads(raw)
        assert report["frames"] == 90 and report["malformed_lines"] == 0
        assert report["moved"] is True
        assert report["x_range"][1] > report["x_range"][0]
        assert report["start"]["x"] < report["end"]["x"], "holding right must move right"
        assert "P:" not in raw, "the per-tick file never reaches the model"
        assert "cannot see how the game LOOKS" in report["note"], (
            "the position-only limit is stated where the model reads it"
        )

    def test_a_missing_level_is_a_named_failure(self, pack: Path) -> None:
        registry = vision_registry(pack)
        with pytest.raises(HeadlessError) as raised:
            registry.get("capture_frames").run({"level_id": "no_such_level", "ticks": 20})
        body = json.loads(str(raised.value))
        assert body["error"] == "harness_failed" and body["tool"] == "capture_frames"
        assert body["returncode"] != 0 and body["stderr"]


class TestProcessContract:
    def test_exit_zero_with_no_frames_is_a_named_error_not_a_silent_pass(self, monkeypatch, pack: Path) -> None:
        stub_harness(monkeypatch, _STUB_SILENT)
        registry = vision_registry(pack)
        with pytest.raises(HeadlessError) as raised:
            registry.get("capture_frames").run({"level_id": "l1", "ticks": 20})
        body = json.loads(str(raised.value))
        assert body["error"] == "harness_no_frames" and body["level_id"] == "l1"

    def test_exit_zero_with_no_trajectory_is_a_named_error(self, monkeypatch, pack: Path) -> None:
        stub_harness(monkeypatch, _STUB_SILENT)
        registry = vision_registry(pack)
        with pytest.raises(HeadlessError) as raised:
            registry.get("run_trajectory").run({"level_id": "l1", "inputs": {"ticks": 20}})
        assert json.loads(str(raised.value))["error"] == "harness_no_trajectory"

    def test_the_child_env_is_scrubbed_of_inherited_plat_hooks(self, monkeypatch, pack: Path) -> None:
        monkeypatch.setenv("PLAT_PLAIN", "1")
        monkeypatch.setenv("PLAT_ANIM", "enemy:leaked")
        monkeypatch.setenv("PLAT_HOLD", "left")
        monkeypatch.setenv("PLAT_SOMETHING_NEW", "1")  # a hook invented after cradle's literal list
        seen = stub_harness(monkeypatch, _STUB_OK)
        registry = vision_registry(pack)
        registry.get("capture_frames").run(
            {"level_id": "l1", "ticks": 60, "every": 20, "script": {"hold": "right"}}
        )
        env = seen[0]
        plat = {k: v for k, v in env.items() if k.startswith("PLAT_")}
        assert plat == {
            "PLAT_CAPTURE_TICKS": "60",
            "PLAT_CAPTURE_EVERY": "20",
            "PLAT_HOLD": "right",
            "PLAT_CAPTURE": plat.get("PLAT_CAPTURE", ""),
        }, "only the PLAT_* this call set may reach the child"
        assert "PLAT_ANIM" not in env and "PLAT_PLAIN" not in env and "PLAT_SOMETHING_NEW" not in env
        assert env["SDL_VIDEODRIVER"] == "dummy", "windowless is what makes this auto-tier"
        assert env.get("PATH") == os.environ.get("PATH"), "same trust as ▶ Play — the rest is inherited"

    def test_scrubbed_env_is_a_prefix_rule(self, monkeypatch) -> None:
        monkeypatch.setenv("PLAT_ANYTHING_AT_ALL", "x")
        env = scrubbed_env({"PLAT_CAPTURE": "/tmp/x"})
        assert "PLAT_ANYTHING_AT_ALL" not in env and env["PLAT_CAPTURE"] == "/tmp/x"

    def test_the_launch_is_engine_resolved(self, pack: Path) -> None:
        """§3.0-I: today no pack carries a pygame engines entry, so the
        built-in harness answers; an entry with a headless launch block wins,
        which is what W2.0's promotion turns into config."""
        assert harness_launch(pack, "l1") == [sys.executable, "-m", HARNESS_MODULE, str(pack), "l1"]
        path = pack / ".canon" / "registry.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({
                "schema": "canon-registry/v1",
                "pack_type": "platformer",
                "engines": [{
                    "id": "pygame",
                    "launch": {"headless": {"cmd": "{python}", "args": ["-m", "other.harness", "{pack}", "{level}"]}},
                }],
            }),
            encoding="utf-8",
        )
        assert harness_launch(pack, "l7") == [sys.executable, "-m", "other.harness", str(pack), "l7"]

    def test_the_tick_budget_is_bounded(self, monkeypatch, pack: Path) -> None:
        """A FIXED tick budget is what makes these auto-tier: the schema
        refuses an over-budget ask, and the body clamps besides (a direct
        call must not be able to spend more than the schema advertises)."""
        seen = stub_harness(monkeypatch, _STUB_OK)
        registry = vision_registry(pack)
        with pytest.raises(ToolInputError, match="ticks"):
            registry.get("capture_frames").run({"level_id": "l1", "ticks": MAX_TICKS * 10})
        registry.get("capture_frames").run({"level_id": "l1"})
        assert seen[0]["PLAT_CAPTURE_TICKS"] == str(DEFAULT_TICKS)

        from canon.agent.tools_vision import capture_frames as body

        body(pack, {"level_id": "l1", "ticks": MAX_TICKS * 10})
        assert seen[1]["PLAT_CAPTURE_TICKS"] == str(MAX_TICKS)

    def test_frames_are_subsampled_and_the_summary_says_so(self, monkeypatch, pack: Path) -> None:
        many = (
            "import os,pathlib;"
            "d=os.environ['PLAT_CAPTURE'];"
            f"png=bytes.fromhex('{_PNG.hex()}');"
            "[pathlib.Path(d,'frame_%04d.png'%i).write_bytes(png) for i in range(0,300,10)]"
        )
        stub_harness(monkeypatch, many)
        registry = vision_registry(pack)
        summary = summary_of(registry.get("capture_frames").run({"level_id": "l1"}))
        assert summary["frames_captured"] == 30 and summary["frames_attached"] == MAX_FRAMES
        ticks = [ref["tick"] for ref in summary["attachments"]]
        assert ticks[0] == 0 and ticks[-1] == 290 and ticks == sorted(ticks)

    def test_an_unknown_hold_or_action_is_a_named_input_error(self, pack: Path) -> None:
        registry = vision_registry(pack)
        with pytest.raises(ToolInputError, match="hold"):
            registry.get("capture_frames").run({"level_id": "l1", "script": {"hold": "sideways"}})


class TestTrajectorySummary:
    def test_malformed_lines_are_counted_never_fatal(self) -> None:
        report = summarize_trajectory(["garbage", "0|P:1:2:0:0|", "1|P:x:y:z:w|", "2|P:3:2:1:1|goblin:9:9:1"])
        assert report["frames"] == 4 and report["malformed_lines"] == 2
        assert report["start"]["x"] == 1.0 and report["end"]["x"] == 3.0
        assert report["coins"] == 1 and report["enemies_alerted"] == ["goblin"]

    def test_a_still_player_reports_moved_false(self) -> None:
        report = summarize_trajectory(["0|P:1:2:0:0|", "1|P:1.1:2:0:0|"])
        assert report["moved"] is False


# ---------------------------------------------------------------------------
# view_asset
# ---------------------------------------------------------------------------


class TestViewAsset:
    def test_manifest_backed_art_attaches_bytes_and_metadata(self, pack: Path) -> None:
        registry = vision_registry(pack)
        stage = stage_of(pack)
        before = fingerprint(pack)

        sheet = summary_of(registry.get("view_asset").run({"target": f"tilesheet:{stage}"}))
        assert sheet["images"] == 1 and sheet["paths"] == [f"tileset/{stage}/tilesheet.png"]
        assert sheet["metadata"]["artifact_id"] and sheet["metadata"]["manifest"]

        backdrop = registry.get("view_asset").run({"target": f"backdrop:{stage}"})
        bands = summary_of(backdrop)
        assert bands["images"] == len(bands["paths"]) >= 1
        assert [block["type"] for block in backdrop] == ["text", *["image"] * bands["images"]]
        assert fingerprint(pack) == before

    def test_more_paths_than_the_cap_attach_the_first_and_say_so(self, pack: Path) -> None:
        """A wide ``backdrop`` truncates at MAX_FRAMES — but never in silence:
        ``images_available`` beside ``images`` plus a note naming what was
        attached and how to fetch the rest (``capture_frames``' pair, in
        ``view_asset``'s vocabulary)."""
        stage = stage_of(pack)
        manifest_path = pack / "backdrop" / stage / "manifest.json"
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        bands = []
        for index in range(MAX_FRAMES + 3):
            rel = f"backdrop/{stage}/band_{index}.png"
            (pack / rel).parent.mkdir(parents=True, exist_ok=True)
            (pack / rel).write_bytes(_PNG)
            bands.append(rel)
        document["band_paths"] = bands
        manifest_path.write_text(json.dumps(document), encoding="utf-8")

        result = vision_registry(pack).get("view_asset").run({"target": f"backdrop:{stage}"})
        summary = summary_of(result)
        assert summary["images_available"] == MAX_FRAMES + 3
        assert summary["images"] == MAX_FRAMES == len([b for b in result if b["type"] == "image"])
        assert str(MAX_FRAMES) in summary["note"] and bands[0] in summary["note"]
        assert bands[-1] not in summary["note"], "the note lists what was ATTACHED"

    def test_a_pack_relative_path_is_the_escape_hatch_and_is_guarded(self, pack: Path) -> None:
        registry = vision_registry(pack)
        stage = stage_of(pack)
        rel = f"review/{stage}/l1_skinned.png"
        summary = summary_of(registry.get("view_asset").run({"target": rel}))
        assert summary["kind"] == "path" and summary["images"] == 1
        assert summary["attachments"][0]["path"] == rel
        for escape in ("../../etc/hosts", "/etc/hosts", ".canon/objects/x.png"):
            with pytest.raises(ValueError):
                registry.get("view_asset").run({"target": escape})

    def test_a_row_with_no_art_yet_reports_it_and_names_the_verb(self, pack: Path) -> None:
        """A $0 pack's sprite phase can legitimately produce nothing; that is a
        state to REPORT (doctrine 4), not an error and not a silent empty."""
        registry = vision_registry(pack)
        enemy_id = sorted(p.stem for p in (pack / "enemy").glob("*.json"))[0]
        result = registry.get("view_asset").run({"target": f"enemy:{enemy_id}"})
        summary = summary_of(result)
        assert summary["kind"] == "enemy" and summary["id"] == enemy_id
        assert summary["images"] == 0 and summary["attachments"] == []
        assert "generate_asset" in summary["note"]
        assert summary["metadata"]["artifact_id"] == f"enemy:{enemy_id}"

    def test_a_row_claiming_a_path_that_is_not_on_disk_is_drift_and_errors(self, pack: Path) -> None:
        registry = vision_registry(pack)
        enemy_id = sorted(p.stem for p in (pack / "enemy").glob("*.json"))[0]
        row_path = pack / "enemy" / f"{enemy_id}.json"
        row = json.loads(row_path.read_text(encoding="utf-8"))
        row["sprite_path"] = f"sprite/enemy/{enemy_id}/base.png"
        row_path.write_text(json.dumps(row), encoding="utf-8")
        with pytest.raises(HeadlessError) as raised:
            registry.get("view_asset").run({"target": f"enemy:{enemy_id}"})
        body = json.loads(str(raised.value))
        assert body["error"] == "asset_file_missing" and body["path"].endswith("base.png")

    def test_an_unknown_target_names_the_known_shapes(self, pack: Path) -> None:
        registry = vision_registry(pack)
        with pytest.raises(ToolInputError) as raised:
            registry.get("view_asset").run({"target": "spaceship:x"})
        assert "enemy:<id>" in str(raised.value) and "backdrop:<id>" in str(raised.value)

    def test_a_non_image_file_is_refused_by_media_type(self, pack: Path) -> None:
        registry = vision_registry(pack)
        with pytest.raises(ToolInputError, match="attachable image"):
            registry.get("view_asset").run({"target": f"tileset/{stage_of(pack)}/manifest.json"})


# ---------------------------------------------------------------------------
# How images reach the model — and how they do NOT reach the transcript
# ---------------------------------------------------------------------------


class TestAttachmentsInTheTranscript:
    def test_a_block_list_result_rides_back_as_blocks(self, pack: Path) -> None:
        """The loop's only widening: a canonical text+image block list passes
        through as CONTENT; every other shape is JSON exactly as before."""
        registry = vision_registry(pack)
        stage = stage_of(pack)
        blocks = registry.get("view_asset").run({"target": f"tileset/{stage}/tilesheet.png"})
        assert _render_result(blocks) is blocks
        assert _render_result({"a": 1}) == '{"a": 1}'
        assert _render_result(["a", "b"]) == '["a", "b"]'
        assert _render_result("plain") == "plain"

    def test_the_transcript_stores_references_not_bytes_and_replay_never_resends(self, pack: Path) -> None:
        registry = vision_registry(pack)
        stage = stage_of(pack)
        blocks = registry.get("view_asset").run({"target": f"tilesheet:{stage}"})
        summary = summary_of(blocks)
        message = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": blocks}]}

        record = record_for(message)
        stored = json.dumps(record, ensure_ascii=False)
        assert record["type"] == "tool_result"
        assert "base64" not in stored and ATTACHMENT_PLACEHOLDER in stored
        # The reference survives: path + sha256 are still readable.
        kept = record["content"][0]["content"]
        assert json.loads(kept[0]["text"])["attachments"][0]["sha256"] == summary["attachments"][0]["sha256"]
        assert json.loads(kept[0]["text"])["attachments"][0]["path"] == summary["paths"][0]
        assert [block["type"] for block in kept] == ["text", "text"]

        store = ConversationStore(pack)
        conversation = store.create("fake", None, None)
        store.append(conversation, record)
        replayed = store.messages(conversation)[-1]
        assert "base64" not in json.dumps(replayed), "a picture is never re-sent on replay (§3.4)"

    def test_dereference_is_identity_when_there_is_nothing_to_dereference(self) -> None:
        content = [{"type": "tool_result", "tool_use_id": "t", "content": "plain text"}]
        assert dereference_attachments(content) is content
        assert dereference_attachments("a string") == "a string"

    def test_attachment_refs_reads_refs_off_a_result_and_tolerates_anything_else(self, pack: Path) -> None:
        registry = vision_registry(pack)
        stage = stage_of(pack)
        blocks = registry.get("view_asset").run({"target": f"backdrop:{stage}"})
        refs = attachment_refs(blocks)
        assert refs and all(ref["sha256"].startswith("sha256:") for ref in refs)
        assert attachment_refs("a string") == [] and attachment_refs([]) == []
        assert attachment_refs([{"type": "text", "text": "not json"}]) == []
