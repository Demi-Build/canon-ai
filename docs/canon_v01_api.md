# Canon v0.1 — API Design Sketch

Python signatures only; no implementations. Pydantic models for data, Protocol classes for interfaces, ABCs where shared behavior + contract is expected. This sketch covers:

1. The core types a canon user interacts with.
2. A "hello world" usage example showing pipeline composition.
3. The cradle extension surface (Python API + CLI).
4. The user extension interfaces (what users implement).
5. Naming and API-shape decisions with justifications.

Read `canon_framing.md` and `canon_v01_scope.md` before this document.

---

## 1. Core types

### 1.1 `Bible` — the single source of truth

```python
from pydantic import BaseModel, Field
from datetime import datetime
from pathlib import Path

class Bible(BaseModel):
    """
    Single source of truth for a generated world. Accumulated incrementally
    by pipeline phases; persisted as JSON; used as context for every LLM
    call after the first.

    Not every field is populated by every pipeline. A tower defense pipeline
    might never touch `dialogues` or `characters`; a visual novel might never
    touch `class_archetypes`. Empty collections are the rule, not the exception.
    """
    canon_version: str                       # Stamped for schema compatibility
    seed: str                                # STORY_SEED used to generate this world
    story: "StoryArc"
    maps: dict[str, "Map"] = Field(default_factory=dict)
    characters: list["Character"] = Field(default_factory=list)
    class_archetypes: dict[str, "ClassArchetype"] = Field(default_factory=dict)
    dialogues: dict[str, "DialogueTree"] = Field(default_factory=dict)
    metadata: "BibleMetadata"

    # Factories
    @classmethod
    def empty(cls, seed: str) -> "Bible": ...

    # Context assembly (LLM-facing)
    def get_context(self, map_id: str) -> str: ...
    def get_cumulative_context(self, map_id: str, max_chars: int | None = None) -> str: ...

    # Convenience accessors (cradle-facing)
    def get_map(self, map_id: str) -> "Map": ...
    def get_character(self, character_id: str) -> "Character": ...
    def get_entity(self, map_id: str, entity_id: str) -> "EntityLore": ...
    def get_dialogue(self, character_id: str) -> "DialogueTree | None": ...

    # Mutation
    def add_character(self, character: "Character") -> None: ...
    def add_entity(self, map_id: str, entity: "EntityLore") -> None: ...
    def add_dialogue(self, dialogue: "DialogueTree") -> None: ...
    def update_entity(self, map_id: str, entity_id: str, **fields) -> None: ...
    def remove_entity(self, map_id: str, entity_id: str) -> None: ...

    # Persistence
    def persist(self, path: str | Path) -> None: ...
    @classmethod
    def load(cls, path: str | Path) -> "Bible": ...


class BibleMetadata(BaseModel):
    generated_at: datetime
    model_stack: dict[str, str]              # {"llm": "claude-sonnet-4-5", ...}
    phases_run: list[str]                    # Ordered list of phase names that executed
    total_cost: float = 0.0
    total_llm_calls: int = 0
```

### 1.2 `Map` and `Zone`

```python
class Map(BaseModel):
    """A spatial unit. MazeWorld's 'room' is a Map. A Destiny destination is a
    Map. A Stardew farm/town/mine area is a Map. A tower defense level is a
    Map. A Growlanser battlefield is a Map."""
    map_id: str
    name: str
    description: str                         # Lore prose
    environment: str                         # "forest", "space_station", "parlor", etc.
    level: int | None = None                 # Difficulty tier; None for non-RPG progression
    story_beat: str                          # This map's role in the escalation arc
    zones: list["Zone"] = Field(default_factory=list)
    entities: list["EntityLore"] = Field(default_factory=list)
    connections: list[str] = Field(default_factory=list)   # map_ids reachable from here
    extra: dict = Field(default_factory=dict)              # Domain-specific escape hatch


class Zone(BaseModel):
    """Optional sub-region within a Map."""
    zone_id: str
    name: str
    description: str
    parent_map_id: str
    entities: list["EntityLore"] = Field(default_factory=list)
```

### 1.3 `StoryArc`

```python
class StoryArc(BaseModel):
    """Opinionated narrative model. Generic enough for non-conflict-driven
    stories (factions and escalation_arc can be empty) but shaped for the
    common case of protagonists working toward a climax.

    `factions` is plural because Destiny has six and MazeWorld has one; a
    single-faction world uses a one-element list."""
    title: str
    synopsis: str
    seed: str
    factions: list["Faction"] = Field(default_factory=list)
    primary_antagonist_faction_id: str | None = None
    escalation_arc: list[str] = Field(default_factory=list)
    climax: str | None = None
    final_entity_id: str | None = None       # ID of the climax character/entity
    final_entity_lore: str | None = None
    beats: list["StoryBeat"] = Field(default_factory=list)


class Faction(BaseModel):
    faction_id: str
    name: str
    description: str
    history: str
    leader: str
    threat_level: int | None = None
    aesthetic: str | None = None             # Visual/tonal notes for generation consistency


class StoryBeat(BaseModel):
    map_id: str
    beat: str                                # Prose describing this map's narrative role
    boss_name: str | None = None
    boss_lore: str | None = None
```

