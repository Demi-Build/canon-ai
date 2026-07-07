"""Tests for canon.pipeline.rng — stable seed derivation.

The load-bearing property: derived seeds are pure functions of their input
parts, immune to PYTHONHASHSEED, platform, and call order. Phase 2's
resume/staleness machinery depends on this.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from canon.pipeline.rng import derive_rng, derive_seed

REPO_SRC = str(Path(__file__).resolve().parent.parent / "src")


class TestDeriveSeed:
    def test_pure_function(self) -> None:
        assert derive_seed("s", "maze_layout", "room_0") == derive_seed(
            "s", "maze_layout", "room_0"
        )

    def test_distinct_parts_distinct_seeds(self) -> None:
        seeds = {
            derive_seed("s", "maze_layout", "room_0"),
            derive_seed("s", "maze_layout", "room_1"),
            derive_seed("s", "placement", "room_0"),
            derive_seed("other", "maze_layout", "room_0"),
        }
        assert len(seeds) == 4

    def test_known_value_pinned(self) -> None:
        """Golden value: changing the derivation silently would invalidate
        every seed users have shared. Fails loudly instead."""
        assert derive_seed("shadowspire_001", "maze_layout", "room_0") == (
            int.from_bytes(
                __import__("hashlib")
                .sha256(b"shadowspire_001:maze_layout:room_0")
                .digest()[:8],
                "big",
            )
        )

    def test_derive_rng_reproducible(self) -> None:
        a = derive_rng("s", "x").random()
        b = derive_rng("s", "x").random()
        assert a == b

    def test_stable_across_hash_randomization(self) -> None:
        """The whole point of derive_seed: identical output under different
        PYTHONHASHSEED salts. Requires subprocesses — the salt is fixed per
        process."""
        code = (
            "from canon.pipeline.rng import derive_seed; "
            "print(derive_seed('s', 'maze_layout', 'room_0'))"
        )

        def run(hashseed: str) -> str:
            result = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=30,
                env={"PYTHONHASHSEED": hashseed, "PYTHONPATH": REPO_SRC},
            )
            assert result.returncode == 0, result.stderr
            return result.stdout.strip()

        assert run("0") == run("12345")


class TestMazeLayoutDeterminism:
    def test_grids_stable_across_hash_randomization(self) -> None:
        """End-to-end regression for the hash(map_id) landmine: the same
        config seed must carve identical mazes under different hash salts."""
        code = (
            "import random\n"
            "from canon.bible.models import Bible, Map\n"
            "from canon.config import CanonConfig\n"
            "from canon.pipeline.phases.maze_layout import MazeLayoutPhase\n"
            "from canon.pipeline.runner import PipelineContext\n"
            "bible = Bible.empty(seed='t')\n"
            "for i in range(2):\n"
            "    bible.maps[f'room_{i}'] = Map(map_id=f'room_{i}', name='', "
            "description='', environment='forest', story_beat='')\n"
            "ctx = PipelineContext(bible=bible, "
            "config=CanonConfig(seed='det_test', output_dir='/tmp/unused'), "
            "rng=random.Random(0))\n"
            "class NullAdapter:\n"
            "    def write_per_map(self, template, map_id, obj): pass\n"
            "ctx.adapter = NullAdapter()\n"
            "MazeLayoutPhase(width=15, height=11).run(ctx)\n"
            "print([bible.maps[m].layout.grid for m in sorted(bible.maps)])\n"
        )

        def run(hashseed: str) -> str:
            result = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=60,
                env={"PYTHONHASHSEED": hashseed, "PYTHONPATH": REPO_SRC},
            )
            assert result.returncode == 0, result.stderr
            return result.stdout

        assert run("1") == run("99999")

    def test_grids_independent_of_map_order(self, tmp_path: Path) -> None:
        """Reordering maps must not change any map's maze — required for
        Phase 2's per-node regeneration."""
        import random

        from canon.bible.models import Bible, Map
        from canon.config import CanonConfig
        from canon.pipeline.phases.maze_layout import MazeLayoutPhase
        from canon.pipeline.runner import PipelineContext

        def build(map_ids: list[str]) -> dict[str, list]:
            bible = Bible.empty(seed="t")
            for mid in map_ids:
                bible.maps[mid] = Map(
                    map_id=mid, name="", description="",
                    environment="forest", story_beat="",
                )
            ctx = PipelineContext(
                bible=bible,
                config=CanonConfig(seed="order_test", output_dir=tmp_path),
                rng=random.Random(0),
            )
            MazeLayoutPhase(width=15, height=11).run(ctx)
            return {mid: bible.maps[mid].layout.grid for mid in map_ids}

        forward = build(["room_0", "room_1", "room_2"])
        reversed_ = build(["room_2", "room_1", "room_0"])
        assert forward == reversed_
