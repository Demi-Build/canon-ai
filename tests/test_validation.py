"""Tests for the validation framework — checkers, validators, report, coherence."""


from canon.validation.checker import BaseChecker, CheckResult
from canon.validation.coherence import check_references, detect_cycles
from canon.validation.validator import (
    BaseValidator,
    ValidationReport,
    ValidationResult,
)

# --- Concrete test implementations ---


class WeaponChecker(BaseChecker):
    """Example checker: validates weapon entities."""

    @property
    def entity_type(self) -> str:
        return "weapon"

    def check(self, data, context=None) -> CheckResult:
        issues = []
        if not data.get("name"):
            issues.append("missing name")
        if data.get("damage_die") not in {"d4", "d6", "d8", "d10", "d12", None}:
            issues.append(f"invalid die: {data.get('damage_die')}")
        return CheckResult(passed=not issues, issues=issues, data=data)


class CharacterChecker(BaseChecker):
    @property
    def entity_type(self) -> str:
        return "character"

    def check(self, data, context=None) -> CheckResult:
        issues = []
        if not data.get("name"):
            issues.append("missing name")
        if not data.get("lore"):
            issues.append("missing lore")
        return CheckResult(passed=not issues, issues=issues, data=data)


class RefValidator(BaseValidator):
    """Example validator: checks that all weapon entity_ids are unique."""

    def validate(self, data, context=None) -> ValidationResult:
        ids = [e.get("entity_id") for e in data if e.get("entity_type") == "weapon"]
        dupes = [i for i in ids if ids.count(i) > 1]
        if dupes:
            return ValidationResult(
                passed=False,
                severity="error",
                issues=[f"Duplicate weapon IDs: {set(dupes)}"],
            )
        return ValidationResult(passed=True, severity="info")


# --- CheckResult tests ---


class TestCheckResult:
    def test_passed(self):
        r = CheckResult(passed=True)
        assert r.passed
        assert r.issues == []
        assert r.data is None

    def test_failed_with_issues(self):
        r = CheckResult(passed=False, issues=["bad field", "missing name"])
        assert not r.passed
        assert len(r.issues) == 2

    def test_with_corrected_data(self):
        corrected = {"name": "Fallback Sword"}
        r = CheckResult(passed=True, issues=[], data=corrected)
        assert r.data == corrected


# --- BaseChecker tests ---


class TestBaseChecker:
    def test_weapon_checker_passes(self):
        checker = WeaponChecker()
        assert checker.entity_type == "weapon"
        result = checker.check({"name": "Iron Sword", "damage_die": "d8"})
        assert result.passed

    def test_weapon_checker_fails_missing_name(self):
        checker = WeaponChecker()
        result = checker.check({"damage_die": "d6"})
        assert not result.passed
        assert "missing name" in result.issues

    def test_weapon_checker_fails_invalid_die(self):
        checker = WeaponChecker()
        result = checker.check({"name": "Bad Sword", "damage_die": "d7"})
        assert not result.passed
        assert any("invalid die" in i for i in result.issues)

    def test_checker_with_context(self):
        checker = WeaponChecker()
        result = checker.check(
            {"name": "Sword", "damage_die": "d6"},
            context={"bible": {"some": "data"}},
        )
        assert result.passed


# --- ValidationResult tests ---


class TestValidationResult:
    def test_defaults(self):
        r = ValidationResult(passed=True)
        assert r.severity == "info"
        assert r.issues == []
        assert r.entity_id is None

    def test_error(self):
        r = ValidationResult(
            passed=False,
            severity="error",
            issues=["broken ref"],
            entity_id="w_001",
        )
        assert not r.passed
        assert r.severity == "error"


# --- ValidationReport tests ---


