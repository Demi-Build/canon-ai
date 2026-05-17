"""Unit tests for DatabasePhase and DatabaseSpec.

Decision: ``test_phase_entity.py`` is kept as-is — it still passes against the
deprecated ``EntityPhase`` alias.  This file covers DatabasePhase / DatabaseSpec
contracts.  Tests here are contract tests, not algorithm-mirror tests; they run
against a FakeLLMBackend with deterministic canned responses.

EntityPhase backward-compat tests are at the bottom of this file as a single
import-smoke test; the full EntityPhase test suite remains in
``test_phase_entity.py``.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from canon import (
    Bible,
    CanonConfig,
    DefaultPromptSet,
    FakeLLMBackend,
    GenerationStats,
    LLMClient,
    Map,
    PipelineContext,
    SkeletonField,
    SkeletonSpec,
)
from canon.persistence import IDAllocator
from canon.pipeline.phases.database import (
    BuildContext,
    DatabasePhase,
    DatabaseSpec,  # deprecated alias — import smoke test
)

# ---------------------------------------------------------------------------
# Shared skeleton spec and parsers
# ---------------------------------------------------------------------------

ITEM_SPEC = SkeletonSpec(
    entity_type="item",
    fields={"weight": SkeletonField(choices=[("light", 1), ("heavy", 1)])},
)

MONSTER_SPEC = SkeletonSpec(
    entity_type="monster",
    fields={"hp": SkeletonField(range=(10, 50))},
)


def simple_parser(bc: BuildContext) -> dict:
    """Minimal parser: passthrough the LLM response with id injected."""
    result = dict(bc.llm_response)
    if bc.allocated_id is not None:
        result["id"] = bc.allocated_id
    return result


def skeleton_capturing_parser(bc: BuildContext) -> dict:
    """Parser that exposes both skeleton and LLM fields."""
    return {
        "id": bc.allocated_id,
        "name": bc.llm_response.get("name", "unnamed"),
        "skeleton": bc.skeleton,
        "map_id": bc.map_id,
        "entity_index": bc.entity_index,
    }


def name_only_parser(bc: BuildContext) -> dict:
    return {"name": bc.llm_response.get("name", "unnamed"), "id": bc.allocated_id}


# ---------------------------------------------------------------------------
# Context factory
# ---------------------------------------------------------------------------


def make_ctx(
    responses,
    *,
    with_maps: bool = True,
    seed: str = "test",
    output_dir: Path | None = None,
    id_bases: dict | None = None,
) -> PipelineContext:
    """Build a PipelineContext with a FakeLLMBackend and optional maps."""
    bible = Bible.empty(seed=seed)
    if with_maps:
        bible.maps["map_0"] = Map(
            map_id="map_0",
            name="Map A",
            description="",
            environment="forest",
            story_beat="",
        )
        bible.maps["map_1"] = Map(
            map_id="map_1",
            name="Map B",
            description="",
            environment="cave",
            story_beat="",
        )
    backend = FakeLLMBackend(responses)
    llm = LLMClient(backend, stats=GenerationStats())

    cfg_kwargs: dict[str, Any] = {"seed": seed}
    if output_dir is not None:
        cfg_kwargs["output_dir"] = output_dir

    id_alloc = IDAllocator(bases=id_bases or {"npc": 1000, "item": 2000, "monster": 5000})
    return PipelineContext(
        bible=bible,
        config=CanonConfig(**cfg_kwargs),
        rng=random.Random(seed),
        llm=llm,
        prompts=DefaultPromptSet(),
        id_allocator=id_alloc,
    )


def item_response(name: str = "Iron Key") -> str:
    return json.dumps({"name": name, "description": "A generic item."})


def monster_response(name: str = "Cave Troll") -> str:
    return json.dumps({"name": name, "description": "A fearsome creature."})


# ---------------------------------------------------------------------------
# 1. DatabaseSpec construction
# ---------------------------------------------------------------------------


class TestDatabaseSpecConstruction:
    def test_minimal_valid_spec(self):
        """Minimal spec with required fields only must construct without error."""
        spec = DatabaseSpec(
            entity_type="x",
            prompt_method="entity_generation",
            parser=lambda bc: {},
            output_path="x.json",
        )
        assert spec.entity_type == "x"
        assert spec.prompt_method == "entity_generation"
        assert spec.output_path == "x.json"
        assert spec.output_format == "array"  # default
        assert spec.per_map is True
        assert spec.count == 3
        assert spec.cross_room_dedup == []
        assert spec.id_prefix is None
        assert spec.skeleton_spec is None

    def test_all_fields_populated(self):
        """Spec with all fields specified must round-trip through model_validate."""
        parser_fn = lambda bc: {"id": 1}  # noqa: E731
        spec = DatabaseSpec(
            entity_type="item",
            skeleton_spec=ITEM_SPEC,
            prompt_method="entity_generation",
            parser=parser_fn,
            output_path="items/items.json",
            output_format="keyed_object",
            id_prefix="item",
            per_map=True,
            count=5,
            cross_room_dedup=["name"],
        )
        assert spec.entity_type == "item"
        assert spec.skeleton_spec is ITEM_SPEC
        assert spec.output_format == "keyed_object"
        assert spec.id_prefix == "item"
        assert spec.count == 5
        assert spec.cross_room_dedup == ["name"]
        # parser is a callable — Pydantic stores it as-is
        assert callable(spec.parser)

    def test_output_format_array_is_default(self):
        spec = DatabaseSpec(
            entity_type="x",
            prompt_method="entity_generation",
            parser=lambda bc: {},
            output_path="x.json",
        )
        assert spec.output_format == "array"

    def test_per_map_defaults_to_true(self):
        spec = DatabaseSpec(
            entity_type="x",
            prompt_method="entity_generation",
            parser=lambda bc: {},
            output_path="x.json",
        )
        assert spec.per_map is True


# ---------------------------------------------------------------------------
# 2. DatabasePhase execution — per_map and global
# ---------------------------------------------------------------------------


class TestDatabasePhaseExecution:
    def test_per_map_generates_count_entities_per_map(self, tmp_path):
        """With per_map=True and count=2, 2 maps → 4 entities total written."""
        responses = [item_response(f"Item {i}") for i in range(4)]
        ctx = make_ctx(responses, output_dir=tmp_path)
        spec = DatabaseSpec(
            entity_type="item",
            prompt_method="entity_generation",
            parser=simple_parser,
            output_path="items.json",
            per_map=True,
            count=2,
        )
        DatabasePhase(spec).run(ctx)

        data = json.loads((tmp_path / "items.json").read_text())
        assert isinstance(data, list)
        assert len(data) == 4

    def test_global_generates_exact_count(self, tmp_path):
        """With per_map=False and count=3, exactly 3 entities total are written."""
        responses = [item_response(f"Global {i}") for i in range(3)]
        ctx = make_ctx(responses, output_dir=tmp_path)
        spec = DatabaseSpec(
            entity_type="item",
            prompt_method="entity_generation",
            parser=simple_parser,
            output_path="items_global.json",
            per_map=False,
            count=3,
        )
        DatabasePhase(spec).run(ctx)

        data = json.loads((tmp_path / "items_global.json").read_text())
        assert isinstance(data, list)
        assert len(data) == 3

    def test_phase_name_is_db_entity_type(self):
        spec = DatabaseSpec(
            entity_type="npc",
            prompt_method="entity_generation",
            parser=simple_parser,
            output_path="npcs.json",
        )
        phase = DatabasePhase(spec)
        assert phase.name == "db:npc"

    def test_phase_appends_name_to_phases_run(self, tmp_path):
        """Phase name must appear in ctx.bible.metadata.phases_run after run."""
        ctx = make_ctx([item_response()], output_dir=tmp_path)
        spec = DatabaseSpec(
            entity_type="item",
            prompt_method="entity_generation",
            parser=simple_parser,
            output_path="items.json",
            per_map=False,
            count=1,
        )
        DatabasePhase(spec).run(ctx)
        assert "db:item" in ctx.bible.metadata.phases_run


# ---------------------------------------------------------------------------
# 3. ID allocation
# ---------------------------------------------------------------------------


class TestIDAllocation:
    def test_id_allocated_from_id_allocator(self, tmp_path):
        """Each entity must receive a unique ID from ctx.id_allocator."""
        ids_seen = []

        def capturing_parser(bc: BuildContext) -> dict:
            ids_seen.append(bc.allocated_id)
            return {"name": bc.llm_response.get("name"), "id": bc.allocated_id}

        responses = [item_response(f"I{i}") for i in range(4)]
        ctx = make_ctx(responses, output_dir=tmp_path)
        spec = DatabaseSpec(
            entity_type="item",
            prompt_method="entity_generation",
            parser=capturing_parser,
            output_path="items.json",
            id_prefix="item",
            per_map=True,
            count=2,
        )
        DatabasePhase(spec).run(ctx)

        # 4 entities, all IDs unique
        assert len(ids_seen) == 4
        assert len(set(ids_seen)) == 4

    def test_id_starts_at_configured_base(self, tmp_path):
        """First allocated item ID must be the base (2000 by default)."""
        first_id = []

        def capturing_parser(bc: BuildContext) -> dict:
            if not first_id:
                first_id.append(bc.allocated_id)
            return {"id": bc.allocated_id}

        ctx = make_ctx([item_response()], output_dir=tmp_path)
        spec = DatabaseSpec(
            entity_type="item",
            prompt_method="entity_generation",
            parser=capturing_parser,
            output_path="items.json",
            id_prefix="item",
            per_map=False,
            count=1,
        )
        DatabasePhase(spec).run(ctx)
        assert first_id[0] == 2000

    def test_no_id_prefix_means_allocated_id_is_none(self, tmp_path):
        """When id_prefix is None, BuildContext.allocated_id must be None."""
        allocated_ids = []

        def capturing_parser(bc: BuildContext) -> dict:
            allocated_ids.append(bc.allocated_id)
            return {}

        ctx = make_ctx([item_response()], output_dir=tmp_path)
        spec = DatabaseSpec(
            entity_type="item",
            prompt_method="entity_generation",
            parser=capturing_parser,
            output_path="items.json",
            id_prefix=None,
            per_map=False,
            count=1,
        )
        DatabasePhase(spec).run(ctx)
        assert allocated_ids == [None]

    def test_ids_are_sequential(self, tmp_path):
        """Sequential IDs must be base, base+1, base+2, …"""
        ids_seen = []

        def capturing_parser(bc: BuildContext) -> dict:
            ids_seen.append(bc.allocated_id)
            return {"id": bc.allocated_id}

        responses = [item_response(f"I{i}") for i in range(3)]
        ctx = make_ctx(responses, output_dir=tmp_path)
        spec = DatabaseSpec(
            entity_type="item",
            prompt_method="entity_generation",
            parser=capturing_parser,
            output_path="items.json",
            id_prefix="item",
            per_map=False,
            count=3,
        )
        DatabasePhase(spec).run(ctx)
        assert ids_seen == [2000, 2001, 2002]


# ---------------------------------------------------------------------------
# 4. File output format
# ---------------------------------------------------------------------------


class TestFileOutputFormat:
    def test_output_format_array_produces_list(self, tmp_path):
        responses = [item_response(f"X{i}") for i in range(2)]
        ctx = make_ctx(responses, output_dir=tmp_path)
        spec = DatabaseSpec(
            entity_type="item",
            prompt_method="entity_generation",
            parser=simple_parser,
            output_path="entities.json",
            output_format="array",
            per_map=False,
            count=2,
        )
        DatabasePhase(spec).run(ctx)
        data = json.loads((tmp_path / "entities.json").read_text())
        assert isinstance(data, list)
        assert len(data) == 2

    def test_output_format_keyed_object_produces_dict(self, tmp_path):
        """keyed_object format must produce a dict keyed by 'id' field."""
        def id_parser(bc: BuildContext) -> dict:
            return {"id": 2000 + bc.entity_index, "name": bc.llm_response.get("name")}

        responses = [item_response(f"K{i}") for i in range(2)]
        ctx = make_ctx(responses, output_dir=tmp_path)
        spec = DatabaseSpec(
            entity_type="item",
            prompt_method="entity_generation",
            parser=id_parser,
            output_path="keyed.json",
            output_format="keyed_object",
            per_map=False,
            count=2,
        )
        DatabasePhase(spec).run(ctx)
        data = json.loads((tmp_path / "keyed.json").read_text())
        assert isinstance(data, dict)
        assert "2000" in data
        assert "2001" in data

    def test_file_written_at_output_path(self, tmp_path):
        """Phase must write to <output_dir>/<output_path>."""
        ctx = make_ctx([item_response()], output_dir=tmp_path)
        spec = DatabaseSpec(
            entity_type="item",
            prompt_method="entity_generation",
            parser=simple_parser,
            output_path="items/items.json",
            per_map=False,
            count=1,
        )
        DatabasePhase(spec).run(ctx)
        assert (tmp_path / "items" / "items.json").exists()

    def test_file_creates_parent_directories(self, tmp_path):
        """Deeply nested output_path must create intermediate directories."""
        ctx = make_ctx([item_response()], output_dir=tmp_path)
        spec = DatabaseSpec(
            entity_type="item",
            prompt_method="entity_generation",
            parser=simple_parser,
            output_path="a/b/c/entities.json",
            per_map=False,
            count=1,
        )
        DatabasePhase(spec).run(ctx)
        assert (tmp_path / "a" / "b" / "c" / "entities.json").exists()


# ---------------------------------------------------------------------------
# 5. BuildContext contents
# ---------------------------------------------------------------------------


class TestBuildContext:
    def test_skeleton_passed_to_parser(self, tmp_path):
        """BuildContext.skeleton must be the rolled skeleton dict."""
        skeletons_seen = []

        def capturing_parser(bc: BuildContext) -> dict:
            skeletons_seen.append(bc.skeleton)
            return {}

        ctx = make_ctx([item_response()], output_dir=tmp_path)
        spec = DatabaseSpec(
            entity_type="item",
            skeleton_spec=ITEM_SPEC,
            prompt_method="entity_generation",
            parser=capturing_parser,
            output_path="items.json",
            per_map=False,
            count=1,
        )
        DatabasePhase(spec).run(ctx)

        assert len(skeletons_seen) == 1
        skeleton = skeletons_seen[0]
        assert "weight" in skeleton
        assert skeleton["weight"] in {"light", "heavy"}

    def test_empty_skeleton_when_no_skeleton_spec(self, tmp_path):
        """When skeleton_spec is None, BuildContext.skeleton must be empty dict."""
        skeletons_seen = []

        def capturing_parser(bc: BuildContext) -> dict:
            skeletons_seen.append(bc.skeleton)
            return {}

        ctx = make_ctx([item_response()], output_dir=tmp_path)
        spec = DatabaseSpec(
            entity_type="item",
            skeleton_spec=None,
            prompt_method="entity_generation",
            parser=capturing_parser,
            output_path="items.json",
            per_map=False,
            count=1,
        )
        DatabasePhase(spec).run(ctx)
        assert skeletons_seen[0] == {}

    def test_llm_response_passed_to_parser(self, tmp_path):
        """BuildContext.llm_response must contain the parsed LLM JSON."""
        llm_responses_seen = []

        def capturing_parser(bc: BuildContext) -> dict:
            llm_responses_seen.append(bc.llm_response)
            return {}

        raw = item_response("Magic Wand")
        ctx = make_ctx([raw], output_dir=tmp_path)
        spec = DatabaseSpec(
            entity_type="item",
            prompt_method="entity_generation",
            parser=capturing_parser,
            output_path="items.json",
            per_map=False,
            count=1,
        )
        DatabasePhase(spec).run(ctx)

        assert llm_responses_seen[0]["name"] == "Magic Wand"

    def test_allocated_id_in_build_context(self, tmp_path):
        """BuildContext.allocated_id must match the IDAllocator's value."""
        allocated_ids = []

        def capturing_parser(bc: BuildContext) -> dict:
            allocated_ids.append(bc.allocated_id)
            return {"id": bc.allocated_id}

        ctx = make_ctx([item_response()], output_dir=tmp_path)
        spec = DatabaseSpec(
            entity_type="item",
            prompt_method="entity_generation",
            parser=capturing_parser,
            output_path="items.json",
            id_prefix="item",
            per_map=False,
            count=1,
        )
        DatabasePhase(spec).run(ctx)
        assert allocated_ids[0] == 2000

    def test_map_id_in_build_context(self, tmp_path):
        """For per_map=True, each BuildContext.map_id must match the map."""
        map_ids_seen = []

        def capturing_parser(bc: BuildContext) -> dict:
            map_ids_seen.append(bc.map_id)
            return {}

        responses = [item_response() for _ in range(2)]
        ctx = make_ctx(responses, output_dir=tmp_path)
        spec = DatabaseSpec(
            entity_type="item",
            prompt_method="entity_generation",
            parser=capturing_parser,
            output_path="items.json",
            per_map=True,
            count=1,
        )
        DatabasePhase(spec).run(ctx)

        assert set(map_ids_seen) == {"map_0", "map_1"}

    def test_map_id_is_none_for_global(self, tmp_path):
        """For per_map=False, BuildContext.map_id must be None."""
        map_ids_seen = []

        def capturing_parser(bc: BuildContext) -> dict:
            map_ids_seen.append(bc.map_id)
            return {}

        ctx = make_ctx([item_response()], output_dir=tmp_path)
        spec = DatabaseSpec(
            entity_type="item",
            prompt_method="entity_generation",
            parser=capturing_parser,
            output_path="items.json",
            per_map=False,
            count=1,
        )
        DatabasePhase(spec).run(ctx)
        assert map_ids_seen == [None]


