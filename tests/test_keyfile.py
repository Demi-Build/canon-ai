"""The .env key file is the ONLY source of provider keys (``canon.keyfile``).

The rule under test: a provider key the shell exports is never read; the key
file wins, and when the file does not carry a key, there is no key. First use
(``canon keys setup`` / ``canon keys set``) creates the file, and changing a
key rewrites only that key's line.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import canon.cli.main as cli
from canon import keyfile


def _env(**pairs: str) -> dict[str, str]:
    return dict(pairs)


# ---------------------------------------------------------------------------
# apply — the file wins, the shell is never read
# ---------------------------------------------------------------------------


def test_the_file_beats_a_shell_export_of_the_same_key(tmp_path: Path) -> None:
    f = tmp_path / ".env"
    f.write_text("ANTHROPIC_API_KEY=from-the-file\n")
    env = _env(ANTHROPIC_API_KEY="from-the-shell")
    out = keyfile.apply(f, env)
    assert env["ANTHROPIC_API_KEY"] == "from-the-file"
    assert out["loaded"] == ["ANTHROPIC_API_KEY"]
    assert out["ignored_shell"] == ["ANTHROPIC_API_KEY"]


def test_a_key_only_the_shell_has_is_dropped_not_used(tmp_path: Path) -> None:
    f = tmp_path / ".env"
    f.write_text("FAL_KEY=fal-from-file\n")
    env = _env(OPENAI_API_KEY="shell-only", FAL_KEY_ID="shell-id", PATH="/bin")
    keyfile.apply(f, env)
    assert "OPENAI_API_KEY" not in env, "no fallback to the shell"
    assert "FAL_KEY_ID" not in env, "fal's id/secret pair is a key too"
    assert env["FAL_KEY"] == "fal-from-file"
    assert env["PATH"] == "/bin", "only key names are touched"


def test_no_file_means_no_keys(tmp_path: Path) -> None:
    env = _env(ANTHROPIC_API_KEY="shell", PIXELLAB_API_KEY="shell")
    out = keyfile.apply(tmp_path / "missing.env", env)
    assert out["exists"] is False
    assert not any(n in env for n in keyfile.key_names())


def test_non_key_settings_keep_shell_precedence(tmp_path: Path) -> None:
    f = tmp_path / ".env"
    f.write_text("CANON_PLAT_SEED=from-file\nOTHER_SETTING=file\n")
    env = _env(CANON_PLAT_SEED="from-shell")
    keyfile.apply(f, env)
    assert env["CANON_PLAT_SEED"] == "from-shell"
    assert env["OTHER_SETTING"] == "file"


def test_a_second_apply_switches_files_wholly(tmp_path: Path) -> None:
    """The root callback applies the default file, then a verb's --env-file
    applies its own: keys from the first file must not linger, and the first
    file's values must not be mistaken for shell exports."""
    a, b = tmp_path / "a.env", tmp_path / "b.env"
    a.write_text("ANTHROPIC_API_KEY=a\nOPENAI_API_KEY=a\n")
    b.write_text("ANTHROPIC_API_KEY=b\n")
    env: dict[str, str] = {}
    keyfile.apply(a, env)
    out = keyfile.apply(b, env)
    assert env["ANTHROPIC_API_KEY"] == "b"
    assert "OPENAI_API_KEY" not in env
    assert out["ignored_shell"] == []


def test_resolve_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert keyfile.resolve_path(None, {}) == tmp_path / ".env"
    assert keyfile.resolve_path(None, {"CANON_ENV_FILE": "/x/keys.env"}) == Path("/x/keys.env")
    assert keyfile.resolve_path(Path("/y.env"), {"CANON_ENV_FILE": "/x"}) == Path("/y.env")


def test_parse_dialect_matches_cradle() -> None:
    assert keyfile.parse_line("export FAL_KEY=\"v\"") == ("FAL_KEY", "v")
    assert keyfile.parse_line("  K='v'  ") == ("K", "v")
    assert keyfile.parse_line("# K=v") is None
    assert keyfile.parse_line("no equals") is None
    assert keyfile.parse_line("=v") is None


