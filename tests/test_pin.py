"""Pin verb (P1) — deliberate protection of liked (paid) art from regen.

Semantics under test:
- pins live on BibleMetadata.pinned (state-on-Bible, survive persist/load);
- the stale cascade HALTS at a pin (mark_stale and detect_edits);
- an EXPLICIT regen target that is pinned is an atomic error;
- the scheduler skips pinned node ids, and the art PHASE BODIES skip
  pinned assets (status alone never gates a body — the guards are what
  keep a pinned sprite intact through a roster re-roll);
- the player sprite is a real artifact now (PlayerDefinition): tracked,
  edit-detected, pinnable;
- `canon pin/unpin` CLI: validation, stale-clearing, idempotent unpin.
"""

from __future__ import annotations

import io
import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("PIL")

from PIL import Image  # noqa: E402

from canon.backends.testing import FakeImageBackend, FakeLLMBackend  # noqa: E402
from canon.bible.artifacts import ArtifactStatus  # noqa: E402
from canon.bible.models import Bible  # noqa: E402
from canon.config import CanonConfig  # noqa: E402
from canon.llm.client import LLMClient  # noqa: E402
from canon.packs.platformer import PlatformerPrompts  # noqa: E402
from canon.packs.platformer.dag import run_orchestrated  # noqa: E402
from canon.packs.platformer.run_slice import make_fake_responder  # noqa: E402
from canon.packs.platformer.tileset_art import DiffusionSheetProducer  # noqa: E402
from canon.pipeline.orchestrator import (  # noqa: E402
    detect_edits,
    mark_stale,
    pinnable_ids,
    pinned_ids,
)
from canon.pipeline.runner import PipelineContext  # noqa: E402

CANON = [sys.executable, "-m", "canon.cli.main"]
SEED = "emberfall_001"
STAGE = "ashen_depths"

#: Distinct bytes standing in for liked paid art — lets tests tell
#: "kept as-is" apart from "regenerated" (the fake producer is
#: deterministic, so its own output can't).
SENTINEL = b"\x89PNG-sentinel-not-really-a-png"


