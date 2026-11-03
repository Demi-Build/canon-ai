"""The animation vocabulary + ladder as data.

The load-bearing test is :class:`TestLadderParity`: the data-driven ladder must
pick exactly what the hand-written one picked, for EVERY combination of the
signals that feed it. A rule table that is subtly different from the code it
replaces is worse than no rule table at all, and `PLAT_TRAJ` cannot catch it
(it is position-only — animation selection is invisible to it).
"""

from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from canon.packs.platformer.animation_spec import (  # noqa: E402
    DEFAULT_SETS,
    DEFAULT_STATES,
    ENEMY_SIGNALS,
    PLAYER_SIGNALS,
    eval_ladder,
    load_spec,
    states_for,
    validate_spec,
)
from canon.packs.platformer.play import (  # noqa: E402
    enemy_anim_candidates,
    player_anim_candidates,
)

# --- The ORACLE ---------------------------------------------------------------
# The hand-written ladders exactly as they read before they became data. The
# shipped functions now delegate to `eval_ladder`, so comparing them against
# each other would be tautological — this frozen copy is what makes the parity
# assertion mean something. Do NOT "simplify" it to call the real code.


def _legacy_player(on_ground, vy, dx, vx, land_t):
    sign = lambda v: 1.0 if v > 0 else (-1.0 if v < 0 else 0.0)  # noqa: E731
    if not on_ground:
        cand = ["fall", "jump"] if vy > 0 else ["jump"]
        why = "airborne · past the peak" if vy > 0 else "airborne · rising"
    else:
        skid = bool(dx and sign(vx) == -dx and abs(vx) > 0.5)
        cand = (
            (["skid"] if skid else [])
            + (["land"] if land_t > 0.0 else [])
            + (["walk"] if dx else [])
        )
        why = (
            "grounded · braking against momentum"
            if skid
            else "grounded · touchdown window"
            if land_t > 0.0
            else "grounded · moving"
            if dx
            else "grounded · at rest"
        )
    return cand + ["idle", "walk"], why


def _legacy_enemy(alive, hurt_t, grounded, moving):
    if not alive:
        return ["death", "hurt", "idle"], "defeated"
    cand = (
        (["hurt"] if hurt_t > 0 else [])
        + (["jump"] if not grounded else [])
        + (["walk"] if moving else [])
        + ["idle", "walk", "hurt", "death"]
    )
    why = (
        "recoiling from a hit"
        if hurt_t > 0
        else "airborne (mid-hop)"
        if not grounded
        else "moving along its path"
        if moving
        else "holding position"
    )
    return cand, why


def _dedup(seq):
    """The hand-written ladders appended their tail without deduping, so a
    state could appear twice. A duplicate later in the list is UNREACHABLE
    (selection takes the first candidate that exists), so comparing deduped
    lists compares what actually matters."""
    out = []
    for s in seq:
        if s not in out:
            out.append(s)
    return out


class TestLadderParity:
    """The data ladder == the code ladder, exhaustively."""

    @pytest.mark.parametrize(
        "on_ground,vy,dx,vx,land_t",
        list(
            itertools.product(
                [True, False],          # on_ground
                [-6.0, 0.0, 5.0],       # vy: rising / neutral / descending
                [-1, 0, 1],             # dx: input direction
                [-4.0, 0.0, 4.0],       # vx: carried momentum
                [0.0, 0.12],            # land_t: touchdown window
            )
        ),
    )
    def test_player_matches_code_ladder(self, on_ground, vy, dx, vx, land_t):
        want_cand, want_why = _legacy_player(on_ground, vy, dx, vx, land_t)
        spec = load_spec(None)
        # The engines derive these named booleans; the rule table only ever
        # tests names, never recomputes gameplay.
        signals = {
            "on_ground": on_ground,
            "descending": vy > 0,
            "moving": dx != 0,
            "braking": bool(dx and (1 if vx > 0 else -1 if vx < 0 else 0) == -dx and abs(vx) > 0.5),
            "landing": land_t > 0.0,
            "submerged": False,
            "vy": vy,
            "vx": vx,
        }
        got_cand, got_why = eval_ladder(
            spec["ladders"]["player"], signals, spec["tails"]["player"],
            spec["why"]["player"],
        )
        assert got_cand == _dedup(want_cand), (on_ground, vy, dx, vx, land_t)
        assert got_why == want_why
        # And the SHIPPED entry point, which is what the game actually calls.
        ship_cand, ship_why = player_anim_candidates(on_ground, vy, dx, vx, land_t)
        assert ship_cand == _dedup(want_cand)
        assert ship_why == want_why

    @pytest.mark.parametrize(
        "alive,hurt_t,grounded,moving",
        list(itertools.product([True, False], [0.0, 0.2], [True, False], [True, False])),
    )
    def test_enemy_matches_code_ladder(self, alive, hurt_t, grounded, moving):
        want_cand, want_why = _legacy_enemy(alive, hurt_t, grounded, moving)
        spec = load_spec(None)
        signals = {
            "alive": alive,
            "hurt": hurt_t > 0,
            "grounded": grounded,
            "moving": moving,
            "submerged": False,
        }
        got_cand, got_why = eval_ladder(
            spec["ladders"]["enemy"], signals, spec["tails"]["enemy"],
            spec["why"]["enemy"],
        )
        assert got_cand == _dedup(want_cand), (alive, hurt_t, grounded, moving)
        assert got_why == want_why
        ship_cand, ship_why = enemy_anim_candidates(alive, hurt_t, grounded, moving)
        assert ship_cand == _dedup(want_cand)
        assert ship_why == want_why


