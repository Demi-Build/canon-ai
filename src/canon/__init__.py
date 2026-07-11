# ruff: noqa: I001
# Import order in this file is intentional: canon.llm must be imported before
# canon.backends to avoid a circular import (canon.backends.base imports
# canon.llm.request; canon.llm.client imports canon.backends.base).
# Alphabetical ordering would break the package — do not let isort reorder.
"""Canon — coherence layer for AI-generated structured content.

This module re-exports the public v0.1 API surface. See
``docs/canon_v01_scope.md`` for the contract.

Names that are not yet implemented (LLM client, generation phases,
cradle-facing operations) are commented out with ``# TODO(WaveN)``
markers and will be uncommented by their respective wave's commit.
"""

__version__ = "0.2.0"

# Bible core
from canon.bible.models import (
    Ability,
    Bible,
    BibleMetadata,
    Character,
    CharacterClass,
    ClassArchetype,
    EntityLore,
    Faction,
    GenerationTrail,
    Map,
    Spell,
    StoryArc,
    StoryBeat,
    Zone,
)

# Layout (Wave 1)
from canon.layout import Layout, MazeLayout

# Persistence (Wave 1)
from canon.persistence import (
    IDAllocator,
    write_array_db,
    write_keyed_db,
    write_per_map_file,
    write_singleton,
)

# Config
from canon.config import CanonConfig

# Dialogue
from canon.dialogue.models import (
    DialogueChoice,
    DialogueNode,
    DialogueTree,
)

# Pipeline
from canon.pipeline.phases import ValidationPhase, validate_bible
from canon.pipeline.retry import retry_with_feedback
from canon.pipeline.runner import Phase, PipelineContext, run_phase, run_pipeline
from canon.pipeline.stats import GenerationStats

# Skeleton
from canon.skeleton.core import SkeletonField, SkeletonSpec, roll_skeleton

# Validation
from canon.validation.checker import BaseChecker, CheckResult
from canon.validation.validator import (
    BaseValidator,
    ValidationReport,
    ValidationResult,
)

# LLM surface (Wave 3)
from canon.llm import LLMClient, LLMRequest
from canon.llm.prompts import DefaultPromptSet, PromptSet
from canon.backends import (
    BackendRegistry,
    FakeImageBackend,
    FakeLLMBackend,
    FakeMusicBackend,
    FakeSFXBackend,
    ImageBackend,
    ImageEditBackend,
    LLMBackend,
    MusicBackend,
    SFXBackend,
)

# Generation phases (Wave 4)
from canon.pipeline.phases import (
    AssetPhase,
    CharacterPhase,
    ClassPhase,
    DatabasePhase,
    DatabaseSpec,
    DialoguePhase,
    EntityPhase,
    ManifestPhase,
    MazeLayoutPhase,
    NarrativePhase,
    SpellPoolPhase,
    StoryPhase,
)

# Cradle-facing operations (Wave 5)
from canon.ops import generate_entity, regenerate_entity, reroll_entity_flavor

__all__ = [
    # Bible core
    "Ability",
    "Bible",
    "Map",
    "Zone",
    "Character",
    "CharacterClass",
    "ClassArchetype",
    "EntityLore",
    "Spell",
    "StoryArc",
    "Faction",
    "StoryBeat",
    "BibleMetadata",
    "GenerationTrail",
    # Layout
    "Layout",
    "MazeLayout",
    # Persistence
    "IDAllocator",
    "write_array_db",
    "write_keyed_db",
    "write_per_map_file",
    "write_singleton",
    # Dialogue
    "DialogueNode",
    "DialogueChoice",
    "DialogueTree",
    # Skeleton
    "SkeletonSpec",
    "SkeletonField",
    "roll_skeleton",
    # Pipeline
    "Phase",
    "PipelineContext",
    "run_pipeline",
    "retry_with_feedback",
    "GenerationStats",
    "AssetPhase",
    "CharacterPhase",
    "ClassPhase",
    "DatabasePhase",
    "DatabaseSpec",
    "DialoguePhase",
    "EntityPhase",
    "ManifestPhase",
    "MazeLayoutPhase",
    "NarrativePhase",
    "SpellPoolPhase",
    "StoryPhase",
    "ValidationPhase",
    # Validation
    "BaseChecker",
    "CheckResult",
    "BaseValidator",
    "ValidationResult",
    "ValidationReport",
    # LLM
    "LLMClient",
    "LLMRequest",
    "LLMBackend",
    "ImageBackend",
    "ImageEditBackend",
    "MusicBackend",
    "SFXBackend",
    "BackendRegistry",
    "FakeLLMBackend",
    "FakeImageBackend",
    "FakeMusicBackend",
    "FakeSFXBackend",
    "PromptSet",
    "DefaultPromptSet",
    # Config
    "CanonConfig",
    # Cradle-facing operations
    "reroll_entity_flavor",
    "regenerate_entity",
    "generate_entity",
    "validate_bible",
    "run_phase",
]

# Resolve DialogueTree forward-reference now that both modules are imported.
# canon.bible.models installs a permissive placeholder if dialogue isn't yet
# loaded; importing canon (which imports both) gives us the real type, so we
# rebuild Bible against the real DialogueTree here.
from canon.bible.models import Bible as _Bible  # noqa: E402
from canon.dialogue.models import DialogueTree as _DialogueTree  # noqa: E402, F401

_Bible.model_rebuild()
del _Bible, _DialogueTree
