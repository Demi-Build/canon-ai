"""Contract tests for SpellPoolPhase.

Tests verify the file shapes and observable behavior of SpellPoolPhase
against the mazeworld classes/spell_pools.json contract.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from canon import (
    Bible,
    CanonConfig,
    GenerationStats,
    PipelineContext,
)
from canon.bible.models import ClassArchetype, Spell
from canon.pipeline.phases.spell_pool import SpellPoolPhase
from canon.pipeline.runner import Phase

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _make_spell(
    name: str = "Fireball",
    spell_type: str = "damage_single",
    element: str = "fire",
    stat: str = "INT",
) -> Spell:
    return Spell(
        name=name,
        spell_type=spell_type,
        element=element,
        stat=stat,
        num_dice=2,
        die_sides=6,
        stamina_cost=4,
        description=f"{name} spell",
    )


def _make_ctx(tmp_path: Path) -> PipelineContext:
    bible = Bible.empty(seed="spell_test")
    config = CanonConfig(seed="spell_test", output_dir=str(tmp_path))
    return PipelineContext(
        bible=bible,
        config=config,
        rng=random.Random(42),
        stats=GenerationStats(),
    )


def _add_archetype(
    ctx: PipelineContext,
    arch_id: str,
    archetype: str,
    spells: list[Spell] | None = None,
) -> None:
    ctx.bible.class_archetypes[arch_id] = ClassArchetype(
        archetype_id=arch_id,
        archetype=archetype,
        name=archetype.capitalize(),
        spell_pool=spells or [],
    )


# ---------------------------------------------------------------------------
# Phase protocol
# ---------------------------------------------------------------------------


class TestPhaseProtocol:
    def test_satisfies_phase_protocol(self):
        assert isinstance(SpellPoolPhase(), Phase)

    def test_phase_name_is_spell_pool(self):
        assert SpellPoolPhase.name == "spell_pool"


# ---------------------------------------------------------------------------
# Happy path — spell pool output
# ---------------------------------------------------------------------------


class TestSpellPoolOutput:
    def test_spell_pools_json_written(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        _add_archetype(ctx, "mage_id", "mage", [_make_spell()])
        SpellPoolPhase().run(ctx)
        assert (tmp_path / "classes" / "spell_pools.json").exists()

    def test_damage_spell_goes_in_damage_pool(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        _add_archetype(ctx, "mage_id", "mage", [
            _make_spell("Fireball", spell_type="damage_single"),
        ])
        SpellPoolPhase().run(ctx)
        data = _load(tmp_path / "classes" / "spell_pools.json")
        assert "mage_damage" in data
        assert len(data["mage_damage"]) == 1

    def test_damage_multi_goes_in_damage_pool(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        _add_archetype(ctx, "mage_id", "mage", [
            _make_spell("Chain Lightning", spell_type="damage_multi"),
        ])
        SpellPoolPhase().run(ctx)
        data = _load(tmp_path / "classes" / "spell_pools.json")
        assert "mage_damage" in data

    def test_heal_spell_goes_in_heal_pool(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        _add_archetype(ctx, "healer_id", "healer", [
            _make_spell("Holy Light", spell_type="heal"),
        ])
        SpellPoolPhase().run(ctx)
        data = _load(tmp_path / "classes" / "spell_pools.json")
        assert "healer_heal" in data
        assert len(data["healer_heal"]) == 1

    def test_buff_spell_goes_in_buff_pool(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        _add_archetype(ctx, "mage_id", "mage", [
            _make_spell("Haste", spell_type="buff_stat"),
        ])
        SpellPoolPhase().run(ctx)
        data = _load(tmp_path / "classes" / "spell_pools.json")
        assert "mage_buff" in data

    def test_multiple_spell_types_multiple_pools(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        _add_archetype(ctx, "mage_id", "mage", [
            _make_spell("Fireball", spell_type="damage_single"),
            _make_spell("Heal", spell_type="heal"),
        ])
        SpellPoolPhase().run(ctx)
        data = _load(tmp_path / "classes" / "spell_pools.json")
        assert "mage_damage" in data
        assert "mage_heal" in data

    def test_multiple_archetypes_separate_keys(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        _add_archetype(ctx, "mage_id", "mage", [
            _make_spell("Fireball", spell_type="damage_single"),
        ])
        _add_archetype(ctx, "healer_id", "healer", [
            _make_spell("Holy Light", spell_type="heal"),
        ])
        SpellPoolPhase().run(ctx)
        data = _load(tmp_path / "classes" / "spell_pools.json")
        assert "mage_damage" in data
        assert "healer_heal" in data

    def test_pool_entries_are_spell_dicts(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        _add_archetype(ctx, "mage_id", "mage", [
            _make_spell("Fireball", spell_type="damage_single", element="fire"),
        ])
        SpellPoolPhase().run(ctx)
        data = _load(tmp_path / "classes" / "spell_pools.json")
        entry = data["mage_damage"][0]
        # Must be a dict with Spell fields
        assert "name" in entry
        assert "spell_type" in entry
        assert "element" in entry
        assert "stat" in entry

    def test_pool_entry_has_correct_name(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        _add_archetype(ctx, "mage_id", "mage", [
            _make_spell("Cosmic Rift", spell_type="damage_single"),
        ])
        SpellPoolPhase().run(ctx)
        data = _load(tmp_path / "classes" / "spell_pools.json")
        assert data["mage_damage"][0]["name"] == "Cosmic Rift"

    def test_archetype_label_from_archetype_field(self, tmp_path: Path):
        """Pool key uses arch.archetype field, not arch_id."""
        ctx = _make_ctx(tmp_path)
        ctx.bible.class_archetypes["some_long_id"] = ClassArchetype(
            archetype_id="some_long_id",
            archetype="warrior",  # label used for pool key
            name="Warrior",
            spell_pool=[_make_spell(spell_type="damage_single")],
        )
        SpellPoolPhase().run(ctx)
        data = _load(tmp_path / "classes" / "spell_pools.json")
        assert "warrior_damage" in data

    def test_archetype_label_falls_back_to_arch_id(self, tmp_path: Path):
        """If arch.archetype is empty, arch_id is used as label."""
        ctx = _make_ctx(tmp_path)
        ctx.bible.class_archetypes["ranger_id"] = ClassArchetype(
            archetype_id="ranger_id",
            archetype="",  # empty — fall back to arch_id
            name="Ranger",
            spell_pool=[_make_spell(spell_type="damage_single")],
        )
        SpellPoolPhase().run(ctx)
        data = _load(tmp_path / "classes" / "spell_pools.json")
        assert "ranger_id_damage" in data


# ---------------------------------------------------------------------------
# Empty class_archetypes
# ---------------------------------------------------------------------------


class TestEmptyArchetypes:
    def test_empty_archetypes_writes_empty_file(self, tmp_path: Path):
        """With no archetypes, spell_pools.json is written as an empty dict."""
        ctx = _make_ctx(tmp_path)
        # No archetypes added
        SpellPoolPhase().run(ctx)
        assert (tmp_path / "classes" / "spell_pools.json").exists()
        data = _load(tmp_path / "classes" / "spell_pools.json")
        assert data == {}

    def test_empty_archetypes_phase_name_in_phases_run(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        SpellPoolPhase().run(ctx)
        assert "spell_pool" in ctx.bible.metadata.phases_run


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


class TestSpellPoolMetadata:
    def test_spell_pool_in_phases_run(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        _add_archetype(ctx, "mage_id", "mage", [_make_spell()])
        SpellPoolPhase().run(ctx)
        assert "spell_pool" in ctx.bible.metadata.phases_run

    def test_spell_pool_appended_not_reset(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        ctx.bible.metadata.phases_run.append("classes")
        _add_archetype(ctx, "mage_id", "mage", [_make_spell()])
        SpellPoolPhase().run(ctx)
        assert "classes" in ctx.bible.metadata.phases_run
        assert "spell_pool" in ctx.bible.metadata.phases_run


# ---------------------------------------------------------------------------
# Output path override
# ---------------------------------------------------------------------------


class TestOutputPathOverride:
    def test_custom_spell_pools_path(self, tmp_path: Path):
        """output_paths['spell_pools'] overrides the default path."""
        ctx = _make_ctx(tmp_path)
        ctx.config.output_paths["spell_pools"] = "custom/pools.json"
        _add_archetype(ctx, "mage_id", "mage", [_make_spell()])
        SpellPoolPhase().run(ctx)
        assert (tmp_path / "custom" / "pools.json").exists()

    def test_default_path_is_classes_slash_spell_pools_json(self, tmp_path: Path):
        """Default output path is classes/spell_pools.json."""
        ctx = _make_ctx(tmp_path)
        # No output_paths override — should use default
        _add_archetype(ctx, "mage_id", "mage", [_make_spell()])
        SpellPoolPhase().run(ctx)
        assert (tmp_path / "classes" / "spell_pools.json").exists()

    def test_creates_parent_directories(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        ctx.config.output_paths["spell_pools"] = "deep/nested/pools.json"
        _add_archetype(ctx, "mage_id", "mage", [_make_spell()])
        SpellPoolPhase().run(ctx)
        assert (tmp_path / "deep" / "nested" / "pools.json").exists()


# ---------------------------------------------------------------------------
# Spell classification
# ---------------------------------------------------------------------------


class TestSpellClassification:
    """_classify_spell maps spell_type strings to pool bucket names."""

    def test_damage_single_maps_to_damage(self):
        assert SpellPoolPhase._classify_spell("damage_single") == "damage"

    def test_damage_multi_maps_to_damage(self):
        assert SpellPoolPhase._classify_spell("damage_multi") == "damage"

    def test_damage_prefix_maps_to_damage(self):
        assert SpellPoolPhase._classify_spell("damage_aoe") == "damage"

    def test_heal_maps_to_heal(self):
        assert SpellPoolPhase._classify_spell("heal") == "heal"

    def test_buff_stat_maps_to_buff(self):
        assert SpellPoolPhase._classify_spell("buff_stat") == "buff"

    def test_buff_sustain_maps_to_buff(self):
        assert SpellPoolPhase._classify_spell("buff_sustain") == "buff"

    def test_unknown_maps_to_other(self):
        assert SpellPoolPhase._classify_spell("unknown_type") == "other"
