"""Row P0-3 — the pack registry seam (P0 paper P.3 / P.4.1 / P.4.6).

Covers the seed shapes (``EntityKind`` / ``GridKind`` / ``DialogueSpec`` /
``PackSpec`` + ``stamped()``), the two built-in seeds (the platformer's
entries are DERIVED from ``ops.DB_TYPES``; the dungeon's nine are the paper's
JSON as data, joined to the generator's DatabaseSpecs), ``resolve_pack``'s
four tiers, ``canon pack info`` on a legacy dungeon fixture and on a fresh
platformer tree, and the ``pack_type`` mirror rule on both manifest writers.
"""

from __future__ import annotations

import dataclasses
import json
import random
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from canon import Bible, CanonConfig, ClassArchetype, GenerationStats, Map, PipelineContext
from canon.packs import (
    PACKS,
    REGISTRY_SCHEMA,
    PackTypeError,
    ResolvedPack,
    pack_info,
    resolve_pack,
)
from canon.packs.dungeon.compose import MAZEWORLD_DEFAULT_COUNTS, compose_mazeworld_specs
from canon.packs.dungeon.phases import MazeworldManifestPhase
from canon.packs.platformer import ops
from canon.packs.spec import (
    CORE_PROTECTED,
    TUNING_RESERVED,
    DialogueSpec,
    EntityKind,
    GridKind,
    PackSpec,
)
from canon.pipeline.phases.manifest import ManifestPhase

REPO = Path(__file__).resolve().parents[1]
DUNGEON_FIXTURE = REPO / "tests" / "reference" / "fixtures" / "cradle_mazeworld_scifi"
CANON = [sys.executable, "-m", "canon.cli.main"]

PLATFORMER = PACKS["platformer"]
DUNGEON = PACKS["dungeon"]
DB_TYPES_ID_FIELDS = {kind: meta["id_field"] for kind, meta in ops.DB_TYPES.items()}


def _canon(*args: str) -> tuple[int, object]:
    result = subprocess.run(CANON + list(args), capture_output=True, text=True, cwd=REPO)
    stream = result.stdout if result.returncode == 0 else result.stderr
    try:
        return result.returncode, json.loads(stream)
    except json.JSONDecodeError:
        return result.returncode, stream


@pytest.fixture(scope="module")
def plat_pack(tmp_path_factory) -> Path:
    """A fresh $0 platformer tree — its manifest carries the P0-3 stamp."""
    out = tmp_path_factory.mktemp("p03_pack")
    subprocess.run(
        [
            sys.executable, "-m", "canon.packs.platformer.run_slice",
            "--backend", "fake", "--engine", "json", "--image-backend", "fake",
            "--music-backend", "none", "--sfx-backend", "none",
            "--num-stages", "1", "--num-levels", "1", "--num-enemies", "2",
            "--output-dir", str(out),
        ],
        check=True,
        capture_output=True,
        cwd=REPO,
    )
    return out


# ---------------------------------------------------------------------------
# P.3 shapes
# ---------------------------------------------------------------------------


