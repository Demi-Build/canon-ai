"""Integration tests for ValidationPhase — the spike's core deliverable.

Tests that ValidationPhase correctly wires checkers and validators
through the Phase protocol and PipelineContext.
"""

import random

from canon.pipeline.phases import ValidationPhase, validate_bible
from canon.pipeline.runner import PipelineContext, run_pipeline
from canon.validation.checker import BaseChecker, CheckResult
from canon.validation.validator import BaseValidator, ValidationReport, ValidationResult

# --- Test bible that mimics canon's future Bible shape ---


class FakeBible:
    """Minimal bible with a flat entities list per map."""

    def __init__(self, seed="spike_test"):
        self.seed = seed
        self.maps = {}

    def add_map(self, map_id, entities):
        self.maps[map_id] = MapData(entities=entities)


class MapData:
    def __init__(self, entities=None):
        self.entities = entities or []


# --- Test bible that mimics MazeWorld's RoomBible shape ---


class MazeWorldBible:
    """Bible shaped like MazeWorld's WorldBible (separate npcs/items/monsters lists)."""

    def __init__(self, seed="mw_test"):
        self.seed = seed
        self.rooms = {}

    def add_room(self, room_id, npcs=None, items=None, monsters=None):
        self.rooms[room_id] = RoomData(
            npcs=npcs or [], items=items or [], monsters=monsters or []
        )


class RoomData:
    def __init__(self, npcs=None, items=None, monsters=None):
        self.npcs = npcs or []
        self.items = items or []
        self.monsters = monsters or []


# --- Concrete checkers/validators for testing ---


class ItemChecker(BaseChecker):
    @property
    def entity_type(self) -> str:
        return "item"

    def check(self, data, context=None) -> CheckResult:
        issues = []
        if not data.get("name"):
            issues.append("missing name")
        if data.get("value", 0) < 0:
            issues.append("negative value")
        return CheckResult(passed=not issues, issues=issues, data=data)


class WeaponChecker(BaseChecker):
    @property
    def entity_type(self) -> str:
        return "weapon"

    def check(self, data, context=None) -> CheckResult:
        issues = []
        if not data.get("name"):
            issues.append("weapon missing name")
        if not data.get("damage_die"):
            issues.append("weapon missing damage_die")
        return CheckResult(passed=not issues, issues=issues, data=data)


class UniqueNameValidator(BaseValidator):
    """Cross-entity: all entity names must be unique."""

    def validate(self, data, context=None) -> ValidationResult:
        names = [e.get("name") for e in data if e.get("name")]
        dupes = [n for n in names if names.count(n) > 1]
        if dupes:
            return ValidationResult(
                passed=False,
                severity="error",
                issues=[f"Duplicate names: {set(dupes)}"],
            )
        return ValidationResult(passed=True, severity="info")


# --- Tests ---


class TestValidateBible:
    def test_all_valid(self):
        bible = FakeBible()
        bible.add_map("map_0", [
            {"entity_type": "item", "entity_id": "i1", "name": "Potion", "value": 10},
            {"entity_type": "item", "entity_id": "i2", "name": "Scroll", "value": 5},
        ])
        report = validate_bible(bible, checkers=[ItemChecker()])
        assert report.passed
        assert report.critical_failures == 0

    def test_checker_catches_invalid(self):
        bible = FakeBible()
        bible.add_map("map_0", [
            {"entity_type": "item", "entity_id": "i1", "name": "", "value": -5},
        ])
        report = validate_bible(bible, checkers=[ItemChecker()])
        assert not report.passed
        assert report.critical_failures == 1

    def test_validator_catches_dupes(self):
        bible = FakeBible()
        bible.add_map("map_0", [
            {"entity_type": "item", "entity_id": "i1", "name": "Potion"},
            {"entity_type": "item", "entity_id": "i2", "name": "Potion"},
        ])
        report = validate_bible(
            bible,
            checkers=[ItemChecker()],
            validators=[UniqueNameValidator()],
        )
        assert not report.passed

    def test_multiple_checkers(self):
        bible = FakeBible()
        bible.add_map("map_0", [
            {"entity_type": "item", "entity_id": "i1", "name": "Potion"},
            {"entity_type": "weapon", "entity_id": "w1", "name": "Sword", "damage_die": "d8"},
            {"entity_type": "weapon", "entity_id": "w2", "name": ""},  # Invalid
        ])
        report = validate_bible(
            bible, checkers=[ItemChecker(), WeaponChecker()]
        )
        assert not report.passed
        assert report.critical_failures == 1  # Only the nameless weapon

    def test_no_checkers_or_validators(self):
        bible = FakeBible()
        report = validate_bible(bible)
        assert report.passed
        assert len(report.results) == 0

    def test_checker_only_matches_entity_type(self):
        bible = FakeBible()
        bible.add_map("map_0", [
            {"entity_type": "character", "entity_id": "c1", "name": "Bob"},
        ])
        report = validate_bible(bible, checkers=[ItemChecker()])
        assert report.passed  # ItemChecker ignores characters

    def test_empty_bible(self):
        bible = FakeBible()
        report = validate_bible(bible, checkers=[ItemChecker()])
        assert report.passed


