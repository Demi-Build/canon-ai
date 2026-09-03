"""Row P1-A7.5 — `game_coder`: edit_project_code, the gate ladder, the
code-evolved probe flag, one-click restore.

Hermetic and $0: the pack is a real platformer tree generated with
``run_slice --engine godot`` on fake backends (so the Godot COPY, its
``.engine.json`` stamp, the journal and ``restore`` are all the real ones),
and no test calls a provider.

GODOT-OPTIONAL, both directions. The engine rungs are proven for real when a
Godot binary is on this machine (``GODOT_BIN`` → PATH → the app bundle) and
skipped otherwise; the "no engine here" behaviour is tested ALWAYS by handing
the ladder a not-found probe, because the thing that must never happen — a
false green on a machine without the engine — has to be pinned on the
machines that do have one too.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from canon import provenance  # noqa: E402
from canon.agent.actors import bind_call, current_call  # noqa: E402
from canon.agent.conversations import ConversationStore  # noqa: E402
from canon.agent.gates import (  # noqa: E402
    GATE_RUNGS,
    GODOT_MISSING,
    godot_bin,
    ladder_summary,
    run_ladder,
    smoke_levels,
    structural_check,
)
from canon.agent.permissions import PermissionEngine  # noqa: E402
from canon.agent.registry import ToolRegistry  # noqa: E402
from canon.agent.roster import load_roster  # noqa: E402
from canon.agent.runs import RunManager  # noqa: E402
from canon.agent.tools_code import CODE_TOOL_NAMES, SYNC_NEVER_ALWAYS, register_code_tools  # noqa: E402
from canon.agent.tools_read import register_read_tools  # noqa: E402
from canon.agent.tools_vision import register_vision_tools  # noqa: E402
from canon.agent.tools_write import register_write_tools  # noqa: E402
from canon.backends.testing import FakeChatBackend  # noqa: E402
from canon.engine_ops import (  # noqa: E402
    TEMPLATE_PHYSICS_NOTE,
    CodeEditRefused,
    apply_unified_diff,
    code_evolved,
    edit_project_code,
)
from canon.packs.platformer import godot_export  # noqa: E402

HAS_GODOT = godot_bin() is not None
needs_godot = pytest.mark.skipif(not HAS_GODOT, reason="no Godot on this machine (GODOT_BIN / PATH / app bundle)")

GD = "godot/main.gd"

#: The Trace A2 edit, near-verbatim: "give the double jump a floatier apex".
APEX_OLD = "\telse:\n\t\tplayer_vy += gravity * delta\n"
APEX_NEW = (
    "\telse:\n"
    "\t\t# Floatier apex: while airborne and near the top of the arc the pull\n"
    "\t\t# eases off, so hang time reads longer without changing jump height.\n"
    "\t\tif not on_ground and absf(player_vy) < 4.0:\n"
    "\t\t\tplayer_vy += gravity * delta * 0.55\n"
    "\t\telse:\n"
    "\t\t\tplayer_vy += gravity * delta\n"
)

#: The same edit with a slip no parser can see: the scale is read from a
#: dictionary that never got the key. It compiles, it boots clean, and it
#: errors on every physics frame — the rung that catches it is the smoke.
APEX_BROKEN = (
    "\telse:\n"
    "\t\t# Floatier apex, reading the scale from tuning that is never filled.\n"
    "\t\tvar tuning := {}\n"
    '\t\tplayer_vy += gravity * delta * float(tuning["apex_scale"])\n'
)


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def generated_tree(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("a75_tree")
    subprocess.run(
        [
            sys.executable, "-m", "canon.packs.platformer.run_slice",
            "--backend", "fake", "--engine", "godot", "--image-backend", "fake",
            "--music-backend", "none", "--sfx-backend", "none",
            "--num-stages", "1", "--num-levels", "2", "--num-enemies", "2", "--num-items", "2",
            "--seed", "a75-coder", "--orchestrate", "--output-dir", str(out),
        ],
        check=True,
        capture_output=True,
        cwd=REPO,
    )
    return out


@pytest.fixture
def pack(generated_tree: Path, tmp_path: Path) -> Path:
    dst = tmp_path / "pack"
    shutil.copytree(generated_tree, dst)
    return dst


def fingerprint(root: Path) -> list[tuple[str, str]]:
    return sorted(
        (str(p.relative_to(root)), hashlib.sha256(p.read_bytes()).hexdigest())
        for p in root.rglob("*")
        if p.is_file()
    )


def apex_diff(pack: Path, old: str = APEX_OLD, new: str = APEX_NEW) -> str:
    """A real unified diff of the pack's own ``main.gd`` (never a hand-typed
    one — the point is that the model's diff is applied to live text)."""
    src = (pack / GD).read_text(encoding="utf-8")
    assert src.count(old) == 1, "the fixture's main.gd no longer carries the apex block"
    return "".join(
        difflib.unified_diff(
            src.splitlines(keepends=True),
            src.replace(old, new).splitlines(keepends=True),
            fromfile=f"a/{GD}",
            tofile=f"b/{GD}",
        )
    )


