# Canon Extraction Discovery Report

**Date:** 2026-04-19
**Scope:** Investigation and proposal only. No code was moved or refactored.

---

## Part 1: What's Actually There

### 1.1 Generation Pipeline Map

A generation run starts at `main.py:1140` (`main()`), which calls `run_generation()` at line 73, which imports and calls `generate_world()` from `src/generate/pipeline.py:1923`.

The pipeline is a 10-phase linear orchestrator with `tqdm` progress tracking. Here is every file and function involved:

**Orchestration:**
| File | Function | Description |
|------|----------|-------------|
| `main.py:73` | `run_generation()` | Entry shim; imports and calls `generate_world()` |
| `src/generate/pipeline.py:1923` | `generate_world()` | Master orchestrator; seeds RNG, wires stats, runs all phases |
| `src/generate/pipeline_utils.py:17` | `_retry_with_feedback()` | Generate-validate-retry loop used by most phases |

**Phase functions (all in `pipeline.py`):**
| Phase | Function | Lines | LLM Calls | Description |
|-------|----------|-------|-----------|-------------|
| 0 | `_phase0_environments()` | 115-132 | 1 | Generate environment sequence (forest, cave, etc.) |
| 1 | `_phase1_story()` | 140-280 | 1+N | Overarching story + per-room beats; creates WorldBible |
| 2 | `_phase2_layouts()` | 281-378 | 0+2 | Maze generation, tile placement, music/SFX prompts |
| 3A | `_phase3a_classes()` | 386-412 | 5 | Player classes with skeleton pre-rolls |
| 3A-ii | `_phase3a_spell_pools()` | 415-449 | varies | Spell pools by element |
| 3B | `_phase3b_items()` | 451-567 | 1/room | Items + weapons with skeletons |
| 3C | `_phase3c_npcs()` | 568-671 | 1/room | NPCs with backstories |
| 3D | `_phase3d_monsters()` | 672-982 | 1/room | Monsters with level scaling |
| 4A | `_phase4a_events()` | 983-1373 | 2-3/room | Events, quests, encounters |
| 4B | `_phase4b_dialogue()` | 1374-1530 | N/room | Dialogue trees per NPC |
| 5 | `_phase5_validate()` | 1531-1565 | 0 | 3-layer validation |
| 6 | `_phase6_narrative()` | 1573-1599 | N+3 | Synopsis, room intros, game over/victory text |
| 7 | `_phase7_portraits()` | 1607-1750 | 0 | Portraits, music, SFX (async) |
| 8 | manifest assembly | 2079-2183 | 0 | Flush entity DBs, write manifest.json |
| 9 | player guide | 2187-2196 | 0 | Optional PDF guide |

**Supporting modules:**
| File | Role |
|------|------|
| `src/generate/llm_client.py` | Single `generate(LLMRequest) -> str` with retry |
| `src/generate/llm_executor.py` | `generate_batch()` for concurrent LLM requests |
| `src/generate/image_client.py` | Portrait generation (parallel async) |
| `src/generate/music_client.py` | Music generation via Lyria 3 |
| `src/generate/sfx_client.py` | SFX generation via ElevenLabs |
| `src/generate/checker.py` | Per-entity structural validation (BaseChecker + 6 concrete) |
| `src/generate/validator.py` | Cross-entity validation (BaseValidator + 6 concrete) + ValidationReport |
| `src/generate/world_editor.py` | Cross-room coherence + gameplay audit |
| `src/generate/class_gen.py` | Class generation with skeleton pipeline |
| `src/generate/placement.py` | Entity-to-tile placement algorithms |
| `src/generate/summary_agent.py` | Narrative text generation |
| `src/generate/guide_builder.py` | PDF player guide |
| `src/generate/generation_stats.py` | Cost and call tracking |
| `src/generate/generators/llm_primitives.py` | Lower-level LLM generation helpers |
| `src/generate/generators/spell_pool_gen.py` | Spell pool generation |
| `src/generate/backends/base.py` | Abstract `LLMBackend`, `ImageBackend`, `MusicBackend` |
| `src/generate/backends/registry.py` | Singleton backend registry (lazy loading) |
| `src/generate/backends/llm_api.py` | Anthropic Claude backend |
| `src/generate/backends/llm_local.py` | HuggingFace/Llama local backend |
| `src/generate/backends/image_api.py` | fal.ai image backend |
| `src/generate/backends/image_local.py` | Local diffusion (FLUX/SDXL) backend |
| `src/prompts/base.py` | `LLMRequest` dataclass + `PromptSet` ABC (28 abstract methods) |
| `src/prompts/generator_prompts/claude_prompts.py` | Claude-optimized prompts (1,772 lines) |
| `src/prompts/generator_prompts/llama_prompts.py` | Llama-optimized prompts (922 lines) |
| `src/prompts/checker_prompts/` | Checker-specific prompts |
| `src/prompts/validator_prompts/` | Validator-specific prompts |
| `src/prompts/world_editor_prompts/` | World editor prompts |

### 1.2 World Bible Implementation

**Data structure:** Pydantic `BaseModel` hierarchy in `src/models/world_bible.py` (181 lines).

