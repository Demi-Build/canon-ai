"""Arc-2 Chunk 1 — the WORLD ITEM POOL: ItemDefinition + ItemGeneratorPhase
+ --num-items. Kinds are a closed mechanical set; every number is a schema
band; the LLM authors only name/flavor; slots 0/1 are guaranteed coin+heal."""

from __future__ import annotations

import json
import random
from pathlib import Path

from canon.backends.testing import FakeLLMBackend
from canon.bible.artifacts import ArtifactStatus
from canon.bible.models import Bible
from canon.bible.platformer import ItemDefinition
from canon.config import CanonConfig
from canon.llm.client import LLMClient
from canon.packs.platformer import PlatformerPrompts, compose_pipeline
from canon.packs.platformer.phases import SCHEMAS_DIR
from canon.packs.platformer.run_slice import make_fake_responder
from canon.pipeline.orchestrator import mark_stale
from canon.pipeline.rng import derive_rng
from canon.pipeline.runner import PipelineContext, run_pipeline
from canon.skeleton.core import roll_skeleton
from canon.skeleton.loader import load_skeleton_spec

KINDS = {"coin", "heal", "shield", "double_jump", "run_boost"}


def _run_slice(output_dir: Path, num_items: int = 5) -> PipelineContext:
    ctx = PipelineContext(
        bible=Bible.empty(seed="emberfall_001"),
        config=CanonConfig(seed="emberfall_001", output_dir=output_dir),
        rng=random.Random("emberfall_001"),
        llm=LLMClient(FakeLLMBackend(make_fake_responder())),
        prompts=PlatformerPrompts(),
    )
    run_pipeline(compose_pipeline(num_items=num_items), ctx)
    return ctx


class TestItemSkeleton:
    def test_rolls_are_deterministic_and_kind_consistent(self) -> None:
        spec = load_skeleton_spec(SCHEMAS_DIR / "item.json")
        a = roll_skeleton(spec, derive_rng("seed", "plat:items", 3))
        b = roll_skeleton(spec, derive_rng("seed", "plat:items", 3))
        assert a == b
        assert a["kind"] in KINDS
        if a["kind"] in ("double_jump", "run_boost"):
            assert 20 <= a["duration_s"] <= 40
        else:
            assert a["duration_s"] == 0

    def test_kind_coverage_across_a_seed_sweep(self) -> None:
        spec = load_skeleton_spec(SCHEMAS_DIR / "item.json")
        kinds = {
            roll_skeleton(spec, derive_rng("sweep", i))["kind"]
            for i in range(60)
        }
        assert kinds == KINDS  # every kind reachable by the weighted roll


class TestItemGeneratorPhase:
    def test_pool_shape_and_guarantees(self, tmp_path: Path) -> None:
        ctx = _run_slice(tmp_path / "run")
        items = list(ctx.bible.items.values())
        assert len(items) == 5
        # Slot guarantees: every world can count on a coin and a heal.
        assert items[0].kind == "coin" and items[1].kind == "heal"
        for item in items:
            assert isinstance(item, ItemDefinition)
            assert item.kind in KINDS
            assert item.rarity in {"common", "uncommon", "rare"}
            assert item.artifact_id == f"item:{item.item_id}"
            assert item.parents == ["world"]
            assert item.provenance_hash, "definition must be stamped"
            assert item.stats["placeholder_color"].startswith("#")
            on_disk = json.loads(
                (tmp_path / "run" / f"item/{item.item_id}.json").read_text()
            )
            assert on_disk["kind"] == item.kind
        # Params carry only the kind's live knobs.
        assert items[0].params == {"coin_value": 1}
        assert items[1].params == {"heal_amount": 1}
        for item in items:
            if item.kind in ("double_jump", "run_boost"):
                assert 20 <= item.params["duration_s"] <= 40

    def test_names_unique_and_num_items_respected(self, tmp_path: Path) -> None:
        ctx = _run_slice(tmp_path / "run", num_items=8)
        items = list(ctx.bible.items.values())
        assert len(items) == 8
        names = [i.name for i in items]
        assert len(set(names)) == len(names), names

    def test_manifest_ships_the_item_pool(self, tmp_path: Path) -> None:
        out = tmp_path / "run"
        ctx = _run_slice(out)
        manifest = json.loads((out / "manifest.json").read_text())
        assert manifest["items"] == sorted(ctx.bible.items)

    def test_item_regen_target_is_known(self, tmp_path: Path) -> None:
        ctx = _run_slice(tmp_path / "run")
        item_id = next(iter(ctx.bible.items))
        plan = mark_stale(ctx.bible, [f"item:{item_id}"])
        assert f"item:{item_id}" in plan.marked
        assert (
            ctx.bible.metadata.node_status[f"item:{item_id}"]
            is ArtifactStatus.STALE
        )


class TestEstimateKnowsItems:
    def test_fresh_forecast_prices_the_item_pool(self, tmp_path: Path) -> None:
        from canon.packs.platformer.estimate import estimate_run

        ctx = PipelineContext(
            bible=Bible.empty(seed="est"),
            config=CanonConfig(seed="est", output_dir=tmp_path),
            rng=random.Random(0),
        )
        result = estimate_run(ctx, [], Bible.empty(seed="est"))
        assert "plat:items" in result["llm"]["by_task"]
        assert result["llm"]["by_task"]["plat:items"]["calls"] == 5