def refusal(exc: CodeEditRefused) -> dict:
    return json.loads(str(exc))


def code_registry(pack: Path) -> ToolRegistry:
    registry = ToolRegistry(PermissionEngine(pack))
    register_read_tools(registry, pack)
    register_write_tools(registry, pack, actor_for=current_call)
    register_vision_tools(registry, pack)
    register_code_tools(registry, pack, actor_for=current_call)
    return registry


def not_found_probe() -> dict:
    return {"found": False, "path": None, "source": None, "problems": [], "reason": f"{GODOT_MISSING}: (test)"}


# ---------------------------------------------------------------------------
# The wall — each refusal by name, and nothing written
# ---------------------------------------------------------------------------


class TestTheWall:
    def test_a_path_outside_the_engine_copy_is_refused_by_name(self, pack: Path) -> None:
        before = fingerprint(pack)
        with pytest.raises(CodeEditRefused) as exc:
            edit_project_code(pack, "manifest.json", apex_diff(pack))
        assert refusal(exc.value)["error"] == "code_path_outside_engine_copy"
        assert "godot/**" in refusal(exc.value)["message"]
        assert fingerprint(pack) == before

    def test_the_shared_template_is_refused_by_name(self, pack: Path) -> None:
        before = fingerprint(pack)
        target = godot_export.TEMPLATE_DIR / "godot" / "main.gd"
        with pytest.raises(CodeEditRefused) as exc:
            edit_project_code(pack, str(target), apex_diff(pack))
        body = refusal(exc.value)
        assert body["error"] == "code_path_is_the_shared_template"
        assert "every project copies it" in body["message"]
        assert fingerprint(pack) == before
        # …and the template on disk is untouched, which is the actual promise.
        assert (target).read_text(encoding="utf-8").count(APEX_OLD) == 1

    def test_canon_source_is_refused_by_name(self, pack: Path) -> None:
        import canon

        before = fingerprint(pack)
        with pytest.raises(CodeEditRefused) as exc:
            edit_project_code(pack, str(Path(canon.__file__).parent / "engine_ops.py"), apex_diff(pack))
        assert refusal(exc.value)["error"] == "code_path_is_canon_source"
        assert fingerprint(pack) == before

    def test_another_pack_is_refused(self, pack: Path, tmp_path: Path) -> None:
        other = tmp_path / "other"
        (other / "godot").mkdir(parents=True)
        (other / "godot" / "main.gd").write_text("extends Node\n")
        with pytest.raises(CodeEditRefused) as exc:
            edit_project_code(pack, "../other/godot/main.gd", apex_diff(pack))
        assert refusal(exc.value)["error"] == "code_path_escapes_the_pack"
        assert (other / "godot" / "main.gd").read_text() == "extends Node\n"

    def test_the_engine_stamp_itself_is_refused(self, pack: Path) -> None:
        with pytest.raises(CodeEditRefused) as exc:
            edit_project_code(pack, godot_export.STAMP_REL, apex_diff(pack))
        assert refusal(exc.value)["error"] == "code_path_is_the_engine_stamp"

    def test_a_file_that_does_not_exist_is_refused_not_created(self, pack: Path) -> None:
        with pytest.raises(CodeEditRefused) as exc:
            edit_project_code(pack, "godot/new_system.gd", apex_diff(pack))
        assert refusal(exc.value)["error"] == "code_path_missing"
        assert not (pack / "godot" / "new_system.gd").exists()

    def test_the_wall_does_not_depend_on_where_the_process_is_running(self, pack: Path, monkeypatch) -> None:
        """A pack-relative path is judged inside the PACK. Resolving it bare
        would anchor it at the CWD, so `godot/main.gd` refused as canon source
        whenever the CLI ran from inside canon's own checkout."""
        import canon

        diff = apex_diff(pack)
        monkeypatch.chdir(Path(canon.__file__).parent)
        result = edit_project_code(pack, GD, diff, actor="agent:conv1/game_coder")
        assert result["stamped"] == "modified"
        assert APEX_NEW in (pack / GD).read_text(encoding="utf-8")
        # …and an ABSOLUTE path into canon's source is still refused by name.
        with pytest.raises(CodeEditRefused) as exc:
            edit_project_code(pack, str(Path(canon.__file__).parent / "engine_ops.py"), diff)
        assert refusal(exc.value)["error"] == "code_path_is_canon_source"


# ---------------------------------------------------------------------------
# The diff: clean applies, dirty refuses, nothing partial
# ---------------------------------------------------------------------------


