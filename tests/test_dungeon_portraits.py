"""The portrait write-back and its repair leg.

Two bugs are pinned here.

1. A generated pack never recorded its portraits. AssetPhase wrote the PNGs and
   passed ``None`` for the entity slots, and the class/character stamps landed
   on in-memory objects AFTER the collections had been written — so every
   ``profile_image`` stayed ``null``, the manifest's four portrait fields
   stayed empty while ``portraits_generated`` claimed ``true``, and a PAID art
   run would have burned the money and referenced none of the output.

2. ``manifest.json`` carried a wall clock (``generated_at`` and the copied
   ``validation_report.timestamp``). That file is emitted pack content, so two
   identical seeded creates differed in bytes no test compared. Neither key is
   written any more: a clock cannot be reproduced from a seed, and a
   seed-derived substitute is read by cradle as the pack's creation date, so
   it would only trade an honest non-reproducible value for a reproducible
   false one. The create time lives in ``.canon/``.

Hermetic and $0: fake backends only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from canon import run_pipeline
from canon.backends.testing import (
    FakeImageBackend,
    FakeLLMBackend,
    FakeMusicBackend,
    FakeSFXBackend,
)
from canon.llm.client import LLMClient
from canon.packs.dungeon.compose import compose_pipeline
from canon.packs.dungeon.fakes import make_fake_responder
from canon.packs.dungeon.portraits import backfill_portraits, image_asset_kinds
from canon.packs.dungeon.spec import PACK_SPEC
from canon.pipeline.phases.asset import (
    FIXED_PORTRAITS,
    entity_portrait_rel,
    row_portrait_rel,
)
from tests.treediff import assert_trees_byte_identical, tree_files

NUM_MAPS = 2

#: kind → the row field that holds its art, straight off the registry seed.
ART_FIELDS: dict[str, str] = {
    entity.kind: field for entity, field in image_asset_kinds(PACK_SPEC)
}


def _generate(
    out: Path, *, images: bool = True, audio: bool = False, seed: str = "portrait-seed"
) -> Path:
    phases, ctx = compose_pipeline(seed=seed, num_maps=NUM_MAPS, output_dir=out)
    ctx.llm = LLMClient(FakeLLMBackend(make_fake_responder(NUM_MAPS)))
    if images:
        ctx.image_backend = FakeImageBackend()
    if audio:
        ctx.music_backend, ctx.sfx_backend = FakeMusicBackend(), FakeSFXBackend()
    for phase in phases:
        if phase.name == "assets":
            phase.skip_image = not images
            phase.skip_music = phase.skip_sfx = not audio
    run_pipeline(phases, ctx)
    return out


def _rows(pack: Path, kind: str) -> list[dict]:
    entity = PACK_SPEC.entities[kind]
    doc = json.loads((pack / entity.layout["path"]).read_text(encoding="utf-8"))
    return list(doc.values()) if isinstance(doc, dict) else doc


@pytest.fixture(scope="module")
def pack(tmp_path_factory) -> Path:
    return _generate(tmp_path_factory.mktemp("portraits") / "pack")


class TestTheRegistryDeclaresTheArtField:
    def test_every_kind_with_a_portrait_names_the_field_that_holds_it(self) -> None:
        """The write-back and the repair are driven by ``EntityKind.asset`` —
        never by a list of kind names in code. Four of the six kinds that
        carry portraits used to declare no asset block at all, which is why a
        data-driven pass over the registry could not see them."""
        assert ART_FIELDS == {
            "npc": "profile_image",
            "monster": "profile_image",
            "item": "profile_image",
            "quest": "profile_image",
            "event": "profile_image",
            "class": "portrait_path",
        }


class TestGeneratedPacksRecordTheirPortraits:
    @pytest.mark.parametrize("kind", sorted(ART_FIELDS))
    def test_every_row_carries_a_path_to_a_file_that_exists(
        self, pack: Path, kind: str
    ) -> None:
        field = ART_FIELDS[kind]
        rows = _rows(pack, kind)
        assert rows, f"no {kind} rows were generated"
        for row in rows:
            path = row.get(field)
            assert path, f"{kind} row {row.get('id') or row.get('archetype')} has no {field}"
            assert (pack / path).is_file(), f"{kind}: {path} names no file"

    def test_the_recorded_path_is_pack_relative(self, pack: Path) -> None:
        """Emitted content must not carry the absolute path of the machine
        that generated it: it differs between two same-seed runs and does not
        resolve on anyone else's disk."""
        for kind, field in ART_FIELDS.items():
            for row in _rows(pack, kind):
                assert not Path(row[field]).is_absolute(), (kind, row[field])
                assert row[field].startswith("portraits/")

    def test_the_manifest_fills_the_four_portrait_fields_it_advertises(
        self, pack: Path
    ) -> None:
        manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["portraits_generated"] is True
        for key, _stem, _prompt in FIXED_PORTRAITS:
            assert manifest[key], f"{key} is empty while portraits_generated is true"
            assert (pack / manifest[key]).is_file()

    def test_every_room_names_its_environment_portrait(self, pack: Path) -> None:
        manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
        assert len(manifest["rooms"]) == NUM_MAPS
        for room in manifest["rooms"]:
            assert room["environment_portrait"]
            assert (pack / room["environment_portrait"]).is_file()

    def test_no_portrait_is_claimed_when_none_was_generated(self, tmp_path: Path) -> None:
        """The honest half: with the image backend off nothing is recorded and
        the manifest says so, rather than reporting a phase that ran."""
        out = _generate(tmp_path / "dark", images=False)
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["portraits_generated"] is False
        assert not any(manifest[key] for key, _s, _p in FIXED_PORTRAITS)
        for kind, field in ART_FIELDS.items():
            assert not any(row.get(field) for row in _rows(out, kind)), kind


