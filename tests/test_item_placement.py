"""Arc-2 Chunk 2 — the whole-map ITEM placement pass: check_item_placements
(fail-closed base-moveset collectibility, snap/drop repairs, rarity caps),
the box overlay + beatability re-check, the items level step, and the $0
end-to-end (trails + boxes + reward-anchor items)."""

from __future__ import annotations

import json
import random
from pathlib import Path

import numpy as np

from canon.backends.testing import FakeLLMBackend
from canon.bible.models import Bible
from canon.config import CanonConfig
from canon.llm.client import LLMClient
from canon.packs.platformer import PlatformerPrompts, compose_pipeline
from canon.packs.platformer.movement import DEFAULT_MOVEMENT
from canon.packs.platformer.run_slice import make_fake_responder
from canon.packs.platformer.tiles import DEFAULT_TILES
from canon.packs.platformer.validate import check_item_placements
from canon.pipeline.runner import PipelineContext, run_pipeline

W, H = 24, 12
FLOOR_ROW = H - 2  # anchors stand at H-3? no: floor tiles at H-2, anchors H-3


def _flat_grid():
    grid = np.zeros((H, W), dtype=np.int8)
    grid[H - 2 :, :] = 1  # floor slab, anchors stand at row H-3
    return grid


ANCHOR_Y = H - 3
ITEM_DEFS = {
    "coin": {"kind": "coin", "rarity": "common"},
    "heart": {"kind": "heal", "rarity": "uncommon"},
    "ward": {"kind": "shield", "rarity": "rare"},
}
SPAWN = (2, ANCHOR_Y)


def _check(placements, grid=None):
    return check_item_placements(
        grid if grid is not None else _flat_grid(),
        placements, SPAWN, ITEM_DEFS, DEFAULT_MOVEMENT, tiles=DEFAULT_TILES,
    )


class TestCheckItemPlacements:
    def test_ground_and_jump_envelope_accepted(self) -> None:
        accepted, problems, repairs = _check([
            {"item_id": "coin", "x": 6, "y": ANCHOR_Y, "source": "trail"},
            {"item_id": "coin", "x": 8, "y": ANCHOR_Y - 3, "source": "trail"},
        ])
        assert not problems and not repairs
        assert [(p["x"], p["y"]) for p in accepted] == [
            (6, ANCHOR_Y), (8, ANCHOR_Y - 3),
        ]

    def test_unreachable_height_snaps_then_drops_fail_closed(self) -> None:
        # jump_height is 3, snap radius 2: an item 5 above the anchor row
        # SNAPS down into the jump envelope (repair, not a drop)...
        accepted, problems, repairs = _check([
            {"item_id": "coin", "x": 6, "y": ANCHOR_Y - 5, "source": "trail"},
        ])
        assert not problems
        assert len(accepted) == 1
        assert accepted[0]["y"] >= ANCHOR_Y - 3
        assert any("snapped" in r for r in repairs)
        # ...while an item beyond snap reach of the envelope is DROPPED.
        accepted, problems, repairs = _check([
            {"item_id": "coin", "x": 6, "y": ANCHOR_Y - 7, "source": "trail"},
        ])
        assert not problems
        assert accepted == []
        assert any("dropped" in r for r in repairs)

    def test_occupied_cell_snaps(self) -> None:
        accepted, problems, repairs = _check([
            {"item_id": "coin", "x": 6, "y": H - 2, "source": "trail"},
        ])
        assert not problems
        assert len(accepted) == 1
        assert accepted[0]["y"] != H - 2
        assert any("snapped" in r for r in repairs)

    def test_box_needs_bump_foothold(self) -> None:
        accepted, _, repairs = _check([
            {"item_id": "ward", "x": 6, "y": ANCHOR_Y - 2, "source": "box"},
        ])
        assert [(p["x"], p["y"]) for p in accepted] == [(6, ANCHOR_Y - 2)]
        # Directly on the anchor row there is no room to stand beneath —
        # box falls outside the bump envelope and gets snapped/dropped.
        accepted, _, repairs = _check([
            {"item_id": "ward", "x": 6, "y": ANCHOR_Y, "source": "box"},
        ])
        assert not accepted or accepted[0]["y"] != ANCHOR_Y

    def test_rarity_caps_drop_extras(self) -> None:
        accepted, problems, repairs = _check([
            {"item_id": "ward", "x": 5, "y": ANCHOR_Y, "source": "trail"},
            {"item_id": "ward", "x": 9, "y": ANCHOR_Y, "source": "trail"},
        ])
        assert not problems
        assert len(accepted) == 1
        assert any("cap" in r for r in repairs)

    def test_unknown_id_is_llm_feedback(self) -> None:
        accepted, problems, _ = _check([
            {"item_id": "nope", "x": 5, "y": ANCHOR_Y, "source": "trail"},
        ])
        assert not accepted
        assert problems and "unknown item" in problems[0]


def _run_slice(output_dir: Path) -> PipelineContext:
    ctx = PipelineContext(
        bible=Bible.empty(seed="emberfall_001"),
        config=CanonConfig(seed="emberfall_001", output_dir=output_dir),
        rng=random.Random("emberfall_001"),
        llm=LLMClient(FakeLLMBackend(make_fake_responder())),
        prompts=PlatformerPrompts(),
    )
    run_pipeline(compose_pipeline(), ctx)
    return ctx


class TestItemsEndToEnd:
    def test_every_level_ships_a_validated_items_layer(
        self, tmp_path: Path
    ) -> None:
        out = tmp_path / "run"
        ctx = _run_slice(out)
        sources = set()
        for level in ctx.bible.levels.values():
            path = out / f"level/{level.stage_id}/{level.level_id}/items.json"
            assert path.exists()
            records = json.loads(path.read_text())
            assert records, f"{level.level_id} shipped no items"
            assert [
                (p.pos[0], p.pos[1]) for p in level.items
            ] == [(r["x"], r["y"]) for r in records]
            assert level.items_hash
            parents = level.step_parents["items"]
            assert any(p.startswith("item:") for p in parents)
            assert any(p.endswith("/entities") for p in parents)
            for record in records:
                sources.add(record["source"])
                assert record["item_id"] in ctx.bible.items
        # The $0 path exercises every source kind.
        assert sources == {"trail", "box", "reward"}

    def test_box_cells_are_open_air_on_the_collision_grid(
        self, tmp_path: Path
    ) -> None:
        """Boxes are an ITEMS-LAYER overlay: collision.npz must stay
        box-free (consumers overlay at load; the parent step is never
        rewritten)."""
        out = tmp_path / "run"
        ctx = _run_slice(out)
        box_id = DEFAULT_TILES.by_name["box"].id
        for level in ctx.bible.levels.values():
            with np.load(
                out / f"level/{level.stage_id}/{level.level_id}/collision.npz"
            ) as data:
                grid = data["collision"]
            assert not (grid == box_id).any()
            for p in level.items:
                if p.overrides.get("source") == "box":
                    assert grid[p.pos[1], p.pos[0]] == 0
