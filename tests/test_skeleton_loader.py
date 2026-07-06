"""Tests for canon.skeleton.loader — Canon field-spec JSON ↔ SkeletonSpec.

The load-bearing property: load(dump(spec)) rolls identically to the
original spec for the same rng. Key-type fidelity (int lookup keys, weight
values) is what the pair-list encoding exists to protect.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from canon.pipeline.rng import derive_rng
from canon.skeleton.core import SkeletonField, SkeletonSpec, roll_skeleton
from canon.skeleton.loader import (
    SCHEMA_FORMAT_VERSION,
    SchemaLoadError,
    dump_skeleton_spec,
    load_skeleton_spec,
    save_skeleton_spec,
)


def _full_spec() -> SkeletonSpec:
    """A spec exercising all three modes + both depends variants."""
    return SkeletonSpec(
        entity_type="enemy",
        fields={
            "archetype": SkeletonField(
                choices=[("walker", 3), ("flyer", 2), ("turret", 0.5)]
            ),
            "hp": SkeletonField(range=(8, 40)),
            "damage_die": SkeletonField(
                lookup={"walker": "d6", "flyer": "d4", "turret": "d8"},
                depends_on="archetype",
            ),
            "tier": SkeletonField(
                lookup={1: "grunt", 2: "elite", 3: "boss"},
                depends_on_context="stage_level",
            ),
        },
    )


class TestRoundTrip:
    def test_dump_load_rolls_identically(self) -> None:
        spec = _full_spec()
        loaded = load_skeleton_spec(dump_skeleton_spec(spec))
        for i in range(20):
            rng_a = derive_rng("loader_test", i)
            rng_b = derive_rng("loader_test", i)
            ctx = {"stage_level": (i % 3) + 1}
            assert roll_skeleton(spec, rng_a, context=ctx) == roll_skeleton(
                loaded, rng_b, context=ctx
            )

    def test_file_round_trip(self, tmp_path: Path) -> None:
        spec = _full_spec()
        path = tmp_path / "schemas" / "enemy.json"
        save_skeleton_spec(spec, path)
        loaded = load_skeleton_spec(path)
        assert dump_skeleton_spec(loaded) == dump_skeleton_spec(spec)

    def test_int_lookup_keys_survive(self) -> None:
        """The reason lookup serializes as pair lists: JSON object keys are
        strings and would corrupt depends_on_context int keys."""
        spec = _full_spec()
        loaded = load_skeleton_spec(
            json.loads(json.dumps(dump_skeleton_spec(spec)))  # full JSON cycle
        )
        assert loaded.fields["tier"].lookup == {1: "grunt", 2: "elite", 3: "boss"}

    def test_field_order_preserved(self) -> None:
        loaded = load_skeleton_spec(dump_skeleton_spec(_full_spec()))
        assert list(loaded.fields) == ["archetype", "hp", "damage_die", "tier"]

    def test_schema_version_carried(self, tmp_path: Path) -> None:
        doc = dump_skeleton_spec(_full_spec())
        assert doc["schema_version"] == SCHEMA_FORMAT_VERSION
        doc["schema_version"] = "7"
        loaded = load_skeleton_spec(doc)
        assert dump_skeleton_spec(loaded)["schema_version"] == "7"


class TestErrors:
    def test_missing_file_names_path(self, tmp_path: Path) -> None:
        with pytest.raises(SchemaLoadError, match="nope.json"):
            load_skeleton_spec(tmp_path / "nope.json")

    def test_bad_json_names_path(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.json"
        path.write_text("{not json")
        with pytest.raises(SchemaLoadError, match="broken.json"):
            load_skeleton_spec(path)

    def test_broken_field_names_field_and_file(self, tmp_path: Path) -> None:
        doc = {
            "entity_type": "enemy",
            "fields": {
                "hp": {"range": [8, 40]},
                "damage": {"range": [1, 6], "choices": [["a", 1]]},  # two modes
            },
        }
        path = tmp_path / "enemy.json"
        path.write_text(json.dumps(doc))
        with pytest.raises(SchemaLoadError, match=r"enemy.json.*'damage'"):
            load_skeleton_spec(path)

    def test_dependency_order_error_surfaces(self) -> None:
        doc = {
            "entity_type": "enemy",
            "fields": {
                # depends_on references a later field — SkeletonSpec's own
                # dependency-order validation must surface through the loader.
                "damage_die": {
                    "lookup": [["walker", "d6"]],
                    "depends_on": "archetype",
                },
                "archetype": {"choices": [["walker", 1]]},
            },
        }
        with pytest.raises(SchemaLoadError, match="archetype"):
            load_skeleton_spec(doc)

    def test_unknown_field_key_rejected(self) -> None:
        doc = {
            "entity_type": "enemy",
            "fields": {"hp": {"rnage": [8, 40]}},  # typo
        }
        with pytest.raises(SchemaLoadError, match="rnage"):
            load_skeleton_spec(doc)

    def test_lookup_as_json_object_rejected(self) -> None:
        """Objects would silently stringify int keys; the format demands
        pair lists."""
        doc = {
            "entity_type": "enemy",
            "fields": {
                "tier": {
                    "lookup": {"1": "grunt"},
                    "depends_on_context": "stage_level",
                }
            },
        }
        with pytest.raises(SchemaLoadError, match="pair"):
            load_skeleton_spec(doc)

    def test_post_callback_refuses_dump(self) -> None:
        spec = SkeletonSpec(
            entity_type="enemy",
            fields={"hp": SkeletonField(range=(1, 2))},
            post=lambda rolled: rolled,
        )
        with pytest.raises(ValueError, match="post"):
            dump_skeleton_spec(spec)
