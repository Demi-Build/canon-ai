# Wave 5.5 — Field Coverage Matrix

Generated: 2026-05-06  
Canon version: 0.2.0  
Canon run: `--backend fake --assets fake --num-maps 3 --output-dir /tmp/canon_5_5`  
Reference: `tests/reference/fixtures/cradle_mazeworld_scifi/` (5-room mazeworld_scifi run)

## Classification key

| Code | Meaning |
|------|---------|
| `canon-model` | Emitted from a canon Pydantic model field; present in output |
| `pack-synth` | Synthesized by `examples/mazeworld_pack/parsers.py` from skeleton/layout data |
| `pack-stub` | Written as a constant stub value by the parser |
| `runtime-fill` | Left null/empty; mazeworld fills at runtime (acceptable) |
| `unmodeled` | Must be addressed before Wave 6 — canon/pack produces no value for it |
| `extra-canon` | Canon emits this field; reference does not have it (informational only) |

---

## world_bible.json

| Field path | Status | Notes |
|-----------|--------|-------|
| `$.story.seed` | `canon-model` | Emitted |
| `$.story.title` | `canon-model` | Emitted |
| `$.story.synopsis` | `canon-model` | Emitted |
| `$.story.faction` | `unmodeled` | Reference has `faction` (single object with name/description/history/goals). Canon emits `factions` (array). Naming mismatch — cradle/mazeworld reads `story.faction` not `story.factions`. |
| `$.story.escalation_arc` | `canon-model` | Emitted |
| `$.story.climax` | `canon-model` | Emitted |
| `$.story.final_boss_name` | `unmodeled` | Reference has scalar `final_boss_name`. Canon emits `final_entity_id` (string ref). Naming mismatch. |
| `$.story.final_boss_lore` | `unmodeled` | Reference has scalar `final_boss_lore`. Canon emits `final_entity_lore`. Naming mismatch. |
| `$.story.key_npc_names` | `unmodeled` | Reference has `key_npc_names` (list of strings). Canon emits `key_character_names`. Naming mismatch. |
| `$.story.beats[0].room_id` | `unmodeled` | Reference uses `room_id` as per-beat room key. Canon uses `map_id`. Naming mismatch. |
| `$.story.beats[0].summary` | `unmodeled` | Reference has `summary` (long narrative). Canon uses `beat` (short stub). Field rename + missing rich content. |
| `$.story.beats[0].faction_presence` | `unmodeled` | Reference has this field describing faction activity per room. Not in canon output. |
| `$.story.beats[0].escalation` | `unmodeled` | Reference has `escalation` (int 1-5). Not in canon output. |
| `$.story.story_npcs` | `unmodeled` | Reference has `story_npcs` list (NPC IDs that are story-relevant). Not emitted. |
| `$.story.story_items` | `unmodeled` | Reference has `story_items`. Not emitted. |
| `$.story.story_monsters` | `unmodeled` | Reference has `story_monsters`. Not emitted. |
| `$.player_classes` | `unmodeled` | Top-level `player_classes` list (mirrors classes.json but in world_bible). Not emitted. |
| `$.entity_index` | `unmodeled` | Top-level `entity_index` dict (maps entity ID → minimal stub). Not emitted. |
| `$.rooms.room_N.encounters` | `unmodeled` | Each room in world_bible should have `encounters` dict (event lookup). Canon emits `events` instead. Naming mismatch. |
| `$.rooms.room_N.gate_encounter_id` | `unmodeled` | Per-room gate encounter ID in world_bible. Not in canon. |
| `$.story.factions` | `extra-canon` | Canon's array form — not in reference. Needs to be renamed/restructured to `faction` (singular). |
| `$.story.final_entity_id` | `extra-canon` | Canon's name for `final_boss_name` concept. Needs rename. |
| `$.story.final_entity_lore` | `extra-canon` | Canon's name for `final_boss_lore` concept. Needs rename. |
| `$.story.key_character_names` | `extra-canon` | Canon's name for `key_npc_names`. Needs rename. |
| `$.story.beats[0].map_id` | `extra-canon` | Canon's name for `room_id` in beats. Needs rename. |
| `$.story.beats[0].beat` | `extra-canon` | Canon stub. Needs to be filled as `summary`. |
| `$.rooms.room_N.events` | `extra-canon` | Canon's name for `encounters`. Needs rename. |
| `$.rooms.room_N.player_classes` | `extra-canon` | Canon adds this per-room. Not in reference. Remove or ignore. |