class TestTheNamingConventionLivesInOnePlace:
    def test_the_repair_asks_for_the_same_path_the_phase_wrote(self, pack: Path) -> None:
        """The whole point of sharing ``row_portrait_rel``: what the phase
        recorded and what a repair would look for must be the same string, or
        the repair silently adopts nothing."""
        for kind, field in ART_FIELDS.items():
            for position, row in enumerate(_rows(pack, kind)):
                assert row[field] == row_portrait_rel(
                    kind,
                    index=position,
                    row_id=row.get(PACK_SPEC.entities[kind].id_field),
                    name=row.get("name"),
                )

    def test_the_two_filename_families(self) -> None:
        """Pinned by example because they are load-bearing for every pack
        already on disk: monsters and items key by NAME slug, everything else
        by id."""
        assert entity_portrait_rel("npc", 1000, "Char 0") == "portraits/npcs/npc_1000.png"
        assert entity_portrait_rel("event", 3000, "Trap") == "portraits/events/evt_3000.png"
        assert entity_portrait_rel("quest", 4000, None) == "portraits/quests/quest_4000.png"
        assert entity_portrait_rel("monster", 5000, "Ash Golem") == "portraits/monsters/mon_ash_golem.png"
        assert entity_portrait_rel("item", 2000, "Iron Key") == "portraits/items/item_iron_key.png"
        assert row_portrait_rel("class", index=2) == "portraits/classes/class_2.png"