### 1.4 `Character` — unified actor

```python
from typing import Literal

CharacterRole = Literal[
    "npc", "pc", "merchant", "hostile", "follower", "boss",
    "rival", "villager", "trainer", "neutral",
]

class Character(BaseModel):
    """Unified model for every named actor canon generates.

    An NPC is a Character with role='npc'. A Stardew villager is role='villager'.
    A Pokémon trainer is role='trainer'. A Destiny vendor is role='merchant'.
    A PC-eligible class is role='pc' with class_data populated.

    `primary_map_id` is optional because Destiny-scale characters (Zavala at
    the Tower but relevant everywhere) aren't bound to a single map. Characters
    that belong to one map set it."""
    character_id: str
    name: str
    role: CharacterRole
    primary_map_id: str | None = None
    lore: str                                # Backstory prose
    personality: str | None = None
    appearance: str | None = None
    faction_id: str | None = None            # Affiliation, if any
    class_data: "CharacterClass | None" = None     # Required for combat-relevant characters
    dialogue_tree_id: str | None = None      # Key into Bible.dialogues
    portrait_path: str | None = None
    generation_trail: "GenerationTrail | None" = None
    extra: dict = Field(default_factory=dict)


class CharacterClass(BaseModel):
    """Optional mechanical data for combat-relevant characters.

    Instantiation of a ClassArchetype for a specific character: the archetype
    defines the template; this records what was rolled for this character."""
    archetype_id: str                        # Key into Bible.class_archetypes
    stats: dict[str, int]
    abilities: list[str]                     # Ability IDs
    spells: list[str]                        # Spell IDs
    equipment: list[str] = Field(default_factory=list)
    skeleton: dict                           # The pre-rolled values this character was built from
```

### 1.5 `ClassArchetype` — generic class/job/archetype scaffolding

```python
class ClassArchetype(BaseModel):
    """Declarative spec for a character class, job, or archetype.

    Canon doesn't know what these are — the user defines them. Examples:
      - MazeWorld: warrior, mage, healer, jester
      - Destiny:   titan, hunter, warlock (with subclass variants)
      - Pokémon:   ace_trainer, youngster, gym_leader_electric
      - Tower defense: archer_tower, mage_tower, trap_tower
      - Stardew:   farmer, fisher, miner, forager (skill-based)
      - Star Ocean: invention-based class tags

    Like SkeletonSpec, this is declarative so cradle can surface and edit
    archetypes without writing Python."""
    archetype_id: str
    name: str
    description: str
    category: str | None = None              # Free-form: "combat", "support", "economy"
    stat_template: dict[str, int] = Field(default_factory=dict)   # Base stats or budget
    stat_budget: int | None = None           # If set, skeleton roller enforces sum
    role_tags: list[str] = Field(default_factory=list)            # "tank", "dps", "crafter"
    ability_pool: list[str] = Field(default_factory=list)
    spell_pool: list[str] = Field(default_factory=list)
    starting_equipment: list[str] = Field(default_factory=list)
    lore: str | None = None                  # Narrative description of the archetype
    extra: dict = Field(default_factory=dict)
```

### 1.6 `EntityLore` and `GenerationTrail`

```python
class EntityLore(BaseModel):
    """Universal lore container for non-Character entities: items, weapons,
    spells, quests, events, monster templates, tower units, capturable
    creatures, crafting recipes — whatever the game needs."""
    entity_id: str
    entity_type: str                         # User-defined: "weapon", "tower", "creature", ...
    name: str
    map_id: str | None                       # Some entities (global spells) aren't map-bound
    lore: str
    skeleton: dict                           # The pre-rolled values that produced this
    tags: list[str] = Field(default_factory=list)
    generation_trail: "GenerationTrail | None" = None
    extra: dict = Field(default_factory=dict)


class GenerationTrail(BaseModel):
    """Per-entity record of how it was produced. Cradle surfaces this on the
    'Generation trail' tab."""
    prompt: str                              # Final prompt sent to the LLM
    response: str                            # Raw LLM response
    validation_history: list[dict]           # Serialized CheckResult/ValidationResult per attempt
    retry_count: int = 0
    cost: float | None = None
    model: str | None = None
```

### 1.7 Dialogue types

