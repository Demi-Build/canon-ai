"""Row P0-9 — W1 P5 dialogue: the selector model, ``dialogue_trees`` storage +
the legacy compat shim, scenes, the one evaluator, and the six ``dialogue``
verbs + three ``scene`` verbs (Phase 0 §7; P0 paper P.1.1, P.1.5, P.2, P.3.3;
master §3.1 stage 6).

Fixture: a COPY of the reference dungeon pack (function-scoped — adopt-on-write
creates ``.canon/`` on the copy, never on the checked-in tree). No test calls a
real provider: ``dialogue improve`` runs on ``none`` / ``fake`` only
(doctrine 3).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from canon.dialogue import evaluator, grammar, ops, storage, verbs
from canon.dialogue.improve import dialogue_improve
from canon.packs import resolve_pack
from canon.provenance import all_events, read_object

REPO = Path(__file__).resolve().parents[1]
DUNGEON_FIXTURE = REPO / "tests" / "reference" / "fixtures" / "cradle_mazeworld_scifi"
CANON = [sys.executable, "-m", "canon.cli.main"]

#: A quest-giving NPC (all four legacy keys) and a plain one (only the base).
QUEST_NPC = "1001"
PLAIN_NPC = "1000"


@pytest.fixture
def pack(tmp_path: Path) -> Path:
    target = tmp_path / "dungeon"
    shutil.copytree(DUNGEON_FIXTURE, target)
    return target


def _canon(*args: str) -> tuple[int, object]:
    result = subprocess.run(CANON + list(args), capture_output=True, text=True, cwd=REPO)
    stream = result.stdout if result.returncode == 0 else result.stderr
    try:
        return result.returncode, json.loads(stream)
    except json.JSONDecodeError:
        return result.returncode, stream


def _rows(pack: Path, rel: str) -> list[dict]:
    return json.loads((pack / rel).read_text(encoding="utf-8"))


def _npc(pack: Path, npc_id: str) -> dict:
    return next(r for r in _rows(pack, "npcs/npcs.json") if str(r["id"]) == npc_id)


def _dialogue_spec(pack: Path):
    return resolve_pack(pack).spec.dialogue


def _update(pack: Path, npc_id: str, op_list: list[dict], actor: str = "user") -> dict:
    return verbs.dialogue_update(pack, npc_id, op_list, actor=actor)


# ---------------------------------------------------------------------------
# Storage — the legacy compat shim (P.1.1, P.9 S9)
# ---------------------------------------------------------------------------


def test_legacy_import_maps_four_variants_onto_quest_selectors(pack: Path) -> None:
    spec = _dialogue_spec(pack)
    trees, source = storage.npc_trees(_npc(pack, QUEST_NPC), QUEST_NPC, spec)
    assert source == "legacy"
    assert [t["tree_id"] for t in trees] == [
        f"{QUEST_NPC}:incomplete", f"{QUEST_NPC}:complete", f"{QUEST_NPC}:failed", f"{QUEST_NPC}:default",
    ]
    assert [t["rank"] for t in trees] == [0, 1, 2, 999]
    assert [t["selector"] for t in trees[:3]] == [
        {"rows": ["quest:4000:active"]},
        {"rows": ["quest:4000:completed"]},
        {"rows": ["quest:4000:failed"]},
    ]
    assert trees[3]["selector"] is None  # the fallback
    assert all(t["axis"] == "quest" for t in trees[:3])


def test_plain_npc_imports_as_one_fallback_tree(pack: Path) -> None:
    spec = _dialogue_spec(pack)
    trees, source = storage.npc_trees(_npc(pack, PLAIN_NPC), PLAIN_NPC, spec)
    assert source == "legacy"
    assert len(trees) == 1
    assert trees[0]["selector"] is None


def test_legacy_write_back_is_byte_compatible_with_the_pipeline(pack: Path) -> None:
    """Every one of the reference fixture's 79 NPC rows survives an
    import → write-back round trip with its four legacy keys UNCHANGED — the
    frozen on-disk contract (P.9 S9), compared against the pipeline's own
    emitted rows."""
    spec = _dialogue_spec(pack)
    legacy = list(spec.storage["legacy_fields"])
    rows = _rows(pack, "npcs/npcs.json")
    assert len(rows) == 79
    for row in rows:
        npc_id = str(row["id"])
        before = {key: json.loads(json.dumps(row.get(key))) for key in legacy}
        trees, _source = storage.npc_trees(row, npc_id, spec)
        work = json.loads(json.dumps(row))
        assert storage.write_back(work, trees, spec) == []
        assert {key: work.get(key) for key in legacy} == before, npc_id


def test_write_back_drops_a_slotless_tree_with_a_named_warning(pack: Path) -> None:
    spec = _dialogue_spec(pack)
    row = _npc(pack, PLAIN_NPC)
    trees, _source = storage.npc_trees(row, PLAIN_NPC, spec)
    trees.append({
        **json.loads(json.dumps(trees[0])),
        "tree_id": "night", "rank": 0, "axis": "time",
        "selector": {"rows": ["time:night"]},
    })
    warnings = storage.write_back(row, trees, spec)
    assert any("time" in w and "engine lag" in w for w in warnings)
    assert "dialogue_tree" in row and "dialogue_tree_complete" not in row
    assert len(row["dialogue_trees"]) == 2  # nothing is deleted (doctrine 6)


def test_engine_copy_drops_conditions_and_the_store_keeps_them(pack: Path) -> None:
    """Doctrine 10's asymmetry, asserted: the engine projection is a subset,
    the authoring store is the superset, and nothing reconciles by deleting."""
    result = _update(pack, PLAIN_NPC, [{
        "k": "choice.conditions", "tree": f"{PLAIN_NPC}:default",
        "node_id": "start", "index": 0, "tokens": ["has_item:2000", "time:night"],
    }, {
        "k": "choice.effects", "tree": f"{PLAIN_NPC}:default",
        "node_id": "start", "index": 0, "tokens": ["set_flag:told"],
    }])
    assert not result["no_change"]
    row = _npc(pack, PLAIN_NPC)
    stored = row["dialogue_trees"][0]["nodes"]["start"]["choices"][0]
    assert stored["conditions"] == ["has_item:2000", "time:night"]
    assert stored["effects"] == ["set_flag:told"]
    engine = row["dialogue_tree"]["nodes"]["start"]["choices"][0]
    assert set(engine) == {"text", "next_node_id"}


# ---------------------------------------------------------------------------
# ops — every EditOp kind round-trips
# ---------------------------------------------------------------------------


def _doc(pack: Path, npc_id: str) -> dict:
    spec = _dialogue_spec(pack)
    trees, _source = storage.npc_trees(_npc(pack, npc_id), npc_id, spec)
    return {"character_id": npc_id, "trees": trees}


def test_every_tree_op_kind_round_trips(pack: Path) -> None:
    doc = _doc(pack, QUEST_NPC)
    tree = f"{QUEST_NPC}:default"
    op_list = [
        {"k": "node.add", "tree": tree, "node_id": "vault", "node": {"prompt": "The vault."}},
        {"k": "node.prompt", "tree": tree, "node_id": "vault", "value": "The vault is shut."},
        {"k": "node.speaker", "tree": tree, "node_id": "vault", "value": "1002"},
        {"k": "node.tags", "tree": tree, "node_id": "vault", "tags": ["gate"]},
        {"k": "choice.add", "tree": tree, "node_id": "vault", "index": 0,
         "choice": {"text": "Open it", "next_node_id": None}},
        {"k": "choice.text", "tree": tree, "node_id": "vault", "index": 0, "value": "Open the vault"},
        {"k": "choice.target", "tree": tree, "node_id": "vault", "index": 0, "value": "start"},
        {"k": "choice.conditions", "tree": tree, "node_id": "vault", "index": 0,
         "tokens": ["has_item:2000"]},
        {"k": "choice.effects", "tree": tree, "node_id": "vault", "index": 0,
         "tokens": ["set_flag:opened"]},
        {"k": "tree.entry", "tree": tree, "node_id": "vault"},
        {"k": "tree.add", "tree": "night", "axis": "time", "label": "night vigil"},
        {"k": "tree.selector", "tree": "night", "selector": {"rows": ["time:night"]}},
        {"k": "tree.duplicate", "tree": "night_copy", "from": "night"},
        {"k": "tree.rank", "order": ["night", "night_copy", tree]},
        {"k": "tree.remove", "tree": "night_copy"},
        {"k": "choice.remove", "tree": tree, "node_id": "vault", "index": 0},
        {"k": "node.remove", "tree": tree, "node_id": "sister_story"},
    ]
    assert {op["k"] for op in op_list} == set(ops.TREE_OPS)
    out, details = ops.apply_ops(doc, op_list)
    assert [d["k"] for d in details] == [op["k"] for op in op_list]
    assert all("target" in d for d in details)

    by_id = {t["tree_id"]: t for t in out["trees"]}
    assert "night_copy" not in by_id
    assert by_id["night"]["axis"] == "time"
    assert by_id["night"]["selector"] == {"rows": ["time:night"]}
    assert by_id["night"]["rank"] == 0
    vault = by_id[tree]["nodes"]["vault"]
    assert vault["prompt"] == "The vault is shut."
    assert vault["speaker"] == "1002"
    assert vault["tags"] == ["gate"]
    assert vault["choices"] == []  # choice.remove undid the adds
    assert by_id[tree]["entry_node_id"] == "vault"
    assert "sister_story" not in by_id[tree]["nodes"]
    # node.remove retargets every inbound choice to "end of conversation"
    removed_detail = details[-1]
    assert removed_detail["retargeted_to_end"]
    for node in by_id[tree]["nodes"].values():
        assert all(c["next_node_id"] != "sister_story" for c in node["choices"])


def test_every_scene_op_kind_round_trips(pack: Path) -> None:
    spec = _dialogue_spec(pack)
    from canon.dialogue.scenes import blank_scene

    scene = blank_scene(3900, spec, title="Vault")
    op_list = [
        {"k": "scene.actor.add", "scene": "3900", "character_id": "1001", "required": True},
        {"k": "scene.actor.add", "scene": "3900", "character_id": "1002", "required": True},
        {"k": "scene.actor.required", "scene": "3900", "character_id": "1002", "required": False},
        {"k": "scene.actor.remove", "scene": "3900", "character_id": "1002"},
        {"k": "scene.settings", "scene": "3900", "value": ["time:night"]},
        {"k": "scene.trigger", "scene": "3900", "value": "talk_any_actor"},
        {"k": "scene.once", "scene": "3900", "value": False},
        {"k": "scene.on_finish", "scene": "3900", "value": ["set_flag:met"]},
        {"k": "scene.line.add", "scene": "3900", "n": 1,
         "value": {"k": "line", "speaker": "1001", "text": "One"}},
        {"k": "scene.line.add", "scene": "3900", "n": 2,
         "value": {"k": "line", "speaker": "1001", "text": "Two"}},
        {"k": "scene.line.add", "scene": "3900", "n": 3,
         "value": {"k": "choice", "options": [{"text": "back", "to": 2}]}},
        {"k": "scene.line.text", "scene": "3900", "n": 2, "value": "Two, revised"},
        {"k": "scene.line.speaker", "scene": "3900", "n": 2, "value": None},
        {"k": "scene.line.conditions", "scene": "3900", "n": 2, "value": ["time:night"]},
        {"k": "scene.line.remove", "scene": "3900", "n": 1},
    ]
    assert {op["k"] for op in op_list} == set(ops.SCENE_OPS)
    out, details = ops.apply_scene_ops(scene, op_list)
    assert [d["k"] for d in details] == [op["k"] for op in op_list]
    assert out["actors"] == [{"character_id": "1001", "required": True}]
    assert out["settings"] == ["time:night"]
    assert out["trigger"] == "talk_any_actor"
    assert out["once"] is False
    assert out["on_finish"] == ["set_flag:met"]
    # removing line 1 renumbers 2→1, 3→2 and remaps the choice's `to`
    assert [line["n"] for line in out["lines"]] == [1, 2]
    assert out["lines"][0]["text"] == "Two, revised"
    assert out["lines"][0]["speaker"] is None
    assert out["lines"][0]["conditions"] == ["time:night"]
    assert out["lines"][1]["options"][0]["to"] == 1


def test_an_illegal_op_refuses_the_whole_batch(pack: Path) -> None:
    before = json.loads((pack / "npcs" / "npcs.json").read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="no node 'nope'"):
        _update(pack, PLAIN_NPC, [
            {"k": "node.prompt", "tree": f"{PLAIN_NPC}:default", "node_id": "start", "value": "ok"},
            {"k": "node.prompt", "tree": f"{PLAIN_NPC}:default", "node_id": "nope", "value": "x"},
        ])
    assert json.loads((pack / "npcs" / "npcs.json").read_text(encoding="utf-8")) == before


def test_a_scene_op_sent_to_dialogue_update_names_the_right_verb(pack: Path) -> None:
    with pytest.raises(ValueError, match="use `canon scene update`"):
        _update(pack, PLAIN_NPC, [{"k": "scene.once", "scene": "1", "value": True}])


# ---------------------------------------------------------------------------
# validate — warnings never block (doctrine 10)
# ---------------------------------------------------------------------------


def test_validate_reports_engine_lag_as_a_warning_not_an_error(pack: Path) -> None:
    report = verbs.dialogue_validate(pack, QUEST_NPC)
    assert report["errors"] == []
    assert any("does not evaluate" in w or "outside that" in w for w in report["warnings"])


def test_unreachable_nodes_and_dangling_targets_are_warnings_and_still_save(pack: Path) -> None:
    result = _update(pack, PLAIN_NPC, [
        {"k": "node.add", "tree": f"{PLAIN_NPC}:default", "node_id": "orphan",
         "node": {"prompt": "Nobody comes here."}},
        {"k": "choice.target", "tree": f"{PLAIN_NPC}:default", "node_id": "start",
         "index": 0, "value": "ghost"},
    ])
    assert not result["no_change"]
    report = verbs.dialogue_validate(pack, PLAIN_NPC)
    assert report["errors"] == []
    assert any("unreachable" in w and "orphan" in w for w in report["warnings"])
    assert any("ghost" in w for w in report["warnings"])
    # …and the data is on disk: a warning never blocked the save.
    assert "orphan" in _npc(pack, PLAIN_NPC)["dialogue_trees"][0]["nodes"]


def test_uncoverable_selector_rows_are_warnings(pack: Path) -> None:
    result = _update(pack, PLAIN_NPC, [
        {"k": "tree.add", "tree": "late", "axis": "time"},
        {"k": "tree.rank", "order": [f"{PLAIN_NPC}:default", "late"]},
        {"k": "node.add", "tree": "late", "node_id": "start", "node": {"prompt": "Late."}},
    ])
    assert not result["no_change"]
    report = verbs.dialogue_validate(pack, PLAIN_NPC)
    assert report["errors"] == []
    assert any("can never be selected" in w for w in report["warnings"])


def test_an_unresolved_id_is_an_error_and_blocks(pack: Path) -> None:
    with pytest.raises(ValueError, match="does not resolve"):
        _update(pack, PLAIN_NPC, [{
            "k": "choice.conditions", "tree": f"{PLAIN_NPC}:default", "node_id": "start",
            "index": 0, "tokens": ["has_item:99999"],
        }])


def test_a_scene_only_namespace_in_a_tree_is_refused_with_the_reason(pack: Path) -> None:
    with pytest.raises(ValueError, match="legal only in scene scope"):
        _update(pack, PLAIN_NPC, [{
            "k": "choice.conditions", "tree": f"{PLAIN_NPC}:default", "node_id": "start",
            "index": 0, "tokens": ["actor:1002:present"],
        }])


def test_an_unknown_namespace_is_a_named_error_not_a_crash(pack: Path) -> None:
    with pytest.raises(ValueError, match="unknown condition namespace 'weather'"):
        _update(pack, PLAIN_NPC, [{
            "k": "choice.conditions", "tree": f"{PLAIN_NPC}:default", "node_id": "start",
            "index": 0, "tokens": ["weather:rain"],
        }])


# ---------------------------------------------------------------------------
# test — one evaluator, every namespace, the failing condition NAMED
# ---------------------------------------------------------------------------


NAMESPACE_CASES = [
    ("has_item:2000", {"inventory": {"2000": 1}}, {"inventory": {}}, "not in inventory"),
    ("quest:4000:completed", {"quests": {"4000": "completed"}}, {"quests": {"4000": "active"}},
     "quest is active, not completed"),
    ("time:night", {"clock": {"period": "night"}}, {"clock": {"period": "day"}},
     "period is day, not night"),
    ("player:health:>=:10", {"player": {"health": 12}}, {"player": {"health": 3}},
     "player.health is 3, not >= 10"),
    ("flag:seen", {"flags": {"seen": True}}, {"flags": {"seen": False}}, "flag is unset"),
    ("flag:seen:false", {"flags": {"seen": False}}, {"flags": {"seen": True}}, "flag is true, not false"),
    ("segment:act2", {"segment": "act2"}, {"segment": "act1"}, "segment is 'act1', not 'act2'"),
    ("room:room_1", {"room": "room_1"}, {"room": "room_2"}, "room is 'room_2', not 'room_1'"),
    ("scene:3000:seen", {"scenes_seen": ["3000"]}, {"scenes_seen": []}, "scene is unseen, not seen"),
    ("event:3000:solved", {"events": {"3000": "solved"}}, {"events": {"3000": "unsolved"}},
     "event is unsolved, not solved"),
]


@pytest.mark.parametrize(("token", "passing", "failing", "reason"), NAMESPACE_CASES)
def test_every_namespace_evaluates_and_names_its_failing_condition(
    pack: Path, token: str, passing: dict, failing: dict, reason: str
) -> None:
    tree = {
        "tree_id": "t", "character_id": PLAIN_NPC, "entry_node_id": "start",
        "nodes": {"start": {"node_id": "start", "prompt": "?", "choices": [
            {"text": "gated", "next_node_id": None, "conditions": [token], "effects": []},
        ]}},
    }
    ok = verbs.dialogue_test(tree, passing, pack_dir=pack)
    assert ok["choices"][0]["pass"] is True
    assert ok["choices"][0]["failing_condition"] is None

    bad = verbs.dialogue_test(tree, failing, pack_dir=pack)
    choice = bad["choices"][0]
    assert choice["pass"] is False
    assert choice["failing_condition"] == token
    assert reason in choice["failing_reason"]
    # every dungeon gate is amber at tree scope today (P.2.4's empty block)
    assert choice["conditions"][0]["verdict"] == "unevaluable"
    assert choice["conditions"][0]["engine_reason"]


def test_the_scene_only_namespace_evaluates_in_scene_scope(pack: Path) -> None:
    result = evaluator.evaluate_condition(
        "actor:1001:absent", evaluator.normalize_state({"actors": {"1001": "present"}}),
        scope="scene", spec=_dialogue_spec(pack),
    )
    assert result["pass"] is False
    assert "actor is present, not absent" in result["reason"]


def test_test_takes_the_unsaved_buffer_and_fires_effects(pack: Path) -> None:
    """The tester tests a PAYLOAD, never a pack lookup (`PLAN.md:256`)."""
    tree = {
        "tree_id": "unsaved", "character_id": PLAIN_NPC, "entry_node_id": "start",
        "nodes": {
            "start": {"node_id": "start", "prompt": "?", "choices": [
                {"text": "take", "next_node_id": "end",
                 "conditions": ["has_item:2000", "time:night"],
                 "effects": ["takes_item:2000", "gives_quest:4000", "set_flag:vault"]},
            ]},
            "end": {"node_id": "end", "prompt": "done", "choices": []},
        },
    }
    state = {"inventory": {"2000": 1}, "clock": {"period": "night"}}
    blocked = verbs.dialogue_test(tree, {"clock": {"period": "night"}}, pack_dir=pack, choose=0)
    assert "blocked" in blocked["refused"]

    walked = verbs.dialogue_test(tree, state, pack_dir=pack, choose=0)
    assert walked["choices"][0]["pass"] is True
    assert walked["next_node_id"] == "end"
    assert walked["post_effect_state"]["inventory"] == {}
    assert walked["post_effect_state"]["quests"] == {"4000": "active"}
    assert walked["post_effect_state"]["flags"] == {"vault": True}
    assert [f["applied"] for f in walked["fired"]] == [True, True, True]


def test_a_buffer_whose_nodes_omit_node_id_still_walks(pack: Path) -> None:
    """The nodes map's KEY names the node — a hand-written buffer never comes
    back with empty ids."""
    tree = {
        "tree_id": "buf", "character_id": PLAIN_NPC, "entry_node_id": "start",
        "nodes": {"start": {"prompt": "hi", "choices": [{"text": "bye"}]}},
    }
    walked = verbs.dialogue_test(tree, {}, pack_dir=pack)
    assert walked["node"]["node_id"] == "start"
    assert walked["choices"][0]["pass"] is True


def test_advance_quest_uses_the_packs_own_state_order(pack: Path) -> None:
    spec = _dialogue_spec(pack)
    post, fired = evaluator.apply_effects(
        ["advance_quest:4000"], evaluator.normalize_state({"quests": {"4000": "active"}}), spec=spec
    )
    assert post["quests"]["4000"] == "completed"
    assert fired[0]["detail"] == "quest 4000: active → completed"
    post, _fired = evaluator.apply_effects(
        ["advance_quest:4000:failed"], evaluator.normalize_state({}), spec=spec
    )
    assert post["quests"]["4000"] == "failed"


def test_token_arity_follows_the_registry_descriptor(pack: Path) -> None:
    spec = _dialogue_spec(pack)
    assert grammar.parse_token("flag:x", spec=spec).slots == {"key": "x"}
    assert grammar.parse_token("flag:x:true", spec=spec).slots == {"key": "x", "value": "true"}
    with pytest.raises(grammar.TokenError, match="quest takes 2 operand"):
        grammar.parse_token("quest:4000", spec=spec)
    with pytest.raises(grammar.TokenError, match="not in this pack's vocabulary"):
        grammar.parse_token("quest:4000:offered", spec=spec)
    with pytest.raises(grammar.TokenError, match="unknown effect 'explodes'"):
        grammar.parse_effect("explodes:1", spec=spec)


# ---------------------------------------------------------------------------
# select — every rejection explained
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"quests": {"4000": "active"}}, f"{QUEST_NPC}:incomplete"),
        ({"quests": {"4000": "completed"}}, f"{QUEST_NPC}:complete"),
        ({"quests": {"4000": "failed"}}, f"{QUEST_NPC}:failed"),
        ({}, f"{QUEST_NPC}:default"),
    ],
)
def test_select_picks_the_right_tree_and_explains_every_rejection(
    pack: Path, state: dict, expected: str
) -> None:
    result = verbs.dialogue_select(pack, QUEST_NPC, state)
    assert result["selected"] == expected
    assert len(result["trees"]) == 4
    for row in result["trees"]:
        if row["tree_id"] == expected:
            assert row["status"] == "selected" and row["would_play"] is True
        else:
            assert row["status"] in ("blocked", "shadowed")
            assert row["why_not"], row["tree_id"]


def test_select_reports_the_selector_level_engine_lag(pack: Path) -> None:
    """The engine evaluates ``quest`` at selector scope only for
    completed/failed (P.2.4), so on an ACTIVE quest it falls through while
    the tester picks the true tree — loud, never blocking."""
    active = verbs.dialogue_select(pack, QUEST_NPC, {"quests": {"4000": "active"}})
    assert active["selected"] == f"{QUEST_NPC}:incomplete"
    assert active["engine"]["selected"] == f"{QUEST_NPC}:default"
    assert active["engine"]["legacy_slot"] == "dialogue_tree"
    assert active["engine"]["diverges"] is True
    assert "falls through" in active["engine"]["reason"]

    done = verbs.dialogue_select(pack, QUEST_NPC, {"quests": {"4000": "completed"}})
    assert done["engine"]["diverges"] is False
    assert done["engine"]["legacy_slot"] == "dialogue_tree_complete"


# ---------------------------------------------------------------------------
# scenes — events.json, type "scene", never event_positions (P.9 S7)
# ---------------------------------------------------------------------------


def _placed_event_ids(pack: Path) -> set[str]:
    out: set[str] = set()
    for maze in pack.glob("rooms/*/maze.json"):
        data = json.loads(maze.read_text(encoding="utf-8"))
        for entry in data.get("event_positions") or []:
            out.add(str(entry.get("event_id")))
    return out


def test_a_scene_lands_in_events_json_and_never_in_event_positions(pack: Path) -> None:
    before_positions = _placed_event_ids(pack)
    result = verbs.scene_update(pack, None, [
        {"k": "scene.actor.add", "scene": "x", "character_id": "1001", "required": True},
        {"k": "scene.line.add", "scene": "x", "n": 1,
         "value": {"k": "line", "speaker": "1001", "text": "You came."}},
    ], create=True, title="The vault meeting")
    scene_id = result["scene"]
    assert result["created"] is True
    assert int(scene_id) >= 3000  # the shared event id space

    rows = _rows(pack, "events/events.json")
    row = next(r for r in rows if str(r["id"]) == str(scene_id))
    assert row["type"] == "scene"
    # the engine loads EVERY event row through a model that requires these
    assert row["name"] and "description" in row
    assert _placed_event_ids(pack) == before_positions
    assert str(scene_id) not in _placed_event_ids(pack)


def test_scene_validate_and_walk_name_every_skip(pack: Path) -> None:
    result = verbs.scene_update(pack, None, [
        {"k": "scene.actor.add", "scene": "x", "character_id": "1001", "required": True},
        {"k": "scene.actor.add", "scene": "x", "character_id": "1002", "required": False},
        {"k": "scene.settings", "scene": "x", "value": ["time:night"]},
        {"k": "scene.on_finish", "scene": "x", "value": ["set_flag:met"]},
        {"k": "scene.line.add", "scene": "x", "n": 1,
         "value": {"k": "line", "speaker": "1001", "text": "You came."}},
        {"k": "scene.line.add", "scene": "x", "n": 2,
         "value": {"k": "line", "speaker": "1002", "text": "I said she would."}},
    ], create=True, title="Vault")
    scene_id = result["scene"]

    report = verbs.scene_validate(pack, scene_id)
    assert report["errors"] == []
    assert any("does not evaluate" in w for w in report["warnings"])

    scene = verbs.load_scene(pack, scene_id)
    played = verbs.scene_test(
        scene, {"clock": {"period": "night"}, "actors": {"1001": "present", "1002": "absent"}},
        pack_dir=pack,
    )
    assert played["plays"] is True
    assert played["transcript"][0]["played"] is True
    assert played["transcript"][1]["played"] is False
    assert "1002 is absent" in played["transcript"][1]["skipped_because"]
    assert played["post_effect_state"]["flags"] == {"met": True}

    cancelled = verbs.scene_test(scene, {"clock": {"period": "night"}, "actors": {}}, pack_dir=pack)
    assert cancelled["plays"] is False
    assert "1001" in cancelled["blocked_by"]

    gated = verbs.scene_test(
        scene, {"clock": {"period": "day"}, "actors": {"1001": "present"}}, pack_dir=pack
    )
    assert gated["plays"] is False
    assert "time:night" in gated["blocked_by"]


def test_creating_an_empty_scene_still_writes_the_row(pack: Path) -> None:
    """A fresh row compares against nothing, so the write core never mistakes
    a created scene with no edits for a no-op."""
    result = verbs.scene_update(pack, None, [], create=True, title="Empty")
    assert result["created"] is True and result["no_change"] is False
    row = next(
        r for r in _rows(pack, "events/events.json") if str(r["id"]) == str(result["scene"])
    )
    assert row["type"] == "scene" and row["title"] == "Empty" and row["lines"] == []


def test_scene_update_refuses_a_non_scene_event_row(pack: Path) -> None:
    with pytest.raises(ValueError, match="`db update` owns that row"):
        verbs.scene_update(pack, "3000", [{"k": "scene.once", "scene": "3000", "value": False}])


def test_db_update_refuses_every_dialogue_and_scene_field(pack: Path) -> None:
    """P.1.1 / P.1.5 route these fields to the dialogue verbs; `db update`
    must refuse them naming the owning surface."""
    from canon.db_ops import update_db_row

    for field in ("dialogue_tree", "dialogue_tree_complete", "dialogue_trees"):
        with pytest.raises(ValueError, match="owned by dialogue"):
            update_db_row(pack, "npc", PLAIN_NPC, {field: {}})
    for field in ("title", "actors", "settings", "trigger", "once", "on_finish", "lines"):
        with pytest.raises(ValueError, match="owned by scene"):
            update_db_row(pack, "event", "3000", {field: []})


# ---------------------------------------------------------------------------
# improve — a proposal, never a write (§7.2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend", ["none", "fake"])
def test_improve_returns_a_proposal_and_writes_nothing(pack: Path, backend: str) -> None:
    _update(pack, PLAIN_NPC, [
        {"k": "node.prompt", "tree": f"{PLAIN_NPC}:default", "node_id": "start",
         "value": "  Hello   there  "},
    ])
    before = (pack / "npcs" / "npcs.json").read_bytes()
    events_before = len(all_events(pack))

    result = dialogue_improve(
        pack, PLAIN_NPC, instruction="tighten", backend=backend, actor="agent:c1/writer"
    )
    assert result["wrote"] is False
    assert result["cost"] == {"usd": 0.0, "paid": False}
    assert result["requested_by"] == "agent:c1/writer"
    assert "no chat backend selected" in result["backend_note"]
    rows = result["proposal"]["rows"]
    assert rows and result["proposal"]["count"] == len(rows)
    fixed = next(r for r in rows if r["target"].endswith("node:start"))
    assert fixed["before"] == "  Hello   there  "
    assert fixed["after"] == "Hello there."
    assert fixed["field"] == "prompt"
    # deterministic: the same call twice gives the same proposal
    assert dialogue_improve(pack, PLAIN_NPC, backend=backend)["proposal"] == result["proposal"]
    assert (pack / "npcs" / "npcs.json").read_bytes() == before
    assert len(all_events(pack)) == events_before


# ---------------------------------------------------------------------------
# doctrine 1 — journal, per-field diff, CAS, restore
# ---------------------------------------------------------------------------


def test_a_save_journals_a_per_field_diff_per_op_and_restores(pack: Path) -> None:
    original = (pack / "npcs" / "npcs.json").read_bytes()
    result = _update(pack, PLAIN_NPC, [
        {"k": "node.prompt", "tree": f"{PLAIN_NPC}:default", "node_id": "start", "value": "Rewritten."},
        {"k": "choice.text", "tree": f"{PLAIN_NPC}:default", "node_id": "start", "index": 0,
         "value": "Rewritten choice."},
    ], actor="agent:conv-1/writer")

    events = [e for e in all_events(pack) if e["artifact_id"] == f"npc:{PLAIN_NPC}"]
    assert len(events) == 1
    event = events[0]
    assert event["op"] == "edit"
    assert event["actor"] == "agent:conv-1/writer"
    detail = event["detail"]
    assert detail["kind"] == "dialogue_update"
    # the per-key file diff …
    assert "dialogue_trees" in detail["changed"] and "dialogue_tree" in detail["changed"]
    # … and the per-OP field diff the design asks for
    assert [op["k"] for op in detail["ops"]] == ["node.prompt", "choice.text"]
    assert detail["ops"][0]["changed"]["prompt"]["to"] == "Rewritten."
    assert detail["ops"][1]["changed"]["text"]["to"] == "Rewritten choice."

    # restore: the pre-write bytes are in the CAS under before_hash
    assert event["before_hash"] == result["before_hash"]
    assert read_object(pack, event["before_hash"]) == original
    assert read_object(pack, event["after_hash"]) == (pack / "npcs" / "npcs.json").read_bytes()


def test_a_no_op_save_writes_and_journals_nothing(pack: Path) -> None:
    # The FIRST save on a legacy NPC materializes `dialogue_trees` — that is a
    # real diff (the read-both shim upgrading on write), so the no-op rule is
    # asserted on the second save.
    first = _update(pack, PLAIN_NPC, [
        {"k": "node.prompt", "tree": f"{PLAIN_NPC}:default", "node_id": "start", "value": "Once."},
    ])
    assert first["no_change"] is False
    assert list(first["changed"]) == ["dialogue_trees", "dialogue_tree"]

    before = (pack / "npcs" / "npcs.json").read_bytes()
    events_before = len(all_events(pack))
    again = _update(pack, PLAIN_NPC, [
        {"k": "node.prompt", "tree": f"{PLAIN_NPC}:default", "node_id": "start", "value": "Once."},
    ])
    assert again["no_change"] is True
    assert (pack / "npcs" / "npcs.json").read_bytes() == before
    assert len(all_events(pack)) == events_before


def test_a_scene_save_journals_on_the_event_artifact(pack: Path) -> None:
    result = verbs.scene_update(pack, None, [
        {"k": "scene.line.add", "scene": "x", "n": 1, "value": {"k": "line", "text": "Hi"}},
    ], create=True, title="Vault", actor="user")
    events = [e for e in all_events(pack) if e["artifact_id"] == f"event:{result['scene']}"]
    assert len(events) == 1
    assert events[0]["op"] == "create"
    assert events[0]["detail"]["kind"] == "scene_update"
    assert events[0]["detail"]["ops"][0]["k"] == "scene.line.add"
    assert "lines" in events[0]["detail"]["changed"]


def test_read_verbs_write_nothing(pack: Path) -> None:
    before = {p: p.read_bytes() for p in pack.rglob("*") if p.is_file()}
    verbs.dialogue_show(pack, QUEST_NPC)
    verbs.dialogue_validate(pack, QUEST_NPC)
    verbs.dialogue_select(pack, QUEST_NPC, {"quests": {"4000": "active"}})
    verbs.dialogue_test(verbs.load_tree(pack, QUEST_NPC), {}, pack_dir=pack)
    assert {p: p.read_bytes() for p in pack.rglob("*") if p.is_file()} == before
    assert not (pack / ".canon").exists()


# ---------------------------------------------------------------------------
# The CLI surface (JSON-emitting, --actor on every write)
# ---------------------------------------------------------------------------


def test_cli_round_trip(pack: Path) -> None:
    code, shown = _canon("dialogue", "show", str(pack), "--npc", QUEST_NPC)
    assert code == 0 and len(shown["trees"]) == 4

    code, saved = _canon(
        "dialogue", "update", str(pack), "--npc", QUEST_NPC, "--actor", "user",
        "--ops", json.dumps([
            {"k": "node.add", "tree": f"{QUEST_NPC}:incomplete", "node_id": "vault",
             "node": {"prompt": "The vault is shut."}},
            {"k": "choice.add", "tree": f"{QUEST_NPC}:incomplete", "node_id": "start", "index": 0,
             "choice": {"text": "Open the vault.", "next_node_id": "vault",
                        "conditions": ["has_item:2000", "time:night"],
                        "effects": ["set_flag:vault_opened"]}},
        ]),
    )
    assert code == 0 and saved["no_change"] is False

    code, tested = _canon(
        "dialogue", "test", str(pack), "--npc", QUEST_NPC, "--tree-id", f"{QUEST_NPC}:incomplete",
        "--state", json.dumps({"clock": {"period": "day"}}),
    )
    assert code == 0
    assert tested["choices"][0]["failing_condition"] == "has_item:2000"

    code, selected = _canon(
        "dialogue", "select", str(pack), "--npc", QUEST_NPC,
        "--state", json.dumps({"quests": {"4000": "active"}}),
    )
    assert code == 0 and selected["selected"] == f"{QUEST_NPC}:incomplete"

    code, report = _canon("dialogue", "validate", str(pack), "--npc", QUEST_NPC)
    assert code == 0 and report["errors"] == []

    code, refused = _canon(
        "dialogue", "update", str(pack), "--npc", QUEST_NPC,
        "--ops", json.dumps([{"k": "node.prompt", "tree": "nope", "node_id": "x", "value": "y"}]),
    )
    assert code == 1 and "no tree 'nope'" in refused["error"]


# ---------------------------------------------------------------------------
# The legacy layout: a reader gap may never refuse a legal token (doctrine 10)
# ---------------------------------------------------------------------------


def test_a_room_token_authors_on_a_tree_that_has_no_rooms_index(pack: Path) -> None:
    """`room:` is one of the ten namespaces and a seeded selector axis, and
    the reference fixture (like both demos) predates `rooms/rooms.json`. Read
    through `load_rows` alone the room table came back EMPTY, and `_unresolved`
    then refused every `room:` token fail-closed. The operand tables now fall
    back to the layout's `row_source` mirror — the same read-both shim
    `db update --type room` resolves a row through."""
    assert not (pack / "rooms" / "rooms.json").exists()
    resolved = resolve_pack(pack)
    tables = verbs.operand_tables(pack, resolved.spec, resolved.spec.dialogue)
    bible = json.loads((pack / "world_bible.json").read_text(encoding="utf-8"))["rooms"]
    assert tables["room"] == set(bible), "the bible mirror stood in as the table"

    result = _update(pack, PLAIN_NPC, [
        {"k": "choice.conditions", "tree": f"{PLAIN_NPC}:default", "node_id": "start",
         "index": 0, "tokens": ["room:room_0"]},
    ])
    assert result["no_change"] is False
    row = _npc(pack, PLAIN_NPC)
    stored = row["dialogue_trees"][0]["nodes"]["start"]["choices"][0]
    assert stored["conditions"] == ["room:room_0"]
    # doctrine 10: legal, kept, and reported as engine lag — never refused
    assert any("does not evaluate 'room'" in w for w in result["warnings"])


def test_an_unreadable_operand_source_warns_at_worst_and_never_refuses(pack: Path) -> None:
    """`{}` ("read, genuinely empty") and `None` ("nothing to read") are
    different answers: a kind with NO readable source drops out of the tables
    entirely, so `_unresolved` says nothing rather than "does not resolve"."""
    resolved = resolve_pack(pack)
    spec = resolved.spec
    assert verbs._rows_of(pack, spec, "no_such_kind") is None
    (pack / "world_bible.json").unlink()
    assert verbs._rows_of(pack, spec, "room") is None
    assert "room" not in verbs.operand_tables(pack, spec, spec.dialogue)
    assert verbs._unresolved({"entity_id": "room_0"}, "room", {}) is None


def test_the_s7_guard_sees_a_planted_event_position(pack: Path) -> None:
    """`_event_positions` joined the grid path through the room ROWS, so it
    was empty on every tree that ships (no index; and the bible rows carry
    `maze_ref: ""` and no `id`, giving `rooms/None/maze.json`). The grids are
    globbed by `tools_read.grid_ids` now, so the invariant actually fires."""
    result = verbs.scene_update(pack, None, [
        {"k": "scene.actor.add", "scene": "x", "character_id": "1001", "required": True},
    ], create=True, title="Vault")
    scene_id = str(result["scene"])
    assert scene_id in {str(i) for i in range(3000, 100000)}

    maze_path = next(pack.glob("rooms/*/maze.json"))
    maze = json.loads(maze_path.read_text(encoding="utf-8"))
    maze["event_positions"].append({"x": 1, "y": 1, "event_id": int(scene_id)})
    maze_path.write_text(json.dumps(maze), encoding="utf-8")

    resolved = resolve_pack(pack)
    assert scene_id in verbs._event_positions(pack, resolved.spec)
    report = verbs.scene_validate(pack, scene_id)
    assert any("event_positions entry" in e for e in report["errors"])
    with pytest.raises(ValueError, match="event_positions entry"):
        verbs.scene_update(pack, scene_id, [{"k": "scene.once", "scene": scene_id, "value": False}])


def test_a_scene_gate_on_a_non_actor_warns(pack: Path) -> None:
    """P.2.1 restricts `actor:` to the scene's own `actors[]`, and the
    descriptor says so (`restrict_to: "scene.actors"`) — a slot nothing read,
    so a gate naming an NPC the scene does not cast passed silently and could
    never be satisfied. A WARNING, never an error (doctrine 10)."""
    result = verbs.scene_update(pack, None, [
        {"k": "scene.actor.add", "scene": "x", "character_id": "1001", "required": True},
        {"k": "scene.line.add", "scene": "x", "n": 1,
         "value": {"k": "line", "speaker": "1001", "text": "You came."}},
        {"k": "scene.line.conditions", "scene": "x", "n": 1, "value": ["actor:1002:present"]},
    ], create=True, title="Vault")
    scene_id = result["scene"]
    assert result["no_change"] is False, "the save happened"

    report = verbs.scene_validate(pack, scene_id)
    assert report["errors"] == []
    assert any(
        "1002 is not an actor of this scene" in w and "never pass" in w
        for w in report["warnings"]
    ), report["warnings"]
    # the same token on an actor the scene DOES cast says nothing
    ok = verbs.scene_update(pack, scene_id, [
        {"k": "scene.line.conditions", "scene": str(scene_id), "n": 1,
         "value": ["actor:1001:present"]},
    ])
    assert not any("is not an actor of this scene" in w for w in ok["warnings"])


def test_a_quest_giver_save_names_the_tree_the_engine_actually_plays(pack: Path) -> None:
    """A generated quest-giver imports FOUR trees because `dialogue_tree` is
    the pipeline's own duplicate of `dialogue_tree_incomplete`. The engine has
    no fallback slot: it plays `dialogue_tree` until the quest resolves. So
    the moment the two slots diverge, an edit to the residual tree is not what
    the player sees — engine lag, named at the point of the save."""
    row = _npc(pack, QUEST_NPC)
    assert row["dialogue_tree"] == row["dialogue_tree_incomplete"], "pipeline duplicate"

    result = _update(pack, QUEST_NPC, [
        {"k": "node.prompt", "tree": f"{QUEST_NPC}:incomplete", "node_id": "start",
         "value": "The vault is shut."},
    ])
    lag = [w for w in result["warnings"] if "until the quest resolves" in w]
    assert len(lag) == 1, result["warnings"]
    assert f"'{QUEST_NPC}:incomplete'" in lag[0] and "dialogue_tree_incomplete" in lag[0]
    assert f"'{QUEST_NPC}:default'" in lag[0]
    # nothing was reconciled behind the author's back (doctrine 6)
    row = _npc(pack, QUEST_NPC)
    assert row["dialogue_tree_incomplete"]["nodes"]["start"]["prompt"] == "The vault is shut."
    assert row["dialogue_tree"]["nodes"]["start"]["prompt"] != "The vault is shut."
    # and `validate` says it too, once
    report = verbs.dialogue_validate(pack, QUEST_NPC)
    assert len([w for w in report["warnings"] if "until the quest resolves" in w]) == 1


def test_load_tree_defaults_to_the_first_tree_in_rank_order(pack: Path) -> None:
    """The docstring used to promise "the selected one"; with no state there
    is nothing to select against, so the default is rank order — and
    `dialogue select` is the verb that answers the state question."""
    assert verbs.load_tree(pack, QUEST_NPC)["tree_id"] == f"{QUEST_NPC}:incomplete"
    selected = verbs.dialogue_select(pack, QUEST_NPC, {"quests": {"4000": "not_started"}})
    assert selected["selected"] == f"{QUEST_NPC}:default"


def test_improve_on_a_registered_chat_backend_clamps_and_writes_nothing(pack: Path) -> None:
    """The paid leg at $0: a fake registered under a NON-reserved id reaches
    `_provider_rows` (`"fake"` short-circuits into the deterministic pass by
    design). Doctrine 3 holds — no provider is called."""
    from canon.backends import BackendRegistry, FakeChatBackend

    try:
        proposals = json.dumps([
            {"tree": f"{PLAIN_NPC}:default", "node_id": "start", "field": "prompt",
             "choice": None, "after": "Rewritten by the model.", "why": "tighter"},
            {"tree": f"{PLAIN_NPC}:default", "node_id": "no_such_node", "field": "prompt",
             "choice": None, "after": "dropped", "why": "names a node the tree lacks"},
            {"tree": f"{PLAIN_NPC}:default", "node_id": "start", "field": "text",
             "choice": 99, "after": "dropped", "why": "choice index out of range"},
        ])
        BackendRegistry.reset()
        BackendRegistry.register_chat(
            "scripted",
            lambda: FakeChatBackend([[{"type": "text", "text": f"here you go {proposals}"}]]),
        )
        before = (pack / "npcs" / "npcs.json").read_bytes()
        result = dialogue_improve(pack, PLAIN_NPC, instruction="tighten", backend="scripted")
        assert result["wrote"] is False
        assert result["cost"]["paid"] is True
        rows = result["proposal"]["rows"]
        assert [r["node_id"] for r in rows] == ["start"], "absent node / bad index dropped"
        assert rows[0]["after"] == "Rewritten by the model."
        assert rows[0]["field"] == "prompt" and rows[0]["before"] != rows[0]["after"]
        assert (pack / "npcs" / "npcs.json").read_bytes() == before
    finally:
        BackendRegistry.reset()
