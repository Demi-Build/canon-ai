"""Row P0-10 — the W2 create flow: registry dispatch, the dungeon StepLog,
phase labels as template data, `pack templates`, and the create-time registry
stamp (master §3.1 stage 7).

Hermetic + $0 throughout: the fake backends only (doctrine 3 — nothing here
may call a real provider). The three subprocess tests run the real CLI, which
is the point of the row: `world new --template <id>` is what cradle shells to.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from canon.packs import PACKS, pack_templates, resolve_pack
from canon.packs.dungeon.spec import PHASE_LABELS as DUNGEON_LABELS
from canon.packs.platformer.spec import PHASE_LABELS as PLATFORMER_LABELS
from canon.pipeline.steplog import CANCEL_FILE_ENV, EXIT_CANCELLED
from tests.treediff import EXCLUDED_DIRS, tree_files

REPO = Path(__file__).resolve().parents[1]
CANON = [sys.executable, "-m", "canon.cli.main"]

#: P.4.4's key list — `pack templates` answers exactly this shape.
#: ``generators`` + ``count_scope`` are the two DERIVED additions (see
#: `canon.packs.template_meta`): the runner's generator lanes, and which count
#: fields are per-map.
P44_KEYS = {
    "id", "label", "description", "vocab", "defaults", "ranges", "advanced",
    "engine", "dimension", "distribution", "beta", "phase_labels",
    "generators", "count_scope",
}


def label_key_for(node: str, labels: dict[str, str]) -> str | None:
    """The label-map key that names *node*, or ``None`` — the resolution rule
    §3.0-E's data contract states, and the twin of cradle's
    ``packTemplates.phaseLabel``: the whole id, then ``<family>:<leaf>``, then
    ``<family>``. Kept in the test rather than in the CLI because canon emits
    the data and cradle reads it; this is what pins the two together."""
    bare = node.removeprefix("phase:")
    if bare in labels:
        return bare
    family, _, rest = bare.partition(":")
    if not rest:
        return None
    leaf = rest.rsplit("/", 1)[-1]
    for key in (f"{family}:{leaf}", family):
        if key in labels:
            return key
    return None


def run_canon(*args: str, expect: int = 0) -> dict:
    """Run the CLI and parse its one JSON document."""
    proc = subprocess.run([*CANON, *args], cwd=REPO, capture_output=True, text=True)
    assert proc.returncode == expect, f"canon {' '.join(args)} → {proc.returncode}\n{proc.stderr[-2000:]}"
    return json.loads(proc.stdout)


def log_events(pack: Path) -> list[dict]:
    path = pack / ".canon" / "log.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# `canon pack templates` (P.4.4)
# ---------------------------------------------------------------------------


class TestPackTemplates:
    def test_both_templates_with_the_p44_keys(self) -> None:
        templates = run_canon("pack", "templates")["templates"]
        assert [t["id"] for t in templates] == ["platformer", "dungeon"]
        for template in templates:
            assert set(template) == P44_KEYS, template["id"]
            assert template["label"] and template["description"]
            assert template["defaults"], "a card with no counts cannot render step 2"
            assert isinstance(template["ranges"], dict) and template["ranges"]
            assert template["dimension"] == "2D"

    def test_ranges_are_authored_and_bracket_the_defaults(self) -> None:
        """P.9 R8: P0-10 authors the bands. A default outside its own band
        would make the wizard open on an invalid value."""
        for template in pack_templates():
            for key, default in template["defaults"].items():
                low, high = template["ranges"][key]
                assert low <= default <= high, f"{template['id']}.{key}={default} outside [{low},{high}]"

    def test_distribution_is_derived_from_the_engines_block(self) -> None:
        """W2.4: never authored — engine choice and distribution are coupled,
        so `exports` on the engines entry is the one datum."""
        by_id = {t["id"]: t for t in pack_templates()}
        assert by_id["platformer"]["distribution"] == ["computer", "mobile", "web"]
        assert by_id["dungeon"]["distribution"] == [], "pygame exports nothing yet"
        for spec in PACKS.values():
            assert not spec.wizard.get("distribution"), "distribution must not be authored in the seed"

    def test_the_dungeon_card_ships_un_badged(self) -> None:
        """W2.1.4: editing is day 1, so there is no 'generates but read-only'
        beta period — the card ships complete."""
        assert {t["id"]: t["beta"] for t in pack_templates()} == {"platformer": False, "dungeon": False}

    def test_advanced_is_w211s_primary_split(self) -> None:
        by_id = {t["id"]: t for t in pack_templates()}
        assert by_id["dungeon"]["advanced"] == ["event", "quest", "class"]
        primary = set(by_id["dungeon"]["defaults"]) - set(by_id["dungeon"]["advanced"])
        assert primary == {"rooms", "npc", "monster", "item"}
        assert by_id["platformer"]["advanced"] == []

    def test_generator_lanes_come_from_the_runner(self) -> None:
        """Doctrine 4 needs the datum: the dungeon has NO vlm lane, so the
        wizard must be able to disable Animation with a reason instead of
        key-gating and spend-confirming a run canon prices at $0 and ignores."""
        by_id = {t["id"]: t for t in pack_templates()}
        assert by_id["platformer"]["generators"] == ["llm", "image", "music", "sfx", "vlm"]
        assert by_id["dungeon"]["generators"] == ["llm", "image", "music", "sfx"]
        for template in pack_templates():
            assert set(template["generators"]) == set(PACKS[template["id"]].runner["backends"])

    def test_count_scope_says_which_counts_are_per_room(self) -> None:
        """W2.1.1's honesty: `DatabasePhase` multiplies a per-map count by the
        map count, so "NPCs 2" on a 3-room dungeon is 6. The flag is the entity
        kind's own `per_map`; the kind that IS the map is not per-map."""
        by_id = {t["id"]: t["count_scope"] for t in pack_templates()}
        assert by_id["dungeon"]["npc"] == "per_room"
        assert by_id["dungeon"]["monster"] == "per_room"
        assert by_id["dungeon"]["item"] == "per_room"
        assert by_id["dungeon"]["class"] == "total"
        assert "rooms" not in by_id["dungeon"], "rooms are the maps, never per room"
        assert by_id["platformer"] == {"enemies": "total", "items": "total"}

    def test_no_surface_speaks_a_structure_the_manifest_lacks(self) -> None:
        """W2.1.1: the dungeon emits ROOMS. The card's vocabulary line and its
        description are rendered verbatim, so neither may say "floors"."""
        for template in pack_templates():
            copy_text = " ".join([template["description"], *template["vocab"]]).lower()
            assert "floor" not in copy_text, template["id"]