def test_every_row_name_is_a_key_name() -> None:
    from canon.providers import env_vars

    names = keyfile.key_names()
    assert set(env_vars()) <= set(names)
    assert {"FAL_KEY_ID", "FAL_KEY_SECRET"} <= set(names)


# ---------------------------------------------------------------------------
# Writing — first use creates the file, a change rewrites one line
# ---------------------------------------------------------------------------


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def test_first_set_creates_the_file_owner_only_with_header(tmp_path: Path) -> None:
    f = tmp_path / "sub" / ".env"
    keyfile.set_key(f, "ANTHROPIC_API_KEY", "  sk-1  ")
    text = f.read_text()
    assert text.startswith("# Provider keys")
    assert text.endswith("ANTHROPIC_API_KEY=sk-1\n")
    if os.name == "posix":
        assert _mode(f) == 0o600


def test_changing_a_key_rewrites_only_its_line(tmp_path: Path) -> None:
    f = tmp_path / ".env"
    f.write_text("# mine\nexport FAL_KEY=\"old\"\n\nGOOGLE_API_KEY=g\nFAL_KEY=dupe\n# tail\n")
    keyfile.set_key(f, "FAL_KEY", "new")
    assert f.read_text() == "# mine\nFAL_KEY=new\n\nGOOGLE_API_KEY=g\n# tail\n"
    keyfile.set_key(f, "OPENAI_API_KEY", "o")
    assert f.read_text().endswith("# tail\nOPENAI_API_KEY=o\n")
    if os.name == "posix":
        assert _mode(f) == 0o600


def test_remove_and_refusals(tmp_path: Path) -> None:
    f = tmp_path / ".env"
    assert keyfile.remove_key(f, "FAL_KEY") is False
    assert not f.exists()
    with pytest.raises(ValueError):
        keyfile.set_key(f, "FAL_KEY", "   ")
    with pytest.raises(ValueError):
        keyfile.set_key(f, "NOT A NAME", "x")
    with pytest.raises(ValueError):
        keyfile.set_key(f, "FAL_KEY", "a\nb")
    assert not f.exists(), "a refused save creates nothing"
    f.write_text("A=1\nFAL_KEY=x\n")
    assert keyfile.remove_key(f, "FAL_KEY") is True
    assert f.read_text() == "A=1\n"


def test_create_is_idempotent(tmp_path: Path) -> None:
    f = tmp_path / ".env"
    assert keyfile.create(f) is True
    before = f.read_text()
    assert keyfile.create(f) is False
    assert f.read_text() == before


# ---------------------------------------------------------------------------
# The CLI — every verb runs with the file as the only source
# ---------------------------------------------------------------------------


def _canon(*args: str, env: dict[str, str], stdin: str = "") -> subprocess.CompletedProcess[str]:
    """The real CLI in a child process — exactly how cradle and a terminal run it."""
    return subprocess.run(
        [sys.executable, "-m", "canon.cli.main", *args],
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def _child_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in keyfile.key_names()}
    env.pop(keyfile.ENV_FILE_VAR, None)
    env.update(extra)
    return env


def test_cli_ignores_a_shell_export(tmp_path: Path) -> None:
    """The bug this module exists for: a stale shell export must never win,
    and must never be used when the file lacks that key."""
    f = tmp_path / ".env"
    f.write_text("GOOGLE_API_KEY=g\n")
    env = _child_env(tmp_path, CANON_ENV_FILE=str(f), ANTHROPIC_API_KEY="sk-CANARY-shell", GOOGLE_API_KEY="stale")
    proc = _canon("providers", "list", env=env)
    assert proc.returncode == 0, proc.stderr
    assert "CANARY" not in proc.stdout + proc.stderr
    status = {r["id"]: r for r in json.loads(proc.stdout)["key_status"]}
    assert status["anthropic"]["set"] is False, "the shell's key is not used"
    assert status["lyria"]["set"] is True

    proc = _canon("keys", "status", env=env)
    doc = json.loads(proc.stdout)
    assert doc["ignored_shell"] == ["ANTHROPIC_API_KEY", "GOOGLE_API_KEY"]
    assert "stale" not in proc.stdout