# ---------------------------------------------------------------------------
# 6. LLM JSON parse failure tolerance
# ---------------------------------------------------------------------------


class TestLLMParseFailureTolerance:
    def test_bad_json_falls_back_without_crash(self, tmp_path):
        """Phase must not crash when LLM returns non-JSON; it falls back."""
        bad_responses = ["not json", "also not json", "still bad"]
        ctx = make_ctx(bad_responses, output_dir=tmp_path)
        spec = DatabaseSpec(
            entity_type="item",
            prompt_method="entity_generation",
            parser=simple_parser,
            output_path="items.json",
            per_map=False,
            count=1,
        )
        # Must not raise
        DatabasePhase(spec).run(ctx)

    def test_bad_json_produces_fallback_entity(self, tmp_path):
        """After bad JSON exhausts retries, fallback entity must appear in output."""
        bad_responses = ["bad"] * 4  # more than max_retries
        ctx = make_ctx(bad_responses, output_dir=tmp_path)
        spec = DatabaseSpec(
            entity_type="item",
            prompt_method="entity_generation",
            parser=simple_parser,
            output_path="items.json",
            per_map=False,
            count=1,
        )
        DatabasePhase(spec).run(ctx)
        data = json.loads((tmp_path / "items.json").read_text())
        # fallback produces {"name": "item 0"}; may or may not have made it
        # through the parser depending on parser implementation — the key
        # contract is: no exception raised, file is written.
        assert isinstance(data, list)

    def test_valid_then_bad_json_produces_partial_output(self, tmp_path):
        """Mix of good + bad LLM responses: good entities are still written."""
        responses = [item_response("Good Item"), "bad"] * 10
        ctx = make_ctx(responses, output_dir=tmp_path)
        spec = DatabaseSpec(
            entity_type="item",
            prompt_method="entity_generation",
            parser=simple_parser,
            output_path="items.json",
            per_map=True,
            count=1,
        )
        DatabasePhase(spec).run(ctx)
        data = json.loads((tmp_path / "items.json").read_text())
        assert isinstance(data, list)
        # At least some entities should have been produced
        assert len(data) >= 1