```python
class DialogueChoice(BaseModel):
    """A player-selectable branch from a dialogue node."""
    text: str                                # What the player sees
    next_node_id: str | None                 # None = ends conversation
    conditions: list[str] = Field(default_factory=list)   # e.g. "has_item:key_1"
    effects: list[str] = Field(default_factory=list)      # e.g. "gives_quest:q_5"


class DialogueNode(BaseModel):
    """A single beat of dialogue, one line from one speaker."""
    node_id: str
    speaker: str                             # character_id
    text: str
    choices: list[DialogueChoice] = Field(default_factory=list)
    is_entry: bool = False                   # First node of the tree
    is_terminal: bool = False                # Leaf (no choices or all choices end)
    tags: list[str] = Field(default_factory=list)         # e.g. "greeting", "quest_complete"


class DialogueTree(BaseModel):
    """A full conversation graph for one character.

    Shape generalizes MazeWorld's existing format. Works for Destiny NPCs,
    Stardew villagers, Pokémon trainers, VN characters. Users who want
    Ink/Yarn-style flow-scripted dialogue define their own entity_type
    instead of subclassing DialogueTree."""
    tree_id: str
    character_id: str
    entry_node_id: str
    nodes: dict[str, DialogueNode]
    generation_trail: GenerationTrail | None = None
```

### 1.8 `SkeletonSpec` / `SkeletonField` / `roll_skeleton`

```python
import random
from typing import Any, Callable, TypeAlias

Weighted: TypeAlias = tuple[Any, float]

class SkeletonField(BaseModel):
    """Declarative spec for a single pre-rolled field.
    Exactly one of `choices`, `range`, or `lookup` must be set."""
    choices: list[Weighted] | None = None          # [(value, weight), ...]
    range: tuple[int, int] | None = None           # inclusive
    lookup: dict[str, Any] | None = None           # value depends on another field
    depends_on: str | None = None                  # field name this lookup keys off


class SkeletonSpec(BaseModel):
    """Declarative spec for an entity type's mechanical pre-rolls.
    `fields` is insertion-ordered: earlier fields are rolled before later
    ones, so `depends_on` relationships resolve cleanly."""
    entity_type: str
    fields: dict[str, SkeletonField]
    post: Callable[[dict], dict] | None = None     # Optional deterministic post-processor
    model_config = {"arbitrary_types_allowed": True}


def roll_skeleton(spec: SkeletonSpec, rng: random.Random | None = None) -> dict:
    """Deterministic pre-roll. Pass a seeded RNG for reproducibility."""
    ...
```

### 1.9 `Phase`, `PipelineContext`, `run_pipeline`

```python
from typing import Protocol
from dataclasses import dataclass, field

class Phase(Protocol):
    """A single pipeline stage. Phases are run in the order the user composes
    them; each reads from and writes to the shared PipelineContext.

    Canon does not enforce phase dependencies — users compose validly. If
    DialoguePhase runs before any characters exist, it produces empty output
    and logs a warning."""
    name: str
    def run(self, ctx: "PipelineContext") -> None: ...


@dataclass
class PipelineContext:
    bible: Bible
    config: "CanonConfig"
    llm: "LLMClient"
    prompts: "PromptSet"
    stats: "GenerationStats"
    rng: random.Random
    schemas: dict[str, SkeletonSpec]         # entity_type -> spec
    archetypes: dict[str, ClassArchetype] = field(default_factory=dict)
    checkers: list["BaseChecker"] = field(default_factory=list)
    validators: list["BaseValidator"] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)  # Inter-phase scratch


def run_pipeline(
    phases: list[Phase],
    ctx: PipelineContext,
    persist_after_each: str | Path | None = None,
) -> Bible:
    """Runs phases sequentially. Phases are opt-in — canon has no default
    pipeline; users compose the list of phases their game needs.

    If persist_after_each is a path, the bible is written to disk after every
    phase for crash-safety. Returns the same Bible instance attached to ctx."""
    ...
```

### 1.10 Built-in phases (all opt-in)

Canon ships these phases as ready-to-use implementations. Users compose their pipeline by picking the phases relevant to their game. **There is no default pipeline — the list below is a menu, not a default.**

