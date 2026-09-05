# API Verification Against MazeWorld Source + Spike Proposal

**Date:** 2026-04-19
**Purpose:** Verify the v0.1 API sketch against actual MazeWorld code. Flag inaccuracies, propose fixes, and identify the best spike phase.

---

## 1. Field-by-Field Verification

### 1.1 `StoryArc` vs MazeWorld's `OverarchingStory`

**MazeWorld actual (`src/models/story.py:56-70`):**
```python
class OverarchingStory(BaseModel):
    seed: str = ""
    title: str = ""
    synopsis: str = ""
    faction: Optional[Faction] = None          # SINGULAR
    escalation_arc: list[str]
    climax: str = ""
    final_boss_name: str = ""                  # Explicit field
    final_boss_lore: str = ""                  # Explicit field
    key_npc_names: list[str]
    beats: list[RoomStoryBeat]
    story_npcs: list[StoryNPC]
    story_items: list[StoryItem]
    story_monsters: list[StoryMonster]
```

**API sketch `StoryArc`:**
```python
class StoryArc(BaseModel):
    title: str
    synopsis: str
    seed: str
    factions: list[Faction]                    # PLURAL
    primary_antagonist_faction_id: str | None
    escalation_arc: list[str]
    climax: str | None
    final_entity_id: str | None               # Generalized
    final_entity_lore: str | None             # Generalized
    beats: list[StoryBeat]
```

**Divergences:**

| Field | MazeWorld | API Sketch | Resolution |
|-------|-----------|------------|------------|
| `faction` | `Optional[Faction]` (singular) | `factions: list[Faction]` (plural) | **Keep plural** — justified in API doc. MazeWorld pays one-element-list tax. |
| `final_boss_name/lore` | Two explicit string fields | `final_entity_id` + `final_entity_lore` | **Keep sketch's generalized form.** Not every game has a "boss." `final_entity_id` is better but should also keep `final_entity_name: str | None` for display. The ID requires the entity to exist in the bible; name is self-contained. |
| `key_npc_names` | `list[str]` — flat name list | Not in sketch | **Add to StoryArc** as `key_character_names: list[str]`. Used in MazeWorld to avoid duplicate NPC naming. Cheap, useful. |
| `story_npcs/items/monsters` | Three typed lists (StoryNPC, StoryItem, StoryMonster) | Not in sketch | **Do not add.** These are pre-generation hints for the LLM, essentially story-seed entities. They map to Characters and EntityLore entries after generation. The bible's `characters` and map `entities` serve this purpose post-generation. If needed, store pre-generation hints in `StoryArc.extra`. |
| `primary_antagonist_faction_id` | Not in MazeWorld (single faction assumed) | In sketch | **Keep** — needed for multi-faction worlds. |

**Faction fields — MazeWorld actual (`story.py:8-13`):**
```python
class Faction(BaseModel):
    name: str
    description: str
    history: str = ""
    leader: str = ""
    threat_level: int = 1
```

**API sketch adds:** `faction_id: str`, `aesthetic: str | None`

**Resolution:** Keep both additions. `faction_id` is needed for cross-referencing. `aesthetic` is useful for portrait/prompt consistency.

---

### 1.2 `Map` vs MazeWorld's `RoomBible`

**MazeWorld actual (`src/models/world_bible.py:39-52`):**
```python
class RoomBible(BaseModel):
    environment: str
    environment_name: str = ""
    level: int = 1
    story_beat: str = ""
    boss_name: str = ""
    boss_lore: str = ""
    maze_ref: str = ""
    npcs: list[EntityLore]
    items: list[EntityLore]
    monsters: list[EntityLore]
    encounters: list[str]         # Just IDs
    quests: list[str]             # Just IDs
    gate_encounter_id: str = ""
```

**API sketch `Map`:**
```python
class Map(BaseModel):
    map_id: str
    name: str
    description: str
    environment: str
    level: int | None = None
    story_beat: str
    zones: list[Zone]
    entities: list[EntityLore]    # FLAT list
    connections: list[str]
    extra: dict
```