class TestDefaultsUnchanged:
    """A pack with no animation.json must generate exactly what it did before
    the vocabulary became data — adding a state to a default set changes what
    every future run generates AND bills."""

    def test_state_sets_match_the_old_constants(self):
        spec = load_spec(None)
        assert states_for(spec, "player") == [
            "idle", "walk", "jump", "fall", "land", "skid"
        ]
        assert states_for(spec, "enemy") == ["idle", "walk", "hurt", "death"]
        # The old enemy_animation_states special case.
        assert states_for(spec, "enemy", "hopper") == [
            "idle", "walk", "hurt", "death", "jump"
        ]
        assert states_for(spec, "enemy", "patroller") == ["idle", "walk", "hurt", "death"]

    def test_swim_is_defined_but_inert(self):
        # Wired into the vocabulary and both ladders, in NO default set: it
        # costs nothing until a pack opts in.
        spec = load_spec(None)
        assert "swim" in spec["states"]
        assert any(r["state"] == "swim" for r in spec["ladders"]["player"])
        for key in DEFAULT_SETS:
            assert "swim" not in DEFAULT_SETS[key], key

    def test_every_default_state_has_a_full_entry(self):
        for name, entry in DEFAULT_STATES.items():
            assert entry.get("brief"), name
            assert isinstance(entry.get("frames"), int), name
            assert entry.get("loop") in ("loop", "once", "ping_pong"), name


class TestPackOverride:
    """The pack-local override — same idiom as schemas/<type>.json."""

    def _pack(self, tmp_path: Path, spec: dict) -> Path:
        (tmp_path / "animation.json").write_text(json.dumps(spec))
        return tmp_path

    def test_missing_file_yields_defaults(self, tmp_path):
        assert load_spec(tmp_path)["sets"] == load_spec(None)["sets"]

    def test_unreadable_file_falls_back_rather_than_crashing(self, tmp_path):
        (tmp_path / "animation.json").write_text("{not json")
        assert load_spec(tmp_path)["sets"] == load_spec(None)["sets"]

    def test_a_pack_can_opt_into_swim_with_one_line(self, tmp_path):
        pack = self._pack(tmp_path, {
            "sets": {"player": ["idle", "walk", "jump", "fall", "land", "skid", "swim"]}
        })
        spec = load_spec(pack)
        assert "swim" in states_for(spec, "player")
        # And it is SELECTED when submerged, ahead of the airborne branch.
        cand, why = eval_ladder(
            spec["ladders"]["player"],
            {"on_ground": False, "descending": True, "submerged": True,
             "moving": False, "braking": False, "landing": False, "vy": 5.0, "vx": 0.0},
            spec["tails"]["player"], spec["why"]["player"],
        )
        assert cand[0] == "swim"
        assert why == "in water"

    def test_state_overlay_is_per_key(self, tmp_path):
        # Retune one state's frame count without restating the vocabulary.
        pack = self._pack(tmp_path, {"states": {"idle": {"frames": 7}}})
        spec = load_spec(pack)
        assert spec["states"]["idle"]["frames"] == 7
        assert spec["states"]["idle"]["brief"] == DEFAULT_STATES["idle"]["brief"]
        assert spec["states"]["walk"] == DEFAULT_STATES["walk"]

    def test_per_actor_row_override_beats_the_archetype(self, tmp_path):
        spec = load_spec(None)
        row = {"animation_states": ["idle", "walk", "death"]}
        assert states_for(spec, "enemy", "hopper", row) == ["idle", "walk", "death"]
        # Unknown names are dropped rather than generated blind.
        row2 = {"animation_states": ["idle", "nonsense"]}
        assert states_for(spec, "enemy", "", row2) == ["idle"]


class TestValidation:
    """Fail-closed, like `db schema`: a bad override is REPORTED, not silently
    turned into a missing animation."""

    def test_clean_default_spec(self):
        assert validate_spec(load_spec(None)) == []

    def test_set_naming_an_undefined_state(self, tmp_path):
        (tmp_path / "animation.json").write_text(
            json.dumps({"sets": {"player": ["idle", "moonwalk"]}})
        )
        problems = validate_spec(load_spec(tmp_path))
        assert any("moonwalk" in p for p in problems)

    def test_rule_testing_an_unknown_signal(self, tmp_path):
        (tmp_path / "animation.json").write_text(json.dumps({
            "ladders": {"player": [
                {"state": "idle", "when": {"is_vibing": True}, "why": "x"}
            ]}
        }))
        problems = validate_spec(load_spec(tmp_path))
        assert any("is_vibing" in p for p in problems)

    def test_signal_vocabularies_are_documented(self):
        # The lists are the contract with main.gd; keep them non-empty and
        # disjointly meaningful so validation errors can name them.
        assert "submerged" in PLAYER_SIGNALS
        assert "submerged" in ENEMY_SIGNALS


class TestConditionOps:
    """Unknown ops fail CLOSED — a typo must not make a rule always fire."""

    def _eval(self, when, signals):
        cand, _ = eval_ladder(
            [{"state": "idle", "when": when, "why": "w"}], signals, [], "d"
        )
        return cand == ["idle"]

    def test_numeric_comparisons(self):
        assert self._eval({"vy": {"gt": 3}}, {"vy": 5.0})
        assert not self._eval({"vy": {"gt": 3}}, {"vy": 1.0})
        assert self._eval({"vy": {"lte": 0}}, {"vy": 0.0})

    def test_unknown_op_never_fires(self):
        assert not self._eval({"vy": {"approximately": 5}}, {"vy": 5.0})

    def test_missing_signal_never_fires_a_true_condition(self):
        assert not self._eval({"submerged": True}, {})

    def test_bool_false_matches_absent_signal(self):
        # `{"on_ground": False}` against a missing signal reads as "not on the
        # ground", which is the safe reading for an engine that didn't send it.
        assert self._eval({"on_ground": False}, {})
