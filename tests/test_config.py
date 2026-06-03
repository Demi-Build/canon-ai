"""Tests for canon.config.CanonConfig — defaults, loaders, round-trips."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from canon.config import CanonConfig

# --- Defaults / required fields ---


def test_defaults_match_spec():
    """Default values must match the documented v0.1 contract."""
    cfg = CanonConfig(seed="test_seed")

    assert cfg.seed == "test_seed"
    assert cfg.num_maps == 3
    assert cfg.max_retries == 3
    assert cfg.max_llm_workers == 8
    assert cfg.context_limit_chars == 20000
    assert cfg.output_dir == Path("./canon_output")
    assert cfg.backend_llm == "anthropic"


def test_seed_is_required():
    """`seed` has no default; constructing without it must raise."""
    with pytest.raises(ValidationError):
        CanonConfig()  # type: ignore[call-arg]


def test_overrides_take_effect():
    cfg = CanonConfig(
        seed="abc",
        num_maps=7,
        max_retries=5,
        max_llm_workers=2,
        context_limit_chars=4096,
        output_dir=Path("/tmp/canon_x"),
        backend_llm="huggingface",
    )
    assert cfg.num_maps == 7
    assert cfg.max_retries == 5
    assert cfg.max_llm_workers == 2
    assert cfg.context_limit_chars == 4096
    assert cfg.output_dir == Path("/tmp/canon_x")
    assert cfg.backend_llm == "huggingface"


# --- output_dir polymorphism ---


def test_output_dir_accepts_string():
    cfg = CanonConfig(seed="s", output_dir="some/relative/dir")  # type: ignore[arg-type]
    assert isinstance(cfg.output_dir, Path)
    assert cfg.output_dir == Path("some/relative/dir")


def test_output_dir_accepts_path():
    p = Path("another/dir")
    cfg = CanonConfig(seed="s", output_dir=p)
    assert isinstance(cfg.output_dir, Path)
    assert cfg.output_dir == p


# --- from_dict round-trip ---


def test_from_dict_round_trip():
    original = CanonConfig(
        seed="round_trip",
        num_maps=4,
        max_retries=2,
        backend_llm="local",
    )
    dumped = original.model_dump()
    rebuilt = CanonConfig.from_dict(dumped)
    assert rebuilt == original


def test_from_dict_minimal():
    cfg = CanonConfig.from_dict({"seed": "hello"})
    assert cfg.seed == "hello"
    assert cfg.num_maps == 3  # default still applied


# --- JSON loader / writer ---


def test_from_json_reads_file(tmp_path: Path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"seed": "json_seed", "num_maps": 5}))
    cfg = CanonConfig.from_json(p)
    assert cfg.seed == "json_seed"
    assert cfg.num_maps == 5
    assert cfg.max_retries == 3  # default preserved


def test_from_json_accepts_string_path(tmp_path: Path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"seed": "stringy"}))
    cfg = CanonConfig.from_json(str(p))
    assert cfg.seed == "stringy"


def test_from_json_missing_seed_raises(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"num_maps": 5}))
    with pytest.raises(ValidationError):
        CanonConfig.from_json(p)


def test_to_json_returns_string():
    cfg = CanonConfig(seed="abc")
    text = cfg.to_json()
    parsed = json.loads(text)
    assert parsed["seed"] == "abc"
    assert parsed["num_maps"] == 3


def test_to_json_writes_file(tmp_path: Path):
    cfg = CanonConfig(seed="written", num_maps=9, output_dir=Path("./out"))
    p = tmp_path / "out" / "config.json"
    cfg.to_json(p)
    assert p.exists()
    rebuilt = CanonConfig.from_json(p)
    assert rebuilt == cfg


def test_to_json_creates_parent_dirs(tmp_path: Path):
    cfg = CanonConfig(seed="x")
    p = tmp_path / "deeply" / "nested" / "config.json"
    cfg.to_json(p)
    assert p.exists()


# --- TOML loader ---


def test_from_toml_flat(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text(
        'seed = "toml_seed"\n'
        "num_maps = 6\n"
        "max_retries = 1\n"
    )
    cfg = CanonConfig.from_toml(p)
    assert cfg.seed == "toml_seed"
    assert cfg.num_maps == 6
    assert cfg.max_retries == 1
    assert cfg.max_llm_workers == 8  # default preserved


def test_from_toml_with_canon_section(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text(
        '[canon]\n'
        'seed = "section_seed"\n'
        "num_maps = 11\n"
    )
    cfg = CanonConfig.from_toml(p)
    assert cfg.seed == "section_seed"
    assert cfg.num_maps == 11


def test_from_toml_canon_section_takes_precedence(tmp_path: Path):
    """If a [canon] table exists, it wins over any top-level keys."""
    p = tmp_path / "config.toml"
    p.write_text(
        'seed = "top_level"\n'
        "\n"
        "[canon]\n"
        'seed = "canon_table"\n'
    )
    cfg = CanonConfig.from_toml(p)
    assert cfg.seed == "canon_table"


def test_from_toml_accepts_string_path(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text('seed = "stringpath"\n')
    cfg = CanonConfig.from_toml(str(p))
    assert cfg.seed == "stringpath"


def test_from_toml_missing_seed_raises(tmp_path: Path):
    p = tmp_path / "bad.toml"
    p.write_text("num_maps = 2\n")
    with pytest.raises(ValidationError):
        CanonConfig.from_toml(p)