class TestTheDiff:
    def test_a_clean_diff_applies_journals_and_stamps_modified(self, pack: Path) -> None:
        result = edit_project_code(
            pack, GD, apex_diff(pack), actor="agent:conv1/game_coder", session="conv1"
        )
        assert result["new_hash"] != result["before_hash"]
        assert result["stamped"] == "modified"
        assert result["added"] == 6 and result["removed"] == 1
        assert APEX_NEW in (pack / GD).read_text(encoding="utf-8")

        events = [e for e in provenance.all_events(pack) if e.get("artifact_id") == f"code:{GD}"]
        assert [e["op"] for e in events] == ["edit"]
        assert events[0]["actor"] == "agent:conv1/game_coder"
        assert events[0]["session"] == "conv1"
        assert events[0]["before_hash"] == result["before_hash"]
        assert events[0]["after_hash"] == result["new_hash"]
        # The CAS holds both versions — which is what makes restore possible.
        assert provenance.read_object(pack, result["before_hash"]).decode("utf-8").count(APEX_OLD) == 1

    def test_the_result_carries_the_panel_code_diff_block(self, pack: Path) -> None:
        block = edit_project_code(pack, GD, apex_diff(pack))["diff"]
        assert block["kind"] == "code" and block["path"] == GD
        assert block["added"] == 6 and block["removed"] == 1
        assert block["unified"].splitlines()[2].startswith("@@")
        assert "+\t\t\tplayer_vy += gravity * delta * 0.55" in block["unified"]

    def test_the_stamp_names_who_edited_it_and_sync_then_refuses(self, pack: Path) -> None:
        edit_project_code(pack, GD, apex_diff(pack), actor="agent:conv1/game_coder", session="conv1")
        status = godot_export.engine_status(pack)
        assert status["modified"] == [GD]
        assert status["code_evolved"] is True
        assert status["attribution"][GD]["actor"] == "agent:conv1/game_coder"
        assert next(f for f in status["files"] if f["path"] == GD)["by"]["op"] == "edit"

        dry = godot_export.engine_sync(pack, dry_run=True)
        assert dry["refused"] == [GD] and dry["would_write"] == []
        live = godot_export.engine_sync(pack, actor="user")
        assert live["refused"] == [GD]
        assert APEX_NEW in (pack / GD).read_text(encoding="utf-8")
        # …and the attribution SURVIVES the sync that refused it.
        assert godot_export.engine_status(pack)["attribution"][GD]["actor"] == "agent:conv1/game_coder"

    def test_force_sync_drops_the_bytes_and_the_attribution_together(self, pack: Path) -> None:
        edit_project_code(pack, GD, apex_diff(pack), actor="agent:conv1/game_coder")
        godot_export.engine_sync(pack, force=True, actor="user")
        status = godot_export.engine_status(pack)
        assert status["modified"] == [] and status["attribution"] == {}
        assert status["code_evolved"] is False

    def test_a_diff_that_does_not_apply_refuses_without_writing(self, pack: Path) -> None:
        before = fingerprint(pack)
        stale = apex_diff(pack).replace("player_vy += gravity * delta", "player_vy += GRAVITY_CONSTANT * delta")
        with pytest.raises(CodeEditRefused) as exc:
            edit_project_code(pack, GD, stale)
        body = refusal(exc.value)
        assert body["error"] == "diff_did_not_apply"
        assert body["hunk"] == 1 and body["expected"]
        assert fingerprint(pack) == before, "a refused diff writes nothing at all"

    def test_a_two_hunk_diff_refuses_whole_when_only_the_second_is_stale(self, pack: Path) -> None:
        """Never partial: the first hunk is perfectly applicable."""
        before = fingerprint(pack)
        src = (pack / GD).read_text(encoding="utf-8")
        edited = src.replace(APEX_OLD, APEX_NEW)
        good = "".join(
            difflib.unified_diff(src.splitlines(keepends=True), edited.splitlines(keepends=True),
                                 fromfile=f"a/{GD}", tofile=f"b/{GD}")
        )
        bad_hunk = "@@ -1,1 +1,2 @@\n extends Node2D — not what line 1 says\n+var injected := 1\n"
        with pytest.raises(CodeEditRefused) as exc:
            edit_project_code(pack, GD, good + bad_hunk)
        assert refusal(exc.value)["error"] == "diff_did_not_apply"
        assert fingerprint(pack) == before

    def test_a_no_op_diff_refuses_rather_than_journalling_a_version(self, pack: Path) -> None:
        before = fingerprint(pack)
        src = (pack / GD).read_text(encoding="utf-8")
        line = src.splitlines()[10]
        noop = f"--- a/{GD}\n+++ b/{GD}\n@@ -11,1 +11,1 @@\n-{line}\n+{line}\n"
        with pytest.raises(CodeEditRefused) as exc:
            edit_project_code(pack, GD, noop)
        assert refusal(exc.value)["error"] == "diff_no_change"
        assert fingerprint(pack) == before

    def test_a_diff_naming_another_file_refuses(self, pack: Path) -> None:
        wrong = apex_diff(pack).replace(f"+++ b/{GD}", "+++ b/godot/other.gd")
        with pytest.raises(CodeEditRefused) as exc:
            edit_project_code(pack, GD, wrong)
        assert refusal(exc.value)["error"] == "diff_path_mismatch"

    def test_a_deletion_shaped_diff_refuses_rather_than_emptying_the_file(self, pack: Path) -> None:
        """`+++ /dev/null` names its target only on the `---` line, so a diff
        labelled for another file used to slip past the one-file check and
        truncate whatever `path` named."""
        before = fingerprint(pack)
        src = (pack / GD).read_text(encoding="utf-8").splitlines()[:3]
        killer = "--- a/godot/other.gd\n+++ /dev/null\n@@ -1,3 +0,0 @@\n" + "".join(f"-{line}\n" for line in src)
        with pytest.raises(CodeEditRefused) as exc:
            edit_project_code(pack, GD, killer)
        assert refusal(exc.value)["error"] == "diff_deletes_the_file"
        assert fingerprint(pack) == before

    def test_a_diff_naming_another_file_on_its_minus_side_refuses(self, pack: Path) -> None:
        wrong = apex_diff(pack).replace(f"--- a/{GD}", "--- a/godot/other.gd")
        with pytest.raises(CodeEditRefused) as exc:
            edit_project_code(pack, GD, wrong)
        assert refusal(exc.value)["error"] == "diff_path_mismatch"

    def test_an_empty_diff_refuses(self, pack: Path) -> None:
        with pytest.raises(CodeEditRefused) as exc:
            edit_project_code(pack, GD, "no hunks here\n")
        assert refusal(exc.value)["error"] == "diff_empty"

    def test_the_applier_places_a_hunk_whose_line_numbers_drifted(self) -> None:
        text = "a\nb\nc\nd\ne\n"
        patch = "@@ -40,3 +40,3 @@\n b\n-c\n+C\n d\n"
        out, stats = apply_unified_diff(text, patch)
        assert out == "a\nb\nC\nd\ne\n"
        assert stats == {"hunks": 1, "added": 1, "removed": 1}