```
WorldBible
  ├── story: OverarchingStory          (from src/models/story.py)
  │     ├── title, synopsis, seed
  │     ├── faction: Faction (name, description, history, leader, threat_level)
  │     ├── escalation_arc: list[str]
  │     ├── climax, final_boss_name, final_boss_lore
  │     ├── beats: list[RoomStoryBeat]
  │     ├── story_npcs: list[StoryNPC]
  │     ├── story_items: list[StoryItem]
  │     └── story_monsters: list[StoryMonster]
  ├── rooms: dict[str, RoomBible]
  │     └── RoomBible
  │           ├── environment, environment_name, level
  │           ├── story_beat, boss_name, boss_lore
  │           ├── npcs: list[EntityLore]
  │           ├── items: list[EntityLore]
  │           ├── monsters: list[EntityLore]
  │           ├── encounters: list[str], quests: list[str]
  │           └── gate_encounter_id
  ├── player_classes: list[EntityLore]
  └── entity_index: dict[str, EntityRef]   (legacy, not populated in production)
```

**EntityLore** is the universal lore container: `entity_type`, `entity_id`, `name`, `room_id`, `lore` (full prose paragraph), `tags`.

**Built incrementally:** Phase 1 creates the bible with empty `RoomBible` entries. Phases 3B-3D add `EntityLore` entries via `bible.add_npc()`, `bible.add_item()`, `bible.add_monster()`. The bible is persisted to disk after each room (`pipeline.py:2002`).

**Serialized as JSON** via `bible.persist()` / `WorldBible.load()` (`world_bible.py:170-180`). Uses `model_dump()` + `json.dump` with 2-space indent.

**Cross-reference index:** `entity_index` exists as a `dict[str, EntityRef]` but is **not populated by the production pipeline**. It's only used in the test-only `build_world_bible()` helper in `world_editor.py:20-127`. The production pipeline uses `get_cumulative_context()` and `get_all_npc_names()` for cross-entity awareness instead.

**Context methods for generators:**
- `get_story_context(room_id)` — story + faction + beat + story NPCs/items/monsters for the room (`world_bible.py:76-100`)
- `get_cumulative_context(room_id)` — story context + all previous rooms' generated entities, soft-capped at `STORY_CONTEXT_LIMIT * 2` chars (`world_bible.py:102-142`)

**Coupling to config:** `world_bible.py` imports `DATA_DIR` and `STORY_CONTEXT_LIMIT` from `config.py` directly at module level (line 14).

### 1.3 Skeleton Pattern

The skeleton pattern is **well-established and consistent** across entity types. Each follows the same two-phase pattern: deterministic pre-roll, then LLM adds only name + description.

**Weapon skeletons** — `src/models/weapon.py:269-308` (`roll_weapon_skeleton()`):
- Pre-rolls: `weapon_type` (heavy/light/sacred/arcane/enchanted/wild), `weapon_category` (simple/martial), `damage_type`, `stat_modifier`, `attack_dice`, optional `magic_element`
- All use weighted random distributions from hardcoded tables
- LLM receives the skeleton and outputs only `{name, desc}` per weapon

**Spell skeletons** — `src/models/spell.py:135-157` (`roll_spell_skeleton()`):
- Pre-rolls: `spell_type`, `targets`, `num_dice`, `die_sides`, `stamina_cost`, `element`, `stat`
- Dice computed from `compute_spell_dice()` based on room level
- LLM outputs only `{name, description}`

**Ability skeletons** — `src/models/player.py:64-73` (`roll_ability_skeleton()`):
- Pre-rolls: `purpose` (break/bash/intimidate/rally/climb/detect/grapple/warcry), `stat`, `stamina_cost`
- Stamina cost from lookup table `ABILITY_STAMINA_BY_PURPOSE`

**Monster scaling** — `src/models/monster.py:171-223` (`instantiate_monster()`):
- Pre-rolls from `LEVEL_SCALING`: `hp`, `ac`, `str_mod`, `dex_mod`, `damage_dice_expr`
- Template defines abilities; stats are level-scaled deterministically

**Class generation** — `src/generate/class_gen.py:69-80`:
- `_build_archetype_skeletons()` creates stat budgets per archetype
- Full pipeline in one function: build skeletons -> LLM generate -> check -> validate

**Merge point:** `src/generate/pipeline_utils.py:196-287` (`_build_items_list()`) is where LLM output merges with weapon skeletons. Skeleton values (dice, type, modifiers) always win; LLM values are fallback only.

**Abstraction consistency:** The skeleton functions are per-entity-type (`roll_weapon_skeleton`, `roll_spell_skeleton`, `roll_ability_skeleton`) but they are **not unified under a common protocol**. Each lives in its respective model file with its own signature. The pattern is consistent in spirit but ad-hoc in implementation.

### 1.4 Three-Stage Validator

The brief's mental model of "Checker -> Validator -> World Editor" maps closely to the code, but the actual staging is slightly different:

**Stage 1: Checker** — `src/generate/checker.py`
- `BaseChecker` ABC (line 81-87) with single abstract method: `check(data, context) -> CheckResult`
- 6 concrete implementations: `ClassChecker`, `QuestChecker`, `EventChecker`, `NPCChecker`, `MonsterChecker`, `ItemChecker`
- Each validates **structural correctness** of a single entity: required fields present, stat budgets balanced, dice formats valid, archetype coverage
- Returns `CheckResult(passed: bool, issues: list[str], data: object)`

