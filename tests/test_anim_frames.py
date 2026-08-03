"""Animation frame inspection and hand-correction.

Two things are being pinned here. First, that the inspector MEASURES the
shipped pixels rather than trusting the atlas rects — the whole reason it
exists is that generation's intent and the art on disk disagreed, silently,
for a whole cycle. Second, that authored offsets are strictly ADDITIVE: a pack
without them must render exactly as it did before the field existed, in both
engines.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from canon.adapters.platformer_read import read_animation  # noqa: E402
from canon.adapters.platformer_write import apply_frames_edit  # noqa: E402

PIL = pytest.importorskip("PIL.Image", reason="needs Pillow")


def _strip(path: Path, n: int, side: int, boxes: list[tuple[int, int, int, int]]) -> None:
    """An n-frame strip whose i-th frame has one opaque rect at `boxes[i]`."""
    from PIL import Image

    img = Image.new("RGBA", (side * n, side), (0, 0, 0, 0))
    for i, (x, y, w, h) in enumerate(boxes):
        block = Image.new("RGBA", (w, h), (255, 0, 0, 255))
        img.paste(block, (i * side + x, y))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def _actor(tmp_path: Path, states: dict[str, dict]) -> Path:
    """A pack with one animated player. `states[name] = {boxes, loop?}`."""
    pack = tmp_path / "pack"
    sprite = pack / "sprite" / "player"
    sprite.mkdir(parents=True)
    meta: dict = {}
    atlas_states: dict = {}
    for name, spec in states.items():
        boxes = spec["boxes"]
        side = spec.get("side", 32)
        rel = f"sprite/player/{name}.png"
        _strip(pack / rel, len(boxes), side, boxes)
        meta[name] = {
            "path": rel,
            "frames": len(boxes),
            "frame_width": side,
            "frame_height": side,
            "duration_ms": 120,
            "loop": spec.get("loop", "loop"),
        }
        atlas_states[name] = {
            "loop": spec.get("loop", "loop"),
            "frames": [
                {"x": i * side, "y": 0, "w": w, "h": h, "ox": x, "oy": y}
                for i, (x, y, w, h) in enumerate(boxes)
            ],
        }
    (sprite / "frames.json").write_text(json.dumps(meta, indent=2))
    (sprite / "atlas.json").write_text(
        json.dumps(
            {"path": "sprite/player/atlas.png", "frame_size": [32, 32], "states": atlas_states},
            indent=2,
        )
    )
    return pack


class TestInspect:
    def test_it_measures_the_shipped_pixels(self, tmp_path: Path):
        pack = _actor(tmp_path, {"idle": {"boxes": [(8, 10, 12, 20), (9, 11, 12, 19)]}})
        state = read_animation(pack, "player")["states"][0]
        assert state["frames"] == 2
        assert state["boxes"][0]["box"] == {"x": 8, "y": 10, "w": 12, "h": 20}
        assert state["widest"] == 12
        assert state["tallest"] == 20

    def test_more_than_one_flush_state_reads_as_independently_sized(self, tmp_path: Path):
        """With ONE shared square only the largest pose may touch the edge, so
        two flush states is the signature of states squared separately — the
        defect that cost the player two thirds of its pixels mid-jump."""
        pack = _actor(
            tmp_path,
            {
                "idle": {"boxes": [(0, 0, 32, 32)]},
                "fall": {"boxes": [(0, 0, 32, 32)]},
                "walk": {"boxes": [(8, 8, 10, 10)]},
            },
        )
        anim = read_animation(pack, "player")
        assert sorted(anim["flush_states"]) == ["fall", "idle"]
        assert anim["independently_sized"] is True

    def test_one_flush_state_is_normal(self, tmp_path: Path):
        pack = _actor(
            tmp_path,
            {"idle": {"boxes": [(0, 0, 32, 32)]}, "walk": {"boxes": [(8, 8, 10, 10)]}},
        )
        assert read_animation(pack, "player")["independently_sized"] is False

    def test_it_reports_feet_wandering_within_a_state(self, tmp_path: Path):
        """Feet moving between frames of ONE state reads as the actor bobbing
        while it should be planted — invisible frame by frame."""
        pack = _actor(tmp_path, {"idle": {"boxes": [(8, 4, 10, 20), (8, 0, 10, 20)]}})
        # foot gaps: 32-24=8 and 32-20=12
        assert read_animation(pack, "player")["states"][0]["foot_wander"] == 4

    def test_an_empty_frame_reports_no_box_rather_than_a_zero_one(self, tmp_path: Path):
        pack = _actor(tmp_path, {"idle": {"boxes": [(8, 8, 10, 10), (0, 0, 0, 0)]}})
        boxes = read_animation(pack, "player")["states"][0]["boxes"]
        assert boxes[1]["box"] is None

    def test_an_unknown_target_is_refused(self, tmp_path: Path):
        with pytest.raises(ValueError, match="unknown animation target"):
            read_animation(_actor(tmp_path, {"idle": {"boxes": [(0, 0, 4, 4)]}}), "goblin")

    def test_it_is_a_pure_read(self, tmp_path: Path):
        pack = _actor(tmp_path, {"idle": {"boxes": [(8, 8, 10, 10)]}})
        before = json.loads((pack / "sprite/player/frames.json").read_text())
        read_animation(pack, "player")
        assert json.loads((pack / "sprite/player/frames.json").read_text()) == before
        assert not (pack / ".canon" / "journal.jsonl").exists()


class TestEdit:
    def _pack(self, tmp_path: Path) -> Path:
        return _actor(tmp_path, {"fall": {"boxes": [(8, 0, 10, 32), (8, 0, 10, 32)]}})

    def test_offsets_round_trip(self, tmp_path: Path):
        pack = self._pack(tmp_path)
        result = apply_frames_edit(pack, "player", "fall", {"offsets": [[0, 3], [0, 2]]})
        assert result["frames_edit"] == "updated"
        assert read_animation(pack, "player")["states"][0]["offsets"] == [[0, 3], [0, 2]]

    def test_it_writes_BOTH_manifests(self, tmp_path: Path):
        """atlas.json wins where present and frames.json is the fallback, so
        writing one would leave the other rendering the uncorrected version."""
        pack = self._pack(tmp_path)
        apply_frames_edit(pack, "player", "fall", {"offsets": [[1, 2], [1, 2]]})
        frames = json.loads((pack / "sprite/player/frames.json").read_text())
        atlas = json.loads((pack / "sprite/player/atlas.json").read_text())
        assert frames["fall"]["offsets"] == [[1, 2], [1, 2]]
        assert atlas["states"]["fall"]["offsets"] == [[1, 2], [1, 2]]

    def test_null_clears_them(self, tmp_path: Path):
        pack = self._pack(tmp_path)
        apply_frames_edit(pack, "player", "fall", {"offsets": [[0, 3], [0, 2]]})
        apply_frames_edit(pack, "player", "fall", {"offsets": None})
        assert read_animation(pack, "player")["states"][0]["offsets"] is None
        atlas = json.loads((pack / "sprite/player/atlas.json").read_text())
        assert "offsets" not in atlas["states"]["fall"]

    def test_a_desynced_offset_list_is_refused(self, tmp_path: Path):
        """Refused at the write rather than silently shifting the wrong frames
        at play time — the loaders' fallback is 'no offsets', which would hide
        the mistake."""
        with pytest.raises(ValueError, match="one .dx, dy. per frame"):
            apply_frames_edit(self._pack(tmp_path), "player", "fall", {"offsets": [[0, 1]]})

    def test_geometry_is_not_editable(self, tmp_path: Path):
        with pytest.raises(ValueError, match="not the generated frame geometry"):
            apply_frames_edit(self._pack(tmp_path), "player", "fall", {"frame_width": 64})

    def test_durations_and_loop(self, tmp_path: Path):
        pack = self._pack(tmp_path)
        apply_frames_edit(
            pack, "player", "fall", {"durations_ms": [90, 200], "loop": "once"}
        )
        state = read_animation(pack, "player")["states"][0]
        assert state["durations_ms"] == [90, 200]
        assert state["loop"] == "once"
        with pytest.raises(ValueError, match="loop must be one of"):
            apply_frames_edit(pack, "player", "fall", {"loop": "boomerang"})

    def test_an_unknown_state_names_what_exists(self, tmp_path: Path):
        with pytest.raises(ValueError, match="no animation state 'sprint'"):
            apply_frames_edit(self._pack(tmp_path), "player", "sprint", {"loop": "once"})

    def test_a_no_op_edit_does_not_journal(self, tmp_path: Path):
        pack = self._pack(tmp_path)
        apply_frames_edit(pack, "player", "fall", {"loop": "once"})
        journal = pack / ".canon" / "journal.jsonl"
        before = len(journal.read_text().splitlines())
        result = apply_frames_edit(pack, "player", "fall", {"loop": "once"})
        assert result["frames_edit"] == "no_change"
        assert len(journal.read_text().splitlines()) == before

    def test_it_journals_against_the_BARE_target(self, tmp_path: Path):
        """`asset animate` journals the bare target; a different id here would
        fork the lineage tree the History tab reads."""
        pack = self._pack(tmp_path)
        apply_frames_edit(pack, "player", "fall", {"offsets": [[0, 1], [0, 1]]}, actor="me")
        events = [
            json.loads(line)
            for line in (pack / ".canon" / "journal.jsonl").read_text().splitlines()
        ]
        ev = events[-1]
        assert ev["artifact_id"] == "player"
        assert (ev["op"], ev["source"], ev["actor"]) == ("edit", "user", "me")
        assert ev["detail"] == {"kind": "frames_edit", "state": "fall", "fields": ["offsets"]}


class TestOffsetsAreAdditive:
    """The load-bearing guarantee: a pack with no offsets must render exactly
    as it did before the field existed."""

    def test_the_offset_contract_matches_durations_ms(self):
        """Absent or desynced ⇒ all-zero, never a partial shift."""
        from examples.platformer_play import _anim_offsets

        assert _anim_offsets({}, 3) == [(0, 0)] * 3
        assert _anim_offsets({"offsets": [[1, 2]]}, 3) == [(0, 0)] * 3, "desynced"
        assert _anim_offsets({"offsets": "nope"}, 2) == [(0, 0)] * 2
        assert _anim_offsets({"offsets": [[1, 2], [3, 4]]}, 2) == [(1, 2), (3, 4)]
        # A garbled entry degrades to zero rather than raising mid-render.
        assert _anim_offsets({"offsets": [[1, 2], "x"]}, 2) == [(1, 2), (0, 0)]

    def test_no_offsets_renders_identically_to_explicit_zeros(self):
        """Pixel-level: the un-authored path and the zero-offset path produce
        the same surface, so shipping this field changes nothing on its own."""
        pygame = pytest.importorskip("pygame")
        from examples.platformer_play import _atlas_frames

        pygame.init()
        pygame.display.set_mode((8, 8))
        try:
            sheet = pygame.Surface((32, 32), pygame.SRCALPHA)
            sheet.fill((255, 0, 0, 255), pygame.Rect(0, 0, 10, 10))
            rects = [{"x": 0, "y": 0, "w": 10, "h": 10, "ox": 4, "oy": 6}]

            plain = _atlas_frames(sheet, rects, (32, 32), (32, 32))
            zeros = _atlas_frames(sheet, rects, (32, 32), (32, 32), [(0, 0)])
            moved = _atlas_frames(sheet, rects, (32, 32), (32, 32), [(3, -2)])

            def raw(surface):
                return pygame.image.tostring(surface, "RGBA")

            assert raw(plain[0]) == raw(zeros[0])
            assert raw(plain[0]) != raw(moved[0]), "an offset must actually move pixels"
        finally:
            pygame.display.quit()
            pygame.quit()