class TestValidateBibleMazeWorldShape:
    """Test that validate_bible works with MazeWorld's bible shape."""

    def test_mazeworld_separate_lists(self):
        bible = MazeWorldBible()
        bible.add_room(
            "room_0",
            npcs=[{"entity_type": "item", "entity_id": "i1", "name": "NPC Item"}],
            items=[{"entity_type": "item", "entity_id": "i2", "name": "Sword", "value": 10}],
            monsters=[{"entity_type": "item", "entity_id": "i3", "name": "Monster Drop"}],
        )
        report = validate_bible(bible, checkers=[ItemChecker()])
        assert report.passed
        # Should find 3 entities across npcs/items/monsters
        info_results = [r for r in report.results if r.severity == "info"]
        assert len(info_results) == 3


class TestValidationPhase:
    def _make_ctx(self, bible, checkers=None, validators=None):
        return PipelineContext(
            bible=bible,
            config=type("C", (), {"seed": "test"})(),
            rng=random.Random(42),
            checkers=checkers or [],
            validators=validators or [],
        )

    def test_phase_stores_report_in_artifacts(self):
        bible = FakeBible()
        bible.add_map("map_0", [
            {"entity_type": "item", "entity_id": "i1", "name": "Potion", "value": 5},
        ])
        ctx = self._make_ctx(bible, checkers=[ItemChecker()])
        phase = ValidationPhase()
        phase.run(ctx)

        assert "validation_report" in ctx.artifacts
        report = ctx.artifacts["validation_report"]
        assert isinstance(report, ValidationReport)
        assert report.passed

    def test_phase_records_seed(self):
        bible = FakeBible(seed="my_seed")
        ctx = self._make_ctx(bible)
        ValidationPhase().run(ctx)
        report = ctx.artifacts["validation_report"]
        assert report.bible_seed == "my_seed"

    def test_phase_in_pipeline(self):
        bible = FakeBible()
        bible.add_map("map_0", [
            {"entity_type": "weapon", "entity_id": "w1", "name": "Axe", "damage_die": "d10"},
        ])
        ctx = self._make_ctx(bible, checkers=[WeaponChecker()])
        run_pipeline([ValidationPhase()], ctx)
        assert ctx.artifacts["validation_report"].passed

    def test_phase_with_failures(self):
        bible = FakeBible()
        bible.add_map("map_0", [
            {"entity_type": "item", "entity_id": "i1", "name": "", "value": -1},
        ])
        ctx = self._make_ctx(bible, checkers=[ItemChecker()])
        ValidationPhase().run(ctx)
        report = ctx.artifacts["validation_report"]
        assert not report.passed

    def test_phase_satisfies_protocol(self):
        from canon.pipeline.runner import Phase

        assert isinstance(ValidationPhase(), Phase)

    def test_phase_composable_with_other_phases(self):
        """ValidationPhase can run alongside other phases in a pipeline."""

        class SetupPhase:
            name = "setup"

            def run(self, ctx):
                ctx.bible.add_map("map_0", [
                    {"entity_type": "item", "entity_id": "i1", "name": "Gem", "value": 100},
                ])

        bible = FakeBible()
        ctx = self._make_ctx(bible, checkers=[ItemChecker()])
        run_pipeline([SetupPhase(), ValidationPhase()], ctx)
        assert ctx.artifacts["validation_report"].passed