---

## manifest.json

| Field path | Status | Notes |
|-----------|--------|-------|
| `$.seed` | `canon-model` | Emitted but TYPE mismatch: canon=string, reference=int. When seed is a string like "shadowspire_001" vs reference int `1234`. mazeworld uses `manifest.seed` to seed RNG — must be int. |
| `$.num_rooms` | `canon-model` | OK |
| `$.environments` | `canon-model` | OK |
| `$.environment_names` | `canon-model` | OK (room names from LLM should fill these) |
| `$.maze_width` | `canon-model` | OK |
| `$.maze_height` | `canon-model` | OK |
| `$.generated_at` | `canon-model` | OK |
| `$.npc_count` | `canon-model` | OK (populated: 6) |
| `$.quest_count` | `canon-model` | OK (0 in fake run) |
| `$.event_count` | `canon-model` | OK (0 in fake run — actually 12 in events.json, manifest count bug) |
| `$.class_count` | `canon-model` | OK |
| `$.portraits_generated` | `canon-model` | OK |
| `$.player_portrait` | `pack-stub` | Emitted as empty string; reference has absolute path. Acceptable for fake run. |
| `$.gameover_portrait` | `pack-stub` | Same — empty string. |
| `$.victory_portrait` | `pack-stub` | Same — empty string. |
| `$.start_portrait` | `pack-stub` | Same — empty string. |
| `$.game_mode` | `pack-stub` | "offline_static" — OK |
| `$.story_title` | `canon-model` | OK |
| `$.faction_name` | `canon-model` | OK |
| `$.story_seed` | `canon-model` | OK |
| `$.rooms[N].room_id` | `canon-model` | OK |
| `$.rooms[N].environment` | `canon-model` | OK |
| `$.rooms[N].environment_name` | `canon-model` | OK |
| `$.rooms[N].npc_count` | `canon-model` | OK |
| `$.rooms[N].event_count` | `canon-model` | Emitted as 0 even when events exist (count bug) |
| `$.rooms[N].quest_count` | `canon-model` | OK |
| `$.rooms[N].environment_portrait` | `runtime-fill` | TYPE mismatch: canon=null, reference=string. Null is acceptable before AssetPhase stamps path. |
| `$.validation_report.status` | `canon-model` | OK |
| `$.validation_report.critical_failures` | `canon-model` | OK |
| `$.validation_report.minor_warnings` | `canon-model` | OK |
| `$.validation_report.details` | `canon-model` | OK |
| `$.validation_report.rooms_validated` | `unmodeled` | Reference has this field; canon does not emit it. |
| `$.validation_report.major_retries` | `unmodeled` | Reference has this field; canon does not emit it. |
| `$.generation_stats.*` | `canon-model` | All 20 reference keys present (canon has 6 extra fields which are fine) |
| `$.music` | `unmodeled` | Canon emits `music: {}` (empty dict). Reference has `music` with per-track paths. ManifestPhase doesn't populate the music/sfx path maps. |
| `$.sfx` | `unmodeled` | Canon emits `sfx: {}` (empty dict). Reference has full sfx path map with 28 keys. ManifestPhase doesn't populate. |

---

## narrative.json

| Field path | Status | Notes |
|-----------|--------|-------|
| `$.synopsis` | `canon-model` | OK |
| `$.game_over` | `canon-model` | OK |
| `$.victory` | `canon-model` | OK |
| `$.room_intro_room_N` | `canon-model` | OK for N = 0,1,2 (3-map run). Reference has 5 rooms; canon has 3. Count-correct. |
| `$.room_intro_room_3` | `N/A` | Reference has 5 rooms; 3-room run correctly omits rooms 3 and 4. Not a gap. |

---

## generation_stats.json