class TestTheBackfill:
    """The repair for packs generated before the write-back existed: it adopts
    the PNGs already on disk and generates nothing."""

    @pytest.fixture
    def stale(self, tmp_path: Path) -> Path:
        """A generated pack with every recorded path stripped back out — what
        every pack on disk looks like today."""
        out = _generate(tmp_path / "stale")
        for kind, field in ART_FIELDS.items():
            entity = PACK_SPEC.entities[kind]
            rel = entity.layout["path"]
            doc = json.loads((out / rel).read_text(encoding="utf-8"))
            for row in (doc.values() if isinstance(doc, dict) else doc):
                row[field] = None
            (out / rel).write_text(json.dumps(doc, indent=2), encoding="utf-8")
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        for key, _stem, _prompt in FIXED_PORTRAITS:
            manifest[key] = ""
        for room in manifest["rooms"]:
            room["environment_portrait"] = None
        manifest["portraits_generated"] = False
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return out

    def test_it_records_every_row_without_generating_anything(self, stale: Path) -> None:
        before = sorted(p.relative_to(stale) for p in stale.rglob("*.png"))
        result = backfill_portraits(stale, actor="tester")
        assert sorted(p.relative_to(stale) for p in stale.rglob("*.png")) == before, \
            "the repair must not produce a single new image"
        assert result["no_file_on_disk"] == []
        for kind, field in ART_FIELDS.items():
            rows = _rows(stale, kind)
            assert result["recorded"][kind] == len(rows)
            for row in rows:
                assert (stale / row[field]).is_file()

    def test_it_repairs_the_manifest_too(self, stale: Path) -> None:
        backfill_portraits(stale, actor="tester")
        manifest = json.loads((stale / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["portraits_generated"] is True
        for key, _stem, _prompt in FIXED_PORTRAITS:
            assert (stale / manifest[key]).is_file()
        for room in manifest["rooms"]:
            assert (stale / room["environment_portrait"]).is_file()

    def test_a_dry_run_writes_nothing(self, stale: Path) -> None:
        before = {
            p: p.read_bytes() for p in stale.rglob("*.json") if ".canon" not in p.parts
        }
        result = backfill_portraits(stale, dry_run=True)
        assert result["recorded"], "a dry run still reports what it would do"
        assert {p: p.read_bytes() for p in before} == before

    def test_it_is_idempotent_and_never_overwrites(self, stale: Path) -> None:
        backfill_portraits(stale, actor="tester")
        chosen = "portraits/classes/class_0.png"
        entity = PACK_SPEC.entities["npc"]
        rel = entity.layout["path"]
        rows = json.loads((stale / rel).read_text(encoding="utf-8"))
        rows[0]["profile_image"] = chosen  # a person picked different art
        (stale / rel).write_text(json.dumps(rows, indent=2), encoding="utf-8")

        again = backfill_portraits(stale, actor="tester")
        assert again["recorded"] == {}, "a second pass has nothing left to record"
        assert json.loads((stale / rel).read_text(encoding="utf-8"))[0]["profile_image"] == chosen

    def test_a_missing_file_is_reported_not_invented(self, stale: Path) -> None:
        (stale / "portraits" / "npcs" / "npc_1000.png").unlink()
        result = backfill_portraits(stale, actor="tester")
        assert "portraits/npcs/npc_1000.png" in result["no_file_on_disk"]
        row = next(r for r in _rows(stale, "npc") if str(r["id"]) == "1000")
        assert not row["profile_image"], "a path to nothing is worse than an empty field"

    def test_every_change_is_journaled_under_the_actor(self, stale: Path) -> None:
        backfill_portraits(stale, actor="repair-agent", session="s1")
        events = [
            json.loads(line)
            for line in (stale / ".canon" / "journal.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        repairs = [e for e in events if e.get("source") == "repair"]
        assert {e["artifact_id"] for e in repairs} >= {
            PACK_SPEC.entities[kind].layout["path"] for kind in ART_FIELDS
        } | {"manifest.json"}
        assert all(e["actor"] == "repair-agent" for e in repairs)
        assert all(e["before_hash"] != e["after_hash"] for e in repairs)


class TestEmittedContentIsReproducible:
    """The `world new` twin of this lives in `test_create_flow`; this one runs
    the pipeline in-process with EVERY generator on, so the asset indexes are
    populated — the manifest's music/sfx maps used to hold absolute paths,
    which differ between two runs into different directories."""

    def test_two_same_seed_runs_are_byte_identical(self, tmp_path: Path) -> None:
        a = _generate(tmp_path / "a", audio=True, seed="reproducible")
        b = _generate(tmp_path / "b", audio=True, seed="reproducible")
        assert_trees_byte_identical(a, b)

    def test_no_emitted_asset_reference_is_absolute(self, tmp_path: Path) -> None:
        pack = _generate(tmp_path / "pack", audio=True, seed="reproducible")
        manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
        references = [
            *manifest["music"].values(),
            *manifest["sfx"].values(),
            *(manifest[key] for key, _s, _p in FIXED_PORTRAITS),
            *(room["environment_portrait"] for room in manifest["rooms"]),
            *(row[field] for kind, field in ART_FIELDS.items() for row in _rows(pack, kind)),
        ]
        assert references, "nothing was generated, so nothing was checked"
        for reference in references:
            assert not Path(reference).is_absolute(), reference
            assert (pack / reference).is_file(), reference


class TestNoClockReachesEmittedContent:
    """`manifest.json` carried `generated_at` and a copied
    `validation_report.timestamp`, both wall clocks, both inside the emitted
    tree. Deriving them from the seed made the create reproducible but left a
    field cradle renders as the pack's creation date holding a 1970 instant —
    a reproducible falsehood. So emitted content now carries no clock at all;
    the real create time stays in `.canon/` (the instance registry's
    `created_at`, the step log's `run_start`), which the determinism contract
    excludes and where a timestamp is allowed to be one.

    The second test is the general form: it fails on ANY timestamp-shaped
    value in the emitted tree, whatever key it hides behind, and it takes its
    scope from `treediff`'s own exclusion lists so "emitted content" has one
    definition.
    """

    #: A value shaped like a clock reading: ISO-8601 date + time-of-day.
    CLOCK = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")

    def test_the_manifest_names_no_creation_date(self, tmp_path: Path) -> None:
        pack = _generate(tmp_path / "pack", seed="clockless")
        manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
        assert "generated_at" not in manifest
        assert "timestamp" not in manifest["validation_report"]
        # The report itself is unharmed — only the copy in emitted content
        # drops the key.
        assert manifest["validation_report"]["status"]

    def test_no_emitted_json_holds_a_timestamp_shaped_value(self, tmp_path: Path) -> None:
        pack = _generate(tmp_path / "pack", audio=True, seed="clockless")
        offenders: list[str] = []

        def walk(node: object, where: str) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    walk(value, f"{where}.{key}")
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    walk(value, f"{where}[{index}]")
            elif isinstance(node, str) and self.CLOCK.match(node):
                offenders.append(f"{where} = {node!r}")

        checked = 0
        for rel in tree_files(pack):
            if rel.suffix != ".json":
                continue
            checked += 1
            walk(json.loads((pack / rel).read_text(encoding="utf-8")), str(rel))
        assert checked, "no emitted JSON was scanned"
        assert not offenders, "a wall clock reached emitted content: " + "; ".join(offenders)
