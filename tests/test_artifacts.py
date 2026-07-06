"""Tests for canon.bible.artifacts — status enums, ArtifactMeta, the
artifact-ID address space, and the provenance-hash composite."""

from __future__ import annotations

import hashlib

import pytest

from canon.bible.artifacts import (
    LEVEL_STEPS,
    ArtifactId,
    ArtifactMeta,
    ArtifactStatus,
    PhaseStatus,
    compute_provenance_hash,
    make_artifact_id,
    parse_artifact_id,
)

# ---------------------------------------------------------------------------
# Enums + ArtifactMeta
# ---------------------------------------------------------------------------


class TestStatusEnums:
    def test_artifact_lifecycle_states_present(self) -> None:
        # PRD §7.3 + §6.3 — the full state machine vocabulary.
        expected = {
            "pending", "running", "done", "failed", "retrying", "escalated",
            "stale", "regenerating", "awaiting_review", "user_edited",
        }
        assert {s.value for s in ArtifactStatus} == expected

    def test_phase_status_coarse(self) -> None:
        assert {s.value for s in PhaseStatus} == {
            "pending", "running", "done", "failed",
        }

    def test_enums_serialize_as_strings(self) -> None:
        assert ArtifactStatus.STALE == "stale"
        assert PhaseStatus.DONE == "done"


class TestArtifactMeta:
    def test_defaults(self) -> None:
        meta = ArtifactMeta()
        assert meta.artifact_id == ""
        assert meta.status is ArtifactStatus.PENDING
        assert meta.provenance_hash == ""
        assert meta.parents == []

    def test_round_trip(self) -> None:
        meta = ArtifactMeta(
            artifact_id="enemy:goblin",
            status=ArtifactStatus.DONE,
            provenance_hash="sha256:ab",
            parents=["stage:s1"],
        )
        again = ArtifactMeta.model_validate(meta.model_dump(mode="json"))
        assert again == meta


# ---------------------------------------------------------------------------
# Artifact IDs
# ---------------------------------------------------------------------------


class TestMakeParse:
    @pytest.mark.parametrize(
        ("namespace", "parts", "expected"),
        [
            ("world", (), "world"),
            ("stage", ("s1",), "stage:s1"),
            ("enemy", ("goblin_grunt",), "enemy:goblin_grunt"),
            ("boss", ("ember_king",), "boss:ember_king"),
            ("tileset", ("s1",), "tileset:s1"),
            ("item", ("rusty_key",), "item:rusty_key"),
            ("level", ("s1", "l3", "collision"), "level:s1/l3/collision"),
        ],
    )
    def test_every_namespace_round_trips(self, namespace, parts, expected) -> None:
        aid = make_artifact_id(namespace, *parts)
        assert aid == expected
        parsed = parse_artifact_id(aid)
        assert parsed == ArtifactId(namespace=namespace, parts=parts)
        assert str(parsed) == expected

    def test_all_level_steps_valid(self) -> None:
        for step in LEVEL_STEPS:
            parse_artifact_id(f"level:s1/l1/{step}")

    @pytest.mark.parametrize(
        "bad",
        [
            "",  # empty
            "npc:gary",  # unknown namespace (npc is MazeWorld-scoped)
            "world:extra",  # world takes no parts
            "enemy",  # missing part
            "enemy:",  # empty part
            "enemy:a/b",  # wrong arity
            "level:s1/l3",  # level needs 3 parts
            "level:s1/l3/teleporters",  # unknown step
            "stage:s1/extra",  # wrong arity
        ],
    )
    def test_malformed_ids_rejected(self, bad: str) -> None:
        with pytest.raises(ValueError):
            parse_artifact_id(bad)

    def test_make_rejects_separator_in_part(self) -> None:
        with pytest.raises(ValueError, match="reserved"):
            make_artifact_id("enemy", "gob:lin")
        with pytest.raises(ValueError, match="reserved"):
            make_artifact_id("stage", "s/1")

    def test_error_names_known_namespaces(self) -> None:
        with pytest.raises(ValueError, match="Known"):
            parse_artifact_id("dungeon:d1")


# ---------------------------------------------------------------------------
# Provenance hash
# ---------------------------------------------------------------------------


class TestProvenanceHash:
    KWARGS = dict(
        schema_version="1", prompt_version="v2", model="claude-x", seed="s"
    )

    def test_deterministic(self) -> None:
        a = compute_provenance_hash("sha256:aa", **self.KWARGS)
        b = compute_provenance_hash("sha256:aa", **self.KWARGS)
        assert a == b and a.startswith("sha256:")

    def test_any_input_changes_hash(self) -> None:
        base = compute_provenance_hash("sha256:aa", **self.KWARGS)
        variants = [
            compute_provenance_hash("sha256:bb", **self.KWARGS),
            compute_provenance_hash("sha256:aa", **{**self.KWARGS, "schema_version": "2"}),
            compute_provenance_hash("sha256:aa", **{**self.KWARGS, "prompt_version": "v3"}),
            compute_provenance_hash("sha256:aa", **{**self.KWARGS, "model": "claude-y"}),
            compute_provenance_hash("sha256:aa", **{**self.KWARGS, "seed": "t"}),
        ]
        assert len({base, *variants}) == 6

    def test_matches_documented_construction(self) -> None:
        expected = "sha256:" + hashlib.sha256(
            b"sha256:aa\n1\nv2\nclaude-x\ns"
        ).hexdigest()
        assert compute_provenance_hash("sha256:aa", **self.KWARGS) == expected