```python
class StoryPhase:
    """Generates Bible.story from the seed. First phase in almost every
    pipeline, but technically optional — a tabletop encounter generator
    could skip it if the user pre-builds a StoryArc."""
    name: str = "story"
    def __init__(self, story_structure: dict | None = None): ...
    def run(self, ctx: PipelineContext) -> None: ...


class CharacterPhase:
    """Generates named Characters. Skip for games without named NPCs
    (most tower defenses, some roguelikes)."""
    name: str = "characters"
    def __init__(
        self,
        per_map: bool = True,
        count: int | Callable[[Map], int] = 2,
        roles: list[CharacterRole] = ("npc",),
    ): ...
    def run(self, ctx: PipelineContext) -> None: ...


class ClassPhase:
    """Generates ClassArchetypes and (optionally) instantiates them for PCs/NPCs.
    Used for games with class/job systems. Skip for classless games."""
    name: str = "classes"
    def __init__(
        self,
        archetype_count: int = 4,
        instantiate_for_roles: list[CharacterRole] = ("pc",),
    ): ...
    def run(self, ctx: PipelineContext) -> None: ...


class EntityPhase:
    """Generates entities of a given type. Skeleton rolled, LLM fills
    name+lore, merged. Instantiate once per entity_type you want:
    EntityPhase(entity_type='weapon'), EntityPhase(entity_type='tower'),
    EntityPhase(entity_type='crop'), etc."""
    name: str
    def __init__(
        self,
        entity_type: str,                    # Keys into ctx.schemas
        per_map: bool = True,
        count: int | Callable[[Map], int] = 3,
    ):
        self.name = f"entity:{entity_type}"


class DialoguePhase:
    """Generates DialogueTree for every Character with a role where
    dialogue makes sense. Skip for dialogue-free games (tower defense,
    most roguelikes). Requires CharacterPhase to have run first."""
    name: str = "dialogue"
    def __init__(
        self,
        roles: list[CharacterRole] = ("npc", "merchant", "villager", "trainer"),
        nodes_per_tree: int | tuple[int, int] = (3, 8),
    ): ...
    def run(self, ctx: PipelineContext) -> None: ...


class ValidationPhase:
    """Runs the 3-stage validation on ctx.bible.
    Stores the ValidationReport in ctx.artifacts['validation_report'].
    Typically run last, but can be inserted mid-pipeline to fail fast."""
    name: str = "validation"
    def run(self, ctx: PipelineContext) -> None: ...


class NarrativePhase:
    """Generates synopsis, map intros, and wrap-up text once all entities
    exist. Skip for games that don't need prose narration."""
    name: str = "narrative"
    def run(self, ctx: PipelineContext) -> None: ...
```

### 1.11 Validation

```python
from abc import ABC, abstractmethod

@dataclass
class CheckResult:
    passed: bool
    issues: list[str]
    data: Any                                # The entity that was checked


class BaseChecker(ABC):
    """Per-entity structural validation. Runs first in the 3-stage pipeline.
    Example: 'does this weapon have all required fields?'"""
    @property
    @abstractmethod
    def entity_type(self) -> str: ...        # What entity_type this checker applies to

    @abstractmethod
    def check(self, data: Any, context: dict | None = None) -> CheckResult: ...


@dataclass
class ValidationResult:
    passed: bool
    severity: Literal["info", "warning", "error"]
    issues: list[str]
    entity_id: str | None = None


class BaseValidator(ABC):
    """Cross-entity integrity. Runs after all checkers pass.
    Example: 'do all quest references resolve?'"""
    @abstractmethod
    def validate(self, data: Any, context: dict | None = None) -> ValidationResult: ...


class ValidationReport(BaseModel):
    results: list[ValidationResult]
    bible_seed: str
    canon_version: str
    timestamp: datetime

    @property
    def passed(self) -> bool: ...
    @property
    def errors(self) -> list[ValidationResult]: ...
    @property
    def warnings(self) -> list[ValidationResult]: ...


def retry_with_feedback(
    generate_fn: Callable[..., Any],
    validate_fn: Callable[[Any], tuple[bool, list[str]]],
    fallback: Any,
    max_retries: int = 3,
    label: str | None = None,
) -> Any:
    """`generate_fn` accepts an optional `feedback: list[str]` kwarg.
    On `validate_fn` failure, reasons are passed as feedback to the next
    attempt. After max_retries failures, returns `fallback`."""
    ...
```

### 1.12 LLM

```python
@dataclass
class LLMRequest:
    system: str
    user_message: str
    examples: list[tuple[str, str]] = field(default_factory=list)  # (user, assistant) pairs
    max_tokens: int = 1024


class LLMBackend(Protocol):
    """Implement for custom providers (OpenAI, Ollama, etc.).
    Registered via BackendRegistry."""
    def generate(self, request: LLMRequest) -> str: ...


class LLMClient:
    """User-facing facade. Wraps a backend, adds retry, stats wiring, and
    concurrent batching."""
    def __init__(
        self,
        backend: LLMBackend,
        stats: "GenerationStats | None" = None,
    ): ...

    def generate(self, request: LLMRequest) -> str: ...
    def generate_batch(
        self,
        requests: list[LLMRequest],
        max_workers: int = 8,
    ) -> list[str | None]: ...
```

### 1.13 `PromptSet` (world-aware defaults)