class TestShapes:
    def test_entity_kind_fields_are_the_paper_list(self) -> None:
        names = [f.name for f in dataclasses.fields(EntityKind)]
        assert names == [
            "kind", "label", "layout", "id_field", "id_alloc", "schema",
            "llm_fields", "code_fields", "user_fields", "hidden", "decorative",
            "nesting", "containers", "protected", "routed", "renames", "refs",
            "phase_label", "per_map", "count_key", "dedup", "asset", "vocab",
            "model", "loader", "parser", "prompt_method", "prompt_kwargs",
            "builder",  # P0-6 additive seed-only: the per-row generation body
        ]
        assert EntityKind.SEED_ONLY == ("model", "loader", "parser", "prompt_method", "prompt_kwargs", "builder")

    def test_grid_kind_fields_are_the_paper_list(self) -> None:
        names = [f.name for f in dataclasses.fields(GridKind)]
        assert names == [
            "kind", "ref_field", "path_template", "file", "steps", "dense", "sparse",
            "placements", "points", "dims", "cell_vocab", "derived", "restorable", "artifact_id",
        ]

    def test_dialogue_spec_fields_are_the_paper_list(self) -> None:
        names = [f.name for f in dataclasses.fields(DialogueSpec)]
        assert names == [
            "storage", "condition_namespaces", "scene_only_namespaces", "effects", "scopes",
            "operands", "selector_axes", "scene", "engine_evaluable_seed", "tree_model",
        ]

    def test_pack_spec_fields_are_the_paper_list_plus_template_dir(self) -> None:
        names = [f.name for f in dataclasses.fields(PackSpec)]
        assert names == [
            "pack_type", "label", "description", "vocab", "entities", "grids", "dialogue",
            "capabilities", "counts", "wizard", "engines", "tuning_vocabulary", "world_fields",
            "phase_labels", "data_files", "runner", "compose", "estimator", "prompts",
            "validators", "archetypes", "schemas", "template_dir",
        ]

    def test_kinds_are_plain_strings(self) -> None:
        # Ids are data (master doctrine 8): nothing here is a Literal union.
        assert EntityKind.__dataclass_fields__["kind"].type == "str"
        assert GridKind.__dataclass_fields__["kind"].type == "str"
        assert PackSpec.__dataclass_fields__["pack_type"].type == "str"

    @pytest.mark.parametrize("pack_type", ["platformer", "dungeon"])
    def test_stamped_round_trips_through_json(self, pack_type: str) -> None:
        stamped = PACKS[pack_type].stamped()
        assert json.loads(json.dumps(stamped)) == stamped
        assert list(stamped) == [
            "pack_type", "label", "description", "vocab", "capabilities", "counts",
            "entities", "grids", *(["dialogue"] if pack_type == "dungeon" else []),
            "engines", "tuning", "world_fields", "phase_labels", "wizard",
        ]
        assert stamped["tuning"] == TUNING_RESERVED
        for entry in stamped["entities"].values():
            assert not set(entry) & set(EntityKind.SEED_ONLY)
        if "dialogue" in stamped:
            assert "tree_model" not in stamped["dialogue"]

    def test_stamped_is_a_copy(self) -> None:
        stamped = DUNGEON.stamped()
        stamped["entities"]["npc"]["llm_fields"].append("x")
        assert "x" not in DUNGEON.entities["npc"].llm_fields

    @pytest.mark.parametrize("pack_type", ["platformer", "dungeon"])
    def test_stamped_entries_omit_the_map_key(self, pack_type: str) -> None:
        # P.4.2 keys `entities` / `grids` by kind; the P.1.1 canonical entry
        # carries no inner copy — one shape for P0-10's template.version hash.
        stamped = PACKS[pack_type].stamped()
        for kind, entry in stamped["entities"].items():
            assert "kind" not in entry, kind
        for kind, entry in stamped["grids"].items():
            assert "kind" not in entry, kind
        # And the entry rebuilds the seed the way the dungeon seed is built.
        npc = DUNGEON.entities["npc"]
        assert EntityKind(kind="npc", **DUNGEON.stamped()["entities"]["npc"]).stamped() == npc.stamped()

    def test_entity_kind_requires_layout_and_id_field(self) -> None:
        # P.3.1 declares both without a default: a kind with no home on disk
        # is a construction error, not a later surprise (P0-6 `db define`).
        with pytest.raises(TypeError, match="layout"):
            EntityKind(kind="x", id_field="id")
        with pytest.raises(TypeError, match="id_field"):
            EntityKind(kind="x", layout={"mode": "per_file", "dir": "x"})
        assert EntityKind(kind="x", layout={"mode": "per_file", "dir": "x"}, id_field="id").label == ""


# ---------------------------------------------------------------------------
# The two seeds
# ---------------------------------------------------------------------------