# ---------------------------------------------------------------------------
# An unstamped pack still gets protected
# ---------------------------------------------------------------------------


def test_editing_an_unstamped_pack_stamps_a_baseline_first(pack: Path) -> None:
    """A pack that predates stamping would otherwise have its agent edit
    silently overwritten by the next sync."""
    (pack / godot_export.STAMP_REL).unlink()
    assert godot_export.engine_status(pack)["stamped"] is False
    edit_project_code(pack, GD, apex_diff(pack), actor="agent:c/game_coder")
    status = godot_export.engine_status(pack)
    assert status["stamped"] is True
    assert status["modified"] == [GD]
    assert godot_export.engine_sync(pack, dry_run=True)["refused"] == [GD]


# ---------------------------------------------------------------------------
# The ladder
# ---------------------------------------------------------------------------


class TestLadder:
    def test_the_rungs_run_in_order(self, pack: Path) -> None:
        ladder = run_ladder(pack, paths=[GD], levels=["l1"], probe=not_found_probe())
        assert [r["rung"] for r in ladder["rungs"]] == list(GATE_RUNGS)

    def test_without_godot_the_engine_rungs_skip_loudly_and_it_is_never_green(self, pack: Path) -> None:
        ladder = run_ladder(pack, paths=[GD], levels=["l1"], probe=not_found_probe())
        rungs = {r["rung"]: r for r in ladder["rungs"]}
        assert ladder["status"] == "unproven", "an absent engine is never 'ok'"
        assert ladder["unproven"] == ["boot", "smoke"]
        assert GODOT_MISSING in ladder["reason"]
        for name in ("boot", "smoke"):
            assert rungs[name]["ok"] is None
            assert GODOT_MISSING in rungs[name]["reason"]
        # …and the two rungs that need no engine still ran and still judged.
        assert rungs["syntax"]["status"] == "ok" and rungs["syntax"]["authoritative"] is False
        assert GODOT_MISSING in rungs["syntax"]["note"]
        assert rungs["validate"]["status"] == "ok"
        summary = ladder_summary(ladder)
        assert summary.startswith("gate ladder unproven")
        # The printed line must not read `syntax: ok` when the rung body says
        # the engine never parsed it — that is the one line the transcript,
        # `canon engine gate` and the run card all show.
        assert "syntax: ok (structural only)" in summary

    def test_a_failure_stops_the_ladder_and_surfaces_rather_than_passing(self, pack: Path) -> None:
        (pack / GD).write_text("func broken(:\n", encoding="utf-8")
        ladder = run_ladder(pack, paths=[GD], levels=["l1"], probe=not_found_probe())
        rungs = {r["rung"]: r for r in ladder["rungs"]}
        assert ladder["status"] == "failed" and ladder["failed_rung"] == "syntax"
        assert rungs["syntax"]["ok"] is False
        for name in ("boot", "smoke", "validate"):
            assert rungs[name]["status"] == "skipped"
            assert "stopped at the syntax rung" in rungs[name]["reason"]

    def test_a_failing_validate_rung_fails_the_ladder(self, pack: Path) -> None:
        def broken(level_id: str) -> dict:
            return {"ok": False, "checks": [{"name": "reachability", "problems": [f"{level_id}: no exit"]}]}

        ladder = run_ladder(pack, paths=[], levels=["l1"], validate=broken, probe=not_found_probe())
        rung = next(r for r in ladder["rungs"] if r["rung"] == "validate")
        assert ladder["status"] == "failed"
        assert rung["reports"][0]["problems"] == ["reachability: l1: no exit"]

    def test_an_engine_that_hangs_or_will_not_launch_is_never_a_syntax_pass(self, pack: Path, monkeypatch) -> None:
        """`_run_godot` answers `returncode: None` for both, and `None` used to
        read as a pass — a green, "authoritative" rung over a parse that never
        happened."""
        import canon.agent.gates as gates

        for spawn in (
            {"cmd": ["godot"], "returncode": None, "output": "", "timeout": gates.GATE_TIMEOUT_S},
            {"cmd": ["godot"], "returncode": None, "output": "OSError: nope", "unlaunchable": True},
        ):
            monkeypatch.setattr(gates, "_run_godot", lambda b, a, e, _s=spawn: dict(_s))
            rung = gates.rung_syntax(pack, [GD], "/nowhere/godot")
            assert rung["status"] == "failed" and rung["ok"] is False
            assert rung["checks"][0]["check_only"]["reason"] == "the engine did not finish"

    def test_a_file_the_engine_cannot_parse_is_not_reported_engine_proven(self, pack: Path) -> None:
        """`--check-only --script` parses GDScript only: handed a .tscn (a
        legal edit_project_code target) Godot prints `ERROR: Can't load
        script` — not SCRIPT ERROR — and exits 0."""
        import canon.agent.gates as gates

        assert gates.syntax_extensions() == (".gd",)
        rung = gates.rung_syntax(pack, ["godot/main.tscn"], godot_bin() or "/nowhere/godot")
        assert rung["authoritative"] is False, "the engine never judged the scene"
        assert rung["unjudged"] == ["godot/main.tscn"]
        assert "godot/main.tscn" in rung["note"]
        assert "structural only" in ladder_summary({"status": "ok", "rungs": [rung]})
        # …and a .gd file alongside it is still engine-judged when Godot is here.
        both = gates.rung_syntax(pack, [GD, "godot/main.tscn"], godot_bin() or "/nowhere/godot")
        assert both["unjudged"] == ["godot/main.tscn"]

    def test_an_engine_copy_with_no_project_file_is_unproven_never_green(self, tmp_path: Path, monkeypatch) -> None:
        """A `godot/**` edit in a tree with no project.godot is exactly the
        case where the engine never spoke; `skipped` is not counted, so this
        used to answer `ok` with no engine evidence at all."""
        import canon.agent.gates as gates

        bare = tmp_path / "bare"
        (bare / "godot").mkdir(parents=True)
        (bare / GD).write_text("extends Node\n", encoding="utf-8")
        monkeypatch.setattr(
            gates, "_run_godot", lambda b, a, e: {"cmd": ["godot"], "returncode": 0, "output": "", "timeout": None}
        )
        ladder = run_ladder(bare, paths=[GD], levels=[], binary="/nowhere/godot",
                            probe={"found": True, "path": "/nowhere/godot", "source": "PATH", "problems": []})
        rungs = {r["rung"]: r for r in ladder["rungs"]}
        assert ladder["status"] == "unproven"
        assert ladder["unproven"] == ["boot", "smoke"]
        assert "project.godot" in ladder["reason"], "it says its own reason, not 'godot not found'"
        assert rungs["boot"]["ok"] is None and rungs["smoke"]["ok"] is None

    def test_the_structural_check_is_the_engine_free_half(self) -> None:
        assert structural_check("func ok():\n\treturn (1 + 2)\n") == []
        assert structural_check("func bad(:\n\treturn 1\n")
        assert structural_check('var s := "a )"  # a bracket in a string is not a bracket\n') == []

    def test_smoke_levels_finds_the_packs_own_first_level(self, pack: Path) -> None:
        assert smoke_levels(pack) == ["l1"]

    @needs_godot
    def test_a_good_edit_boots_smokes_and_validates_green(self, pack: Path) -> None:
        edit_project_code(pack, GD, apex_diff(pack), actor="agent:conv1/game_coder", session="conv1")
        ladder = run_ladder(pack, paths=[GD], levels=["l1"])
        rungs = {r["rung"]: r for r in ladder["rungs"]}
        assert ladder["status"] == "ok", ladder_summary(ladder)
        assert rungs["syntax"]["authoritative"] is True
        assert rungs["boot"]["script_errors"] == 0
        assert rungs["smoke"]["traj_lines"] > 100, "the smoke proves the trajectory, not the exit code"

    @needs_godot
    def test_a_script_error_fails_the_boot_rung_even_though_godot_exits_zero(self, pack: Path) -> None:
        source = (pack / GD).read_text(encoding="utf-8")
        # A runtime error at _ready: parses fine, so only a real boot catches it.
        broken = source.replace("func _ready() -> void:\n", "func _ready() -> void:\n\tvar _x = null.nope\n", 1)
        assert broken != source
        (pack / GD).write_text(broken, encoding="utf-8")
        ladder = run_ladder(pack, paths=[], levels=["l1"])
        rungs = {r["rung"]: r for r in ladder["rungs"]}
        assert rungs["boot"]["ok"] is False
        assert rungs["boot"]["script_errors"] >= 1
        assert rungs["boot"]["returncode"] == 0, "the exit code lies — the SCRIPT ERROR count is the verdict"
        assert ladder["status"] == "failed"


