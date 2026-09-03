"""Row P0-4 (W3.2 packaging surgery) — the packs live INSIDE the package.

Guards the wheel-acceptance contract at unit-test speed: the two built-in
packs import without ``examples/`` anywhere near ``sys.path``, nothing under
``src/canon`` reaches back into ``examples``, the pack DATA (schemas, cost
model, rule overrides, Godot template, graphics lanes, ``canon.toml``) is
addressable as package data, and the slice runner runs as a module from a
neutral cwd. The full gate (fresh venv, wheel only, every verb) is run by
hand at the row's exit — these tests are the cheap standing proxy.
"""

from __future__ import annotations

import re
import subprocess
import sys
from importlib import resources
from pathlib import Path

SRC_CANON = Path(__file__).resolve().parents[1] / "src" / "canon"

_EXAMPLES_IMPORT = re.compile(r"^\s*(from\s+examples\b|import\s+examples\b)", re.MULTILINE)


class TestPacksInsideThePackage:
    def test_packs_import_without_examples(self, tmp_path: Path) -> None:
        """Both packs import from a neutral cwd and never drag ``examples`` in.

        ``-I`` would also drop the editable-install path, so instead the
        child runs from a temp cwd (no repo root on ``sys.path``) and proves
        ``examples`` was never imported.
        """
        code = (
            "import sys\n"
            "import canon.packs.platformer.ops\n"
            "import canon.packs.dungeon.specs\n"
            "import canon.packs.platformer.run_slice\n"
            "assert 'examples' not in sys.modules, sorted(m for m in sys.modules if m.startswith('examples'))\n"
            "print('ok')\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "ok"

    def test_no_module_under_src_canon_imports_examples(self) -> None:
        offenders = [
            str(p.relative_to(SRC_CANON.parent))
            for p in sorted(SRC_CANON.rglob("*.py"))
            if _EXAMPLES_IMPORT.search(p.read_text(encoding="utf-8"))
        ]
        assert offenders == []

    def test_no_repo_root_resolver_under_src_canon(self) -> None:
        """The four ``parents[2]`` repo-root sites are gone (W3.2 "one resolver")."""
        offenders = [
            str(p.relative_to(SRC_CANON.parent))
            for p in sorted(SRC_CANON.rglob("*.py"))
            if "parents[2]" in p.read_text(encoding="utf-8")
        ]
        assert offenders == []


class TestPackageData:
    def test_platformer_pack_data_is_package_data(self) -> None:
        root = resources.files("canon.packs.platformer")
        for rel in (
            "godot_template/project.godot",
            "godot_template/godot/main.gd",
            "godot_template/godot/main.tscn",
            "cost_model.json",
            "rule_overrides.json",
            "schemas/enemy.json",
            "schemas/item.json",
            "schemas/level_layout.json",
            "graphics_specs/hand_drawn_16bit.json",
            "graphics.json",
            "README.md",
        ):
            assert root.joinpath(rel).is_file(), rel

    def test_mazeworld_pack_data_is_package_data(self) -> None:
        assert resources.files("canon.packs.dungeon").joinpath("canon.toml").is_file()


class TestSliceRunnerAsModule:
    def test_run_slice_help_from_neutral_cwd(self, tmp_path: Path) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "canon.packs.platformer.run_slice", "--help"],
            cwd=tmp_path, capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert "--output-dir" in proc.stdout


class TestPlayHarnessAsModule:
    """The pygame play harness moved INTO the package (2026-09-01,
    ``canon.packs.platformer.play``) so a bundled cradle can ▶ Play a level
    before W2.0 — extends the ``TestSliceRunnerAsModule`` proxy above to the
    second promoted entry point."""

    def test_play_help_from_neutral_cwd(self, tmp_path: Path) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "canon.packs.platformer.play", "--help"],
            cwd=tmp_path, capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert "PLAT_ANIM" in proc.stdout

    def test_importing_the_pack_never_imports_pygame(self, tmp_path: Path) -> None:
        """pygame is an optional extra (``play``); the pack — and the harness
        module itself — must import without dragging it in (it is imported
        lazily inside the entry points, exactly where it was)."""
        code = (
            "import sys\n"
            "import canon.packs.platformer\n"
            "import canon.packs.platformer.play\n"
            "assert 'pygame' not in sys.modules, sorted(m for m in sys.modules if m.startswith('pygame'))\n"
            "print('ok')\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "ok"