```python
class PromptSet(ABC):
    """Domain-specific prompt factory.

    Canon ships DefaultPromptSet with world-aware prompts — they pull genre,
    tone, and setting from the accumulated bible context rather than
    hardcoding fantasy tropes. Users subclass only when their domain needs
    structural changes to the prompts, not cosmetic ones.

    ~17 abstract methods total (consolidated from MazeWorld's 28).
    Representative subset shown; full list spans story, map, character,
    class, entity, dialogue, narrative, and validation-feedback variants."""

    @abstractmethod
    def story_generation(self, seed: str, structure: dict) -> LLMRequest: ...
    @abstractmethod
    def map_generation(self, story_context: str, map_index: int) -> LLMRequest: ...
    @abstractmethod
    def character_generation(
        self, context: str, skeleton: dict, role: CharacterRole
    ) -> LLMRequest: ...
    @abstractmethod
    def class_archetype_generation(
        self, context: str, category: str | None
    ) -> LLMRequest: ...
    @abstractmethod
    def entity_generation(
        self, entity_type: str, context: str, skeleton: dict
    ) -> LLMRequest: ...
    @abstractmethod
    def dialogue_tree_generation(
        self, character: Character, context: str
    ) -> LLMRequest: ...
    @abstractmethod
    def narrative_generation(self, bible: Bible, target: str) -> LLMRequest: ...

    # Retry variants accept feedback:
    @abstractmethod
    def entity_generation_with_feedback(
        self, entity_type: str, context: str, skeleton: dict, feedback: list[str]
    ) -> LLMRequest: ...
    # ... similar _with_feedback variants for story, map, character, class, dialogue


class DefaultPromptSet(PromptSet):
    """World-aware defaults. Prompts reference the seed, story, and
    accumulated bible context to produce content consistent with whatever
    the user is building — fantasy dungeon, scifi station, suburban farm,
    tower defense map, visual novel scene.

    Override individual methods for domain-specific structural needs."""
    ...
```

### 1.14 `CanonConfig` and `GenerationStats`

```python
class CanonConfig(BaseModel):
    seed: str
    num_maps: int = 3
    max_retries: int = 3
    max_llm_workers: int = 8
    context_limit_chars: int = 20000
    output_dir: Path = Path("./canon_output")
    backend_llm: str = "anthropic"           # registry key


class GenerationStats(BaseModel):
    """Cost and call tracking, wired into LLMClient."""
    llm_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost: float = 0.0
    by_phase: dict[str, dict] = Field(default_factory=dict)

    def record_call(
        self, phase: str, input_tokens: int, output_tokens: int, cost: float
    ) -> None: ...
```

---

## 2. Hello world — composing pipelines

The minimum a user writes depends on the kind of game they're building. **There is no single "hello world" — composition is the API.** Three examples:

### 2.1 A MazeWorld-shaped fantasy dungeon-crawler

```python
import random
from canon import (
    Bible, CanonConfig, LLMClient, DefaultPromptSet,
    SkeletonSpec, SkeletonField,
    PipelineContext, run_pipeline,
    StoryPhase, CharacterPhase, ClassPhase, EntityPhase,
    DialoguePhase, ValidationPhase, NarrativePhase,
    GenerationStats,
)
from canon.backends import AnthropicBackend

weapon_spec = SkeletonSpec(
    entity_type="weapon",
    fields={
        "weapon_type": SkeletonField(choices=[("sword", 3), ("bow", 2), ("staff", 1)]),
        "damage_die":  SkeletonField(choices=[("d6", 2), ("d8", 3), ("d10", 1)]),
        "element":     SkeletonField(choices=[("none", 5), ("fire", 1), ("ice", 1)]),
    },
)
monster_spec = SkeletonSpec(
    entity_type="monster",
    fields={
        "hp":         SkeletonField(range=(8, 40)),
        "damage_die": SkeletonField(choices=[("d4", 3), ("d6", 2), ("d8", 1)]),
    },
)

config = CanonConfig(seed="shadowspire_001", num_maps=3)
stats = GenerationStats()
llm = LLMClient(backend=AnthropicBackend(model="claude-sonnet-4-5"), stats=stats)

ctx = PipelineContext(
    bible=Bible.empty(seed=config.seed),
    config=config, llm=llm, prompts=DefaultPromptSet(),
    stats=stats, rng=random.Random(config.seed),
    schemas={"weapon": weapon_spec, "monster": monster_spec},
)

bible = run_pipeline(
    phases=[
        StoryPhase(),
        ClassPhase(archetype_count=4),
        CharacterPhase(per_map=True, count=2, roles=["npc", "merchant"]),
        EntityPhase(entity_type="weapon", per_map=True, count=3),
        EntityPhase(entity_type="monster", per_map=True, count=2),
        DialoguePhase(),
        ValidationPhase(),
        NarrativePhase(),
    ],
    ctx=ctx,
    persist_after_each="data/world.json",
)
```

### 2.2 A tower defense — skips dialogue and characters

```python
tower_spec = SkeletonSpec(
    entity_type="tower",
    fields={
        "tower_type":   SkeletonField(choices=[("archer", 3), ("mage", 2), ("trap", 1)]),
        "damage":       SkeletonField(range=(10, 100)),
        "range":        SkeletonField(range=(1, 5)),
    },
)
enemy_spec = SkeletonSpec(
    entity_type="enemy_wave",
    fields={
        "wave_size":  SkeletonField(range=(5, 30)),
        "enemy_type": SkeletonField(choices=[("grunt", 3), ("elite", 1), ("boss", 0.2)]),
    },
)

ctx = PipelineContext(
    bible=Bible.empty(seed="defend_the_hold_007"),
    config=config, llm=llm, prompts=DefaultPromptSet(),
    stats=stats, rng=random.Random("defend_the_hold_007"),
    schemas={"tower": tower_spec, "enemy_wave": enemy_spec},
)

bible = run_pipeline(
    phases=[
        StoryPhase(),
        ClassPhase(archetype_count=5, instantiate_for_roles=[]),  # archetypes for towers, not PCs
        EntityPhase(entity_type="tower", per_map=False, count=5),
        EntityPhase(entity_type="enemy_wave", per_map=True, count=8),
        ValidationPhase(),
    ],
    ctx=ctx,
)
# No CharacterPhase, no DialoguePhase, no NarrativePhase — not needed for a TD.
```