| Field path | Status | Notes |
|-----------|--------|-------|
| `$.llm_backend` | `canon-model` | OK |
| `$.image_backend` | `canon-model` | OK |
| `$.music_backend` | `canon-model` | OK |
| `$.sfx_backend` | `canon-model` | OK |
| `$.llm_calls` | `canon-model` | OK |
| `$.input_tokens` | `canon-model` | OK |
| `$.output_tokens` | `canon-model` | OK |
| `$.total_tokens` | `canon-model` | OK |
| `$.images_attempted` | `canon-model` | OK |
| `$.images_succeeded` | `canon-model` | OK |
| `$.music_attempted` | `canon-model` | OK |
| `$.music_succeeded` | `canon-model` | OK |
| `$.sfx_attempted` | `canon-model` | OK |
| `$.sfx_succeeded` | `canon-model` | OK |
| `$.llm_cost_usd` | `canon-model` | OK |
| `$.image_cost_usd` | `canon-model` | OK |
| `$.audio_cost_usd` | `canon-model` | OK |
| `$.total_cost_usd` | `canon-model` | OK |
| `$.generation_time_seconds` | `canon-model` | OK |
| `$.generation_time_human` | `canon-model` | OK |
| `$.by_phase` | `extra-canon` | Canon adds per-phase breakdown — informational, reference doesn't have it |
| `$.image_attempts` | `extra-canon` | Duplicate of `images_attempted` — minor redundancy |
| `$.image_successes` | `extra-canon` | Duplicate of `images_succeeded` — minor redundancy |
| `$.total_cost` | `extra-canon` | Duplicate of `total_cost_usd` |
| `$.total_input_tokens` | `extra-canon` | Duplicate of `input_tokens` |
| `$.total_output_tokens` | `extra-canon` | Duplicate of `output_tokens` |

---

## npcs/npcs.json

| Field path | Status | Notes |
|-----------|--------|-------|
| `$[0].id` | `canon-model` | OK (int, e.g. 1000) |
| `$[0].type` | `pack-synth` | Synthesized from skeleton `behavior_type` — OK |
| `$[0].name` | `canon-model` | OK |
| `$[0].job` | `canon-model` | OK |
| `$[0].personality` | `canon-model` | OK |
| `$[0].hobby` | `canon-model` | OK |
| `$[0].backstory` | `canon-model` | OK |
| `$[0].environment` | `pack-synth` | From map_obj — OK |
| `$[0].environment_name` | `pack-synth` | From map_obj — currently "Room 0" (fake names); OK for fake run |
| `$[0].opening_greeting` | `canon-model` | OK |
| `$[0].portrait_prompt` | `canon-model` | OK |
| `$[0].profile_image` | `runtime-fill` | null before AssetPhase — TYPE mismatch (null vs string) is expected before asset phase runs. Cradle handles null (shows placeholder). |
| `$[0].dialogue_tree` | `runtime-fill` | null because FakeDialogue produces empty `nodes`. DialoguePhase falls back to null. Cradle handles null gracefully. Reference has full tree — gap is FakeLLMBackend's dialogue canned response, not missing field. |
| `$[0].quest_id` | `pack-stub` | null — OK |
| `$[0].quest_type` | `pack-stub` | null — OK |
| `$[0].quest_target_tile` | `pack-stub` | null — OK |
| `$[0].max_exchanges` | `pack-stub` | 5 — OK |
| `$[0].is_story_npc` | `pack-stub` | false — OK |
| `$[0].x` | `pack-synth` | From MazeLayout position — currently (1,1) because npc_positions not back-populated in maze.json. Values present in NPC record. |
| `$[0].y` | `pack-synth` | Same as x — OK in NPC record |
| `$[0].selected` | `pack-stub` | true — OK |
| `$[0].exhausted_dialogue` | `canon-model` | OK |
| `$[0].personality_notes` | `canon-model` | OK |
| `$[0].color` | `extra-canon` | RGB color tuple from ENV_TO_COLOR — not in reference but harmless |
| `$[0].dialogue_tree_incomplete` | `extra-canon` | Canon adds — not in reference |
| `$[0].dialogue_tree_complete` | `extra-canon` | Canon adds — not in reference |
| `$[0].dialogue_tree_failed` | `extra-canon` | Canon adds — not in reference |

---

## items/items.json

