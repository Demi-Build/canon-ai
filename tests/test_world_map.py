"""World-map authoring: durable overrides layered over the deterministic
layout `compose._world_map` computes from the seed.

The map is recomputed on EVERY resume (compose is an always-node), so the
load-bearing property is that hand-authoring survives a recompose while an
un-authored pack stays byte-identical to what it produced before any of this
existed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from examples.platformer_pack.compose import _world_map  # noqa: E402


def _ctx(world=None, seed="emberfall_001"):
    return SimpleNamespace(
        config=SimpleNamespace(seed=seed),
        bible=SimpleNamespace(world=world),
    )


def _stages():
    return [
        SimpleNamespace(stage_id="s1", level_ids=["l1", "l2", "l3"]),
        SimpleNamespace(stage_id="s2", level_ids=["l4", "l5"]),
    ]


class _World:
    def __init__(self, **kw):
        self.map_nodes = kw.get("map_nodes", {})
        self.map_edges = kw.get("map_edges", [])
        self.map_locked = kw.get("map_locked", False)


class TestDefaultPreserving:
    def test_no_world_at_all_matches_empty_authoring(self):
        """Packs predating this have no map_* fields — they must not change."""
        bare = _world_map(_ctx(None), _stages())
        empty = _world_map(_ctx(_World()), _stages())
        assert bare == empty

    def test_unauthored_output_has_no_new_keys(self):
        out = _world_map(_ctx(_World()), _stages())
        assert set(out) == {"nodes", "edges"}
        # ...and no node carries an `origin`, so the payload is unchanged.
        assert all("origin" not in n for n in out["nodes"])

    def test_layout_is_deterministic_for_a_seed(self):
        assert _world_map(_ctx(None), _stages()) == _world_map(_ctx(None), _stages())


class TestNodeOverrides:
    def test_placed_node_wins_and_is_marked_manual(self):
        w = _World(map_nodes={"l2": {"pos": [0.42, 0.77]}})
        out = _world_map(_ctx(w), _stages())
        node = next(n for n in out["nodes"] if n["level_id"] == "l2")
        assert node["pos"] == [0.42, 0.77]
        assert node["origin"] == "manual"

    def test_other_nodes_keep_their_computed_positions(self):
        """The rng is consumed per node either way, so overriding one position
        must not shift the seeded jitter of the nodes after it."""
        base = _world_map(_ctx(None), _stages())
        w = _World(map_nodes={"l2": {"pos": [0.42, 0.77]}})
        out = _world_map(_ctx(w), _stages())
        for a, b in zip(base["nodes"], out["nodes"]):
            if a["level_id"] == "l2":
                continue
            assert a == b, f"{a['level_id']} moved when l2 was placed"

    def test_a_removed_override_returns_the_node_to_the_generator(self):
        w = _World(map_nodes={})
        assert _world_map(_ctx(w), _stages()) == _world_map(_ctx(None), _stages())


class TestEdges:
    def test_derived_chain_when_unauthored(self):
        out = _world_map(_ctx(None), _stages())
        assert out["edges"] == [["l1", "l2"], ["l2", "l3"], ["l3", "l4"], ["l4", "l5"]]
        assert "edge_specs" not in out

    def test_authored_edges_replace_the_chain(self):
        w = _World(map_edges=[{"a": "l1", "b": "l4", "kind": "lock", "condition": "key"}])
        out = _world_map(_ctx(w), _stages())
        # Plain pair list stays the shape every existing consumer reads...
        assert out["edges"] == [["l1", "l4"]]
        # ...with the typing alongside it.
        assert out["edge_specs"][0]["kind"] == "lock"
        assert out["edge_specs"][0]["condition"] == "key"

    def test_locked_flag_surfaces(self):
        assert _world_map(_ctx(_World(map_locked=True)), _stages())["locked"] is True
        assert "locked" not in _world_map(_ctx(_World()), _stages())


class TestWriteVerb:
    """`apply_world_map_edit` is the only way these overrides get written."""

    def _pack(self, tmp_path: Path) -> Path:
        p = tmp_path / "pack"
        p.mkdir()
        (p / "world.json").write_text(json.dumps({"title": "T"}))
        (p / "manifest.json").write_text(
            json.dumps(
                {
                    "world": "T",
                    "stages": [{"stage_id": "s1", "theme": "x", "levels": ["l1", "l2"]}],
                    "world_map": {
                        "nodes": [
                            {"level_id": "l1", "display_name": "1-1", "stage_id": "s1", "pos": [0.1, 0.5]},
                            {"level_id": "l2", "display_name": "1-2", "stage_id": "s1", "pos": [0.4, 0.5]},
                        ],
                        "edges": [["l1", "l2"]],
                    },
                }
            )
        )
        return p

    def test_place_type_and_lock_round_trip(self, tmp_path: Path):
        from canon.adapters.platformer_write import apply_world_map_edit, read_world_map

        p = self._pack(tmp_path)
        r = apply_world_map_edit(
            p,
            {
                "nodes": {"l2": {"pos": [0.42, 0.77]}},
                "edges": [{"a": "l1", "b": "l2", "kind": "lock", "condition": "key"}],
                "locked": True,
            },
            actor="test",
        )
        assert r["world_map"] == "updated"
        world = json.loads((p / "world.json").read_text())
        assert world["map_nodes"]["l2"]["pos"] == [0.42, 0.77]
        assert world["map_edges"][0]["kind"] == "lock"
        assert world["map_locked"] is True
        # The read verb reports the authored state.
        assert read_world_map(p)["locked"] is True
        assert read_world_map(p)["manual_count"] == 1

    def test_no_op_edit_does_not_journal(self, tmp_path: Path):
        """Same hygiene rule as apply_level_edit's grab-and-release-in-place."""
        from canon.adapters.platformer_write import apply_world_map_edit

        p = self._pack(tmp_path)
        apply_world_map_edit(p, {"locked": True}, actor="test")
        before = len((p / ".canon" / "journal.jsonl").read_text().splitlines())
        r = apply_world_map_edit(p, {"locked": True}, actor="test")
        after = len((p / ".canon" / "journal.jsonl").read_text().splitlines())
        assert r["world_map"] == "no_change"
        assert after == before

    def test_null_node_hands_it_back_to_the_generator(self, tmp_path: Path):
        from canon.adapters.platformer_write import apply_world_map_edit

        p = self._pack(tmp_path)
        apply_world_map_edit(p, {"nodes": {"l2": {"pos": [0.4, 0.4]}}}, actor="test")
        apply_world_map_edit(p, {"nodes": {"l2": None}}, actor="test")
        assert json.loads((p / "world.json").read_text())["map_nodes"] == {}

    def test_out_of_range_positions_clamp_rather_than_fail(self, tmp_path: Path):
        """Overshooting the canvas edge is a normal drag, not an error."""
        from canon.adapters.platformer_write import apply_world_map_edit

        p = self._pack(tmp_path)
        apply_world_map_edit(p, {"nodes": {"l1": {"pos": [-0.5, 2.0]}}}, actor="test")
        assert json.loads((p / "world.json").read_text())["map_nodes"]["l1"]["pos"] == [0.0, 1.0]

    def test_bad_input_is_refused(self, tmp_path: Path):
        from canon.adapters.platformer_write import apply_world_map_edit

        p = self._pack(tmp_path)
        with pytest.raises(ValueError, match="not one of"):
            apply_world_map_edit(p, {"edges": [{"a": "l1", "b": "l2", "kind": "warp"}]})
        with pytest.raises(ValueError, match="needs both"):
            apply_world_map_edit(p, {"edges": [{"a": "l1"}]})
        with pytest.raises(ValueError, match="needs pos"):
            apply_world_map_edit(p, {"nodes": {"l1": {"pos": [0.5]}}})

    def test_read_reflects_edits_without_waiting_for_a_recompose(self, tmp_path: Path):
        """The write lands on world.json; the manifest only picks it up on the
        next pipeline run. If the read verb didn't layer the overrides on too,
        the editor would appear to ignore every edit it just made."""
        from canon.adapters.platformer_write import apply_world_map_edit, read_world_map

        p = self._pack(tmp_path)
        apply_world_map_edit(
            p,
            {
                "nodes": {"l2": {"pos": [0.9, 0.1]}},
                "edges": [
                    {"a": "l1", "b": "l2", "kind": "path"},
                    {"a": "l2", "b": "l1", "kind": "one"},
                ],
            },
            actor="test",
        )
        # The manifest is untouched — this is purely the read layering.
        assert json.loads((p / "manifest.json").read_text())["world_map"]["nodes"][1][
            "pos"
        ] == [0.4, 0.5]

        m = read_world_map(p)
        node = next(n for n in m["nodes"] if n["level_id"] == "l2")
        assert node["pos"] == [0.9, 0.1]
        assert node["origin"] == "manual"
        assert len(m["edges"]) == 2
        assert any(e["kind"] == "one" for e in m["edges"])

    def test_drafts_appear_as_planned_and_rooms_never_do(self, tmp_path: Path):
        """A draft is a `planned` node — on the map, not yet in the
        progression. A SECRET ROOM is a sub-room INSIDE a level, not a stop on
        the world map, so it gets no node at all."""
        from canon.adapters.platformer_write import read_world_map

        p = self._pack(tmp_path)
        for lid, extra in (("l7", {}), ("l1r1", {"parent_level": "l1"})):
            d = p / "level" / "s1" / lid
            d.mkdir(parents=True)
            (d / "level.json").write_text(json.dumps({"level_id": lid, **extra}))

        ids = {n["level_id"]: n for n in read_world_map(p)["nodes"]}
        assert ids["l7"]["status"] == "planned"
        assert "l1r1" not in ids, "a secret room must not appear on the world map"

    def test_read_exposes_stages_as_areas(self, tmp_path: Path):
        """Areas ARE stages — they already carry theme/biome/membership."""
        from canon.adapters.platformer_write import read_world_map

        m = read_world_map(self._pack(tmp_path))
        assert m["areas"][0]["stage_id"] == "s1"
        assert m["areas"][0]["level_ids"] == ["l1", "l2"]
        # An unauthored pack still reports edges in the typed shape.
        assert m["edges"] == [{"a": "l1", "b": "l2", "kind": "path"}]