**Divergences:**

| Field | MazeWorld | API Sketch | Resolution |
|-------|-----------|------------|------------|
| Entity storage | Separate `npcs`, `items`, `monsters` lists | Single `entities` list | **Keep sketch's flat list.** Entity type is on `EntityLore.entity_type`. MazeWorld filters by type at read time. Simpler, more extensible. |
| `name` | `environment_name` | `name` | **Keep `name`** — cleaner. MazeWorld maps `environment_name` → `name`. |
| `description` | Not in RoomBible (story_beat serves this) | In sketch | **Keep** — `story_beat` maps to it. Rename `story_beat` → `description` would lose semantic clarity. **Keep both**: `description` for general prose, `story_beat` for narrative role. |
| `boss_name/boss_lore` | Explicit fields | Not in sketch | **Don't add.** Boss is a Character with `role="boss"`. Store `boss_character_id` in `extra` if needed. |
| `maze_ref` | Grid reference | Not in sketch | **Correctly omitted** — MazeWorld-specific. |
| `gate_encounter_id` | Boss gate encounter | Not in sketch | **Correctly omitted** — MazeWorld-specific. Goes in `extra`. |
| `encounters`, `quests` | ID lists | Not in sketch | **Correctly omitted** — these are entity types. Encounters and quests are EntityLore entries in the flat `entities` list. |
| `connections` | Not in MazeWorld | In sketch | **Keep** — MazeWorld's room progression is implicit (room_0 → room_1 → ...). Other games need explicit map connections. |
| `map_id` | Not on RoomBible (key of `rooms` dict) | Explicit field | **Keep** — self-contained is better than depending on dict key. |

---

### 1.3 `Character` vs MazeWorld's NPC/PlayerClass

**MazeWorld actual — NPC (`src/models/npc.py:10-47`):**
```python
class NPC(BaseModel):
    x: int; y: int                           # Position (game-specific)
    id: int
    profile_image: Optional[str] = None
    portrait_prompt: Optional[str] = None
    name: Optional[str] = None
    job: Optional[str] = None
    hobby: Optional[str] = None
    personality: Optional[str] = None
    description: Optional[str] = None
    backstory: Optional[str] = None
    environment: Optional[str] = None
    environment_name: Optional[str] = None
    identity: Optional[str] = None           # LLM system prompt
    opening_greeting: Optional[str] = None
    dialogue_tree: Optional[dict] = None
    dialogue_tree_incomplete: Optional[dict] = None
    dialogue_tree_complete: Optional[dict] = None
    dialogue_tree_failed: Optional[dict] = None
    quest_id: Optional[int] = None
    current_dc: int = 10
    zone: Optional[List[int]] = None
    selected: bool = True
    interaction_history: List[dict]
    has_met_player: bool = False
    exhausted_dialogue: str
    finished_dialogue: str
    dialogue_exhausted: bool = False
    personality_notes: List[str]
    max_dialogue_turns: int = 10
    availability: Optional[str] = None       # "day" | "night" | "always"
    color: Tuple[int, int, int]              # RGB (game-specific)
    move_interval: int = 5000                # Milliseconds (game-specific)
    last_move_time: int = 0                  # Game-specific
```

**API sketch `Character`:**
```python
class Character(BaseModel):
    character_id: str
    name: str
    role: CharacterRole
    primary_map_id: str | None = None
    lore: str
    personality: str | None = None
    appearance: str | None = None
    faction_id: str | None = None
    class_data: CharacterClass | None = None
    dialogue_tree_id: str | None = None
    portrait_path: str | None = None
    generation_trail: GenerationTrail | None = None
    extra: dict
```

**Divergences:**