class TestSeeds:
    def test_registry_ids_are_the_seed_ids(self) -> None:
        assert list(PACKS) == ["platformer", "dungeon"]
        for pack_type, spec in PACKS.items():
            assert spec.pack_type == pack_type == spec.wizard["id"]

    @pytest.mark.parametrize("pack_type", ["platformer", "dungeon"])
    def test_every_entity_has_label_id_field_layout(self, pack_type: str) -> None:
        for kind, entity in PACKS[pack_type].entities.items():
            assert entity.kind == kind
            assert entity.label, kind
            assert entity.id_field, kind
            assert entity.layout.get("mode") in {"per_file", "collection"}, kind
            if entity.layout["mode"] == "collection":
                assert entity.layout["format"] in {"array", "keyed_object", "array_positional"}
                assert entity.layout["path"].endswith(".json")
            else:
                assert entity.layout["dir"]

    def test_platformer_entries_are_derived_from_db_types(self) -> None:
        assert set(PLATFORMER.entities) == set(ops.DB_TYPES)
        for kind, meta in ops.DB_TYPES.items():
            entity = PLATFORMER.entities[kind]
            assert entity.layout == {"mode": "per_file", "dir": meta["dir"]}
            assert entity.id_field == meta["id_field"]
            assert entity.llm_fields == meta["llm_fields"]
            assert entity.code_fields == meta["code_fields"]
            assert entity.phase_label == meta["phase_label"]
            assert entity.nesting == ops._UPDATE_NESTING[kind]
            assert entity.containers == list(ops._DICT_CONTAINERS[kind])
            assert set(entity.protected) <= ops._PROTECTED_FIELDS - CORE_PROTECTED
            assert meta["id_field"] in entity.protected
            assert entity.schema == f"schemas/{kind}.json"
            assert (PLATFORMER.template_dir / entity.schema).is_file()

    @pytest.mark.parametrize("pack_type", ["platformer", "dungeon"])
    def test_wizard_and_engine_seed_shapes(self, pack_type: str) -> None:
        # P.4.4 wizard metadata (data only, no verb yet) and the P.4.3 worked
        # engine entry — pinned so a future edit cannot drift the key sets.
        spec = PACKS[pack_type]
        assert set(spec.wizard) == {
            "id", "label", "description", "vocab", "defaults", "ranges", "advanced",
            "engine", "dimension", "distribution", "beta", "phase_labels",
        }
        assert spec.wizard["defaults"] == spec.counts
        assert spec.wizard["label"] == spec.label and spec.wizard["vocab"] == spec.vocab
        assert spec.wizard["engine"] == [e["id"] for e in spec.engines]
        engine = spec.primary_engine()
        assert set(engine) >= {"id", "template", "launch", "live_channel", "artifacts", "exports", "primary"}
        assert engine["primary"] is True
        assert set(engine["template"]) == {"ref", "version"} and engine["template"]["version"] is None  # R7
        assert set(engine["launch"]) == {"cmd", "args", "env"}
        assert set(engine["live_channel"]) == {"kind", "protocol"}
        assert "{pack}" in engine["launch"]["args"]

    def test_platformer_declares_no_dialogue(self) -> None:
        assert PLATFORMER.capabilities == ["grid"]
        assert PLATFORMER.dialogue is None
        assert "dialogue" not in PLATFORMER.stamped()
        assert "evaluable_namespaces" not in PLATFORMER.primary_engine()
        assert PLATFORMER.primary_engine()["id"] == "godot"
        assert PLATFORMER.tuning_vocabulary == "rule_overrides.json"
        assert set(PLATFORMER.world_fields) == {"title", "unlock_rules"}
        assert PLATFORMER.grids["level"].placements["entities"]["kind"] == "enemy"
        # Row P0-10 filled the §3.0-E map: the 22 phase ids cradle's
        # CreateProgress used to hardcode are template data now, plus the
        # orchestrator's per-artifact families (`level:*` / `review`) the
        # create default emits — see `tests/test_create_flow.py`.
        assert len([k for k in PLATFORMER.phase_labels if k.startswith("plat:")]) == 22
        assert PLATFORMER.phase_labels["plat:manifest"] == "Manifest"

    def test_dungeon_has_nine_kinds_incl_music_and_sfx(self) -> None:
        assert list(DUNGEON.entities) == [
            "npc", "monster", "item", "quest", "event", "class", "room", "music", "sfx",
        ]
        assert DUNGEON.entities["music"].id_field == "track_id"
        assert DUNGEON.entities["sfx"].id_field == "sfx_id"
        assert DUNGEON.entities["class"].id_field == "archetype"
        assert DUNGEON.entities["npc"].renames == {"behavior_type": "type"}
        assert DUNGEON.entities["room"].protected == ["id", "maze_ref"]
        assert DUNGEON.capabilities == ["grid", "dialogue", "per_step_roll"]
        assert DUNGEON.counts == {"rooms": 3, **MAZEWORLD_DEFAULT_COUNTS}
        assert DUNGEON.tuning_vocabulary is None

    def test_dungeon_layouts_agree_with_the_generator(self) -> None:
        # The DatabaseSpecs the pipeline runs are the one source for paths,
        # formats and generation callables — the entries join them.
        for spec in compose_mazeworld_specs():
            entity = DUNGEON.entities[spec.entity_type]
            assert entity.layout["path"] == spec.output_path
            assert entity.layout["format"] == spec.output_format
            assert entity.per_map == spec.per_map
            assert entity.parser is spec.parser
            assert entity.prompt_method == spec.prompt_method
            assert entity.dedup == list(spec.cross_room_dedup or [])

    def test_dungeon_dialogue_and_engine_seed(self) -> None:
        dialogue = DUNGEON.dialogue
        assert dialogue.scopes == ["tree", "selector", "scene", "effects", "music"]
        assert dialogue.scene_only_namespaces == ["actor"]
        assert set(dialogue.operands) == set(dialogue.condition_namespaces) | {"actor"}
        engine = DUNGEON.primary_engine()
        assert engine["id"] == "pygame" and engine["primary"] is True
        assert engine["evaluable_namespaces"] == dialogue.engine_evaluable_seed["pygame"]
        assert engine["evaluable_namespaces"]["selector"] == {"quest": {"states": ["completed", "failed"]}}
        assert engine["evaluable_bindings"] == {
            "music": ["environment", "state", "screen"],
            "sfx": ["event", "environment"],
        }
        placements = DUNGEON.grids["room"].placements
        assert [p["kind"] for p in placements.values()] == ["npc", "event", "item"]
        assert placements["event_positions"]["grid_stamp"] == -1
        assert "story.title" in DUNGEON.world_fields
        assert DUNGEON.world_fields["story.title"]["mirrors"][1] == {
            "file": "manifest.json", "path": "story_title",
        }

    # --- row P0-5: the loader slot (P.3.1 / §8.2) + the tile registry (P.6.3)

    def test_every_dungeon_kind_seeds_a_loader_bound_to_its_own_layout(self) -> None:
        from canon.packs.dungeon.loaders import load_rows

        for kind, entity in DUNGEON.entities.items():
            assert callable(entity.loader), kind
            assert entity.loader.func is load_rows and entity.loader.keywords == {"entity": entity}
            assert "loader" not in entity.stamped()

    def test_platformer_loader_wraps_the_per_file_read(self, plat_pack: Path) -> None:
        for kind, entity in PLATFORMER.entities.items():
            assert callable(entity.loader), kind
            rows = entity.loader(plat_pack)
            assert rows.keys() == ops._load_defs(plat_pack, kind).keys()
            id_field = DB_TYPES_ID_FIELDS[kind]
            for key, model in rows.items():
                assert getattr(model, id_field) == key  # the kind's model, keyed by its id_field
            assert "loader" not in entity.stamped()
        assert len(PLATFORMER.entities["enemy"].loader(plat_pack)) == 2

    def test_dungeon_cell_vocab_names_the_shipped_tile_registry(self) -> None:
        grid = DUNGEON.grids["room"]
        registry = json.loads((DUNGEON.template_dir / grid.cell_vocab).read_text(encoding="utf-8"))
        assert [(t["id"], t["name"], t["category"], t["color_role"]) for t in registry["tiles"]] == [
            (0, "empty", "empty", "background"),
            (1, "wall", "solid", "wall"),
        ]
        assert registry["tile_px"] == 20
        assert registry["palette"]["wall_by_environment"]["ruins"] == "#645f55"
        assert registry["palette"]["wall_fallback"] in registry["palette"]["wall_by_environment"]

    def test_pack_info_placements_build_the_dock_tabs(self) -> None:
        """P.3.2: Dock tabs = the ``kind`` of every ``placements`` entry, in
        order, labelled from the entity block — cradle reads exactly this."""
        doc = pack_info(DUNGEON_FIXTURE)
        placements = doc["grids"]["room"]["placements"]
        kinds = [p["kind"] for p in placements.values()]
        assert kinds == ["npc", "event", "item"]
        assert [doc["entities"][k]["label"] for k in kinds] == ["NPCs", "Events", "Items"]
        assert [p["wire"] for p in placements.values()] == ["entities", "triggers", "items"]
        assert all(doc["entities"][k]["placeable"] for k in kinds)


