"""Engine runtime sync: keeping a pack's Godot code current with the template.

The runtime is COPIED into a pack at generation time and the export phase only
runs during a full pipeline run, so a pack keeps whatever engine code existed
the day it was made. That is not a theoretical problem: the AtlasTexture-margin
render fix reached the template and no shipped pack, leaving every existing pack
mis-drawing trimmed frames in normal gameplay.

These tests pin the two properties that make the fix safe to run on a pack
somebody has been working in: it refreshes what canon wrote, and it refuses to
silently overwrite what a human changed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from examples.platformer_pack.godot_export import (  # noqa: E402
    STAMP_REL,
    engine_status,
    engine_sync,
    template_files,
    template_manifest,
)


def _pack(tmp_path: Path, *, stale: bool = True, stamped: bool = False) -> Path:
    """A pack with a Godot runtime — optionally an OLD one, optionally stamped."""
    pack = tmp_path / "pack"
    (pack / "godot").mkdir(parents=True)
    files = template_files()
    for rel, data in files.items():
        target = pack / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"# an older build\n" if stale and rel.endswith(".gd") else data)
    if stamped:
        # Stamp what we ACTUALLY wrote, so the older .gd reads as stale rather
        # than hand-edited.
        written = {rel: (pack / rel).read_bytes() for rel in files}
        (pack / STAMP_REL).write_text(json.dumps(template_manifest(written), indent=2))
    return pack


class TestStatus:
    def test_a_current_pack_reports_current(self, tmp_path: Path):
        pack = _pack(tmp_path, stale=False, stamped=True)
        status = engine_status(pack)
        assert status["current"] is True
        assert status["behind"] == []
        assert {f["state"] for f in status["files"]} == {"current"}

    def test_a_stale_pack_names_what_is_behind(self, tmp_path: Path):
        status = engine_status(_pack(tmp_path, stamped=True))
        assert status["current"] is False
        assert status["behind"] == ["godot/main.gd"]
        assert status["modified"] == [], "matching its stamp means stale, not edited"

    def test_an_unstamped_pack_is_syncable_but_says_so(self, tmp_path: Path):
        """Packs predating the stamp can't distinguish an old build from a hand
        edit — report `unstamped` rather than guessing either way."""
        status = engine_status(_pack(tmp_path, stamped=False))
        assert status["stamped"] is False
        assert [f["state"] for f in status["files"] if f["path"].endswith(".gd")] == [
            "unstamped"
        ]

    def test_a_missing_file_is_missing_not_modified(self, tmp_path: Path):
        pack = _pack(tmp_path, stale=False, stamped=True)
        (pack / "godot" / "main.tscn").unlink()
        states = {f["path"]: f["state"] for f in engine_status(pack)["files"]}
        assert states["godot/main.tscn"] == "missing"


class TestSync:
    def test_sync_brings_the_current_runtime_in(self, tmp_path: Path):
        pack = _pack(tmp_path, stamped=True)
        result = engine_sync(pack, actor="test")
        assert result["engine"] == "updated"
        assert result["written"] == ["godot/main.gd"]
        # The file now matches the template byte for byte, and the pack reads
        # as current afterwards.
        assert (pack / "godot/main.gd").read_bytes() == template_files()["godot/main.gd"]
        assert engine_status(pack)["current"] is True

    def test_dry_run_writes_nothing(self, tmp_path: Path):
        pack = _pack(tmp_path, stamped=True)
        before = (pack / "godot/main.gd").read_bytes()
        result = engine_sync(pack, dry_run=True)
        assert result["engine"] == "dry_run"
        assert result["would_write"] == ["godot/main.gd"]
        assert (pack / "godot/main.gd").read_bytes() == before
        assert not (pack / ".canon" / "journal.jsonl").exists()

    def test_syncing_twice_is_a_no_op_and_does_not_journal(self, tmp_path: Path):
        """Same hygiene rule apply_level_edit and world map-edit follow — a
        run that changes nothing must not leave a trail saying it did."""
        pack = _pack(tmp_path, stamped=True)
        engine_sync(pack, actor="test")
        journal = pack / ".canon" / "journal.jsonl"
        before = len(journal.read_text().splitlines())

        result = engine_sync(pack, actor="test")
        assert result["engine"] == "no_change"
        assert result["written"] == []
        assert len(journal.read_text().splitlines()) == before

    def test_sync_journals_one_engine_event(self, tmp_path: Path):
        pack = _pack(tmp_path, stamped=True)
        engine_sync(pack, actor="cradle:user")
        events = [
            json.loads(line)
            for line in (pack / ".canon" / "journal.jsonl").read_text().splitlines()
        ]
        engine_events = [e for e in events if e["artifact_id"] == "engine:godot"]
        assert len(engine_events) == 1
        ev = engine_events[0]
        assert (ev["op"], ev["source"], ev["actor"]) == ("import", "code", "cradle:user")
        assert ev["detail"]["kind"] == "engine_sync"
        assert ev["detail"]["written"] == ["godot/main.gd"]

    def test_a_pack_with_no_godot_runtime_is_refused(self, tmp_path: Path):
        bare = tmp_path / "bare"
        bare.mkdir()
        with pytest.raises(FileNotFoundError, match="no project.godot"):
            engine_sync(bare)


class TestHandEditsAreNotClobbered:
    """The stamp is the only evidence of what canon put there, so a file that
    differs from its OWN stamp is a human's work. Fail closed and name it."""

    def _edited(self, tmp_path: Path) -> Path:
        pack = _pack(tmp_path, stale=False, stamped=True)
        path = pack / "godot/main.gd"
        path.write_bytes(path.read_bytes() + b"\n# my own tweak\n")
        return pack

    def test_a_hand_edit_reads_as_modified(self, tmp_path: Path):
        assert engine_status(self._edited(tmp_path))["modified"] == ["godot/main.gd"]

    def test_sync_refuses_it_by_name_and_leaves_it_alone(self, tmp_path: Path):
        pack = self._edited(tmp_path)
        result = engine_sync(pack, actor="test")
        assert result["refused"] == ["godot/main.gd"]
        assert result["written"] == []
        assert b"# my own tweak" in (pack / "godot/main.gd").read_bytes()

    def test_force_overwrites_it(self, tmp_path: Path):
        pack = self._edited(tmp_path)
        result = engine_sync(pack, force=True, actor="test")
        assert result["written"] == ["godot/main.gd"]
        assert b"# my own tweak" not in (pack / "godot/main.gd").read_bytes()

    def test_dry_run_reports_the_refusal_too(self, tmp_path: Path):
        result = engine_sync(self._edited(tmp_path), dry_run=True)
        assert result["refused"] == ["godot/main.gd"]
        assert result["would_write"] == []


class TestExportStamps:
    def test_the_export_phase_stamps_what_it_wrote(self, tmp_path: Path):
        """A freshly generated pack must read as current — otherwise cradle
        would nag about staleness the moment a world is created."""
        from examples.platformer_pack.godot_export import GodotExportPhase

        pack = tmp_path / "out"
        pack.mkdir()

        class _Adapter:
            def write_binary(self, rel: str, data: bytes) -> str:
                target = pack / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                return "sha256:stub"

            def resolve_path(self, rel: str) -> Path:
                return pack / rel

        class _Ctx:
            adapter = _Adapter()
            bible = type("B", (), {"metadata": None})()

        GodotExportPhase().run(_Ctx())
        assert (pack / STAMP_REL).is_file()
        status = engine_status(pack)
        assert status["stamped"] is True
        assert status["current"] is True