### 2.3 A visual novel with stats — skips maps-as-locations and monsters

```python
route_spec = SkeletonSpec(entity_type="route", fields={
    "romance_target": SkeletonField(choices=[("friendship", 2), ("romance", 1)]),
    "ending_tier":    SkeletonField(choices=[("good", 3), ("true", 1), ("bad", 2)]),
})

ctx = PipelineContext(
    bible=Bible.empty(seed="summer_at_akashi_harbor"),
    config=CanonConfig(seed="summer_at_akashi_harbor", num_maps=4),
    llm=llm, prompts=DefaultPromptSet(),
    stats=stats, rng=random.Random("summer_at_akashi_harbor"),
    schemas={"route": route_spec},
)

bible = run_pipeline(
    phases=[
        StoryPhase(),
        CharacterPhase(per_map=False, count=5, roles=["npc"]),   # Global cast, not map-bound
        EntityPhase(entity_type="route", per_map=False, count=5),
        DialoguePhase(nodes_per_tree=(8, 20)),                   # Dialogue-heavy
        ValidationPhase(),
        NarrativePhase(),
    ],
    ctx=ctx,
)
# No ClassPhase, no monster EntityPhase, no weapon EntityPhase.
```

All three pipelines produce a `Bible` with the same shape; only the populated fields differ. Cradle loads any of them without special-casing.

---

## 3. Cradle extension surface

Cradle invokes canon via the Python API (when embedded — not v0.1) or the `canon` CLI (from Tauri). Every operation has both forms.

### 3.1 Python functions

```python
# Inspection — use Bible.load() and walk the tree
bible = Bible.load("data/world.json")
# cradle walks bible.maps, bible.characters, bible.dialogues,
# bible.class_archetypes, entity.generation_trail, etc.

# Re-roll only the flavor text of a single entity (keeps the skeleton)
def reroll_entity_flavor(
    bible: Bible,
    map_id: str,
    entity_id: str,
    llm: LLMClient,
    prompts: PromptSet,
) -> EntityLore: ...

# Regenerate an entity from scratch (new skeleton + new flavor)
def regenerate_entity(
    bible: Bible,
    map_id: str,
    entity_type: str,
    entity_id: str,                          # ID of entity to replace
    llm: LLMClient,
    prompts: PromptSet,
    spec: SkeletonSpec,
    rng: random.Random | None = None,
) -> EntityLore: ...

# Add a brand-new entity to a map
def generate_entity(
    bible: Bible,
    map_id: str,
    entity_type: str,
    llm: LLMClient,
    prompts: PromptSet,
    spec: SkeletonSpec,
    rng: random.Random | None = None,
) -> EntityLore: ...

# Re-run the 3-stage validation on the current bible state
def validate_bible(
    bible: Bible,
    checkers: list[BaseChecker],
    validators: list[BaseValidator],
) -> ValidationReport: ...

# Re-run a single pipeline phase on the current bible
def run_phase(phase: Phase, ctx: PipelineContext) -> None: ...
```

### 3.2 CLI surface

Every command accepts `--bible <path>` (or positional path) and emits JSON on stdout. All commands exit `0` on success and non-zero on failure, with errors as JSON on stderr. All output is versioned with `"canon_version"` in the top-level object.

```
canon bible load <path>
    → dumps the full bible as JSON to stdout.

canon bible validate <path> [--checkers <module:attr>] [--validators <module:attr>]
    → runs validate_bible; emits ValidationReport as JSON.

canon reroll <path> --map <map_id> --entity <entity_id>
    → re-rolls flavor for the entity; writes updated bible back;
      emits the new EntityLore as JSON.

canon regenerate <path> --map <map_id> --entity <entity_id> --spec <module:attr>
    → regenerates entity with a fresh skeleton; emits new EntityLore;
      writes bible back.

canon generate <path> --map <map_id> --entity-type <type> --spec <module:attr>
    → creates a new entity of that type; emits new EntityLore;
      writes bible back.

canon phase <phase_name> <path> [--phase-args <json>] [--pipeline <module:attr>]
    → runs a single phase on the current bible state; writes bible back.

canon --version
    → prints canon version; useful for cradle's compatibility check.
```

Flags common to all commands:
- `--config <path>` — load a `CanonConfig` from JSON/TOML.
- `--human` — emit human-readable output instead of JSON (debugging only).
- `--quiet` — suppress progress bars/logs (stderr still gets errors).

`<module:attr>` is standard Python entry-point syntax (e.g., `mygame.specs:weapon_spec`).