def test_cli_reads_dot_env_in_the_working_directory(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("OPENAI_API_KEY=o\n")
    env = _child_env(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "canon.cli.main", "providers", "list"],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    status = {r["id"]: r["set"] for r in json.loads(proc.stdout)["key_status"]}
    assert status["openai"] is True


def test_cli_keys_set_from_stdin_then_status(tmp_path: Path) -> None:
    f = tmp_path / ".env"
    env = _child_env(tmp_path)
    proc = _canon("keys", "set", "anthropic", "--stdin", "--env-file", str(f), env=env, stdin="sk-CANARY-1\n")
    assert proc.returncode == 0, proc.stderr
    assert "CANARY" not in proc.stdout + proc.stderr
    assert json.loads(proc.stdout)["var"] == "ANTHROPIC_API_KEY"
    assert "ANTHROPIC_API_KEY=sk-CANARY-1" in f.read_text()

    proc = _canon("keys", "set", "ANTHROPIC_API_KEY", "--stdin", "--env-file", str(f), env=env, stdin="sk-2\n")
    assert proc.returncode == 0, proc.stderr
    assert f.read_text().count("ANTHROPIC_API_KEY=") == 1
    assert "ANTHROPIC_API_KEY=sk-2" in f.read_text()

    proc = _canon("keys", "status", "--env-file", str(f), env=env)
    doc = json.loads(proc.stdout)
    assert "sk-2" not in proc.stdout
    assert doc["exists"] is True
    assert {r["id"]: r["set"] for r in doc["providers"]}["anthropic"] is True

    proc = _canon("keys", "remove", "anthropic", "--env-file", str(f), env=env)
    assert json.loads(proc.stdout)["removed"] is True
    assert "ANTHROPIC_API_KEY" not in f.read_text()


def test_cli_keys_setup_refuses_without_a_terminal(tmp_path: Path) -> None:
    proc = _canon("keys", "setup", "--env-file", str(tmp_path / ".env"), env=_child_env(tmp_path))
    assert proc.returncode == 1
    assert "terminal" in proc.stderr
    assert not (tmp_path / ".env").exists()


def test_keys_setup_first_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """First run: no file yet; each prompt is hidden; Enter skips; the file is
    created with what was entered and nothing else. A second run only asks for
    what is still missing."""
    f = tmp_path / ".env"
    emitted: list[dict] = []
    prompts: list[str] = []
    answers = iter(["sk-anthropic", "", "g-key"] + [""] * 20)

    def fake_ask(prompt: str) -> str:
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr(cli, "_ask_secret", fake_ask)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr(cli, "_emit", lambda data, **_: emitted.append(data))
    cli.keys_setup_cmd(env_file=f, all_rows=False)
    doc = emitted[-1]
    assert doc["saved"] == ["ANTHROPIC_API_KEY", "GOOGLE_API_KEY"]
    assert "FAL_KEY" in doc["skipped"]
    assert "sk-anthropic" not in json.dumps(doc)
    assert keyfile.read_pairs(f) == {"ANTHROPIC_API_KEY": "sk-anthropic", "GOOGLE_API_KEY": "g-key"}
    if os.name == "posix":
        assert _mode(f) == 0o600

    prompts.clear()
    answers = iter([""] * 20)
    cli.keys_setup_cmd(env_file=f, all_rows=False)
    assert not any("ANTHROPIC_API_KEY" in p or "GOOGLE_API_KEY" in p for p in prompts)
    assert emitted[-1]["already_set"] == ["anthropic", "lyria"]
