"""Built-in pipeline phases.

Every phase is opt-in — canon ships these as ready-to-use implementations,
but composition is the user's job. There is no default pipeline.
"""

from canon.pipeline.phases.asset import AssetPhase
from canon.pipeline.phases.character import CharacterPhase
from canon.pipeline.phases.class_archetype import ClassPhase
from canon.pipeline.phases.database import DatabasePhase, DatabaseSpec
from canon.pipeline.phases.dialogue import DialoguePhase

# EntityPhase is deprecated in v0.2; use DatabasePhase instead.
# Kept as a re-export for backward compatibility until v0.3 removes it.
from canon.pipeline.phases.entity import EntityPhase
from canon.pipeline.phases.manifest import ManifestPhase
from canon.pipeline.phases.maze_layout import MazeLayoutPhase
from canon.pipeline.phases.narrative import NarrativePhase
from canon.pipeline.phases.spell_pool import SpellPoolPhase
from canon.pipeline.phases.story import StoryPhase
from canon.pipeline.phases.validation import ValidationPhase, validate_bible

__all__ = [
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
    "validate_bible",
]