# ---------------------------------------------------------------------------
# resolve_pack — the four tiers
# ---------------------------------------------------------------------------


class TestResolvePack:
    def test_tier1_registry(self, tmp_path: Path) -> None:
        (tmp_path / ".canon").mkdir()
        registry = {
            "schema": REGISTRY_SCHEMA,
            "pack_type": "dungeon",
            "template": {"id": "dungeon", "version": "sha256:x"},
        }
        (tmp_path / ".canon" / "registry.json").write_text(json.dumps(registry))
        # A conflicting manifest stamp loses: the registry is the source of truth.
        (tmp_path / "manifest.json").write_text(json.dumps({"pack_type": "platformer"}))
        (tmp_path / "level").mkdir()
        resolved = resolve_pack(tmp_path)
        assert isinstance(resolved, ResolvedPack)
        assert (resolved.source, resolved.pack_type) == ("registry", "dungeon")
        # P0-6: tier 1 answers with the EFFECTIVE spec — the seed overlaid by
        # the file; a registry carrying no blocks keeps the seed's entries.
        assert resolved.spec.pack_type == DUNGEON.pack_type
        assert list(resolved.spec.entities) == list(DUNGEON.entities)
        assert resolved.spec.compose is DUNGEON.compose
        assert resolved.registry == registry
        # pack info passes the stamped template block through (P.4.6's
        # non-null version case) and names the tier.
        doc = pack_info(tmp_path)
        assert doc["source"] == "registry"
        assert doc["template"] == registry["template"]
        assert doc["pack_type"] == "dungeon"

    def test_tier1_registry_naming_an_unregistered_seed_is_an_error(self, tmp_path: Path) -> None:
        (tmp_path / ".canon").mkdir()
        registry = {"schema": REGISTRY_SCHEMA, "pack_type": "shooter"}
        (tmp_path / ".canon" / "registry.json").write_text(json.dumps(registry))
        (tmp_path / "level").mkdir()  # a shape answer exists — and is NOT consulted
        with pytest.raises(PackTypeError, match=r"'shooter' \(registry\).*platformer"):
            resolve_pack(tmp_path)

    def test_tier1_registry_without_a_pack_type_is_a_hard_error(self, tmp_path: Path) -> None:
        # Fail-closed (doctrine 1): a present v1 registry is the source of
        # truth; a malformed one is never guessed past by the later tiers.
        (tmp_path / ".canon").mkdir()
        (tmp_path / ".canon" / "registry.json").write_text(json.dumps({"schema": REGISTRY_SCHEMA}))
        (tmp_path / "manifest.json").write_text(json.dumps({"pack_type": "platformer"}))
        (tmp_path / "level").mkdir()
        with pytest.raises(PackTypeError, match="without a pack_type"):
            resolve_pack(tmp_path)
        (tmp_path / ".canon" / "registry.json").write_text(json.dumps({"schema": REGISTRY_SCHEMA, "pack_type": ""}))
        with pytest.raises(PackTypeError, match="without a pack_type"):
            resolve_pack(tmp_path)

    def test_tier1_ignores_a_registry_with_another_schema(self, tmp_path: Path) -> None:
        (tmp_path / ".canon").mkdir()
        (tmp_path / ".canon" / "registry.json").write_text(json.dumps({"schema": "other/v9", "pack_type": "dungeon"}))
        (tmp_path / "manifest.json").write_text(json.dumps({"pack_type": "platformer"}))
        assert resolve_pack(tmp_path).source == "manifest"

    def test_tier2_manifest_stamp(self, tmp_path: Path) -> None:
        (tmp_path / "manifest.json").write_text(json.dumps({"seed": 1, "pack_type": "dungeon"}))
        resolved = resolve_pack(tmp_path)
        assert (resolved.source, resolved.pack_type, resolved.registry) == ("manifest", "dungeon", None)
        assert resolved.spec is DUNGEON

    def test_tier3_shape_platformer(self, tmp_path: Path) -> None:
        (tmp_path / "manifest.json").write_text(json.dumps({"game": "platformer_slice"}))
        (tmp_path / "level").mkdir()
        resolved = resolve_pack(tmp_path)
        assert (resolved.source, resolved.pack_type) == ("shape", "platformer")

    def test_tier3_shape_dungeon(self, tmp_path: Path) -> None:
        (tmp_path / "rooms").mkdir()
        (tmp_path / "world_bible.json").write_text("{}")
        resolved = resolve_pack(tmp_path)
        assert (resolved.source, resolved.pack_type) == ("shape", "dungeon")
        assert resolve_pack(DUNGEON_FIXTURE).source == "shape"

    def test_tier3_needs_both_dungeon_markers(self, tmp_path: Path) -> None:
        (tmp_path / "rooms").mkdir()
        with pytest.raises(PackTypeError, match="unknown pack type"):
            resolve_pack(tmp_path)

    def test_tier4_error_names_the_dir(self, tmp_path: Path) -> None:
        with pytest.raises(PackTypeError, match=str(tmp_path)):
            resolve_pack(tmp_path)

    def test_unknown_stamp_is_an_error_naming_the_seeds(self, tmp_path: Path) -> None:
        (tmp_path / "manifest.json").write_text(json.dumps({"pack_type": "shooter"}))
        with pytest.raises(PackTypeError, match="'shooter'.*platformer"):
            resolve_pack(tmp_path)

    def test_a_broken_manifest_falls_through_to_shape(self, tmp_path: Path) -> None:
        (tmp_path / "manifest.json").write_text("{not json")
        (tmp_path / "level").mkdir()
        assert resolve_pack(tmp_path).source == "shape"

    def test_resolve_writes_nothing(self, tmp_path: Path) -> None:
        (tmp_path / "level").mkdir()
        before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
        resolve_pack(tmp_path)
        doc = pack_info(tmp_path)
        assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before
        # P.4.6 / step 6: a per_file kind whose dir is absent counts 0.
        assert doc["entities"]["enemy"]["count"] == 0
        assert doc["entities"]["item"]["count"] == 0