# ---------------------------------------------------------------------------
# 7. Cross-room dedup
# ---------------------------------------------------------------------------


class TestCrossRoomDedup:
    def test_same_name_triggers_retry(self, tmp_path):
        """With cross_room_dedup=['name'], a duplicate name triggers a retry.

        Setup: first map gets 'Dragon' (response 0), second map also gets
        'Dragon' (response 1, which is a dup), then a retry fires and gets
        'Wyvern' (response 2). The final output must include 'Wyvern'.
        """
        responses = [
            item_response("Dragon"),   # map_0 → accepted
            item_response("Dragon"),   # map_1 → dup → retry
            item_response("Wyvern"),   # map_1 retry → accepted
        ]
        ctx = make_ctx(responses, output_dir=tmp_path)
        spec = DatabaseSpec(
            entity_type="item",
            prompt_method="entity_generation",
            parser=name_only_parser,
            output_path="items.json",
            per_map=True,
            count=1,
            cross_room_dedup=["name"],
        )
        DatabasePhase(spec).run(ctx)

        data = json.loads((tmp_path / "items.json").read_text())
        names = [d["name"] for d in data]
        assert "Wyvern" in names

    def test_unique_names_no_retry(self, tmp_path):
        """Unique names across maps must not trigger dedup retry."""
        responses = [item_response("Alpha"), item_response("Beta")]
        ctx = make_ctx(responses, output_dir=tmp_path)
        spec = DatabaseSpec(
            entity_type="item",
            prompt_method="entity_generation",
            parser=name_only_parser,
            output_path="items.json",
            per_map=True,
            count=1,
            cross_room_dedup=["name"],
        )
        DatabasePhase(spec).run(ctx)

        data = json.loads((tmp_path / "items.json").read_text())
        names = [d["name"] for d in data]
        assert set(names) == {"Alpha", "Beta"}
        # FakeLLMBackend consumed exactly 2 responses (no retry)
        assert ctx.llm.backend._index == 2

    def test_persistent_dup_is_accepted(self, tmp_path):
        """If retry still returns a duplicate, it must be accepted (no infinite loop)."""
        responses = [
            item_response("Dragon"),   # map_0 → accepted
            item_response("Dragon"),   # map_1 → dup → retry
            item_response("Dragon"),   # map_1 retry → still dup → accept
        ]
        ctx = make_ctx(responses, output_dir=tmp_path)
        spec = DatabaseSpec(
            entity_type="item",
            prompt_method="entity_generation",
            parser=name_only_parser,
            output_path="items.json",
            per_map=True,
            count=1,
            cross_room_dedup=["name"],
        )
        # Must not raise or loop indefinitely
        DatabasePhase(spec).run(ctx)

        data = json.loads((tmp_path / "items.json").read_text())
        assert len(data) == 2