| Field | MazeWorld | API Sketch | Resolution |
|-------|-----------|------------|------------|
| ID type | `id: int` | `character_id: str` | **Keep str** — more flexible. MazeWorld converts at boundary. |
| `backstory` | Separate from `description` | `lore` (single field) | **Keep `lore`** — combine MazeWorld's `backstory` + `description`. |
| `job`, `hobby` | Separate fields | Not in sketch | **Add to `extra`** or add optional fields. These are NPC personality traits used in prompt building. Could add `traits: dict[str, str] = {}` as a generic bag. |
| `identity` | LLM system prompt for live dialogue | Not in sketch | **Correctly omitted** — runtime concern, not canon's job. |
| `opening_greeting` | Pre-generated first line | Not in sketch | **Correctly omitted** — this is part of the dialogue tree (entry node prompt). |
| `availability` | "day"/"night"/"always" | Not in sketch | **Add to `extra`** — game-specific scheduling. |
| `dialogue_tree_id` | Not in MazeWorld (tree is inline on NPC) | In sketch | **Keep** — better design. Dialogue trees in Bible.dialogues, referenced by ID. |
| Position fields (`x`, `y`, `color`, etc.) | On NPC | Not in sketch | **Correctly omitted** — game runtime state. |
| `appearance` | Not on NPC (portrait_prompt serves this) | In sketch | **Keep** — useful for non-visual games (text descriptions). |
| `faction_id` | Not on NPC | In sketch | **Keep** — needed for multi-faction worlds. |
| `class_data` | Not on NPC (only on PlayerCharacter) | On Character | **Keep** — this is the unified model's core value. |

**MazeWorld's `PlayerClass` vs sketch's `CharacterClass`:**

MazeWorld actual:
```python
class PlayerClass(BaseModel):
    name: str
    archetype: str
    flavor_text: str = ""
    environment: str = ""
    stats: Stats                              # 7 named int fields (STR-LUCK)
    starting_weapon: str = ""
    abilities: list[Ability]
    spells: list[Spell]
    portrait_path: Optional[str] = None
    portrait_prompt: Optional[str] = None
    ability_pool: list[Ability]               # Level-up pool
    spell_pool: list[Spell]                   # Level-up pool
```

API sketch:
```python
class CharacterClass(BaseModel):
    archetype_id: str
    stats: dict[str, int]                     # Generic stat dict
    abilities: list[str]                      # Ability IDs
    spells: list[str]                         # Spell IDs
    equipment: list[str]
    skeleton: dict
```

**Divergences:**

| Field | MazeWorld | API Sketch | Resolution |
|-------|-----------|------------|------------|
| `stats` | `Stats` model with named fields (STR, DEX, etc.) | `dict[str, int]` | **Keep dict** — generic. MazeWorld's stat names are domain-specific. |
| `abilities/spells` | Inline `list[Ability]`/`list[Spell]` objects | `list[str]` IDs | **Keep IDs** — abilities and spells are EntityLore entries. Reference by ID. But note: MazeWorld currently embeds full objects, not IDs. This is a meaningful refactor for MazeWorld. |
| `ability_pool/spell_pool` | Level-up progression pools | Not in sketch | **Add** `ability_pool: list[str]` and `spell_pool: list[str]` (as IDs). Or generalize to `progression_pools: dict[str, list[str]]`. Level-up pools are common across RPGs. |
| `flavor_text` | On PlayerClass | Not in sketch | **Omit** — this is `lore` on the Character, not on the class data. |
| `starting_weapon` | String name | `equipment: list[str]` | **Keep `equipment`** — more general. |

---

### 1.4 `EntityLore`

**MazeWorld actual (`world_bible.py:28-36`):**
```python
class EntityLore(BaseModel):
    entity_type: str
    entity_id: str = ""
    name: str = ""
    room_id: str = ""
    lore: str = ""
    tags: list[str]
```

**API sketch:**
```python
class EntityLore(BaseModel):
    entity_id: str
    entity_type: str
    name: str
    map_id: str | None
    lore: str
    skeleton: dict                            # Pre-rolled values
    tags: list[str]
    generation_trail: GenerationTrail | None
    extra: dict
```