**Stage 2: Validator** — `src/generate/validator.py`
- `BaseValidator` ABC (line 123-129) with abstract `validate(data, context) -> ValidationResult`
- 6 concrete validators mirror checkers: `ClassValidator`, `QuestValidator`, `EventValidator`, `NPCValidator`, `MonsterValidator`, `ItemValidator`
- Validates **business logic**: stat values within archetype role ranges, quest references resolve, event solvability
- `ValidationReport` dataclass (line 29-106) accumulates findings with severity levels

**Stage 3: World Editor** — `src/generate/world_editor.py`
- Three functions, not a class:
  - `editor_coherence_check(bible, room_results)` (line 387-431): Cross-room coherence (duplicate NPC names, escalation progression, empty rooms)
  - `cross_validate(bible, npc_pool, event_list, quest_list, item_placements)` (line 130-153): Quest reference integrity
  - `gameplay_audit(bible, npc_pool, event_list, quest_list, item_placements)` (line 174-379): Full coherence audit (story quests exist, chains acyclic, combat events have monsters, time-gate ratio)

**Retry-with-feedback loop:** `src/generate/pipeline_utils.py:17-44`
```python
def _retry_with_feedback(generate_fn, validate_fn, fallback, label, max_retries=3):
    feedback = None
    for attempt in range(1, max_retries + 1):
        content = generate_fn(feedback=feedback) if feedback else generate_fn()
        passed, reasons = validate_fn(content)
        if passed: return content
        feedback = reasons  # Feed failure reasons back to generator
    return fallback
```

The feedback mechanism is **uniform**: all stages produce `list[str]` reasons, all generators accept an optional `feedback` kwarg. The loop is a single generic function, not bespoke per stage.

**Phase 5 orchestration** (`pipeline.py:1531-1565`) runs all three world-editor functions sequentially, collecting issues into a `ValidationReport`.

### 1.5 LLM Interface

**Clean abstraction layer exists:**

```
User code
  └── llm_client.generate(LLMRequest) -> str       # src/generate/llm_client.py
        └── get_llm_backend() -> LLMBackend         # src/generate/backends/registry.py
              ├── ApiLLMBackend (Claude)              # src/generate/backends/llm_api.py
              └── LocalLLMBackend (Llama)             # src/generate/backends/llm_local.py
```

- `LLMRequest` dataclass (`src/prompts/base.py:5-20`): `system`, `examples: list[tuple[str,str]]`, `user_message`, `max_tokens`
- `LLMBackend` ABC (`src/generate/backends/base.py:6-12`): single method `generate(LLMRequest) -> str`
- `BackendRegistry` singleton (`src/generate/backends/registry.py:10-48`): lazy-loads backend based on `config.LLM_BACKEND`
- `generate_batch()` (`src/generate/llm_executor.py:16-53`): ThreadPoolExecutor for concurrent API calls; forces `max_workers=1` for local backend

**Mode switch** is config-driven (`config.py:121`):
- `GAME_MODE = os.getenv("GAME_MODE", "offline_static")` — controls runtime dialogue behavior
- `LLM_BACKEND = os.getenv("LLM_BACKEND", "api")` — controls generation-time backend
- `IMAGE_BACKEND = os.getenv("IMAGE_BACKEND", "api")` — controls image backend

Runtime dialogue in `src/utils/conversation_utils.py` dispatches on `GAME_MODE`:
- `offline_static`: navigates pre-generated dialogue tree (zero LLM calls)
- `offline_local` / `online`: makes live LLM calls using `llm_client.generate()`

**Prompt selection:** `src/prompts/__init__.py` returns `ClaudePromptSet` or `LlamaPromptSet` based on `LLM_BACKEND`. Both implement the 28-method `PromptSet` ABC.

### 1.6 Asset Generation

**Image generation:**
- `ImageBackend` ABC (`backends/base.py:15-25`): `generate_image(prompt, width, height)` + `generate_and_save(prompt, filepath)`
- API: fal.ai (`backends/image_api.py`) using `fal-ai/nano-banana`
- Local: FLUX.1-schnell (CUDA) or SDXL Turbo (MPS/CPU) (`backends/image_local.py`)
- Orchestration: `image_client.py` with async parallel generation, 15-concurrent semaphore

**Music generation:** `music_client.py`
- Google Lyria 3 via `google.genai` SDK
- No `MusicBackend` ABC integration — the `MusicBackend` in `base.py` has a TODO and no implementations use it
- Hardcoded story-independent tracks + LLM-generated faction-specific combat prompts
- Cost tracking: $0.08/pro track, $0.04/clip

**SFX generation:** `sfx_client.py`
- ElevenLabs SDK
- No ABC integration either — standalone module
- 10-concurrent semaphore, 3 retries per effect
- Cost tracking: $0.04/effect

**Shared infrastructure:** `GenerationStats` (`generation_stats.py`) is wired into all backends for cost/call tracking. Each asset module is otherwise **independent**: separate prompt building, file naming, retry logic. Phase 7 coordinates them via `asyncio.gather()`.