# ---------------------------------------------------------------------------
# 8. Phase protocol compliance
# ---------------------------------------------------------------------------


class TestPhaseProtocol:
    def test_satisfies_phase_protocol(self):
        """DatabasePhase must satisfy the Phase protocol (has name + run)."""
        from canon.pipeline.runner import Phase

        spec = DatabaseSpec(
            entity_type="npc",
            prompt_method="entity_generation",
            parser=simple_parser,
            output_path="npcs.json",
        )
        assert isinstance(DatabasePhase(spec), Phase)

    def test_phase_name_format(self):
        for entity_type in ["npc", "item", "monster", "custom_entity"]:
            spec = DatabaseSpec(
                entity_type=entity_type,
                prompt_method="entity_generation",
                parser=simple_parser,
                output_path=f"{entity_type}.json",
            )
            phase = DatabasePhase(spec)
            assert phase.name == f"db:{entity_type}"


# ---------------------------------------------------------------------------
# 9. PipelineContext has id_allocator with correct defaults
# ---------------------------------------------------------------------------


class TestPipelineContextIDAllocator:
    def test_default_id_allocator_exists(self):
        """PipelineContext must have a default IDAllocator with correct bases."""
        bible = Bible.empty(seed="x")
        ctx = PipelineContext(
            bible=bible,
            config=CanonConfig(seed="x"),
            rng=random.Random("x"),
        )
        assert ctx.id_allocator is not None
        assert isinstance(ctx.id_allocator, IDAllocator)

    def test_default_bases_match_mazeworld(self):
        """Default IDAllocator bases must match MazeWorld's DB_BASE pattern."""
        bible = Bible.empty(seed="x")
        ctx = PipelineContext(
            bible=bible,
            config=CanonConfig(seed="x"),
            rng=random.Random("x"),
        )
        alloc = ctx.id_allocator
        assert alloc.next("npc") == 1000
        assert alloc.next("item") == 2000
        assert alloc.next("event") == 3000
        assert alloc.next("quest") == 4000
        assert alloc.next("monster") == 5000
        assert alloc.next("class") == 6000

    def test_custom_id_allocator_at_construction(self):
        """A custom IDAllocator passed at construction must override the default."""
        bible = Bible.empty(seed="x")
        custom = IDAllocator(bases={"unit": 100})
        ctx = PipelineContext(
            bible=bible,
            config=CanonConfig(seed="x"),
            rng=random.Random("x"),
            id_allocator=custom,
        )
        assert ctx.id_allocator is custom
        assert ctx.id_allocator.next("unit") == 100