class TestValidationReport:
    def test_empty_report_passes(self):
        report = ValidationReport()
        assert report.passed
        assert report.critical_failures == 0
        assert report.minor_warnings == 0

    def test_report_with_error_fails(self):
        report = ValidationReport()
        report.add_error("something broke", entity_id="e_1", phase="test")
        assert not report.passed
        assert report.critical_failures == 1

    def test_report_with_warning_passes(self):
        report = ValidationReport()
        report.add_warning("minor issue", phase="test")
        assert report.passed
        assert report.minor_warnings == 1

    def test_mixed_results(self):
        report = ValidationReport()
        report.add_warning("warn1")
        report.add_error("err1")
        report.add_result(ValidationResult(passed=True, severity="info"))
        assert not report.passed
        assert report.critical_failures == 1
        assert report.minor_warnings == 1
        assert len(report.results) == 3

    def test_to_dict(self):
        report = ValidationReport(bible_seed="test_seed", canon_version="0.1.0")
        report.add_error("broke")
        d = report.to_dict()
        assert d["status"] == "failed"
        assert d["critical_failures"] == 1
        assert d["bible_seed"] == "test_seed"
        assert len(d["details"]) == 1


# --- BaseValidator tests ---


class TestBaseValidator:
    def test_ref_validator_passes(self):
        v = RefValidator()
        entities = [
            {"entity_type": "weapon", "entity_id": "w1"},
            {"entity_type": "weapon", "entity_id": "w2"},
        ]
        result = v.validate(entities)
        assert result.passed

    def test_ref_validator_detects_dupes(self):
        v = RefValidator()
        entities = [
            {"entity_type": "weapon", "entity_id": "w1"},
            {"entity_type": "weapon", "entity_id": "w1"},
        ]
        result = v.validate(entities)
        assert not result.passed
        assert result.severity == "error"


# --- Coherence utilities ---


class TestCheckReferences:
    def test_valid_reference(self):
        issues = check_references(
            {"giver_id": "npc_1"},
            field_name="giver_id",
            valid_ids={"npc_1", "npc_2"},
        )
        assert issues == []

    def test_invalid_reference(self):
        issues = check_references(
            {"giver_id": "npc_99"},
            field_name="giver_id",
            valid_ids={"npc_1", "npc_2"},
            label="Quest 5",
        )
        assert len(issues) == 1
        assert "npc_99" in issues[0]

    def test_list_references(self):
        issues = check_references(
            {"target_ids": ["e_1", "e_99"]},
            field_name="target_ids",
            valid_ids={"e_1", "e_2"},
        )
        assert len(issues) == 1
        assert "e_99" in issues[0]

    def test_none_field_no_issues(self):
        issues = check_references(
            {"giver_id": None},
            field_name="giver_id",
            valid_ids={"npc_1"},
        )
        assert issues == []

    def test_missing_field_no_issues(self):
        issues = check_references(
            {},
            field_name="giver_id",
            valid_ids={"npc_1"},
        )
        assert issues == []


class TestDetectCycles:
    def test_no_cycles(self):
        items = [
            {"id": "q1", "prerequisite_id": None},
            {"id": "q2", "prerequisite_id": "q1"},
            {"id": "q3", "prerequisite_id": "q2"},
        ]
        assert detect_cycles(items) == []

    def test_detects_cycle(self):
        items = [
            {"id": "q1", "prerequisite_id": "q3"},
            {"id": "q2", "prerequisite_id": "q1"},
            {"id": "q3", "prerequisite_id": "q2"},
        ]
        issues = detect_cycles(items)
        assert len(issues) > 0
        assert any("Circular" in i for i in issues)

    def test_detects_missing_parent(self):
        items = [
            {"id": "q1", "prerequisite_id": "q_nonexistent"},
        ]
        issues = detect_cycles(items)
        assert len(issues) == 1
        assert "non-existent" in issues[0]

    def test_custom_field_names(self):
        items = [
            {"quest_id": "a", "parent": None},
            {"quest_id": "b", "parent": "a"},
        ]
        assert detect_cycles(items, id_field="quest_id", parent_field="parent") == []