**Divergences:**

| Field | MazeWorld | API Sketch | Resolution |
|-------|-----------|------------|------------|
| `room_id` | String | `map_id: str | None` | **Keep `map_id`** — rename. |
| `skeleton` | Not stored on EntityLore | In sketch | **Keep** — this is a design improvement. MazeWorld discards the skeleton after merge. Storing it enables cradle's re-roll feature. |
| `generation_trail` | Not in MazeWorld | In sketch | **Keep** — new capability for cradle. |
| `extra` | Not in MazeWorld | In sketch | **Keep** — escape hatch. |
| Default values | Most fields default to `""` | Required fields | **Make `name` required, keep `lore` defaulting to `""`**. An entity without a name is invalid; an entity without lore is valid (some items are mechanical-only). |

---

### 1.5 Dialogue Structure

**MazeWorld actual structure (from generated JSON):**
```json
{
  "nodes": {
    "start": {
      "prompt": "NPC speaks...",
      "choices": [
        {"text": "Player option", "next_node_id": "explain"},
        {"text": "Another option", "next_node_id": "end"}
      ]
    },
    "explain": { "prompt": "...", "choices": [...] },
    "end": { "prompt": "...", "choices": [] }
  }
}
```

**Key facts:**
- Entry node is **always `"start"`** — hardcoded, no `entry_node_id` field
- No `is_entry` or `is_terminal` flags — determined by position and empty `choices`
- No `tags` on nodes
- No `speaker` field on nodes — implied by context (the NPC who owns the tree)
- No `conditions` or `effects` on choices — these are baked into quest state elsewhere
- Quest NPCs get **three separate trees**: `dialogue_tree` (incomplete), `dialogue_tree_complete` (success), `dialogue_tree_failed` (failure)
- Node field is `prompt`, not `text`

**API sketch:**
```python
class DialogueNode(BaseModel):
    node_id: str
    speaker: str                              # character_id
    text: str                                 # API uses "text"
    choices: list[DialogueChoice]
    is_entry: bool = False
    is_terminal: bool = False
    tags: list[str]

class DialogueChoice(BaseModel):
    text: str
    next_node_id: str | None
    conditions: list[str]                     # e.g. "has_item:key_1"
    effects: list[str]                        # e.g. "gives_quest:q_5"

class DialogueTree(BaseModel):
    tree_id: str
    character_id: str
    entry_node_id: str
    nodes: dict[str, DialogueNode]
    generation_trail: GenerationTrail | None
```

**Divergences and resolutions:**

| Field | MazeWorld | API Sketch | Resolution |
|-------|-----------|------------|------------|
| Node text field | `prompt` | `text` | **Use `text`** — more generic. MazeWorld's "prompt" is NPC-centric naming. Document in migration guide. |
| `speaker` | Not on nodes (implicit) | On each node | **Keep** — enables multi-speaker trees (cutscenes, group dialogues). Default to the tree's `character_id` if omitted. Make optional: `speaker: str | None = None`. |
| `entry_node_id` | Always `"start"` (hardcoded) | Explicit field | **Keep explicit field** — more flexible. Default to `"start"` for compatibility. |
| `is_entry/is_terminal` | Not in MazeWorld | In sketch | **Remove.** Derivable: `is_entry` = matches `entry_node_id`; `is_terminal` = empty choices. Adding flags creates sync risk. |
| `tags` on nodes | Not in MazeWorld | In sketch | **Keep but default to `[]`** — cheap, useful for cradle filtering. |
| `conditions/effects` | Not in MazeWorld | On DialogueChoice | **Keep but default to `[]`** — MazeWorld manages quest gating externally, but other games need it on the choice. |
| Quest variants | Three separate trees: incomplete/complete/failed | Not addressed | **Add to DialogueTree:** `variants: dict[str, dict[str, DialogueNode]] = {}`. Keys like `"quest_complete"`, `"quest_failed"`. The base `nodes` is the default/incomplete tree. Or: store as separate DialogueTree entries with a `variant` field. **Recommend separate entries** with `variant: str | None = None` on DialogueTree — simpler, each is independently editable in cradle. |