# ---------------------------------------------------------------------------
# The verify loop's code leg (A7's loop, one more leg — never a second path)
# ---------------------------------------------------------------------------


def manager_for(pack: Path) -> RunManager:
    manager = RunManager(
        pack_dir=pack,
        registry=code_registry(pack),
        backend=FakeChatBackend([]),
        store=ConversationStore(pack),
        roster=load_roster(),
    )
    manager.register_tools()
    return manager


def _run_with(manager: RunManager, artifacts: list[dict]):
    from canon.agent.runs import Run

    return Run(
        run_id="run_1", conversation="conv1", specialist="game_coder",
        actor="agent:conv1/game_coder", task="floatier apex", refs=[], budget=None,
        tools=[], dropped=[], model=None, batch_id=None, artifacts=artifacts,
    )


class TestVerifyLoopCodeLeg:
    def test_a_code_artifact_runs_the_ladder_inside_verify_run(self, pack: Path, monkeypatch) -> None:
        import canon.agent.gates as gates

        seen: dict = {}

        def fake_ladder(pack_dir, **kwargs):
            seen.update(kwargs)
            return {"status": "ok", "rungs": [], "unproven": [], "godot": {"found": True}}

        monkeypatch.setattr(gates, "run_ladder", fake_ladder)
        manager = manager_for(pack)
        verdict = manager.verify_run(_run_with(manager, [{"id": f"code:{GD}", "op": "edit"}]))
        assert verdict["status"] == "ok"
        assert verdict["code"] == [GD]
        check = next(c for c in verdict["checks"] if c["kind"] == "code")
        assert check["targets"] == [GD]
        # A pure code edit still gets a level to smoke and validate.
        assert seen["paths"] == [GD] and seen["levels"] == ["l1"]
        assert callable(seen["validate"]), "the ladder validates through the REGISTERED validate_level"

    def test_a_failing_ladder_fails_the_verdict(self, pack: Path, monkeypatch) -> None:
        import canon.agent.gates as gates

        monkeypatch.setattr(
            gates, "run_ladder",
            lambda pack_dir, **kw: {"status": "failed", "failed_rung": "boot", "rungs": [], "unproven": []},
        )
        manager = manager_for(pack)
        verdict = manager.verify_run(_run_with(manager, [{"id": f"code:{GD}", "op": "edit"}]))
        assert verdict["status"] == "failed"
        assert next(c for c in verdict["checks"] if c["kind"] == "code")["ok"] is False

    def test_an_unproven_ladder_is_a_skip_never_a_pass(self, pack: Path, monkeypatch) -> None:
        import canon.agent.gates as gates

        real = gates.run_ladder
        monkeypatch.setattr(gates, "run_ladder", lambda p, **kw: real(p, probe=not_found_probe(), **kw))
        manager = manager_for(pack)
        verdict = manager.verify_run(_run_with(manager, [{"id": f"code:{GD}", "op": "edit"}]))
        check = next(c for c in verdict["checks"] if c["kind"] == "code")
        assert check["ok"] is None
        assert verdict["status"] == "skipped"
        assert GODOT_MISSING in check["reason"]

    def test_a_run_with_no_code_artifact_never_runs_the_ladder(self, pack: Path, monkeypatch) -> None:
        import canon.agent.gates as gates

        monkeypatch.setattr(gates, "run_ladder", lambda *a, **k: pytest.fail("the ladder ran for a data-only run"))
        manager = manager_for(pack)
        verdict = manager.verify_run(_run_with(manager, [{"id": "level:s1/l1/entities", "op": "edit"}]))
        assert [c["kind"] for c in verdict["checks"]] == ["level"]