def _blob_png(size: int = 64) -> bytes:
    img = Image.new("RGB", (size, size), (255, 255, 255))
    for y in range(size // 4, 3 * size // 4):
        for x in range(size // 4, 3 * size // 4):
            img.putpixel((x, y), (180, 40, 40))
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def _producer(tmp_path: Path) -> DiffusionSheetProducer:
    path = tmp_path / "blob.png"
    if not path.exists():
        path.write_bytes(_blob_png())
    return DiffusionSheetProducer(FakeImageBackend(placeholder=path))


def _ctx(output_dir: Path, bible: Bible | None = None) -> PipelineContext:
    return PipelineContext(
        bible=bible if bible is not None else Bible.empty(seed=SEED),
        config=CanonConfig(seed=SEED, output_dir=output_dir),
        rng=random.Random(SEED),
        llm=LLMClient(FakeLLMBackend(make_fake_responder())),
        prompts=PlatformerPrompts(),
    )


def _fresh(out: Path, tmp_path: Path, with_art: bool = True) -> PipelineContext:
    ctx = _ctx(out)
    run_orchestrated(
        ctx, persist_path=out / "bible.json",
        image_producer=_producer(tmp_path) if with_art else None,
    )
    return ctx


def _resume(out: Path, tmp_path: Path, with_art: bool = True):
    bible = Bible.load(out / "bible.json")
    ctx = _ctx(out, bible=bible)
    detect_edits(bible, out)
    report = run_orchestrated(
        ctx, persist_path=out / "bible.json",
        image_producer=_producer(tmp_path) if with_art else None,
    )
    return ctx, report


def _pin(bible: Bible, *ids: str) -> None:
    bible.metadata.pinned = sorted(set(bible.metadata.pinned) | set(ids))


class TestCascadeBarrier:
    def test_pin_halts_style_cascade(self, tmp_path: Path) -> None:
        """Pinned tileset: a style regen marks style itself but nothing
        downstream of the pin — the tileset branch is the ONLY path from
        style into the level layers."""
        out = tmp_path / "run"
        ctx = _fresh(out, tmp_path, with_art=False)
        _pin(ctx.bible, f"tileset:{STAGE}")

        plan = mark_stale(ctx.bible, ["phase:plat:style"])
        assert plan.kept_pinned == [f"tileset:{STAGE}"]
        assert plan.marked == ["phase:plat:style"]
        assert not any("level:" in nid for nid in plan.marked)

    def test_explicit_pinned_target_errors_atomically(
        self, tmp_path: Path
    ) -> None:
        out = tmp_path / "run"
        ctx = _fresh(out, tmp_path, with_art=False)
        _pin(ctx.bible, f"tileset:{STAGE}")
        before = dict(ctx.bible.metadata.node_status)

        with pytest.raises(KeyError, match="PINNED.*unpin"):
            mark_stale(ctx.bible, [f"tileset:{STAGE}", "phase:plat:style"])
        # Atomic: nothing was marked, not even the non-pinned target.
        assert ctx.bible.metadata.node_status == before

    def test_detect_edits_cascade_halts_at_pin(self, tmp_path: Path) -> None:
        """Internal barrier semantics (hand-set pin, below the CLI): the
        cascade walk never marks or traverses a pinned id. The CLI
        restricts pins to art artifacts — level steps like the one used
        here are deliberately NOT pinnable (see pinnable_ids) — but the
        BFS barrier itself is id-agnostic."""
        out = tmp_path / "run"
        ctx = _fresh(out, tmp_path, with_art=False)
        level = ctx.bible.levels["l2"]
        entities_id = f"level:{STAGE}/l2/entities"
        _pin(ctx.bible, entities_id)

        grid = np.zeros((level.grid_height, level.grid_width), dtype=np.int8)
        grid[-2, :] = 1
        np.savez_compressed(out / level.collision, collision=grid)

        report = detect_edits(ctx.bible, out)
        assert f"level:{STAGE}/l2/collision" in report.user_edited
        assert f"level:{STAGE}/l2/terrain" in report.stale
        assert entities_id not in report.stale
        assert report.pinned_edited == []

    def test_pinned_file_edit_is_adopted_and_loud(self, tmp_path: Path) -> None:
        """Editing a pinned file: adopted like any edit (hash re-stamped,
        user_edited), pin kept, and reported under pinned_edited."""
        out = tmp_path / "run"
        ctx = _fresh(out, tmp_path)
        backdrop_id = f"backdrop:{STAGE}"
        _pin(ctx.bible, backdrop_id)
        band = out / ctx.bible.backdrops[STAGE].band_paths[0]
        band.write_bytes(_blob_png(16))

        report = detect_edits(ctx.bible, out)
        assert backdrop_id in report.user_edited
        assert report.pinned_edited == [backdrop_id]
        assert backdrop_id in pinned_ids(ctx.bible)
        # Adopted: second pass is clean.
        assert detect_edits(ctx.bible, out).user_edited == []


class TestPhaseBodyGuards:
    def test_pinned_sprite_survives_roster_reroll(self, tmp_path: Path) -> None:
        """SpriteArtPhase re-rolls the WHOLE roster whenever it runs; the
        per-asset guard is what keeps one liked sprite byte-identical."""
        out = tmp_path / "run"
        ctx = _fresh(out, tmp_path)
        enemy_ids = sorted(ctx.bible.enemy_definitions)
        liked, other = enemy_ids[0], enemy_ids[1]
        liked_rel = ctx.bible.enemy_definitions[liked].sprite_path
        other_rel = ctx.bible.enemy_definitions[other].sprite_path

        # Stand in liked paid art with sentinel bytes, adopted as an edit.
        (out / liked_rel).write_bytes(SENTINEL)
        (out / other_rel).write_bytes(SENTINEL)
        detect_edits(ctx.bible, out)
        _pin(ctx.bible, f"enemy:{liked}")
        mark_stale(ctx.bible, ["phase:plat:sprite_art"])
        ctx.bible.persist(out / "bible.json")
        liked_hash = ctx.bible.enemy_definitions[liked].sprite_hash

        ctx2, report = _resume(out, tmp_path)
        assert "phase:plat:sprite_art" in report.done
        assert (out / liked_rel).read_bytes() == SENTINEL
        assert ctx2.bible.enemy_definitions[liked].sprite_hash == liked_hash
        assert (out / other_rel).read_bytes() != SENTINEL  # re-rolled

    def test_pinned_tilesheet_survives_producerless_resume(
        self, tmp_path: Path
    ) -> None:
        """The placeholder-overwrite trap: a resume WITHOUT an image
        producer re-runs plat:tileset (owns the stale tileset id) and
        would replace generated art with flat squares — a pin blocks both
        the repaint and the entity replacement."""
        out = tmp_path / "run"
        ctx = _fresh(out, tmp_path)
        sheet_rel = ctx.bible.tilesets[STAGE].tilesheet_path
        art_bytes = (out / sheet_rel).read_bytes()
        slots_before = [s.model_dump() for s in ctx.bible.tilesets[STAGE].slots]

        _pin(ctx.bible, f"tileset:{STAGE}")
        mark_stale(ctx.bible, ["phase:plat:tileset"])  # node id — allowed
        ctx.bible.persist(out / "bible.json")

        ctx2, report = _resume(out, tmp_path, with_art=False)
        assert "phase:plat:tileset" in report.done  # phase ran, guard held
        assert (out / sheet_rel).read_bytes() == art_bytes
        slots_after = [s.model_dump() for s in ctx2.bible.tilesets[STAGE].slots]
        assert slots_after == slots_before

    def test_pinned_backdrop_bands_kept(self, tmp_path: Path) -> None:
        out = tmp_path / "run"
        ctx = _fresh(out, tmp_path)
        band_rel = ctx.bible.backdrops[STAGE].band_paths[0]
        (out / band_rel).write_bytes(SENTINEL)
        detect_edits(ctx.bible, out)
        _pin(ctx.bible, f"backdrop:{STAGE}")
        mark_stale(ctx.bible, ["phase:plat:backdrop_art"])
        ctx.bible.persist(out / "bible.json")

        _ctx2, report = _resume(out, tmp_path)
        assert "phase:plat:backdrop_art" in report.done
        assert (out / band_rel).read_bytes() == SENTINEL


class TestPlayerArtifact:
    def test_player_sprite_is_tracked_and_pinnable(self, tmp_path: Path) -> None:
        out = tmp_path / "run"
        ctx = _fresh(out, tmp_path)
        player = ctx.bible.player
        assert player is not None and player.artifact_id == "player"
        assert player.sprite_path == "sprite/player/base.png"
        import hashlib

        actual = "sha256:" + hashlib.sha256(
            (out / player.sprite_path).read_bytes()
        ).hexdigest()
        assert player.sprite_hash == actual
        assert "player" in pinnable_ids(ctx.bible)

    def test_pinned_player_sprite_survives_reroll(self, tmp_path: Path) -> None:
        out = tmp_path / "run"
        ctx = _fresh(out, tmp_path)
        rel = ctx.bible.player.sprite_path
        (out / rel).write_bytes(SENTINEL)
        detect_edits(ctx.bible, out)
        _pin(ctx.bible, "player")
        mark_stale(ctx.bible, ["phase:plat:sprite_art"])
        ctx.bible.persist(out / "bible.json")

        _ctx2, report = _resume(out, tmp_path)
        assert "phase:plat:sprite_art" in report.done
        assert (out / rel).read_bytes() == SENTINEL

    def test_hand_edited_player_sprite_is_detected(self, tmp_path: Path) -> None:
        out = tmp_path / "run"
        ctx = _fresh(out, tmp_path)
        (out / ctx.bible.player.sprite_path).write_bytes(SENTINEL)
        report = detect_edits(ctx.bible, out)
        assert "player" in report.user_edited


class TestSplashArtifact:
    """The world splash card is LEAF art addressed as ``splash`` — the
    fields live on World, but neither pinning nor edit-adoption ever
    routes through the ``world`` id (whose descendant set is the whole
    game)."""

    def test_splash_is_tracked_and_pinnable_as_leaf(
        self, tmp_path: Path
    ) -> None:
        out = tmp_path / "run"
        ctx = _fresh(out, tmp_path)
        world = ctx.bible.world
        assert world is not None and world.splash_path == "splash/world.png"
        import hashlib

        actual = "sha256:" + hashlib.sha256(
            (out / world.splash_path).read_bytes()
        ).hexdigest()
        assert world.splash_hash == actual
        assert "splash" in pinnable_ids(ctx.bible)
        assert "world" not in pinnable_ids(ctx.bible)

    def test_pinned_splash_survives_reroll(self, tmp_path: Path) -> None:
        out = tmp_path / "run"
        ctx = _fresh(out, tmp_path)
        rel = ctx.bible.world.splash_path
        (out / rel).write_bytes(SENTINEL)
        detect_edits(ctx.bible, out)
        _pin(ctx.bible, "splash")
        mark_stale(ctx.bible, ["phase:plat:world_art"])
        ctx.bible.persist(out / "bible.json")

        _ctx2, report = _resume(out, tmp_path)
        assert "phase:plat:world_art" in report.done
        assert (out / rel).read_bytes() == SENTINEL

    def test_hand_edited_splash_is_a_leaf_edit(self, tmp_path: Path) -> None:
        # THE cascade regression guard: a tweaked card adopts on
        # ``splash`` alone — zero stale descendants, ``world`` untouched
        # (a resume after a splash touch-up must regenerate nothing).
        out = tmp_path / "run"
        ctx = _fresh(out, tmp_path)
        (out / ctx.bible.world.splash_path).write_bytes(SENTINEL)
        report = detect_edits(ctx.bible, out)
        assert report.user_edited == ["splash"]
        assert report.stale == []

    def test_splash_is_a_regen_target(self, tmp_path: Path) -> None:
        # "splash" lives in no entity's parents and no node id — it is
        # addressable because hash-tracked file ids join mark_stale's
        # known set; the phase re-runs via owns() (initial_skips).
        out = tmp_path / "run"
        ctx = _fresh(out, tmp_path)
        plan = mark_stale(ctx.bible, ["splash"])
        assert plan.marked == ["splash"]
        with pytest.raises(KeyError, match="unknown regen target"):
            mark_stale(ctx.bible, ["splish"])


class TestPersistence:
    def test_pins_survive_persist_and_resume(self, tmp_path: Path) -> None:
        out = tmp_path / "run"
        ctx = _fresh(out, tmp_path, with_art=False)
        _pin(ctx.bible, f"tileset:{STAGE}")
        ctx.bible.persist(out / "bible.json")

        ctx2, report = _resume(out, tmp_path, with_art=False)
        assert pinned_ids(ctx2.bible) == {f"tileset:{STAGE}"}
        assert not report.escalated

    def test_no_pins_changes_nothing(self, tmp_path: Path) -> None:
        """Empty pin set: plans and reports carry empty pin fields."""
        out = tmp_path / "run"
        ctx = _fresh(out, tmp_path, with_art=False)
        plan = mark_stale(ctx.bible, ["phase:plat:style"])
        assert plan.kept_pinned == []
        assert detect_edits(ctx.bible, out).pinned_edited == []


class TestPinCli:
    def _run_cli(self, *args: str):
        return subprocess.run(
            [*CANON, *args], capture_output=True, text=True, check=False,
        )

    def test_pin_unpin_roundtrip(self, tmp_path: Path) -> None:
        out = tmp_path / "run"
        _fresh(out, tmp_path, with_art=False)
        bible_path = str(out / "bible.json")

        res = self._run_cli("pin", bible_path, f"tileset:{STAGE}")
        payload = json.loads(res.stdout)
        assert payload["result"] == "pinned"
        assert payload["pinned"] == [f"tileset:{STAGE}"]

        res = self._run_cli("pin", bible_path, "--list")
        payload = json.loads(res.stdout)
        assert payload["pinned"] == [f"tileset:{STAGE}"]
        # Art-only pinnable set: no art ran, so enemies/player have no
        # tracked sprites yet and level steps are excluded by design —
        # only the tileset qualifies.
        assert payload["pinnable"] == [f"tileset:{STAGE}"]

        res = self._run_cli("unpin", bible_path, f"tileset:{STAGE}", "enemy:nope")
        payload = json.loads(res.stdout)
        assert payload["unpinned"] == [f"tileset:{STAGE}"]
        assert payload["not_pinned"] == ["enemy:nope"]
        assert pinned_ids(Bible.load(out / "bible.json")) == set()

    def test_pin_rejects_unpinnable_atomically(self, tmp_path: Path) -> None:
        out = tmp_path / "run"
        _fresh(out, tmp_path, with_art=False)
        bible_path = str(out / "bible.json")

        res = self._run_cli(
            "pin", bible_path, f"tileset:{STAGE}", "phase:plat:style",
        )
        assert res.returncode != 0
        assert "not pinnable" in res.stderr + res.stdout
        assert pinned_ids(Bible.load(out / "bible.json")) == set()

    def test_pin_rejects_level_steps(self, tmp_path: Path) -> None:
        """Level steps are hash-tracked but NOT pinnable: a pinned step
        under a regenerating parent leaves the Bible claiming content its
        skipped node never restored — USER_EDITED is the protection story
        for level layers."""
        out = tmp_path / "run"
        _fresh(out, tmp_path, with_art=False)

        res = self._run_cli(
            "pin", str(out / "bible.json"), f"level:{STAGE}/l2/entities",
        )
        assert res.returncode != 0
        assert "not pinnable" in res.stderr + res.stdout
        assert pinned_ids(Bible.load(out / "bible.json")) == set()

    def test_pin_clears_stale_mark(self, tmp_path: Path) -> None:
        """mark → inspect → pin → resume: pinning a stale artifact clears
        the mark, or the owning phase would re-run and defeat the pin."""
        out = tmp_path / "run"
        ctx = _fresh(out, tmp_path, with_art=False)
        mark_stale(ctx.bible, ["phase:plat:style"])  # cascades to tileset
        ctx.bible.persist(out / "bible.json")

        res = self._run_cli("pin", str(out / "bible.json"), f"tileset:{STAGE}")
        payload = json.loads(res.stdout)
        assert payload["stale_cleared"] == [f"tileset:{STAGE}"]
        bible = Bible.load(out / "bible.json")
        assert (
            bible.metadata.node_status[f"tileset:{STAGE}"]
            is ArtifactStatus.DONE
        )

    def test_status_shows_pins_outside_attention(self, tmp_path: Path) -> None:
        out = tmp_path / "run"
        ctx = _fresh(out, tmp_path, with_art=False)
        _pin(ctx.bible, f"tileset:{STAGE}")
        ctx.bible.persist(out / "bible.json")

        res = self._run_cli("status", str(out / "bible.json"))
        payload = json.loads(res.stdout)
        assert payload["pinned"] == [f"tileset:{STAGE}"]
        assert f"tileset:{STAGE}" not in payload["attention"]

    def test_mark_only_enumerates_kept_pinned(self, tmp_path: Path) -> None:
        out = tmp_path / "run"
        ctx = _fresh(out, tmp_path, with_art=False)
        _pin(ctx.bible, f"tileset:{STAGE}")
        ctx.bible.persist(out / "bible.json")

        res = self._run_cli(
            "regen", str(out / "bible.json"), "phase:plat:style", "--mark-only",
        )
        payload = json.loads(res.stdout)
        assert payload["regen"]["kept_pinned"] == [f"tileset:{STAGE}"]
        assert payload["regen"]["marked"] == ["phase:plat:style"]
