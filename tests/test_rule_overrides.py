"""Per-level rule/movement overrides (combat/level-picks arc, K1):
the closed vocabulary, fail-closed validation, stage-plan flag flow,
persistence, and effective-movement generation."""

from __future__ import annotations

import json
import random
from pathlib import Path

import numpy as np

from canon import CanonConfig, FakeLLMBackend, LLMClient, run_pipeline
from canon.bible.models import Bible
from canon.packs.platformer import PlatformerPrompts, compose_pipeline
from canon.packs.platformer.movement import DEFAULT_MOVEMENT, PlayerMovementSpec
from canon.packs.platformer.rules import (
    load_override_vocabulary,
    validate_overrides,
)
from canon.packs.platformer.run_slice import make_fake_responder
from canon.pipeline.runner import PipelineContext

SEED = "emberfall_001"


class TestVocabulary:
    def test_pack_vocabulary_loads(self) -> None:
        vocab = load_override_vocabulary()
        assert "gravity" in vocab and vocab["gravity"]["target"] == "movement"
        assert "platform_drop_through" in vocab
        # The main()-read rule must never join the vocabulary (its
        # consumer read sits OUTSIDE run_level — see rule_overrides.json
        # _doc); generation-gating rules stay out too.
        for forbidden in (
            "checkpoint_enemy_reset", "water_containment", "enemy_water_policy",
        ):
            assert forbidden not in vocab

    def test_fail_closed_matrix(self) -> None:
        rules_o, move_o, problems = validate_overrides(
            {
                "gravity": 20.0,  # ok -> movement
                "platform_drop_through": False,  # ok -> rules
                "chase_speed_mult": 99.0,  # out of band -> dropped
                "jump_height": 3.7,  # coerced to int 3, in band -> ok
                "not_a_rule": 1,  # unknown -> dropped
                "break_delay_s": "soon",  # wrong type -> dropped
            }
        )
        assert move_o == {"gravity": 20.0, "jump_height": 3}
        assert rules_o == {"platform_drop_through": False}
        assert len(problems) == 3
        assert any("not in the vocabulary" in p for p in problems)
        assert any("outside the allowed band" in p for p in problems)
        assert any("is not a float" in p for p in problems)

    def test_bool_is_strict(self) -> None:
        _r, _m, problems = validate_overrides({"platform_drop_through": 1})
        assert problems  # an int is not a bool (fail-closed typing)

    def test_int_coercion_keeps_types_honest(self) -> None:
        _r, move_o, _p = validate_overrides({"jump_height": 4.0})
        assert isinstance(move_o["jump_height"], int)
        spec = PlayerMovementSpec.model_validate(
            {**DEFAULT_MOVEMENT.model_dump(), **move_o}
        )
        assert spec.jump_height == 4


def _run_world(output_dir: Path) -> PipelineContext:
    ctx = PipelineContext(
        bible=Bible.empty(seed=SEED),
        config=CanonConfig(seed=SEED, output_dir=output_dir),
        rng=random.Random(SEED),
        llm=LLMClient(FakeLLMBackend(make_fake_responder())),
        prompts=PlatformerPrompts(),
    )
    run_pipeline(compose_pipeline(num_stages=3, num_enemies=7), ctx)
    return ctx


class TestOverridesEndToEnd:
    def test_finale_override_persists_and_validates(self, tmp_path) -> None:
        ctx = _run_world(tmp_path / "run")
        finale = ctx.bible.levels["l9"]
        assert finale.movement_overrides == {"gravity": 20.0}
        assert finale.rules_overrides == {}
        assert finale.layout_fallback is False  # validated under low-g
        # Every other main level carries no overrides.
        for lid, level in ctx.bible.levels.items():
            if lid in ("l9",) or level.parent_level == "l9":
                continue
            assert not level.movement_overrides, lid
            assert not level.rules_overrides, lid
        # level.json ships both fields for the consumers.
        d = json.loads(
            (tmp_path / "run/level/frostspire_peaks/l9/level.json").read_text()
        )
        assert d["movement_overrides"] == {"gravity": 20.0}
        # Rooms inherit the parent's overrides (one fiction, one physics).
        for rid in finale.secret_rooms:
            room = ctx.bible.levels[rid]
            assert room.movement_overrides == finale.movement_overrides

    def test_low_gravity_changes_validation(self) -> None:
        """A gap crossable ONLY under low gravity (same jump HEIGHT —
        the design invariant — but longer airtime carries farther):
        reachable with the override's effective spec, unreachable under
        the default — proof the sim validates each level under its own
        physics. Gap 8 discriminates (default clears ≤6, g=15 clears
        ≤10 — probed)."""
        from canon.packs.platformer.tiles import DEFAULT_TILES
        from canon.packs.platformer.validate import reachable_cells

        grid = np.zeros((14, 40), dtype=np.int8)
        grid[12, :] = 1
        grid[13, :] = 1
        grid[12, 15:23] = 0
        grid[13, 15:23] = 0  # an 8-wide bottomless gap
        spawn = (2, 11)
        landing = (23, 11)
        low_g = PlayerMovementSpec.model_validate(
            {**DEFAULT_MOVEMENT.model_dump(), "gravity": 15.0}
        )
        assert landing not in reachable_cells(
            grid, spawn, DEFAULT_MOVEMENT, DEFAULT_TILES
        )
        assert landing in reachable_cells(grid, spawn, low_g, DEFAULT_TILES)