class TestTheWholeTraceA2:
    """Trace A2 end to end, through the real run manager: the foreman
    delegates, ``game_coder`` edits the pack's own ``main.gd`` behind an
    ask chip, and the ladder runs INSIDE that run without anyone calling it."""

    def _drive(self, pack: Path, diff: str):
        from canon.agent.actors import agent_actor
        from canon.agent.runs import DELEGATE_TOOL

        script: dict[str, list] = {
            "game_coder": [
                [{"type": "tool_use", "name": "edit_project_code", "input": {"path": GD, "diff": diff}}],
                [{"type": "text", "text": "Apex eased near the top of the arc."}],
            ]
        }

        def backend_script(request):
            role = "game_coder" if "`game_coder`" in (request.system or "") else "foreman"
            turns = script.get(role) or []
            return turns.pop(0) if turns else [{"type": "text", "text": f"({role} done)"}]

        manager = RunManager(
            pack_dir=pack, registry=code_registry(pack), backend=FakeChatBackend(backend_script),
            store=ConversationStore(pack), roster=load_roster(),
        )
        manager.register_tools()
        conversation = manager.store.create("fake", None, None)
        engine = manager.registry.permissions
        chips: list = []

        def on_request(request) -> None:
            chips.append(request)
            engine.decide(request.request_id, "accept")

        with manager.turn(conversation, emit=lambda e, d: None):
            with engine.listen(conversation, on_request=on_request, on_decision=lambda r, d: None):
                with bind_call(agent_actor(conversation, "foreman"), conversation):
                    result = json.loads(manager.registry.execute(
                        DELEGATE_TOOL,
                        {"specialist": "game_coder", "task": "Give the double jump a floatier apex."},
                        actor=agent_actor(conversation, "foreman"), conversation=conversation,
                    ))
        return result, chips, conversation

    @needs_godot
    def test_the_edit_lands_the_ladder_runs_green_and_the_stamp_holds(self, pack: Path) -> None:
        result, chips, conversation = self._drive(pack, apex_diff(pack))
        assert result["status"] == "ok", result.get("error")
        assert [c.tool for c in chips] == ["edit_project_code"]
        assert chips[0].tier == "ask" and chips[0].target == f"edit the project's own {GD}"
        assert chips[0].input["diff"].startswith(f"--- a/{GD}")
        assert result["artifacts_touched"] == [
            {"id": f"code:{GD}", "before": result["artifacts_touched"][0]["before"],
             "after": result["artifacts_touched"][0]["after"], "op": "edit"}
        ]
        verify = result["verify"]
        assert verify["status"] == "ok" and verify["code"] == [GD]
        ladder = next(c for c in verify["checks"] if c["kind"] == "code")["ladder"]
        assert [r["rung"] for r in ladder["rungs"]] == list(GATE_RUNGS)
        assert ladder["status"] == "ok" and ladder["levels"] == ["l1"]
        assert APEX_NEW in (pack / GD).read_text(encoding="utf-8")
        assert godot_export.engine_status(pack)["attribution"][GD]["actor"] == (
            f"agent:{conversation}/game_coder"
        )

    @needs_godot
    def test_an_edit_that_breaks_the_running_game_fails_the_run(self, pack: Path) -> None:
        """The ladder is not a rubber stamp. ``APEX_BROKEN`` compiles and
        boots clean and only comes apart once the game is actually played —
        which is precisely what the smoke rung is for."""
        result, _chips, _conversation = self._drive(pack, apex_diff(pack, new=APEX_BROKEN))
        assert result["status"] == "failed", "a red ladder must never come back as done"
        error = json.loads(result["error"])
        assert error["error"] == "verify_failed"
        check = next(c for c in error["verify"]["checks"] if c["kind"] == "code")
        assert check["ok"] is False
        ladder = check["ladder"]
        assert ladder["failed_rung"] == "smoke"
        rungs = {r["rung"]: r for r in ladder["rungs"]}
        assert rungs["syntax"]["ok"] is True and rungs["boot"]["ok"] is True
        assert rungs["smoke"]["script_errors"] > 0
        assert rungs["smoke"]["returncode"] == 0, "the exit code lies; the run's own output is the verdict"
        # The edit still LANDED (nothing is silently rolled back) and the
        # failure came back to the foreman with the evidence.
        assert APEX_BROKEN in (pack / GD).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# One-click restore, through the EXISTING restore path