# ---------------------------------------------------------------------------
# Phase labels as template data (§3.0-E)
# ---------------------------------------------------------------------------


class TestPhaseLabels:
    def test_both_templates_carry_a_label_map(self) -> None:
        maps = {t["id"]: t["phase_labels"] for t in pack_templates()}
        plat = maps["platformer"]
        assert len([k for k in plat if k.startswith("plat:")]) == 22, \
            "the 22 ids CreateProgress used to hardcode"
        assert plat["plat:sprite_animation"] == "Animation"
        # …plus the orchestrator's per-artifact families, which the create
        # default emits 41 nodes' worth of (see the label-coverage test).
        assert plat["level:terrain"] == "Terrain" and plat["review"] == "Review"
        assert maps["dungeon"]["db:npc"] == "NPCs"
        assert maps["dungeon"] and not any(k.startswith("plat:") for k in maps["dungeon"])

    def test_the_map_is_the_one_the_registry_stamps(self) -> None:
        """One map, three surfaces (§3.0-E): the template metadata, the seed
        and the stamped registry must be the same dict."""
        for template in pack_templates():
            seed = PACKS[template["id"]]
            assert template["phase_labels"] == seed.phase_labels
            assert seed.stamped()["phase_labels"] == seed.phase_labels

    @pytest.mark.parametrize(
        ("pack_type", "labels"),
        [("platformer", PLATFORMER_LABELS), ("dungeon", DUNGEON_LABELS)],
    )
    def test_every_label_key_is_a_phase_id_shape(self, pack_type: str, labels: dict) -> None:
        for node_id, label in labels.items():
            assert not node_id.startswith("phase:"), "keys are bare ids; the reader strips the prefix"
            assert label and label[0].isupper()