| Field path | Status | Notes |
|-----------|--------|-------|
| `$[key].category` | `pack-synth` | From skeleton `item_kind` — OK |
| `$[key].name` | `canon-model` | OK |
| `$[key].desc` | `canon-model` | OK |
| `$[key].room_level` | `unmodeled` | Reference has `room_level` (int 1-5) on each item. Not emitted by parser. mazeworld uses this for loot table filtering. |
| `$[key].item_stats` | `pack-synth` | Present — OK (stamina_value, health_value, uses, price) |
| `$[key].item_stats.attribute` | `extra-canon` | Canon adds `attribute: null` — not in reference consumable shape. Minor extra field. |
| `$[key].profile_image` | `runtime-fill` | null — TYPE mismatch expected before AssetPhase. |
| `$[key].id` | `extra-canon` | Canon includes id key in item values; reference does not (key IS the id). Harmless. |
| `$[key].portrait_prompt` | `extra-canon` | Not in reference item shape. Harmless extra. |
| `$[key].rarity` | `extra-canon` | Not in reference. Harmless. |

---

## monsters/monsters.json

| Field path | Status | Notes |
|-----------|--------|-------|
| `$[key].name` | `canon-model` | OK |
| `$[key].species` | `canon-model` | OK |
| `$[key].description` | `canon-model` | OK |
| `$[key].backstory` | `canon-model` | OK |
| `$[key].hp_range` | `pack-synth` | OK — derived from skeleton |
| `$[key].ac_range` | `pack-synth` | OK |
| `$[key].damage_type` | `canon-model` | OK |
| `$[key].physical_type` | `canon-model` | OK |
| `$[key].elemental_affinity` | `canon-model` | TYPE mismatch: canon="none" (string), reference=null. Minor — mazeworld checks truthiness; "none" string may behave differently than null in comparisons. |
| `$[key].weakness` | `canon-model` | OK |
| `$[key].abilities` | `canon-model` | OK |
| `$[key].is_boss` | `pack-synth` | OK |
| `$[key].portrait_prompt` | `canon-model` | OK |
| `$[key].id` | `canon-model` | OK |
| `$[key].level` | `unmodeled` | Reference has `level` (int). Not emitted. mazeworld uses level for spawn probability scaling. |
| `$[key].time_availability` | `unmodeled` | Reference has `time_availability` ("always", "day", "night"). Not emitted. mazeworld filters by time of day. |
| `$[key].profile_image` | `runtime-fill` | null — expected before AssetPhase. TYPE mismatch expected. |
| `$[key].damage_die` | `extra-canon` | Canon adds `damage_die` — not in reference but used by combat |
| `$[key].environment` | `extra-canon` | Canon adds `environment` — not in reference |
| `$[key].tier` | `extra-canon` | Canon adds `tier` — not in reference |

---

## events/events.json

| Field path | Status | Notes |
|-----------|--------|-------|
| `$[0].id` | `canon-model` | OK |
| `$[0].type` | `pack-synth` | From skeleton `event_type` — OK |
| `$[0].name` | `canon-model` | OK |
| `$[0].description` | `canon-model` | OK |
| `$[0].difficulty` | `canon-model` | TYPE mismatch: canon=string ("medium"), reference=int (1). mazeworld uses `difficulty` as an integer difficulty rating. This will break mazeworld's combat scaling. |
| `$[0].money_drop` | `canon-model` | OK |
| `$[0].loot_table` | `canon-model` | OK (empty in fake run) |
| `$[0].portrait_prompt` | `unmodeled` | Reference has `portrait_prompt`. Not emitted by parse_event. |
| `$[0].profile_image` | `unmodeled` | Reference has `profile_image`. Not emitted. Events need portrait for display. |
| `$[0].time_gate` | `unmodeled` | Reference has `time_gate` (null or condition string). Not emitted. |
| `$[0].x` | `unmodeled` | Reference has `x` position in the room grid. Not emitted by parser — must be synthesized from MazeLayout. |
| `$[0].y` | `unmodeled` | Reference has `y` position. Same issue as `x`. |
| `$[0].monster_ids` | `unmodeled` | Reference has `monster_ids` list (actual monster int IDs for combat events). Parser emits `monster_count` (int) instead. mazeworld uses `monster_ids` to instantiate combat. This is a breaking gap. |
| `$[0].room_level` | `unmodeled` | Reference has `room_level`. Not emitted. |
| `$[0].monster_count` | `extra-canon` | Canon's placeholder for `monster_ids`. Must be resolved to actual IDs. |

---

## quests/quests.json