# ---------------------------------------------------------------------------
# canon pack info
# ---------------------------------------------------------------------------


class TestPackInfo:
    def test_legacy_dungeon_fixture(self) -> None:
        code, doc = _canon("pack", "info", str(DUNGEON_FIXTURE))
        assert code == 0, doc
        assert doc["canon_version"] == "0.1"
        assert doc["pack_type"] == "dungeon"
        assert doc["source"] == "shape"
        assert doc["label"] == "Dungeon crawler"
        assert doc["capabilities"] == ["grid", "dialogue", "per_step_roll"]
        # W2.1.1: the wizard does not speak a structure the manifest lacks.
        assert doc["vocab"] == ["rooms", "encounters", "loot"]
        entities = doc["entities"]
        assert list(entities) == ["npc", "monster", "item", "quest", "event", "class", "room", "music", "sfx"]
        npcs = json.loads((DUNGEON_FIXTURE / "npcs" / "npcs.json").read_text())
        items = json.loads((DUNGEON_FIXTURE / "items" / "items.json").read_text())
        classes = json.loads((DUNGEON_FIXTURE / "classes" / "classes.json").read_text())
        assert entities["npc"]["count"] == len(npcs)
        assert entities["item"]["count"] == len(items)  # keyed object → keys
        assert entities["class"]["count"] == len(classes)  # positional array
        # legacy tree: no rooms/rooms.json → the count falls back to the GridKind's
        # per-room files (decided 2026-09-01, P0 paper P.9)
        assert entities["room"]["count"] == len(list((DUNGEON_FIXTURE / "rooms").glob("room_*/maze.json")))
        assert entities["music"]["count"] == 0 and entities["sfx"]["count"] == 0
        assert entities["npc"] == {
            "label": "NPCs", "id_field": "id",
            "layout": {"mode": "collection", "path": "npcs/npcs.json", "format": "array"},
            "count": len(npcs), "placeable": True, "schema_source": None,
        }
        assert {k: v["placeable"] for k, v in entities.items() if v["placeable"]} == {
            "npc": True, "event": True, "item": True,
        }
        assert doc["grids"]["room"]["placements"] == {
            "npc_positions": {"kind": "npc", "wire": "entities"},
            "event_positions": {"kind": "event", "wire": "triggers"},
            "item_placements": {"kind": "item", "wire": "items"},
        }
        assert doc["grids"]["room"]["points"] == ["player_start", "door_position"]
        assert doc["grids"]["room"]["dims"]["default"] == [40, 30]
        assert doc["dialogue"]["condition_namespaces"] == [
            "has_item", "quest", "time", "player", "flag", "segment", "room", "scene", "event",
        ]
        assert doc["dialogue"]["scene_only_namespaces"] == ["actor"]
        assert doc["dialogue"]["effects"] == ["gives_item", "takes_item", "gives_quest", "advance_quest", "set_flag"]
        assert doc["dialogue"]["scopes"] == ["tree", "selector", "scene", "effects", "music"]
        assert "operands" in doc["dialogue"] and "engine_evaluable_seed" not in doc["dialogue"]
        assert doc["engine_evaluable_namespaces"] == {
            "tree": {}, "selector": {"quest": {"states": ["completed", "failed"]}},
            "scene": {}, "effects": {}, "music": {},
        }
        assert doc["engine_evaluable_bindings"] == {
            "music": ["environment", "state", "screen"], "sfx": ["event", "environment"],
        }
        assert doc["engines"] == [{"id": "pygame", "primary": True}]
        assert doc["template"] == {"id": "dungeon", "version": None}

    def test_fresh_platformer_tree_resolves_by_manifest(self, plat_pack: Path) -> None:
        code, doc = _canon("pack", "info", str(plat_pack))
        assert code == 0, doc
        assert (doc["pack_type"], doc["source"]) == ("platformer", "manifest")
        assert doc["label"] == "Platformer"
        assert doc["capabilities"] == ["grid"]
        assert "dialogue" not in doc
        assert "engine_evaluable_namespaces" not in doc
        assert "engine_evaluable_bindings" not in doc
        assert doc["engines"] == [{"id": "godot", "primary": True}]
        assert doc["template"] == {"id": "platformer", "version": None}
        entities = doc["entities"]
        assert list(entities) == ["enemy", "item"]
        assert entities["enemy"]["count"] == len(list((plat_pack / "enemy").glob("*.json"))) == 2
        assert entities["item"]["count"] == len(list((plat_pack / "item").glob("*.json")))
        assert entities["enemy"]["layout"] == {"mode": "per_file", "dir": "enemy"}
        assert entities["enemy"]["id_field"] == "enemy_id"
        assert entities["enemy"]["placeable"] and entities["item"]["placeable"]
        assert entities["enemy"]["schema_source"] == "template"
        assert doc["grids"]["level"]["placements"] == {
            "entities": {"kind": "enemy", "wire": "entities"},
            "items": {"kind": "item", "wire": "items"},
        }
        assert doc["grids"]["level"]["points"] == ["spawn", "exit"]

    def test_pack_local_schema_reads_as_pack(self, plat_pack: Path, tmp_path: Path) -> None:
        copy = tmp_path / "copy"
        shutil.copytree(plat_pack, copy)
        (copy / "schemas").mkdir()
        shutil.copy(PLATFORMER.template_dir / "schemas" / "enemy.json", copy / "schemas" / "enemy.json")
        doc = pack_info(copy)
        assert doc["entities"]["enemy"]["schema_source"] == "pack"
        assert doc["entities"]["item"]["schema_source"] == "template"

    def test_unstamped_copy_resolves_by_shape(self, plat_pack: Path, tmp_path: Path) -> None:
        copy = tmp_path / "legacy"
        shutil.copytree(plat_pack, copy)
        manifest = json.loads((copy / "manifest.json").read_text())
        del manifest["pack_type"]
        (copy / "manifest.json").write_text(json.dumps(manifest))
        code, doc = _canon("pack", "info", str(copy))
        assert code == 0, doc
        assert (doc["pack_type"], doc["source"]) == ("platformer", "shape")
        assert doc["entities"]["enemy"]["count"] == 2

    def test_evaluability_falls_back_to_the_dialogue_seed(self, tmp_path: Path, monkeypatch) -> None:
        # P.2.4: a primary engine without its own evaluable_namespaces answers
        # from DialogueSpec.engine_evaluable_seed[engine id] — never "all
        # supported". Registered as data: a third seed is a PACKS entry.
        seed = {"tree": {}, "selector": {"flag": {}}}
        spec = PackSpec(
            pack_type="crawler",
            label="Crawler",
            capabilities=["dialogue"],
            dialogue=DialogueSpec(condition_namespaces=["flag"], engine_evaluable_seed={"x": seed}),
            engines=[{"id": "x", "primary": True}],
        )
        monkeypatch.setitem(PACKS, "crawler", spec)
        (tmp_path / "manifest.json").write_text(json.dumps({"pack_type": "crawler"}))
        doc = pack_info(tmp_path)
        assert doc["source"] == "manifest" and doc["pack_type"] == "crawler"
        assert doc["engine_evaluable_namespaces"] == seed
        assert "engine_evaluable_bindings" not in doc
        assert doc["dialogue"]["condition_namespaces"] == ["flag"]
        assert doc["engines"] == [{"id": "x", "primary": True}]

    def test_non_pack_is_a_json_error(self, tmp_path: Path) -> None:
        code, doc = _canon("pack", "info", str(tmp_path))
        assert code == 1
        assert "unknown pack type" in doc["error"] and str(tmp_path) in doc["error"]