---

### 1.6 `ClassArchetype`

**MazeWorld actual:** There is **no ClassArchetype model**. The closest equivalent is `ARCHETYPE_STAT_ROLES` — a plain dict:

```python
ARCHETYPE_STAT_ROLES = {
    "warrior": {"primary": ["STR", "CON"], "secondary": ["DEX", "CHA"], "dump": ["INT", "WIS"]},
    "mage": {"primary": ["INT"], "secondary": ["WIS", "DEX"], "dump": ["STR", "CON", "CHA"]},
    ...
}
```

Plus `ABILITY_DISTRIBUTIONS`, `SPELL_DISTRIBUTIONS`, `ARCHETYPE_WEAPON_CATEGORIES` — all separate dicts.

**API sketch `ClassArchetype`:**
```python
class ClassArchetype(BaseModel):
    archetype_id: str
    name: str
    description: str
    category: str | None
    stat_template: dict[str, int]
    stat_budget: int | None
    role_tags: list[str]
    ability_pool: list[str]
    spell_pool: list[str]
    starting_equipment: list[str]
    lore: str | None
    extra: dict
```

**Resolution:** The sketch's `ClassArchetype` is a **design improvement**, not an extraction. MazeWorld doesn't have this as a model — it's scattered across dicts and constants. This is fine — canon introduces the abstraction; MazeWorld populates it from its existing data during cutover. No inaccuracy to fix, just a gap to note.

**Concern:** `stat_template: dict[str, int]` doesn't capture MazeWorld's primary/secondary/dump role system. It's a base-value template, not a role-range template. Options:
1. Keep `stat_template` as base values; store role ranges in `extra`
2. Add `stat_roles: dict[str, list[str]]` (maps "primary"/"secondary"/"dump" to stat names)

**Recommend option 2** — `stat_roles` is broadly useful (any game with stat emphasis systems) and directly maps what MazeWorld already has.

---

### 1.7 Context Builder

**MazeWorld actual (`world_bible.py:76-142`):**

`get_story_context(room_id: str) -> str`:
- Input: `room_id` string
- Output: newline-joined text with title, synopsis, faction name + description + history, room beat, boss name/lore, story NPCs/items/monsters for this room
- No truncation

`get_cumulative_context(room_id: str) -> str`:
- Input: `room_id` string
- Output: story context + all previous rooms' NPCs/items/monsters (name + truncated lore snippets)
- Truncation: `STORY_CONTEXT_LIMIT * 2` characters, hard cut with `"[...earlier rooms truncated]"`
- Lore snippets: NPC lore truncated to 120 chars, item/monster lore to 80 chars

**API sketch:**
```python
def get_context(self, map_id: str) -> str: ...
def get_cumulative_context(self, map_id: str, max_chars: int | None = None) -> str: ...
```

**Resolution:** Sketch matches well. The `max_chars` parameter generalizes MazeWorld's hardcoded `STORY_CONTEXT_LIMIT * 2`. Keep the sketch; implement with MazeWorld's truncation behavior as default.

---

### 1.8 `SkeletonSpec`

**MazeWorld actual:** Skeletons are **imperative per-entity functions**, not declarative specs.

`roll_weapon_skeleton()` uses:
- `random.choices()` with weights (weapon_type, weapon_category)
- `random.choice()` from flat list (damage_type)
- `random.random() < 0.10` threshold (magic_element — conditional)
- Deterministic derivation: `stat_modifier` ← `weapon_type`, `num_dice/die_sides` ← `weapon_type + room_level`

`roll_spell_skeleton()` and `roll_ability_skeleton()` are **purely deterministic** — they just look up values from pre-defined distribution tables.