### 1.7 Coupling Points

**Generation code imports from game/model code:**
- `src/models/maze.Maze, TileMeta` — maze layout and tile placement structures
- `src/models/story.Faction, OverarchingStory, RoomStoryBeat, StoryNPC, StoryItem, StoryMonster` — story data structures
- `src/models/world_bible.EntityLore, RoomBible, WorldBible` — world bible
- `src/models/weapon.roll_weapon_skeleton` — skeleton pre-roll
- `src/models/spell.roll_spell_skeleton, compute_spell_dice` — skeleton pre-roll
- `src/models/player.roll_ability_skeleton, ARCHETYPE_STAT_ROLES, STAT_BUDGET, STAT_NAMES, Stats` — skeleton + validation constants
- `src/models/monster.instantiate_monster, LEVEL_SCALING` — monster scaling
- `src/registry.registry` — data loading/saving
- `config.*` — ~20 config values imported directly
- `src/data/world_data.ENVIRONMENT_TYPES` — list of valid environment types

**Game code imports from generation code:**
- `main.py` imports `generate_world` from `pipeline.py` (only entry point)
- `src/utils/conversation_utils.py` imports `generate` from `llm_client.py` and `get_prompt_set()` from `prompts` (runtime dialogue)

**Game-specific assumptions baked into generation:**
1. **Tile-grid maze:** `pipeline.py` imports `MAZE_WIDTH`, `MAZE_HEIGHT`, `GRID_SIZE` from config; generation produces maze JSON with x/y tile positions
2. **Pygame colors:** `config.py:103-107` defines `MAP_COLORS` with RGB tuples
3. **Entity ID scheme:** XYYY ranges (1000-series NPCs, 2000-series items, etc.) — hardcoded convention in pipeline
4. **Room structure:** `room_0` through `room_N` naming, `data/rooms/room_X/` directory structure
5. **MazeWorld-specific entity types:** `StaticNPC`, `RandomNPC`, `MerchantNPC`, `AggressiveNPC` are game-specific NPC subtypes baked into generation prompts
6. **Dialogue tree format:** The `{nodes: {start: {prompt, choices: [{text, next_node_id}]}}}` structure is MazeWorld's dialogue system format
7. **Player class archetypes:** Hardcoded to warrior/mage/healer/jester (4 classes always)
8. **Screen dimensions:** `SCREEN_WIDTH=800`, `SCREEN_HEIGHT=700` affect portrait sizing

---

## Part 2: Library-Shaped vs. Game-Shaped

### Classification