---

## 4. User extension interfaces

### 4.1 Custom LLM backend

```python
from canon.backends import LLMBackend, BackendRegistry
from canon import LLMRequest, LLMClient

class OllamaBackend:
    """Implements the LLMBackend protocol."""
    def __init__(self, model: str = "llama3.1", host: str = "http://localhost:11434"):
        self.model = model
        self.host = host

    def generate(self, request: LLMRequest) -> str:
        # user's HTTP call to Ollama
        ...

BackendRegistry.register_llm("ollama", OllamaBackend)
llm = LLMClient(backend=BackendRegistry.llm("ollama"))
```

### 4.2 Custom checker and validator

```python
from canon import BaseChecker, CheckResult, BaseValidator, ValidationResult

class WeaponChecker(BaseChecker):
    entity_type = "weapon"

    def check(self, data, context=None) -> CheckResult:
        issues = []
        if data["damage_die"] not in {"d4", "d6", "d8", "d10", "d12"}:
            issues.append(f"invalid die: {data['damage_die']}")
        if not data.get("name"):
            issues.append("missing name")
        return CheckResult(passed=not issues, issues=issues, data=data)


class QuestIntegrityValidator(BaseValidator):
    """Cross-entity: every quest's target character must exist."""
    def validate(self, data, context=None) -> ValidationResult:
        bible = context["bible"]
        issues = []
        # ... walk references, check resolution
        return ValidationResult(
            passed=not issues,
            severity="error" if issues else "info",
            issues=issues,
        )
```

### 4.3 Custom skeleton schema

Users construct `SkeletonSpec` declaratively. No subclassing. For derived fields that can't be expressed declaratively, use the `post` callback:

```python
ABILITY_STAMINA_BY_PURPOSE = {"break": 2, "bash": 3, "rally": 1}

ability_spec = SkeletonSpec(
    entity_type="ability",
    fields={
        "purpose": SkeletonField(choices=[("break", 1), ("bash", 1), ("rally", 1)]),
        "stat":    SkeletonField(choices=[("str", 1), ("cha", 1)]),
    },
    post=lambda skel: {**skel, "stamina_cost": ABILITY_STAMINA_BY_PURPOSE[skel["purpose"]]},
)
```

### 4.4 Custom PromptSet

```python
from canon import DefaultPromptSet, LLMRequest

class MyHardScifiPromptSet(DefaultPromptSet):
    """Subclass the default; override only what needs structural customization."""
    def entity_generation(self, entity_type, context, skeleton) -> LLMRequest:
        if entity_type == "ship":
            return LLMRequest(
                system="You design spacecraft for a hard-scifi setting grounded in "
                       "realistic orbital mechanics and plausible near-future tech...",
                user_message=(
                    f"World context:\n{context}\n\n"
                    f"Ship skeleton (pre-rolled stats):\n{skeleton}\n\n"
                    "Produce the ship's designation and description as JSON."
                ),
                max_tokens=512,
            )
        return super().entity_generation(entity_type, context, skeleton)
```

### 4.5 Custom pipeline phase

```python
from canon import Phase, PipelineContext

class TerritoryAssignmentPhase:
    """Custom phase for a 4X-style game: assign factions to maps based on
    proximity and threat level."""
    name: str = "territories"

    def run(self, ctx: PipelineContext) -> None:
        for faction in ctx.bible.story.factions:
            # ...pick maps, assign, persist to bible.extra or a custom field
            ...

# Then compose it like any built-in:
run_pipeline(
    phases=[StoryPhase(), TerritoryAssignmentPhase(), CharacterPhase(), ValidationPhase()],
    ctx=ctx,
)
```

---

## 5. Naming and API decisions

**`Bible` vs `WorldBible` vs `KnowledgeGraph`.** Kept `Bible`. MazeWorld's term, shorter than `WorldBible`, more evocative than `KnowledgeGraph` (which over-promises structure — a bible is prose *plus* structure). `Canon` would collide with the package name. The term is standard in games writing ("series bible," "world bible").

**`Map` vs `Room` vs `Section` vs `Location`.** Kept `Map`. `Room` is too MazeWorld-specific; `Section` is generic but meaningless; `Location` is overloaded. `Map` gives a clear mental model and works for dungeons, destinations, farms, battlefields, routes.

**`Character` unified via a `role` field.** Per discovery decision. Roles expanded from MazeWorld's four to include `rival`, `villager`, `trainer` — covering Pokémon, Stardew, VN, and shooter needs. Role discrimination belongs in data, not in types. The `class_data: CharacterClass | None` is the escape valve: characters without mechanics don't pay for the mechanical schema.

**`Character.primary_map_id` is optional.** MazeWorld's NPCs are always map-bound. Destiny's Zavala lives at the Tower but is relevant across every destination — he's not really "bound" to the Tower the way a MazeWorld merchant is bound to Room 3. Making the field optional costs nothing for map-bound characters and correctly models the Destiny case.