# ---------------------------------------------------------------------------


def test_restore_reverts_the_file_and_clears_the_modified_stamp(pack: Path) -> None:
    registry = code_registry(pack)
    with bind_call("agent:conv1/game_coder", "conv1"):
        written = json.loads(registry.get("edit_project_code").run({"path": GD, "diff": apex_diff(pack)}))
    assert godot_export.engine_status(pack)["code_evolved"] is True

    with bind_call("cradle:user", "conv1"):
        result = json.loads(
            registry.get("restore").run({"target": f"code:{GD}", "version_hash": written["before_hash"]})
        )
    assert result["stamp_cleared"] is True and result["new_hash"] == written["before_hash"]
    assert APEX_NEW not in (pack / GD).read_text(encoding="utf-8")

    status = godot_export.engine_status(pack)
    assert status["modified"] == [] and status["attribution"] == {} and status["code_evolved"] is False
    assert godot_export.engine_sync(pack, dry_run=True)["refused"] == []
    # Nothing is deleted: the restore is a NEW version on the same lineage.
    ops = [e["op"] for e in provenance.all_events(pack) if e.get("artifact_id") == f"code:{GD}"]
    assert ops == ["edit", "restore"]


def test_restore_only_rewinds_this_artifacts_own_lineage(pack: Path) -> None:
    edit_project_code(pack, GD, apex_diff(pack), actor="agent:c/game_coder")
    stray = provenance.snapshot_bytes(pack, b"bytes from nowhere\n")
    with pytest.raises(CodeEditRefused) as exc:
        from canon.engine_ops import restore_code_file

        restore_code_file(pack, f"code:{GD}", stray)
    assert refusal(exc.value)["error"] == "restore_not_in_lineage"


# ---------------------------------------------------------------------------
# The probe flag + the roster
# ---------------------------------------------------------------------------