# ---------------------------------------------------------------------------
# The pack_type mirror rule (P.4.1) — both writers, first key, resume-safe
# ---------------------------------------------------------------------------


def _dungeon_ctx(tmp_path: Path, pack_type: str | None = None) -> PipelineContext:
    bible = Bible.empty(seed="p03")
    bible.maps["room_0"] = Map(
        map_id="room_0", name="Room 0", description="", environment="ruins", level=1, story_beat="",
    )
    bible.class_archetypes["warrior"] = ClassArchetype(archetype_id="warrior", name="Warrior")
    return PipelineContext(
        bible=bible,
        config=CanonConfig(seed="p03", output_dir=str(tmp_path)),
        rng=random.Random(0),
        stats=GenerationStats(),
        pack_type=pack_type,
    )


class TestManifestMirror:
    def test_platformer_manifest_stamps_pack_type_first(self, plat_pack: Path) -> None:
        manifest = json.loads((plat_pack / "manifest.json").read_text())
        assert list(manifest)[:2] == ["pack_type", "game"]
        assert manifest["pack_type"] == "platformer"

    def test_pipeline_context_default_keeps_legacy_callers_identical(self) -> None:
        assert PipelineContext.__dataclass_fields__["pack_type"].default is None

    def test_mazeworld_writer_stamps_from_ctx(self, tmp_path: Path) -> None:
        MazeworldManifestPhase().run(_dungeon_ctx(tmp_path, pack_type="dungeon"))
        manifest = json.loads((tmp_path / "manifest.json").read_text())
        assert list(manifest)[:2] == ["pack_type", "seed"]
        assert manifest["pack_type"] == "dungeon"
        assert resolve_pack(tmp_path).source == "manifest"

    def test_mazeworld_writer_falls_back_to_its_own_id(self, tmp_path: Path) -> None:
        # A legacy caller (no ctx.pack_type) still stamps — the writer's own id.
        MazeworldManifestPhase().run(_dungeon_ctx(tmp_path))
        manifest = json.loads((tmp_path / "manifest.json").read_text())
        assert list(manifest)[0] == "pack_type" and manifest["pack_type"] == "dungeon"
        assert resolve_pack(tmp_path).source == "manifest"

    def test_core_writer_carries_no_pack_id(self, tmp_path: Path) -> None:
        # The pack id lives on the pack's writer, never in canon.pipeline:
        # the base phase emits no key unless the context carries one.
        assert ManifestPhase.pack_type is None
        ManifestPhase().run(_dungeon_ctx(tmp_path))
        manifest = json.loads((tmp_path / "manifest.json").read_text())
        assert "pack_type" not in manifest and list(manifest)[0] == "seed"

    def test_ctx_pack_type_wins_over_the_writer_default(self, tmp_path: Path) -> None:
        # Ids are data: a compose that says "crawler" is mirrored verbatim.
        ManifestPhase().run(_dungeon_ctx(tmp_path, pack_type="crawler"))
        assert json.loads((tmp_path / "manifest.json").read_text())["pack_type"] == "crawler"

    def test_mazeworld_compose_sets_the_registry_id(self, tmp_path: Path) -> None:
        from canon.packs.dungeon.compose import compose_pipeline

        _phases, ctx = compose_pipeline(seed="p03", num_maps=1, output_dir=tmp_path)
        assert ctx.pack_type == "dungeon"

    def test_platformer_make_ctx_sets_the_registry_id(self, plat_pack: Path) -> None:
        assert ops.make_ctx(ops.load_pack(plat_pack)).pack_type == "platformer"

    def test_set_world_title_preserves_the_stamp(self, plat_pack: Path, tmp_path: Path) -> None:
        # P0-6 / P.9 R13: `world new --name` routes through the journaled
        # write core (`set_world_title`) — the `_set_world_name` bypass is gone.
        from canon.world_ops import set_world_title

        copy = tmp_path / "named"
        shutil.copytree(plat_pack, copy)
        set_world_title(copy, "Renamed World", actor="test")
        manifest = json.loads((copy / "manifest.json").read_text())
        assert list(manifest)[0] == "pack_type" and manifest["pack_type"] == "platformer"
        assert manifest["world"] == "Renamed World"
        assert json.loads((copy / "world.json").read_text())["title"] == "Renamed World"
        assert resolve_pack(copy).source == "manifest"


def test_pack_info_counts_legacy_rooms_from_per_room_dirs():
    """Legacy dungeon trees carry ``rooms/room_N/maze.json`` but no
    ``rooms/rooms.json`` index; the room count falls back to the GridKind's
    per-room files instead of reading 0 (decided 2026-09-01, P0 paper P.9)."""
    from pathlib import Path as _Path

    from canon.packs import pack_info

    fixture = DUNGEON_FIXTURE
    info = pack_info(fixture)
    expected = len(list(_Path(fixture).glob("rooms/room_*/maze.json")))
    assert expected > 0
    assert info["entities"]["room"]["count"] == expected