# ---------------------------------------------------------------------------
# `world new --template dungeon` — the tree, the registry, the engines seed
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dungeon_pack(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("create") / "dungeon"
    run_canon(
        "world", "new", str(out), "--template", "dungeon",
        "--name", "Shadow Keep", "--rooms", "2", "--seed", "p010-dungeon",
    )
    return out


class TestDungeonCreate:
    def test_the_reader_and_pack_info_accept_the_tree(self, dungeon_pack: Path) -> None:
        info = run_canon("pack", "info", str(dungeon_pack))
        assert info["pack_type"] == "dungeon"
        assert info["source"] == "registry", "create stamped a registry, so tier 1 answers"
        assert info["entities"]["room"]["count"] == 2
        for kind in ("npc", "monster", "item", "quest", "event", "class"):
            assert info["entities"][kind]["count"] > 0, kind
        # The read-back loaders (P0-5) parse every row of every kind.
        spec = resolve_pack(dungeon_pack).spec
        for kind, entity in spec.entities.items():
            if entity.loader is None or kind in ("music", "sfx"):
                continue
            entity.loader(dungeon_pack)

    def test_the_name_reached_the_pack(self, dungeon_pack: Path) -> None:
        """The wizard's Name field lands through the journaled write core on
        the template's OWN title field (`story.title` here) + its mirrors."""
        bible = json.loads((dungeon_pack / "world_bible.json").read_text())
        manifest = json.loads((dungeon_pack / "manifest.json").read_text())
        assert bible["story"]["title"] == "Shadow Keep"
        assert manifest["story_title"] == "Shadow Keep"
        assert manifest["pack_type"] == "dungeon"

    def test_the_registry_carries_exactly_one_engines_entry(self, dungeon_pack: Path) -> None:
        """§3.0-H + P.9 R6: template create stamps THE pack's one entry —
        `pygame` on a dungeon."""
        registry = json.loads((dungeon_pack / ".canon" / "registry.json").read_text())
        assert registry["schema"] == "canon-registry/v1"
        assert registry["template"]["id"] == "dungeon"
        assert registry["template"]["version"].startswith("sha256:")
        assert [e["id"] for e in registry["engines"]] == ["pygame"]
        assert registry["engines"][0]["primary"] is True
        assert registry["tuning"] == {"schema": "canon-tuning/v0", "status": "reserved", "keys": {}}
        assert registry["phase_labels"] == DUNGEON_LABELS

    def test_the_registry_stamp_is_journaled(self, dungeon_pack: Path) -> None:
        events = [
            json.loads(line)
            for line in (dungeon_pack / ".canon" / "journal.jsonl").read_text().splitlines()
            if line.strip()
        ]
        synth = [e for e in events if e.get("artifact_id") == "registry"]
        assert len(synth) == 1 and synth[0]["op"] == "create"
        assert synth[0]["detail"]["kind"] == "registry_synthesize"

    def test_the_seed_reaches_the_runner(self, tmp_path: Path) -> None:
        """W2's papercut: the seed used to be dropped. Two creates on one seed
        produce the same tree."""
        a, b = tmp_path / "a", tmp_path / "b"
        for out in (a, b):
            run_canon("world", "new", str(out), "--template", "dungeon",
                      "--rooms", "1", "--seed", "same-seed", "--name", "Twin")
        files = tree_files(a)
        assert files == tree_files(b)
        assert json.loads((a / "manifest.json").read_text())["seed"] == \
            json.loads((b / "manifest.json").read_text())["seed"]

    def test_a_platformer_flag_is_refused_by_name_not_dropped(self, tmp_path: Path) -> None:
        """Doctrine 4 — disabled WITH a reason."""
        result = run_canon(
            "world", "new", str(tmp_path / "w"), "--template", "dungeon",
            "--rooms", "1", "--stages", "3", "--seed", "foreign",
        )
        assert any("--stages ignored" in w for w in result["warnings"])

    def test_orchestrate_on_a_template_without_a_dag_says_so(self, tmp_path: Path) -> None:
        result = run_canon(
            "world", "new", str(tmp_path / "w"), "--template", "dungeon",
            "--rooms", "1", "--orchestrate", "--seed", "orch",
        )
        assert result["orchestrated"] is False
        assert any("--orchestrate ignored" in w for w in result["warnings"])

    def test_the_count_flags_accept_the_template_s_own_count_keys(self, tmp_path: Path) -> None:
        """The 1:1 map W2.1.1 asks for: the wizard renders its fields from
        `pack templates` (`rooms`, `npc`, `monster`, `item`, …) and sends those
        names straight through, so no translation table lives on the cradle
        side. Each flag takes the plural CLI name AND the template's key."""
        out = tmp_path / "aliased"
        result = run_canon(
            "world", "new", str(out), "--template", "dungeon", "--seed", "alias",
            "--rooms", "2", "--npc", "1", "--monster", "1", "--item", "1",
            "--event", "1", "--quest", "1", "--class", "2",
        )
        assert "warnings" not in result, result.get("warnings")
        info = run_canon("pack", "info", str(out))
        assert info["entities"]["npc"]["count"] == 2  # 1 per room × 2 rooms
        assert info["entities"]["class"]["count"] == 2
        # And the estimator takes the same names (one vocabulary, two verbs).
        est = run_canon(
            "world", "estimate", "--template", "dungeon", "--rooms", "2", "--npc", "1",
        )["estimate"]
        assert est["template"] == "dungeon" and est["warnings"] == []

    def test_an_unknown_template_names_the_installed_ones(self, tmp_path: Path) -> None:
        proc = subprocess.run(
            [*CANON, "world", "new", str(tmp_path / "w"), "--template", "roguelike"],
            cwd=REPO, capture_output=True, text=True,
        )
        assert proc.returncode != 0
        message = proc.stdout + proc.stderr
        assert "roguelike" in message and "dungeon" in message


# ---------------------------------------------------------------------------
# The dungeon StepLog (W2's second wiring blocker)
# ---------------------------------------------------------------------------


class TestDungeonStepLog:
    def test_all_five_event_kinds(self, dungeon_pack: Path) -> None:
        events = log_events(dungeon_pack)
        kinds = {e["event"] for e in events}
        assert {"run_start", "node_start", "node_item", "node_done", "run_end"} <= kinds

    def test_run_start_carries_the_phase_count_and_run_end_is_ok(self, dungeon_pack: Path) -> None:
        events = log_events(dungeon_pack)
        starts = [e for e in events if e["event"] == "run_start"]
        assert len(starts) == 1 and starts[0]["phases"] > 0
        ends = [e for e in events if e["event"] == "run_end"]
        assert len(ends) == 1 and ends[0]["ok"] is True

    def test_node_ids_resolve_through_the_template_label_map(self, dungeon_pack: Path) -> None:
        """The whole point of §3.0-E: what the log emits is what the label map
        is keyed by, so cradle renders names, not raw ids."""
        nodes = {e["node"] for e in log_events(dungeon_pack) if e["event"] == "node_start"}
        unlabelled = {n for n in nodes if n.removeprefix("phase:") not in DUNGEON_LABELS}
        assert not unlabelled, f"phases with no label: {sorted(unlabelled)}"

    def test_items_are_announced_with_index_and_total(self, dungeon_pack: Path) -> None:
        items = [e for e in log_events(dungeon_pack) if e["event"] == "node_item"]
        assert items, "the long per-entity legs must announce each item"
        assert all("item" in e and e["index"] >= 1 and e["total"] >= e["index"] for e in items)
        assert any(e["node"] == "phase:db:npc" for e in items)


@pytest.fixture(scope="module")
def platformer_pack(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("create") / "plat"
    run_canon(
        "world", "new", str(out), "--name", "Ember", "--seed", "p010-plat",
        "--stages", "1", "--levels", "2", "--enemies", "2", "--items", "2",
    )
    return out


@pytest.mark.slow
class TestPlatformerStepLog:
    """The dungeon's twin, on the template that already had a DAG — written
    because `--orchestrate` is now the create DEFAULT (master §8 Q6) and the
    dungeon-only versions of these checks could not see what the flip changed
    about the emitted stream."""

    def test_node_ids_resolve_through_the_template_label_map(self, platformer_pack: Path) -> None:
        """§3.0-E on the DEFAULT create: 41 of the 55 nodes are the
        orchestrator's per-artifact ids, and every one of them must be named by
        the template's map (through the family rule) — not humanized from a raw
        id in cradle."""
        nodes = {
            e["node"] for e in log_events(platformer_pack)
            if e["event"] in {"node_start", "node_skipped"}
        }
        assert len(nodes) > 40, "the orchestrated create is per-artifact, not 21 phases"
        unlabelled = {n for n in nodes if label_key_for(n, PLATFORMER_LABELS) is None}
        assert not unlabelled, f"nodes with no label: {sorted(unlabelled)}"
        # The two shapes the rule exists for, pinned by example (cradle's
        # `phaseLabel` test asserts the same two ids render as these labels).
        assert label_key_for("level:ashen_depths/l1/terrain", PLATFORMER_LABELS) == "level:terrain"
        assert label_key_for("review:ashen_depths/l1", PLATFORMER_LABELS) == "review"

    def test_run_start_carries_a_step_total_for_every_segment(self, platformer_pack: Path) -> None:
        """Doctrine 5, honest progress: the orchestrated scheduler's `run_start`
        carries `nodes` where the sequential one carries `phases`. A relay that
        reads only one key shows "counting steps…" for the whole create, so the
        contract is "a step total under one of the two names, always"."""
        starts = [e for e in log_events(platformer_pack) if e["event"] == "run_start"]
        assert starts, "a create must announce its size"
        for start in starts:
            total = start.get("phases", start.get("nodes"))
            assert isinstance(total, int) and total > 0, start

    def test_a_fresh_create_is_two_passes(self, platformer_pack: Path) -> None:
        """The bootstrap pass (macro phases invent the stage plan) then the
        full graph — so `run_end` is NOT the run's terminal event and no
        surface may treat the first one as the end (the job's own terminal
        `job-updated` is). Pinned because the display froze on it."""
        events = log_events(platformer_pack)
        assert len([e for e in events if e["event"] == "run_start"]) == 2
        assert len([e for e in events if e["event"] == "run_end"]) == 2
        assert all(e["ok"] is True for e in events if e["event"] == "run_end")

    def test_the_second_pass_skips_what_the_first_completed(self, platformer_pack: Path) -> None:
        """The macro nodes are `node_skipped` in pass 2 because pass 1 of the
        SAME run did them — a reader must not report them as "unchanged"."""
        events = log_events(platformer_pack)
        skipped = {e["node"] for e in events if e["event"] == "node_skipped"}
        done_first = {e["node"] for e in events if e["event"] == "node_done"}
        assert skipped and skipped <= done_first


class TestDungeonCancel:
    """§3.0-D on the dungeon, for free: the create goes through the SAME
    `node_item` emitter A4.5 put the cancel check in, so ⏹ Stop starts nothing
    new, keeps what landed, and exits 3 — A4.5's platformer test, one template
    over. Split in two so neither half races the $0 run (a fake dungeon run is
    over in a fifth of a second): the boundary is proven in-process against the
    real phase list, the exit code + message against the real runner.
    """

    def test_the_boundary_keeps_what_landed_and_starts_nothing_new(self, tmp_path: Path) -> None:
        from canon.backends.testing import FakeLLMBackend
        from canon.llm.client import LLMClient
        from canon.packs.dungeon.compose import compose_pipeline
        from canon.packs.dungeon.fakes import make_fake_responder
        from canon.pipeline.runner import run_pipeline
        from canon.pipeline.steplog import RunCancelled, StepLog

        out = tmp_path / "dungeon"
        cancel = tmp_path / "cancel" / "job-1"
        phases, ctx = compose_pipeline(seed="p010-cancel", num_maps=2, output_dir=out)
        ctx.steplog = StepLog(out, cancel_file=cancel)
        ctx.llm = LLMClient(FakeLLMBackend(make_fake_responder(2)), stats=ctx.stats)

        class _PressStop:
            """Stands in for the user hitting ⏹ mid-run: cradle's JobQueue
            writes the cancel file this run was spawned with."""

            name = "test:press_stop"

            def run(self, _ctx: object) -> None:
                cancel.parent.mkdir(parents=True, exist_ok=True)
                cancel.write_text("cancel\n", encoding="utf-8")

        at = next(i for i, p in enumerate(phases) if p.name == "maze_layout") + 1
        phases = [*phases[:at], _PressStop(), *phases[at:]]

        with pytest.raises(RunCancelled) as raised:
            run_pipeline(phases, ctx)
        error = raised.value
        assert error.node.startswith("phase:db:"), "stopped at the first item after the stop"
        assert "phase:maze_layout" in error.kept and "phase:classes" in error.kept

        events = log_events(out)
        ends = [e for e in events if e["event"] == "run_end"]
        assert len(ends) == 1, "one cancel-aware run_end, never a second line"
        assert ends[0]["ok"] is False and ends[0]["cancelled"] is True
        assert ends[0]["kept"] == error.kept
        # Nothing was announced after the boundary: the interrupted node's
        # last announced item is the one the cancel raised BEFORE.
        announced = [e for e in events if e["event"] == "node_item" and e["node"] == error.node]
        assert not announced, "the item the cancel refused was never announced"
        # Keep what landed: the rooms written before the stop are still there.
        assert list(out.glob("rooms/*/maze.json")), "completed work stays on disk"

    @pytest.mark.slow
    def test_the_runner_exits_3_and_says_so(self, tmp_path: Path) -> None:
        """The whole-process half: an already-present cancel file stops the
        run at its very first item boundary (start nothing new)."""
        out = tmp_path / "dungeon"
        cancel = tmp_path / "cancel-now"
        cancel.write_text("cancel\n", encoding="utf-8")
        proc = subprocess.run(
            [
                sys.executable, "-m", "canon.packs.dungeon.run_world",
                "--backend", "fake", "--output-dir", str(out),
                "--num-maps", "2", "--seed", "p010-cancel-proc",
            ],
            cwd=REPO,
            env={**os.environ, CANCEL_FILE_ENV: str(cancel)},
            capture_output=True,
        )
        assert proc.returncode == EXIT_CANCELLED, proc.stdout.decode(errors="replace")[-800:]
        assert b"Cancelled" in proc.stdout
        ends = [e for e in log_events(out) if e["event"] == "run_end"]
        assert len(ends) == 1 and ends[0]["cancelled"] is True

    @pytest.mark.slow
    def test_world_new_passes_the_stop_through_instead_of_failing(self, tmp_path: Path) -> None:
        """The verb cradle actually shells to. `world new` used to swallow the
        runner's 3 as a generic failure, which left the tree un-named and
        un-registered and made the worker's clean-stop branch unreachable for
        creates: every ⏹ read as a crash (§3.0-D says the opposite)."""
        out = tmp_path / "dungeon"
        cancel = tmp_path / "cancel-now"
        cancel.write_text("cancel\n", encoding="utf-8")
        proc = subprocess.run(
            [*CANON, "world", "new", str(out), "--template", "dungeon",
             "--rooms", "2", "--seed", "p010-newcancel", "--name", "Stopped"],
            cwd=REPO,
            env={**os.environ, CANCEL_FILE_ENV: str(cancel)},
            capture_output=True, text=True,
        )
        assert proc.returncode == EXIT_CANCELLED, proc.stderr[-800:]
        result = json.loads(proc.stdout)
        assert result["cancelled"] is True and "error" not in result
        assert result["pack_dir"] == str(out.resolve())
        # What landed stays on disk, and the log is the record of what it was.
        ends = [e for e in log_events(out) if e["event"] == "run_end"]
        assert len(ends) == 1 and ends[0]["cancelled"] is True

    def test_a_stop_inside_the_asset_phase_does_not_claim_the_phase_landed(
        self, tmp_path: Path
    ) -> None:
        """AssetPhase gathers its tasks with `return_exceptions=True`, which
        captured the cancel the item emitter raised: the phase reported
        `node_done` and `kept` claimed `phase:assets` with zero files written —
        the exact opposite of "keep what landed, say what it cost"."""
        from canon import AssetPhase
        from canon.backends.testing import FakeImageBackend, FakeLLMBackend
        from canon.llm.client import LLMClient
        from canon.packs.dungeon.compose import compose_pipeline
        from canon.packs.dungeon.fakes import make_fake_responder
        from canon.pipeline.runner import run_pipeline
        from canon.pipeline.steplog import RunCancelled, StepLog

        out = tmp_path / "dungeon"
        cancel = tmp_path / "cancel" / "job-2"
        phases, ctx = compose_pipeline(seed="p010-assetstop", num_maps=2, output_dir=out)
        ctx.steplog = StepLog(out, cancel_file=cancel)
        ctx.llm = LLMClient(FakeLLMBackend(make_fake_responder(2)), stats=ctx.stats)
        # The dungeon's own AssetPhase is skip-all until a caller wires
        # backends (compose.py) — this is that caller, $0 fakes only.
        ctx.image_backend = FakeImageBackend()

        class _PressStop:
            name = "test:press_stop"

            def run(self, _ctx: object) -> None:
                cancel.parent.mkdir(parents=True, exist_ok=True)
                cancel.write_text("cancel\n", encoding="utf-8")

        at = next(i for i, p in enumerate(phases) if p.name == "assets")
        phases = [*phases[:at], _PressStop(), AssetPhase(skip_music=True, skip_sfx=True)]

        with pytest.raises(RunCancelled) as raised:
            run_pipeline(phases, ctx)
        assert raised.value.node == "phase:assets", "the stop is seen INSIDE the asset phase"
        assert "phase:assets" not in raised.value.kept
        events = log_events(out)
        assert not [e for e in events if e["event"] == "node_done" and e["node"] == "phase:assets"]
        ends = [e for e in events if e["event"] == "run_end"]
        assert len(ends) == 1 and "phase:assets" not in ends[0]["kept"]


# ---------------------------------------------------------------------------
# The platformer stays byte-identical (doctrine 7 as amended by Q6)
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_the_orchestrate_flip_adds_bible_json_and_nothing_else(tmp_path: Path) -> None:
    """Master §8 Q6 + doctrine 7: `--orchestrate` is the create DEFAULT, and
    its ONLY sanctioned emitted-tree delta is an additive `bible.json`. The
    pre-flip tree is `--no-orchestrate`, which is exactly what `world new`
    did before this row. `.canon/` is outside the byte-determinism contract
    (P.9 R14) — the registry, journal and step log live there.
    """
    pre, post = tmp_path / "pre", tmp_path / "post"
    run_canon("world", "new", str(pre), "--no-orchestrate", "--name", "Twin", "--seed", "q6")
    run_canon("world", "new", str(post), "--name", "Twin", "--seed", "q6")

    # The EMITTED tree, compared with nothing excluded but `.canon/` (whose
    # exemption is R14's): every delta must be the one sanctioned file.
    def emitted(root: Path) -> set[Path]:
        return {
            p.relative_to(root)
            for p in root.rglob("*")
            if p.is_file() and ".canon" not in p.relative_to(root).parts
        }

    added, dropped = emitted(post) - emitted(pre), emitted(pre) - emitted(post)
    assert added == {Path("bible.json")}, f"unsanctioned additions: {sorted(added)}"
    assert not dropped, f"the flip dropped files: {sorted(dropped)}"
    # And every file both trees carry is byte-for-byte the same (the shared
    # exemptions — generation_stats.json — stay excluded via tree_files).
    files_pre, files_post = tree_files(pre), tree_files(post)
    assert files_pre == files_post
    for rel in files_pre:
        assert (pre / rel).read_bytes() == (post / rel).read_bytes(), f"{rel} differs after the flip"
    # And the manifest's only P0-3 delta is still the additive `pack_type` key.
    assert json.loads((post / "manifest.json").read_text())["pack_type"] == "platformer"


@pytest.mark.slow
def test_a_platformer_create_stamps_exactly_one_engines_entry(tmp_path: Path) -> None:
    """§3.0-H + P.9 R6: `godot`, primary — the un-promoted pygame harness is
    NOT in the pack, so it is not an attached engine in Phase 0."""
    out = tmp_path / "plat"
    result = run_canon("world", "new", str(out), "--name", "Ember", "--seed", "engines")
    assert result["engines"] == ["godot"] and result["orchestrated"] is True
    registry = json.loads((out / ".canon" / "registry.json").read_text())
    assert [e["id"] for e in registry["engines"]] == ["godot"]
    assert registry["engines"][0]["primary"] is True
    assert registry["engines"][0]["exports"] == ["computer", "web", "mobile"]
    assert registry["phase_labels"] == PLATFORMER_LABELS
    assert json.loads((out / "world.json").read_text())["title"] == "Ember"


def test_the_canon_dir_is_outside_the_determinism_contract() -> None:
    """P.9 R14 as decided: `tests/treediff.py` excludes the whole `.canon/`
    directory, not three basenames — the instance registry, the journal, its
    CAS objects and the step log all live there."""
    assert ".canon" in EXCLUDED_DIRS