`instantiate_monster()` uses `random.randint()` for ranges and `random.choice()` from lists.

**Can these be expressed as `SkeletonSpec`?** Yes, with two additions the sketch needs:

1. **Conditional fields** — weapon's `magic_element` has a 10% chance of existing. The current `SkeletonField` has `choices` and `range` but no probability gate. Add: `probability: float | None = None` — if set, the field only populates with this probability, else None.

2. **Context-dependent specs** — Monster scaling depends on `room_level`, which isn't part of the skeleton itself. The sketch's `SkeletonSpec.post` callback handles this, but the primary fields need to be parameterized. **Recommend:** `roll_skeleton(spec, rng, context: dict | None = None)` where context carries `room_level` etc. `SkeletonField.lookup` can key off context values.

**`SkeletonField.depends_on`** in the sketch is correct — weapon's `stat_modifier` depends on `weapon_type`. But the current `lookup: dict[str, Any]` shape won't handle `compute_weapon_dice(room_level, weapon_type)` which derives from both a context value AND another field. **Recommend:** Keep `post` as the escape hatch for multi-input derivations. Don't over-complicate `SkeletonField`.

---

## 2. Summary of Required Sketch Revisions

### Add to `StoryArc`:
- `key_character_names: list[str] = []` — used to prevent duplicate naming

### Add to `Character`:
- `traits: dict[str, str] = {}` — generic bag for job, hobby, personality notes

### Add to `CharacterClass`:
- `progression_pools: dict[str, list[str]] = {}` — level-up ability/spell pools

### Add to `ClassArchetype`:
- `stat_roles: dict[str, list[str]] = {}` — primary/secondary/dump stat assignments

### Modify `DialogueNode`:
- Remove `is_entry` and `is_terminal` (derivable)
- Make `speaker` optional: `speaker: str | None = None`
- Rename internal field name note: MazeWorld uses `prompt`, canon uses `text`

### Add to `DialogueTree`:
- `variant: str | None = None` — e.g. "quest_complete", "quest_failed". Quest NPCs get multiple trees.

### Add to `SkeletonField`:
- `probability: float | None = None` — field populates only with this probability

### Add to `roll_skeleton`:
- `context: dict | None = None` parameter — carries room_level, environment, etc.

### No changes needed:
- `Map` — sketch is a clean generalization of RoomBible
- `EntityLore` — sketch is correct, adds skeleton + trail storage
- `Faction` — sketch adds useful fields (id, aesthetic)
- `LLMRequest`, `LLMClient`, `LLMBackend` — match cleanly
- `PipelineContext`, `Phase`, `run_pipeline` — correct shape
- `BaseChecker`, `BaseValidator`, `ValidationReport` — match closely
- `retry_with_feedback` — matches exactly
- `CanonConfig`, `GenerationStats` — appropriate

---

## 3. Spike Proposal: Extract `ValidationPhase`

### Why Validation?

1. **Lowest coupling.** Phase 5 (`_phase5_validate`) reads from bible + room_results and produces a `ValidationReport`. It writes nothing to the bible, mutates no state, and has no offset counters.

2. **Already matches Phase protocol.** The function signature is:
   ```python
   def _phase5_validate(bible: WorldBible, room_results: list[dict], story: OverarchingStory) -> ValidationReport
   ```
   This maps cleanly to:
   ```python
   class ValidationPhase:
       def run(self, ctx: PipelineContext) -> None:
           report = validate_bible(ctx.bible, ctx.checkers, ctx.validators)
           ctx.artifacts["validation_report"] = report
   ```

3. **Validates multiple canon core abstractions simultaneously:**
   - `BaseChecker` / `CheckResult` (already ABCs)
   - `BaseValidator` / `ValidationResult` / `ValidationReport` (already ABCs)
   - `Phase` protocol (does `run(ctx)` work?)
   - `PipelineContext` shape (does it carry what phases need?)
   - `retry_with_feedback` (used by validation callers)
   - Bible read interface (`bible.rooms`, `bible.story`)