class TestProbeAndRoster:
    def test_the_probe_reports_code_evolved_with_the_interim_rule(self, pack: Path) -> None:
        from canon.packs import pack_info

        assert pack_info(pack)["engine_copy"]["code_evolved"] is False
        edit_project_code(pack, GD, apex_diff(pack), actor="agent:conv1/game_coder", session="conv1")
        block = pack_info(pack)["engine_copy"]
        assert block["engine"] == "godot" and block["present"] is True
        assert block["code_evolved"] is True and block["modified"] == [GD]
        assert block["attribution"][GD]["actor"] == "agent:conv1/game_coder"
        assert "BEFORE" in block["disclose"]
        assert block["note"] == TEMPLATE_PHYSICS_NOTE
        assert "W2.0" in block["note"], "the interim rule says where it ends"

    def test_describe_pack_carries_it_to_the_model(self, pack: Path) -> None:
        edit_project_code(pack, GD, apex_diff(pack))
        registry = code_registry(pack)
        info = json.loads(registry.get("describe_pack").run({}))
        assert info["engine_copy"]["code_evolved"] is True

    def test_the_assembled_prompt_states_the_disclosure_obligation(self, pack: Path) -> None:
        from canon.agent.prompt import CODE_EVOLVED_DISCLOSURE, assemble

        edit_project_code(pack, GD, apex_diff(pack))
        prompt = assemble(pack, load_roster()["game_coder"])
        assert "Engine: godot" in prompt and "CODE-EVOLVED" in prompt
        assert CODE_EVOLVED_DISCLOSURE in prompt
        assert TEMPLATE_PHYSICS_NOTE in prompt

    def test_an_edited_file_the_template_lacks_still_reads_code_evolved(self, pack: Path) -> None:
        """`engine_status` classified only the CURRENT template's files, so an
        attributed edit to any other engine-copy file (a hand-added
        `godot/hud.gd`, or one a later template drops) left the pack answering
        `code_evolved: false` and §7.1's disclosure never fired for it."""
        extra = "godot/hud.gd"
        (pack / extra).write_text("extends Node\nfunc a():\n\tpass\n", encoding="utf-8")
        assert extra not in godot_export.template_files()
        diff = f"--- a/{extra}\n+++ b/{extra}\n@@ -1,3 +1,4 @@\n extends Node\n func a():\n \tpass\n+\t# tweak\n"
        edit_project_code(pack, extra, diff, actor="agent:conv1/game_coder", session="conv1")

        status = godot_export.engine_status(pack)
        assert extra in status["modified"]
        assert status["attribution"][extra]["actor"] == "agent:conv1/game_coder"
        assert next(f for f in status["files"] if f["path"] == extra)["in_template"] is False
        assert status["code_evolved"] is True
        block = code_evolved(pack)
        assert block["code_evolved"] is True and extra in block["modified"]
        # …and sync has no template bytes for it, so it is refused, never written.
        assert godot_export.engine_sync(pack, dry_run=True, force=True)["refused"] == [extra]

    def test_the_pygame_surfaces_carry_the_interim_rule_when_the_pack_is_evolved(self, pack: Path) -> None:
        """Master §3.0-I asks for the rule where a reader HITS it: the frames
        and trajectory a code-evolved pack returns run TEMPLATE physics."""
        from canon.agent.tools_vision import _template_physics_note

        assert _template_physics_note(pack) is None
        edit_project_code(pack, GD, apex_diff(pack), actor="agent:conv1/game_coder")
        assert _template_physics_note(pack) == TEMPLATE_PHYSICS_NOTE

    def test_a_pack_with_no_engine_copy_says_so_rather_than_guessing(self, tmp_path: Path) -> None:
        bare = tmp_path / "bare"
        bare.mkdir()
        block = code_evolved(bare)
        assert block["present"] is False and block["code_evolved"] is False
        assert "no engine copy" in block["reason"]

    def test_game_coder_now_resolves_edit_project_code(self, pack: Path) -> None:
        manager = manager_for(pack)
        entry = next(row for row in manager.roster_report() if row["id"] == "game_coder")
        assert entry["missing"] == [], f"game_coder still cannot reach {entry['missing']}"
        for name in CODE_TOOL_NAMES:
            assert name in entry["available"]
        assert "edit_project_code" in entry["available"]

    def test_the_tools_register_at_the_right_tiers(self, pack: Path) -> None:
        registry = code_registry(pack)
        assert registry.get("engine_status").tier == "auto"
        assert registry.get("engine_sync").tier == "ask"
        assert registry.get("edit_project_code").tier == "ask"
        assert registry.permissions.never_always_reason("engine_sync") == SYNC_NEVER_ALWAYS

    def test_the_edit_chip_names_the_file(self, pack: Path) -> None:
        engine = code_registry(pack).permissions
        tool = code_registry(pack).get("edit_project_code")
        assert engine.target_for(tool, {"path": GD, "diff": ""}) == f"edit the project's own {GD}"

    def test_engine_status_as_a_tool_is_a_pure_read(self, pack: Path) -> None:
        registry = code_registry(pack)
        before = fingerprint(pack)
        with bind_call("agent:conv1/game_coder", "conv1"):
            doc = json.loads(registry.get("engine_status").run({}))
        assert doc["engine_copy"]["code_evolved"] is False
        assert doc["status"]["current"] is True
        assert fingerprint(pack) == before