| Module / File | Classification | Notes |
|--------------|----------------|-------|
| **src/generate/pipeline.py** | Canon core, needs generalization | Orchestrator is engine-agnostic in structure but hardcodes MazeWorld entity types, room naming, directory layout, and config imports |
| **src/generate/pipeline_utils.py** | Canon core | `_retry_with_feedback()` is fully generic. Item/event builders are MazeWorld-specific — **file needs splitting** |
| **src/generate/llm_client.py** | Canon core | Clean, generic LLM client |
| **src/generate/llm_executor.py** | Canon core | Generic batch executor |
| **src/generate/backends/base.py** | Canon core | Clean ABCs |
| **src/generate/backends/registry.py** | Canon core, needs generalization | Hardcodes "api"/"local" as only options; should be extensible |
| **src/generate/backends/llm_api.py** | Canon core | Anthropic backend |
| **src/generate/backends/llm_local.py** | Canon core | HuggingFace backend |
| **src/generate/backends/image_api.py** | Canon core (or canon-assets) | fal.ai backend |
| **src/generate/backends/image_local.py** | Canon core (or canon-assets) | Local diffusion backend |
| **src/generate/checker.py** | Straddles | `BaseChecker` + `CheckResult` are canon core. Concrete checkers (ClassChecker, ItemChecker, etc.) are MazeWorld reference implementations. **File needs splitting.** |
| **src/generate/validator.py** | Straddles | `BaseValidator` + `ValidationReport` + `ValidationResult` are canon core. Concrete validators are MazeWorld reference implementations. **File needs splitting.** |
| **src/generate/world_editor.py** | Straddles | `cross_validate()` and `gameplay_audit()` contain generic patterns (reference integrity, cycle detection) but operate on MazeWorld-specific data shapes. `editor_coherence_check()` is more generic. **File needs splitting.** |
| **src/generate/class_gen.py** | Reference implementation | MazeWorld player class generation |
| **src/generate/placement.py** | MazeWorld-specific | Tile-grid placement algorithms |
| **src/generate/summary_agent.py** | Straddles | Narrative generation patterns are generic; prompts are MazeWorld-specific |
| **src/generate/guide_builder.py** | MazeWorld-specific | PDF guide with MazeWorld formatting |
| **src/generate/generation_stats.py** | Canon core | Generic cost/call tracking |
| **src/generate/image_client.py** | Canon core (or canon-assets) | Orchestration layer for image generation |
| **src/generate/music_client.py** | MazeWorld-specific / canon-assets candidate | Lyria integration, but tightly coupled to MazeWorld prompt structure |
| **src/generate/sfx_client.py** | MazeWorld-specific / canon-assets candidate | ElevenLabs integration, same coupling |
| **src/generate/generators/llm_primitives.py** | Straddles | Low-level generation helpers; some generic, some MazeWorld-specific |
| **src/generate/generators/spell_pool_gen.py** | Reference implementation | Spell generation for MazeWorld |
| **src/prompts/base.py** | Canon core | `LLMRequest` + `PromptSet` ABC |
| **src/prompts/generator_prompts/*.py** | Reference implementation | MazeWorld-specific prompt content |
| **src/prompts/checker_prompts/** | Reference implementation | MazeWorld-specific |
| **src/prompts/validator_prompts/** | Reference implementation | MazeWorld-specific |
| **src/prompts/world_editor_prompts/** | Reference implementation | MazeWorld-specific |
| **src/models/world_bible.py** | Canon core, needs generalization | `EntityLore` and `WorldBible` patterns are generic; `RoomBible` has MazeWorld-specific fields; imports `config.DATA_DIR` directly |
| **src/models/story.py** | Canon core, needs generalization | `OverarchingStory` is a good generic pattern but fields (faction, escalation_arc, final_boss) are somewhat MazeWorld-flavored |
| **src/models/weapon.py** (skeleton functions) | Reference implementation | `roll_weapon_skeleton()` demonstrates the pattern |
| **src/models/spell.py** (skeleton functions) | Reference implementation | `roll_spell_skeleton()` demonstrates the pattern |
| **src/models/player.py** (skeleton + constants) | Reference implementation | Stat budget system |
| **src/models/monster.py** (scaling) | Reference implementation | Level scaling tables |
| **src/models/maze.py** | MazeWorld-specific | Dungeon maze generation |
| **src/utils/conversation_utils.py** | MazeWorld-specific | Runtime dialogue with MazeWorld NPC types |
| **config.py** | MazeWorld-specific | Monolithic config with game + generation settings intermixed |
| **src/registry.py** | MazeWorld-specific | Game data registry |
| **src/data/world_data.py** | Straddles | `ENVIRONMENT_TYPES` list is MazeWorld content but the pattern is generic |
| **All views/, controllers/, systems/** | MazeWorld-specific | Game runtime |

### Files That Need Splitting

1. **`pipeline_utils.py`** — `_retry_with_feedback()` is canon core; `_build_items_list()`, `_event_fallback()`, etc. are MazeWorld-specific
2. **`checker.py`** — `BaseChecker` + `CheckResult` are canon; 6 concrete checkers are reference implementations
3. **`validator.py`** — `BaseValidator` + `ValidationReport` are canon; 6 concrete validators are reference implementations
4. **`world_editor.py`** — Reference integrity patterns are generic; actual checks operate on MazeWorld entities
5. **`config.py`** — Generation config (LLM_BACKEND, IMAGE_BACKEND, etc.) needs separation from game config (SCREEN_WIDTH, MAP_COLORS, etc.)
6. **`world_bible.py`** — `EntityLore` and the bible pattern are generic; `RoomBible` fields and config imports are MazeWorld-specific

---

## Part 3: Shape of the Package

### 3.1 Proposed Module Layout

```
canon/
├── __init__.py              # Public API surface
├── bible/
│   ├── __init__.py
│   ├── models.py            # EntityLore, Bible (generic WorldBible), Section (generic RoomBible)
│   └── context.py           # Cumulative context builder
├── skeleton/
│   ├── __init__.py
│   └── core.py              # SkeletonField, SkeletonSpec, roll_skeleton() generic
├── pipeline/
│   ├── __init__.py
│   ├── runner.py            # Pipeline orchestrator (phase-based)
│   ├── retry.py             # _retry_with_feedback (generic)
│   └── stats.py             # GenerationStats
├── validation/
│   ├── __init__.py
│   ├── checker.py           # BaseChecker, CheckResult
│   ├── validator.py         # BaseValidator, ValidationResult, ValidationReport
│   └── coherence.py         # Generic coherence checks (ref integrity, cycle detection)
├── llm/
│   ├── __init__.py
│   ├── client.py            # generate(LLMRequest) -> str
│   ├── executor.py          # generate_batch() concurrent executor
│   ├── request.py           # LLMRequest dataclass
│   └── prompts.py           # PromptSet ABC
├── backends/
│   ├── __init__.py
│   ├── base.py              # LLMBackend, ImageBackend ABCs
│   ├── registry.py          # Pluggable backend registry
│   ├── anthropic.py         # Claude backend
│   ├── huggingface.py       # Local HF backend
│   ├── fal.py               # fal.ai image backend
│   └── diffusers.py         # Local diffusion backend
└── config.py                # Canon-only config (backend selection, concurrency, context limits)
```

### 3.2 Core Abstractions (Interface Sketches)

```python
# 1. Bible — the single source of truth
class Bible(BaseModel):
    """World bible: accumulated lore for the generated world."""
    story: StoryArc
    sections: dict[str, Section]    # Generalized from "rooms"
    entity_classes: list[EntityLore]

    def get_context(self, section_id: str) -> str: ...
    def get_cumulative_context(self, section_id: str) -> str: ...
    def add_entity(self, section_id: str, lore: EntityLore) -> None: ...
    def persist(self, path: str) -> None: ...
    @classmethod
    def load(cls, path: str) -> "Bible": ...

# 2. Skeleton — deterministic pre-roll spec
class SkeletonSpec:
    """Defines the mechanical properties to pre-roll for an entity type."""
    fields: dict[str, SkeletonField]  # field_name -> distribution/table

class SkeletonField:
    """A single deterministic field: weighted choices, range, or lookup."""
    choices: list[tuple[Any, float]] | None = None  # value, weight
    range: tuple[int, int] | None = None
    lookup: dict[str, Any] | None = None

def roll_skeleton(spec: SkeletonSpec, seed: int | None = None) -> dict: ...

# 3. Pipeline — phase-based generation orchestrator
class Phase(Protocol):
    name: str
    def run(self, ctx: PipelineContext) -> None: ...

class PipelineContext:
    bible: Bible
    config: CanonConfig
    llm: LLMClient
    stats: GenerationStats
    artifacts: dict[str, Any]  # phase outputs

def run_pipeline(phases: list[Phase], ctx: PipelineContext) -> Bible: ...

# 4. Checker/Validator pair
class BaseChecker(ABC):
    @abstractmethod
    def check(self, data: Any, context: dict | None = None) -> CheckResult: ...

class BaseValidator(ABC):
    @abstractmethod
    def validate(self, data: Any, context: dict | None = None) -> ValidationResult: ...

def retry_with_feedback(
    generate_fn: Callable,
    validate_fn: Callable,
    fallback: Any,
    max_retries: int = 3,
) -> Any: ...

# 5. LLMClient — backend-agnostic generation
class LLMClient:
    def generate(self, request: LLMRequest) -> str: ...
    def generate_batch(self, requests: list[LLMRequest], max_workers: int = 8) -> list[str | None]: ...

# 6. PromptSet — user-provided prompt templates
class PromptSet(ABC):
    """Abstract prompt factory. Users implement this for their domain."""
    @abstractmethod
    def entity_generation(self, entity_type: str, context: str, skeleton: dict) -> LLMRequest: ...
    @abstractmethod
    def story_generation(self, seed: str, structure: dict) -> LLMRequest: ...
    # ... minimal set; MazeWorld's 28-method PromptSet is the reference implementation
```

**Cradle-facing operations** (these would be methods on `Bible` or standalone functions):

```python
# Re-roll flavor text for a specific entity
def reroll_entity_flavor(
    bible: Bible, section_id: str, entity_id: str,
    llm: LLMClient, prompts: PromptSet, skeleton: dict | None = None,
) -> EntityLore: ...

# Regenerate an entire entity (new skeleton + new flavor)
def regenerate_entity(
    bible: Bible, section_id: str, entity_type: str,
    llm: LLMClient, prompts: PromptSet, spec: SkeletonSpec,
) -> EntityLore: ...

# Re-run validation on the current bible state
def validate_bible(
    bible: Bible, checkers: list[BaseChecker], validators: list[BaseValidator],
) -> ValidationReport: ...
```

### 3.3 Extension Points

1. **LLM backends** — already pluggable via `LLMBackend` ABC. Canon ships Anthropic + HuggingFace; users can add OpenAI, Ollama, etc.
2. **Image backends** — already pluggable via `ImageBackend` ABC.
3. **Validators/Checkers** — user registers domain-specific checkers for their entity types.
4. **PromptSet** — user implements their own prompt templates for their domain.
5. **Entity schemas** — user defines their own `SkeletonSpec` per entity type (weapons, spells, etc. are MazeWorld examples).
6. **Pipeline phases** — user composes their pipeline from built-in + custom `Phase` implementations.
7. **Coherence checks** — user registers domain-specific coherence rules for the world editor.

The code reveals one additional extension point worth formalizing: **context builders**. The `get_cumulative_context()` method's truncation strategy and format could be customizable.

### 3.4 What Canon Does NOT Ship With

**Recommendation: Asset generation (images, music, SFX) should be a separate optional package.**

Evidence:
- Music (`music_client.py`) and SFX (`sfx_client.py`) have **zero shared infrastructure** with the core pipeline beyond `GenerationStats` wiring
- Neither uses the `MusicBackend` ABC — it exists in `base.py` with a TODO and no implementations
- Image generation is better integrated (uses `ImageBackend` ABC, registry) but is still invoked only in Phase 7 as a separate step
- All three are async-only, adding heavy dependencies (`google-genai`, `elevenlabs`, `fal-client`)
- The `pyproject.toml` already separates them as optional `[api]` extras

Proposed packaging:
- `canon` — core library (bible, skeleton, pipeline, validation, LLM backends)
- `canon[images]` — image backends (fal.ai, local diffusers)
- `canon[audio]` — music + SFX generation (would need new ABCs to be useful)
- MazeWorld ships with its own prompt sets, entity schemas, and pipeline phase implementations

---

## Part 4: Cradle Integration Surface

### 4.1 Read Interface

**Current on-disk format:** JSON files in a well-defined directory structure:
```
data/
├── world_bible.json      # WorldBible (Pydantic model)
├── manifest.json         # Generation metadata + asset paths
├── story/story.json      # OverarchingStory
├── classes/classes.json   # Player classes
├── items/items.json       # All items
├── npcs/npcs.json         # All NPCs
├── monsters/monsters.json # All monsters
├── events/events.json     # All events
├── quests/quests.json     # All quests
├── narrative.json         # Synopsis, room intros
└── rooms/room_N/          # Per-room maze + entity placement
```

**The format is stable enough to read directly.** All models are Pydantic with `model_dump()`/`model_validate()`, so the schema is well-defined. However, cradle should **not** parse JSON manually — it should use canon's Python API:

```python
from canon import Bible

bible = Bible.load("data/world_bible.json")
for section_id, section in bible.sections.items():
    for npc in section.npcs:
        print(npc.name, npc.lore)
```

This protects cradle from schema changes and gives it access to helper methods like `get_cumulative_context()`.

### 4.2 Drive Interface

Cradle needs to invoke these operations:

```python
# 1. Re-roll a field (e.g., NPC name, item description)
new_lore = canon.reroll_entity_flavor(bible, "room_0", entity_id="1003", llm=client, prompts=my_prompts)

# 2. Regenerate an entity entirely
new_entity = canon.regenerate_entity(bible, "room_0", "weapon", llm=client, prompts=my_prompts, spec=weapon_spec)

# 3. Re-run validation
report = canon.validate_bible(bible, checkers=[ItemChecker(), NPCChecker()], validators=[...])

# 4. Generate a new entity of a given type and add it to a section
new_npc = canon.generate_entity(bible, "room_2", "npc", llm=client, prompts=my_prompts, skeleton=npc_skeleton)
bible.add_entity("room_2", new_npc)
bible.persist("data/world_bible.json")

# 5. Re-run a single pipeline phase
canon.run_phase(narrative_phase, ctx)
```

### 4.3 Packaging Recommendation

**Cradle (Tauri) calls canon via a thin CLI with JSON over stdout.**

Reasoning:
- Cradle is a Tauri app (Rust backend, JS frontend) — cannot import Python directly.
- CLI subprocess is simpler than HTTP: no daemon lifecycle, no port conflicts, no extra dependencies. Tauri's Rust backend has excellent subprocess support.
- Canon already serializes everything as JSON (Pydantic `model_dump()`), so the CLI output format is natural.
- No need for Flask/FastAPI/uvicorn dependencies.

Canon needs a `__main__.py` or CLI entry point (click/typer) wrapping the Python API:
- `canon bible load <path>` — dumps bible as JSON to stdout
- `canon bible validate <path>` — runs validation, returns report as JSON
- `canon reroll <path> --map room_0 --entity 1003` — re-rolls entity, writes updated bible
- `canon generate <phase> <path>` — runs a specific pipeline phase

Upgrade path: `canon serve` with WebSocket if real-time streaming (e.g., generation progress) is needed later.

---

## Part 5: Extraction Sequencing Proposal

### 5.1 Pre-Extraction Refactors Inside MazeWorld

**Must-do before extraction:**

1. **Split `config.py` into game config and generation config.** Currently one file with ~50 settings mixing screen dimensions, fog-of-war radii, LLM backends, and maze sizes. Canon needs its own config that doesn't import pygame constants. This is the single biggest blocker.

2. **Remove `config.*` imports from `world_bible.py`.** Lines 14 imports `DATA_DIR` and `STORY_CONTEXT_LIMIT`. The bible model should accept these as constructor params or method args, not import them from a MazeWorld config module.

3. **Split checker/validator files.** Extract `BaseChecker` + `CheckResult` and `BaseValidator` + `ValidationReport` + `ValidationResult` into base modules. Leave concrete implementations (ClassChecker, ItemChecker, etc.) as MazeWorld-specific.

4. **Split `pipeline_utils.py`.** Move `_retry_with_feedback()` to a canon-owned module. Leave `_build_items_list()`, `_event_fallback()`, etc. in MazeWorld.

5. **Formalize the skeleton pattern.** Currently each entity type's `roll_*_skeleton()` is in its model file with bespoke signatures. Introduce a common `SkeletonSpec` type so canon can provide a generic `roll_skeleton()`.

**Nice-to-have but not blocking:**

6. **Wire music/SFX through the `MusicBackend` ABC** that already exists in `base.py` but is unused (has a TODO comment). This would make asset generation pluggable.

7. **Remove the legacy `entity_index` from WorldBible.** It's not populated in production and adds confusion.

### 5.2 Extraction Phases

**Phase 1: Foundation layer** (lowest risk, no pipeline changes)
- Extract: `LLMRequest`, `LLMBackend`/`ImageBackend` ABCs, backend registry, `llm_client.py`, `llm_executor.py`, `generation_stats.py`
- These have zero MazeWorld-specific content
- MazeWorld switches to `from canon.llm import ...`
- Test: MazeWorld still generates worlds identically

**Phase 2: Validation framework**
- Extract: `BaseChecker`, `CheckResult`, `BaseValidator`, `ValidationResult`, `ValidationReport`, `_retry_with_feedback()`
- Leave concrete checkers/validators in MazeWorld
- MazeWorld imports base classes from canon, concrete implementations stay local

**Phase 3: Bible and skeleton**
- Extract: `EntityLore`, generic `Bible` (parameterized `Section` type), `StoryArc` base
- Extract: `SkeletonSpec`, `SkeletonField`, `roll_skeleton()`
- MazeWorld subclasses or configures these for its specific entity types
- **This is the highest-risk phase** — bible is imported everywhere

**Phase 4: Pipeline orchestrator**
- Extract: Phase-based pipeline runner, pipeline context
- MazeWorld defines its phases as `Phase` implementations that use canon's infrastructure
- MazeWorld's `generate_world()` becomes a composition of custom phases

**Phase 5: Backend implementations**
- Extract: Anthropic, HuggingFace, fal.ai, diffusers backends
- These are already behind ABCs, so extraction is mechanical

**Phase 6: PromptSet ABC and reference prompts**
- Extract: `PromptSet` ABC to canon
- Keep `ClaudePromptSet` and `LlamaPromptSet` in MazeWorld as reference implementations
- Document them as examples

### 5.3 The Cutover Moment

MazeWorld stops containing canon's code after **Phase 4** is complete. At that point:
- `src/generate/` is reduced to MazeWorld-specific phase implementations, concrete checkers/validators, prompts, and placement
- Everything in `canon/` is `pip install canon`
- MazeWorld's `pyproject.toml` adds `canon` as a dependency

Phase 5 and 6 are refinements that can happen after cutover.

### 5.4 Risks

1. **`config.py` is a gravity well.** Almost every generation file imports from it. The extraction plan depends on cleanly splitting generation config from game config. If this split is incomplete, canon will either carry MazeWorld config or break on import.

2. **`pipeline.py` is a 2,200-line monolith.** Every phase function is a private function in one file. Extracting the orchestrator means either keeping this file intact (and having MazeWorld own the orchestration) or breaking it into composable phases — which is a significant refactor.

3. **Pydantic model coupling.** `WorldBible` imports `OverarchingStory` from `src/models/story.py`, which has MazeWorld-specific fields (`faction`, `final_boss_name`, `escalation_arc`). Canon needs to decide: are these generic enough to keep, or should `StoryArc` be minimal with extension points? I'd keep `faction` and `escalation_arc` as they're broadly useful for narrative games.

4. **Test coverage gap.** The `pyproject.toml` coverage config (`[tool.coverage.run]`) explicitly **omits** `views`, `pipeline`, `music_client`, `sfx_client`, `image_client`, and all prompts. The pipeline itself is not covered by unit tests — only integration tests exist (`test_pipeline_integration.py`, `test_validation_pipeline.py`). This means refactors have limited safety nets.

5. **`conversation_utils.py` runtime coupling.** This file imports `llm_client.generate` and `get_prompt_set()` for live dialogue. After extraction, MazeWorld's runtime dialogue needs to import from canon's LLM module — or this file needs to accept the LLM client as a dependency rather than importing it directly.

6. **Hardcoded ID scheme.** The XYYY entity ID convention (1000-series NPCs, 2000-series items, etc.) is baked into pipeline orchestration code. Canon's pipeline would need to either adopt this convention or make ID allocation pluggable.

---

## Questions for the Repo Owner — Resolved

1. **Should canon's `Bible` be generic over section type?**
   **Decision:** Canon uses "Map" as the primary spatial unit (what MazeWorld calls "rooms"). Zones are sub-regions within maps (e.g., RPG Maker-style encounter zones). `RoomBible` becomes a `Map`-based type with game-specific fields remaining as optional/extensible properties.

2. **What is cradle's tech stack?**
   **Decision:** Tauri (Rust + JS frontend). Canon integration via thin CLI subprocess calls with JSON over stdout. No HTTP daemon. CLI wraps the same Python API. WebSocket upgrade path available later if real-time streaming is needed.

3. **Should the PromptSet ABC stay as large as it is (28 methods)?**
   **Decision:** Opinionated, consolidated to ~17 methods with sensible defaults. No subclassing required for basic usage. Key rollups: portrait methods collapse to one, weapons fold into items, story methods consolidate, batch becomes default. Canon ships defaults so a game dev can generate a world without writing a PromptSet subclass.

4. **Do you want canon to own the `OverarchingStory` model?**
   **Decision:** Yes. Canon ships an opinionated story model with faction, escalation arc, beats, climax entity. Keep it generic but not minimal — consistent with the opinionated library philosophy.

5. **What's the priority: pip-installable quickly or abstractions done right?**
   **Decision:** Full path — design abstractions properly first. Cradle benefits from stable APIs from day one. Sequencing: design core abstractions -> refactor MazeWorld to match -> extract mechanically -> MazeWorld becomes reference impl -> cradle builds against stable CLI.

### Additional Design Decision: Unified Character Model

**Decision:** NPCs, PCs, followers, merchants, and hostiles are unified as a single `Character` model with a `role` field. Class/archetype with stats/abilities/spells is optional — an NPC can be promoted to PC by assigning a class. MazeWorld's NPC subtypes (Static, Random, Merchant, Aggressive) become role values or behavioral tags. Canon generates Characters (identity + class + dialogue); MazeWorld wraps them with runtime state (health, inventory, position, combat record).
