"""Art SAMPLE mode ($0): fake fan-out artifacts, contact sheet,
grid-drop, palette swatch, missing-key resilience, determinism."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PIL")

from PIL import Image  # noqa: E402

from examples.platformer_pack.art_sampling import run_art_sample  # noqa: E402
from examples.platformer_pack.graphics import GraphicsSpec  # noqa: E402


def _assert_png(path: str) -> Image.Image:
    p = Path(path)
    assert p.exists(), f"missing artifact {p}"
    img = Image.open(p)
    img.load()  # full decode — a broken stream must fail here, not downstream
    return img


def test_player_sample_fake(tmp_path):
    graphics = GraphicsSpec()
    result = run_art_sample("player", ["fake"], graphics, tmp_path)

    assert result["subject"] == "player"
    (entry,) = result["results"]
    assert entry["backend"] == "fake"
    asset = _assert_png(entry["files"]["asset"])
    fw, fh = graphics.player_footprint
    assert asset.size == (fw * graphics.base_cell, fh * graphics.base_cell)
    assert Path(entry["files"]["asset"]).name == "fake.png"
    _assert_png(entry["files"]["griddrop"])
    _assert_png(result["contact_sheet"])
    assert Path(result["contact_sheet"]).parent == tmp_path / "player"


def test_tile_sample_fake(tmp_path):
    graphics = GraphicsSpec()
    result = run_art_sample("tile:floor", ["fake"], graphics, tmp_path)

    (entry,) = result["results"]
    asset = _assert_png(entry["files"]["asset"])
    assert asset.size == (graphics.tile_px, graphics.tile_px)
    # Fake 1x1 black -> conform flat-fills the role hex: proves the
    # registry -> PLACEHOLDER_PALETTE role plumbing end to end.
    assert asset.convert("RGBA").getpixel((0, 0)) == (110, 110, 120, 255)
    _assert_png(entry["files"]["griddrop"])
    _assert_png(result["contact_sheet"])
    assert Path(entry["files"]["asset"]).parent == tmp_path / "tile-floor"


def test_palette_swatch(tmp_path):
    # Palette is code-rendered — backend names are ignored, no generation.
    result = run_art_sample("palette", [], GraphicsSpec(), tmp_path)

    (entry,) = result["results"]
    assert "error" not in entry
    swatch = _assert_png(entry["files"]["asset"])
    assert swatch.size[0] > 0 and swatch.size[1] > 0
    _assert_png(result["contact_sheet"])


def test_missing_key_records_error_without_sinking_fake(monkeypatch, tmp_path):
    monkeypatch.delenv("RD_API_KEY", raising=False)
    result = run_art_sample("player", ["retro", "fake"], GraphicsSpec(), tmp_path)

    by_backend = {entry["backend"]: entry for entry in result["results"]}
    assert "RD_API_KEY" in by_backend["retro"]["error"]
    assert "files" not in by_backend["retro"]
    # The fake leg still produced everything.
    _assert_png(by_backend["fake"]["files"]["asset"])
    _assert_png(by_backend["fake"]["files"]["griddrop"])
    _assert_png(result["contact_sheet"])


@pytest.mark.parametrize("subject", ["player", "tile:floor", "palette"])
def test_deterministic_bytes(subject, tmp_path):
    graphics = GraphicsSpec()
    first = run_art_sample(subject, ["fake"], graphics, tmp_path / "a")
    second = run_art_sample(subject, ["fake"], graphics, tmp_path / "b")

    for entry_a, entry_b in zip(first["results"], second["results"]):
        for key, path_a in entry_a["files"].items():
            path_b = entry_b["files"][key]
            assert Path(path_a).read_bytes() == Path(path_b).read_bytes(), key
    sheet_a = Path(first["contact_sheet"]).read_bytes()
    assert sheet_a == Path(second["contact_sheet"]).read_bytes()


@pytest.mark.parametrize("subject", ["level", "tile:volcano_rock", "tile:"])
def test_bad_subject_fails_loudly(subject, tmp_path):
    with pytest.raises(ValueError):
        run_art_sample(subject, ["fake"], GraphicsSpec(), tmp_path)


def test_exemplar_carries_readable_provenance(tmp_path):
    # G6: the generated exemplar is stamped like batch assets; derived
    # views (contact sheet) stay unstamped.
    result = run_art_sample("player", ["fake"], GraphicsSpec(), tmp_path)
    (entry,) = result["results"]
    asset = _assert_png(entry["files"]["asset"])
    assert asset.text["canon:generator"] == "canon-ai harness"
    assert asset.text["canon:asset"] == "sample:player/fake"
    assert asset.text["canon:backend-model"] == "FakeImageBackend"
    assert asset.text["canon:seed"] == ""  # sample mode has no pipeline seed
    sheet = _assert_png(result["contact_sheet"])
    assert "canon:generator" not in getattr(sheet, "text", {})