| Field path | Status | Notes |
|-----------|--------|-------|
| `$[0].id` | `canon-model` | OK |
| `$[0].type` | `pack-synth` | OK |
| `$[0].title` | `canon-model` | OK |
| `$[0].description` | `canon-model` | OK |
| `$[0].reward` | `canon-model` | OK |
| `$[0].failure_penalty` | `canon-model` | OK |
| `$[0].success_dialogue` | `canon-model` | OK |
| `$[0].failure_dialogue` | `canon-model` | OK |
| `$[0].giver_npc_id` | `unmodeled` | Reference has `giver_npc_id` (int). Not emitted. mazeworld uses this to wire quests to NPCs. |
| `$[0].room_id` | `unmodeled` | Reference has `room_id` (string). Not emitted. |
| `$[0].is_story_quest` | `unmodeled` | Reference has bool field. Not emitted. |
| `$[0].portrait_prompt` | `unmodeled` | Reference has `portrait_prompt`. Not emitted. |
| `$[0].profile_image` | `unmodeled` | Reference has `profile_image`. Not emitted. |
| `$[0].prerequisite_quest_id` | `unmodeled` | Reference has `prerequisite_quest_id` (null or int). Not emitted. |
| `$[0].target_zone` | `unmodeled` | Reference has `target_zone` ([x,y] tile for fetch/combat quests). Not emitted. |
| `$[0].escort_npc_id` | `unmodeled` | Reference has `escort_npc_id` (null or int, for escort quests). Not emitted. |
| `$[0].destination_room` | `unmodeled` | Reference has `destination_room` (int index). Not emitted. |
| `$[0].is_complete` | `extra-canon` | Canon adds runtime state — not in reference. |
| `$[0].is_failed` | `extra-canon` | Same — remove from persistence shape. |

---

## classes/classes.json

| Field path | Status | Notes |
|-----------|--------|-------|
| `$[0].name` | `canon-model` | OK |
| `$[0].archetype` | `canon-model` | OK |
| `$[0].flavor_text` | `canon-model` | OK |
| `$[0].environment` | `unmodeled` | Reference has `environment` (string). Not emitted. mazeworld uses this to assign classes to starting rooms. |
| `$[0].stats` | `unmodeled` | Reference has `stats` (STR/DEX/CON/INT/WIS/CHA/LUCK dict). Canon emits `stat_template` with same content but different key name. Naming mismatch. |
| `$[0].starting_weapon` | `canon-model` | OK |
| `$[0].abilities` | `canon-model` | OK |
| `$[0].spells` | `canon-model` | OK |
| `$[0].portrait_path` | `runtime-fill` | null before AssetPhase — TYPE mismatch expected. |
| `$[0].portrait_prompt` | `canon-model` | OK |
| `$[0].ability_pool` | `canon-model` | OK |
| `$[0].spell_pool` | `canon-model` | OK |
| `$[0].stat_template` | `extra-canon` | Canon's name for `stats` — needs rename to `stats` |
| `$[0].description` | `extra-canon` | Not in reference. Harmless. |
| `$[0].category` | `extra-canon` | Not in reference. Harmless. |
| `$[0].lore` | `extra-canon` | Not in reference. Harmless. |
| `$[0].role_tags` | `extra-canon` | Not in reference. Harmless. |
| `$[0].starting_equipment` | `extra-canon` | Not in reference. Harmless. |
| `$[0].stat_budget` | `extra-canon` | Internal canon field — not in reference. |
| `$[0].stat_roles` | `extra-canon` | Internal canon field — not in reference. |
| `$[0].extra` | `extra-canon` | Generic extra dict. Harmless. |

---

## classes/spell_pools.json

| Field path | Status | Notes |
|-----------|--------|-------|
| `$.mage_damage` | `unmodeled` | Reference has pool of 10 spells. Canon emits empty dict `{}`. SpellPoolPhase not running/not outputting anything. |
| `$.healer_damage` | `unmodeled` | Same — empty. |
| `$.heal` | `unmodeled` | Same — empty. |
| `$.buff` | `unmodeled` | Same — empty. |

---

## rooms/room_0/maze.json