**`factions: list[Faction]`, not `faction: Faction | None`.** Destiny has six factions. MazeWorld has one. The plural list covers both. Trade-off: MazeWorld pays a one-element-list tax. Accepted. Alternative considered: `primary_faction` + `additional_factions`. Rejected — privileges the single-faction case at the expense of multi-faction clarity.

**`StoryArc.escalation_arc` is singular in v0.1.** Destiny's interweaving seasonal arcs don't fit one list. For v0.1 we ship a single arc and note parallel arcs as v0.2. The field name (`escalation_arc`, not `primary_arc`) leaves room to add `side_arcs` later without renaming.

**`ClassArchetype` as generic declarative scaffolding.** Mirrors `SkeletonSpec` philosophy. Canon knows nothing about archetype content; users declare them. `stat_budget` (optional) lets skeleton rolling enforce totals for games that need them. Alternative considered: make `ClassArchetype` domain-specific with required fields like `hp`, `mp`, `attack`. Rejected — not every game has HP/MP/attack; Stardew's farmer archetype doesn't. Generic is the right call even if it means MazeWorld's archetypes carry their specifics in `stat_template` + `extra`.

**`EntityLore` as universal container for non-character entities.** Weapons, items, spells, quests, events, monster templates, tower units, capturable creatures, crafting recipes, Stardew crops, Destiny weapon perks — all are `EntityLore` with different `entity_type` strings. Canon doesn't know what entity types exist; that's user-defined. The `extra: dict` is the escape valve.

**Dialogue types in canon core.** `DialogueNode`, `DialogueChoice`, `DialogueTree` ship in canon, not MazeWorld. The `{nodes, choices, next_node_id, entry_node_id}` shape generalizes cleanly across Destiny vendor dialogue, Stardew villager chat, Pokémon trainer exchanges, VN branching, and MazeWorld NPC dialogue. Alternative considered: leave dialogue as a MazeWorld reference format. Rejected — dialogue is close to universal for RPG-shaped games and belongs in core. Custom dialogue shapes (Ink, Yarn) are user-defined entity types, not subclasses.

**`SkeletonField` declarative, not functional.** (`choices`, `range`, `lookup`) rather than `Callable[[rng], Any]`. Declarative wins because specs are serializable (cradle sees why a value was rolled), editable (cradle can mutate them without writing Python), and diffable. `SkeletonSpec.post` is the function escape valve for derivations the declarative form can't express.

**`Phase` as `Protocol`, not `ABC`.** Any class with `name: str` and `run(ctx) -> None` satisfies it. No inheritance required. Phases share a contract, not behavior.

**All built-in phases are opt-in.** No default pipeline. The list of phases in `run_pipeline(phases, ctx)` is whatever the user specifies. Alternative considered: ship a default pipeline a user can extend. Rejected — every game needs different phases; a default would either be too opinionated (fantasy dungeon-crawler-centric) or too empty to be useful. The menu-of-phases approach forces users to think about what they need, which is the right mental model.

**Canon does not enforce phase dependencies.** `DialoguePhase` logs a warning if it runs with no characters, but canon doesn't refuse to run an invalid composition. Alternative considered: a DAG-based phase system with declared dependencies. Rejected as over-engineering for v0.1. Users compose validly or see warnings.

**`PipelineContext` as mutable dataclass.** Phases write to `ctx.bible` and `ctx.artifacts`. `artifacts: dict[str, Any]` is the escape hatch for phase-to-phase communication that doesn't belong in the bible itself.

**`retry_with_feedback` as a free function.** Stateless, ~30 lines. A class would add noise.

**`LLMClient` wraps `LLMBackend`.** `LLMBackend` is the provider adapter (thin by design). `LLMClient` is the user-facing facade with retry, stats, and batch. Separation of concerns.

**`PromptSet` subclassing over composition.** Users inherit `DefaultPromptSet` and override methods; `super()` falls back to defaults. Idiomatic Python; benefits from IDE support.

**`DefaultPromptSet` is world-aware, not genre-specific.** Its prompts pull genre/tone/setting from the accumulated bible context rather than assuming fantasy. A seed of "derelict generation ship, 2387" produces scifi content; a seed of "Stardew-style valley town" produces cozy farming-sim content. This is the key decision that makes opt-in composition + default prompts actually work for non-MazeWorld games.

**CLI framework: Typer.** Type-hint-based; matches canon's Pydantic-centric style.

**Bible persisted as JSON.** Human-readable, diffable, parseable by cradle's Rust backend without Pydantic.

**Schema versioning via single `canon_version` field.** v0.1 stamps, v0.2 adds migration.

**No async at the pipeline level.** `run_pipeline` is synchronous. Concurrency is inside `LLMClient.generate_batch()` via threads. Async pipelines are a v0.3+ concern.

**CLI is the only non-Python interface in v0.1.** Per discovery decision: cradle (Tauri) calls `canon` as a subprocess. No HTTP, no WebSocket, no PyO3. `canon serve` is a later upgrade path, not v0.1.