4. **Concrete checkers/validators stay in MazeWorld.** The spike extracts the framework; MazeWorld keeps `ClassChecker`, `ItemChecker`, `NPCChecker`, etc. as reference implementations. Clean separation.

5. **Test coverage exists.** `test_validation_pipeline.py` and `test_bible_final.py` exercise the validation path. The spike can verify against existing tests.

6. **Surfaces real friction early.** Key questions the spike answers:
   - Can `room_results` (MazeWorld's dict-of-lists) be mapped to `PipelineContext.artifacts`?
   - Does the Bible's read API (`get_room`, walk `rooms`) work from canon's side?
   - Can MazeWorld's concrete checkers import from canon's base classes without circular deps?

### Why NOT other phases?

- **StoryPhase** (Phase 1): Creates the Bible from scratch. Requires the LLM client, prompt set, and JSON parsing — too many moving parts for a spike.
- **EntityPhase** (Phases 3B-3D): Tightly coupled via ID offsets, bible mutations, room loops, and the enrichment gate. Highest-risk refactor.
- **CharacterPhase** (Phase 3C): Depends on bible cumulative context, NPC name deduplication, and ID offsets.
- **DialoguePhase** (Phase 4B): Depends on quest context and NPC pool — moderate coupling.
- **NarrativePhase** (Phase 6): Simple but exercises fewer abstractions than validation.

### Spike Plan

**Step 1: Extract validation framework to canon**
```
canon/validation/
├── __init__.py
├── checker.py      # BaseChecker, CheckResult
├── validator.py    # BaseValidator, ValidationResult, ValidationReport
└── coherence.py    # Generic ref integrity + cycle detection utilities
```

**Step 2: Implement `ValidationPhase`**
```
canon/pipeline/
├── runner.py       # Phase protocol, PipelineContext, run_pipeline
└── phases.py       # ValidationPhase (first built-in phase)
```

**Step 3: Wire MazeWorld**
- MazeWorld's concrete checkers (`ClassChecker`, `ItemChecker`, etc.) import `BaseChecker` from canon
- MazeWorld's `_phase5_validate` calls canon's `ValidationPhase` via `run_pipeline`
- Existing tests still pass

**Step 4: Validate the Phase protocol shape**
- Does `PipelineContext` carry everything validation needs?
- Does the `artifacts` dict work for `room_results` (MazeWorld's primary inter-phase data)?
- Can we run a single phase independently (`run_phase(validation_phase, ctx)`)?

### What the Spike Produces

1. Working `canon.validation` module with `BaseChecker`, `BaseValidator`, `ValidationReport`
2. Working `canon.pipeline.runner` with `Phase`, `PipelineContext`, `run_pipeline`
3. Working `ValidationPhase` that MazeWorld can use in place of `_phase5_validate`
4. Proof that the Phase protocol works — or documented friction requiring design changes
5. Confidence to commit the pattern across all phases

---

## 4. Revised Sequencing (Post-Spike)

1. **Spike: ValidationPhase** — validates Phase protocol, extracts validation framework
2. **Bible models** — Bible, Map, Character, EntityLore, StoryArc, Faction (data layer)
3. **Skeleton system** — SkeletonSpec, SkeletonField, roll_skeleton
4. **LLM layer** — LLMRequest, LLMClient, LLMBackend, BackendRegistry, Anthropic backend
5. **PromptSet** — ABC + DefaultPromptSet
6. **StoryPhase** — first content-generating phase
7. **EntityPhase** — generic entity generation (replaces 3B/3C/3D)
8. **CharacterPhase + ClassPhase** — character and class generation
9. **DialoguePhase** — dialogue tree generation
10. **NarrativePhase** — synopsis, map intros, wrap-up text
11. **CLI** — typer entry point wrapping all operations
12. **MazeWorld cutover** — replace `src/generate/` imports with canon

At every step, MazeWorld must still generate worlds and pass existing tests.