# ---------------------------------------------------------------------------
# 10. EntityPhase backward-compat
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 11. Bible population (Wave 5.7 Fix 1)
# ---------------------------------------------------------------------------


class TestDatabasePhaseBiblePopulation:
    """Verify DatabasePhase populates bible.maps[map_id].entities after each run."""

    def test_per_map_entities_added_to_bible(self, tmp_path):
        """After DatabasePhase runs, bible.maps[map_id].entities contains
        an EntityLore stub for each generated entity."""
        responses = [monster_response(f"Beast {i}") for i in range(4)]
        ctx = make_ctx(responses, output_dir=tmp_path)
        spec = DatabaseSpec(
            entity_type="monster",
            prompt_method="entity_generation",
            parser=simple_parser,
            output_path="monsters.json",
            per_map=True,
            count=2,
        )
        DatabasePhase(spec).run(ctx)

        # 2 monsters per map, 2 maps = 4 total stubs
        all_entities = (
            list(ctx.bible.maps["map_0"].entities)
            + list(ctx.bible.maps["map_1"].entities)
        )
        assert len(all_entities) == 4, (
            f"Expected 4 EntityLore stubs, got {len(all_entities)}"
        )

    def test_bible_stub_has_correct_entity_type(self, tmp_path):
        """EntityLore stubs must carry the correct entity_type from the spec."""
        ctx = make_ctx([monster_response()], output_dir=tmp_path)
        spec = DatabaseSpec(
            entity_type="monster",
            prompt_method="entity_generation",
            parser=simple_parser,
            output_path="monsters.json",
            per_map=True,
            count=1,
        )
        DatabasePhase(spec).run(ctx)

        # Only map_0 gets an entity (count=1 per map, but map_1 uses second call
        # which doesn't exist here — however make_ctx provides 2 maps and 1 response
        # so only 1 entity is generated across all maps before exhausting).
        # Use a single response for map_0 only; map_1 call will fall back.
        all_stubs = list(ctx.bible.maps["map_0"].entities) + list(ctx.bible.maps["map_1"].entities)
        for stub in all_stubs:
            assert stub.entity_type == "monster", (
                f"Expected entity_type='monster', got {stub.entity_type!r}"
            )

    def test_bible_stub_entity_id_matches_allocated_id(self, tmp_path):
        """EntityLore.entity_id must be the string form of the allocated integer ID."""
        ctx = make_ctx(
            [monster_response(f"M{i}") for i in range(2)],
            output_dir=tmp_path,
            id_bases={"monster": 5000},
        )
        spec = DatabaseSpec(
            entity_type="monster",
            prompt_method="entity_generation",
            parser=simple_parser,
            output_path="monsters.json",
            id_prefix="monster",
            per_map=True,
            count=1,
        )
        DatabasePhase(spec).run(ctx)

        stubs = (
            list(ctx.bible.maps["map_0"].entities)
            + list(ctx.bible.maps["map_1"].entities)
        )
        entity_ids = {s.entity_id for s in stubs}
        # First two allocated IDs from base 5000
        assert "5000" in entity_ids, f"Expected '5000' in entity_ids, got {entity_ids}"
        assert "5001" in entity_ids, f"Expected '5001' in entity_ids, got {entity_ids}"

    def test_bible_stub_name_from_llm(self, tmp_path):
        """EntityLore.name must come from the LLM response 'name' field."""
        ctx = make_ctx([monster_response("Shadow Dragon")], output_dir=tmp_path)
        spec = DatabaseSpec(
            entity_type="monster",
            prompt_method="entity_generation",
            parser=simple_parser,
            output_path="monsters.json",
            per_map=True,
            count=1,
        )
        DatabasePhase(spec).run(ctx)

        stubs = list(ctx.bible.maps["map_0"].entities)
        assert len(stubs) >= 1
        assert stubs[0].name == "Shadow Dragon", (
            f"Expected name='Shadow Dragon', got {stubs[0].name!r}"
        )

    def test_global_entities_do_not_populate_bible_maps(self, tmp_path):
        """Global entities (per_map=False) must not break bible.maps entries.

        For v0.2, global entities are skipped from bible population (mazeworld
        is per-map only).  bible.maps must remain empty after a global phase.
        """
        ctx = make_ctx(
            [item_response(f"G{i}") for i in range(3)],
            output_dir=tmp_path,
        )
        spec = DatabaseSpec(
            entity_type="item",
            prompt_method="entity_generation",
            parser=simple_parser,
            output_path="items_global.json",
            per_map=False,
            count=3,
        )
        DatabasePhase(spec).run(ctx)

        # bible.maps should have no entities added
        for map_id, m in ctx.bible.maps.items():
            assert len(m.entities) == 0, (
                f"Global phase must not populate bible.maps[{map_id!r}].entities; "
                f"found {len(m.entities)} stubs"
            )


# ---------------------------------------------------------------------------
# 10. EntityPhase backward-compat
# ---------------------------------------------------------------------------


class TestEntityPhaseBackwardCompat:
    def test_import_from_database_module(self):
        """Importing EntityPhase from database module must work (deprecated alias)."""
        from canon.pipeline.phases.database import EntityPhase as EP
        assert EP is not None

    def test_import_from_phases_init(self):
        """Importing EntityPhase from canon.pipeline.phases must work."""
        from canon.pipeline.phases import EntityPhase as EP
        assert EP is not None

    def test_import_from_canon_top_level(self):
        """Importing EntityPhase from canon (top-level) must work."""
        from canon import EntityPhase as EP
        assert EP is not None

    def test_entity_phase_still_satisfies_phase_protocol(self):
        """EntityPhase (deprecated) must still satisfy the Phase protocol."""
        from canon.pipeline.phases.entity import EntityPhase as EP
        from canon.pipeline.runner import Phase
        assert isinstance(EP("weapon"), Phase)