| Field path | Status | Notes |
|-----------|--------|-------|
| `$.grid` | `canon-model` | OK (40x30) |
| `$.environment` | `canon-model` | OK |
| `$.environment_name` | `canon-model` | OK |
| `$.door_position` | `canon-model` | OK |
| `$.door_revealed` | `canon-model` | OK (false) |
| `$.gate_encounter_id` | `runtime-fill` | TYPE mismatch: canon=null, reference=int. Null acceptable before event wiring. |
| `$.npc_positions` | `pack-synth` | Canon emits `{}` (empty). Reference has `{npc_id: [x, y]}` for all room NPCs. MazeLayoutPhase assigns positions to NPC records but does NOT back-populate `npc_positions` dict in maze.json. This is a pack-synth gap: parsers synthesize x/y in NPC records but maze.json npc_positions dict stays empty. |
| `$.player_start` | `canon-model` | OK ([1,1]) |
| `$.item_placements` | `pack-synth` | Canon emits `[]` (empty list). Reference has `[{x, y, item_id, name, portrait_prompt, profile_image}]`. Item placements not being populated in maze.json — pack-synth gap. |
| `$.event_positions` | `pack-synth` | Canon emits `[]`. Reference has `[{x, y, event_id}]`. Not populated — pack-synth gap. |
| `$.quest_ids` | `pack-synth` | Canon emits `[]`. Reference has list of quest IDs for this room. Not populated — pack-synth gap. |
| `$.layout_type` | `extra-canon` | Not in reference. Harmless. |
| `$.extra` | `extra-canon` | Not in reference. Harmless. |
| `$.width` | `extra-canon` | Not in reference (reference infers from grid). Harmless. |
| `$.height` | `extra-canon` | Same. Harmless. |

---

## Summary counts

| Classification | Count |
|---------------|-------|
| `canon-model` | ~85 fields — emitting correctly |
| `pack-synth` | ~15 fields — synthesized; 5 have gaps (npc_positions not backfilled in maze.json, item_placements/event_positions/quest_ids empty, x/y in NPC records not backfilled to maze.json) |
| `pack-stub` | ~12 fields — stub values (empty portrait paths, boolean defaults) |
| `runtime-fill` | ~10 fields — null before post-generation phases (profile_image, dialogue_tree, gate_encounter_id, environment_portrait) |
| `unmodeled` | **44 fields** — must be addressed before Wave 6 |
| `extra-canon` | ~35 fields — canon adds extra fields not in reference (informational, not blocking) |

### Unmodeled fields by file

| File | Count | Critical? |
|------|-------|-----------|
| world_bible.json | 12 | Yes — story field renames, missing `entity_index` and `player_classes` |
| manifest.json | 3 | Yes — empty `music`/`sfx` dicts, missing `rooms_validated`/`major_retries` |
| npcs/npcs.json | 0 | No — all covered |
| items/items.json | 1 | Medium — `room_level` needed for loot table filtering |
| monsters/monsters.json | 2 | Medium — `level` and `time_availability` affect spawn logic |
| events/events.json | 8 | **CRITICAL** — missing `monster_ids`, `x`, `y`, `portrait_prompt`, `profile_image`, `time_gate`, `room_level` |
| quests/quests.json | 9 | **CRITICAL** — missing `giver_npc_id`, `room_id`, `is_story_quest`, `portrait_prompt`, `profile_image`, `prerequisite_quest_id`, `target_zone`, `escort_npc_id`, `destination_room` |
| classes/classes.json | 2 | Yes — `environment` and `stats` (rename from `stat_template`) |
| classes/spell_pools.json | 4 | **CRITICAL** — entire file is empty dict |
| rooms/maze.json | 3 | Yes — `npc_positions`, `item_placements`, `event_positions` backfill |

### TYPE mismatches (all blocking)

| File | Field | Canon type | Reference type | Impact |
|------|-------|------------|----------------|--------|
| manifest.json | `$.seed` | string | int | mazeworld uses seed as RNG int |
| manifest.json | `$.rooms[N].environment_portrait` | null | string | runtime-fill; null OK pre-asset |
| npcs/npcs.json | `$[0].dialogue_tree` | null | object | runtime-fill; null OK pre-dialogue |
| npcs/npcs.json | `$[0].profile_image` | null | string | runtime-fill; null OK pre-asset |
| items/items.json | `$[key].profile_image` | null | string | runtime-fill; null OK |
| monsters/monsters.json | `$[key].elemental_affinity` | string ("none") | null | Minor — "none" vs null |
| monsters/monsters.json | `$[key].profile_image` | null | string | runtime-fill |
| events/events.json | `$[0].difficulty` | string | int | **CRITICAL** — mazeworld uses int for scaling |
| classes/classes.json | `$[0].portrait_path` | null | string | runtime-fill |
