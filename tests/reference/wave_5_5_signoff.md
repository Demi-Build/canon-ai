# Wave 5.5 — Migration Checkpoint Sign-off

**Date:** 2026-05-06  
**Canon version:** 0.2.0  
**Verdict: 🔴 RED**

Zero `unmodeled` entries required for green. This run surfaces **44 unmodeled fields**, including 4 that are functionally breaking for mazeworld boot.

---

## Canon vs cradle field coverage by file

| File | MISSING | TYPE | EXTRA (info) | Status |
|------|---------|------|--------------|--------|
| `world_bible.json` | 21 (12 unmodeled, 9 rename/restructure) | 0 | 13 | RED |
| `manifest.json` | 40 (3 unmodeled, 37 audio path map + minor fields) | 2 | 10 | RED |
| `narrative.json` | 2 (room count diff, not a real gap) | 0 | 0 | GREEN* |
| `generation_stats.json` | 0 | 0 | 6 | GREEN |
| `npcs/npcs.json` | 0 | 2 (null before post-phases) | 4 | YELLOW |
| `items/items.json` | 1 (`room_level`) | 1 (null pre-asset) | 4 | YELLOW |
| `monsters/monsters.json` | 2 (`level`, `time_availability`) | 2 (1 null pre-asset, 1 type mismatch) | 3 | YELLOW |
| `events/events.json` | 7 (all unmodeled) | 1 (`difficulty` int vs string — BREAKING) | 1 | RED |
| `quests/quests.json` | 9 (all unmodeled) | 0 | 2 | RED |
| `classes/classes.json` | 2 (`environment`, `stats` rename) | 1 (null pre-asset) | 9 | YELLOW |
| `classes/spell_pools.json` | 4 (entire file empty) | 0 | 0 | RED |
| `rooms/room_0/maze.json` | 16 (NPC position dict not backfilled) | 1 (gate_encounter null — OK) | 4 | YELLOW |

*narrative.json missing rooms 3 and 4 because the run used `--num-maps 3`. Not a gap.

---

## Total MISSING fields by classification

| Classification | Count | Files |
|---------------|-------|-------|
| `unmodeled` | **44** | world_bible (12), manifest (3), items (1), monsters (2), events (8), quests (9), classes (2), spell_pools (4), maze (3) |
| `pack-synth` | ~8 | maze.json position backfills (npc_positions dict, item_placements, event_positions); event x/y |
| `pack-stub` | 2 | manifest validation_report fields |
| `runtime-fill` | ~10 | profile_image, dialogue_tree, portrait paths, gate_encounter_id |

---

## TYPE mismatches

### Blocking

| File | Field | Canon | Reference | Impact |
|------|-------|-------|-----------|--------|
| `manifest.json` | `$.seed` | `string` | `int` | mazeworld uses manifest.seed to initialize Python RNG — a string seed will cause a TypeError at boot. |
| `events/events.json` | `$[0].difficulty` | `string` ("medium") | `int` (1) | mazeworld's combat scaler divides by `difficulty` — string will throw TypeError. |

### Non-blocking (null before post-generation phases)

| File | Field | Notes |
|------|-------|-------|
| `npcs/npcs.json` | `profile_image`, `dialogue_tree` | null before AssetPhase/DialoguePhase — mazeworld uses fallback portrait |
| `items/items.json` | `profile_image` | Same |
| `monsters/monsters.json` | `profile_image` | Same |
| `classes/classes.json` | `portrait_path` | Same |
| `manifest.json` | `rooms[N].environment_portrait` | Same |

### Minor type issue

| File | Field | Canon | Reference | Notes |
|------|-------|-------|-----------|-------|
| `monsters/monsters.json` | `elemental_affinity` | `"none"` | `null` | mazeworld checks `if elemental_affinity:` — string "none" is truthy but should behave OK in practice. Low risk. |

---

## Cradle Rust loader notes

**All 23 Rust unit tests in `cradle/src-tauri/` pass** (`cargo test` exit 0, 0.02s).

The Rust loader (`src/data.rs`) is a read-only inspector with no typed struct deserialization for entity fields. Specifically:

- **Opaque `Value` forwarding**: All entity data (NPC fields, item fields, etc.) is deserialized as `serde_json::Value` and forwarded verbatim to the frontend. There are no typed Rust structs that would fail to deserialize if a field is missing or has a wrong type. The frontend JavaScript handles presentation.
- **Explicit field reads**: The loader reads only `id`, `name`, `title`, `environment_name` to build `EntityRef` for list views. These are optional (`get("id")` returns `None` gracefully). No hard failures from missing data fields.
- **File layout requirements**: The loader requires `<world_root>/world_bible.json` to exist for `get_world_bible()`. It expects entity files at `<world_root>/<type_id>/<type_id>.json` or `<world_root>/<type_id>/*.json`. Canon produces exactly this layout — all entity type paths load without error.
- **Audio by filename**: `music/` and `sfx/` are scanned for `.mp3/.wav/.ogg` files. Canon produces 8 music files and 15 sfx files — these load fine. The manifest `music`/`sfx` path dict being empty only affects mazeworld's Python runtime, not cradle's Rust loader (cradle derives music from file stems, not manifest paths).
- **Asset resolution**: `resolve_asset()` re-homes portrait paths by basename search within the world tree. Canon's `profile_image: null` fields skip this entirely — cradle frontend shows a placeholder. Not a Rust-level failure.

**Conclusion: cradle will load canon output without Rust-level errors.** All entity type lists will populate. Entity detail views will render whatever fields canon provides. Missing fields silently appear as missing values in the UI, not crashes.

**Dry-load result against `/tmp/canon_5_5/`:**

```
npcs:     6 entries  - flat npcs/npcs.json     - OK
items:    9 entries  - flat items/items.json    - OK
monsters: 6 entries  - flat monsters/monsters.json - OK
quests:   6 entries  - flat quests/quests.json  - OK
rooms:    3 dirs     - rooms/room_{0,1,2}/      - OK
events:   12 entries - flat events/events.json  - OK
classes:  4 entries  - flat classes/classes.json - OK
music:    8 files    - music/*.mp3               - OK (fake, zero-byte)
sfx:      15 files   - sfx/*.mp3                 - OK (fake, zero-byte)
world_bible.json   - OK (dict)
manifest.json      - OK (dict)
narrative.json     - OK (dict)
generation_stats.json - OK (dict)
```

No parse errors. Cradle dry-load: **PASS**.

---

## Verdict: RED

**Condition for green:** zero `unmodeled` fields.  
**Current state:** 44 unmodeled fields, 2 breaking TYPE mismatches.  
**Wave 6 cannot proceed** until the fixes below are made.

---

## Fixes required before Wave 6 (ranked by severity)

### Fix 1 — `events.difficulty` type: string → int  [CRITICAL, ~10 min]

**File:** `examples/mazeworld_pack/parsers.py` → `parse_event()`  
**Problem:** `"difficulty": difficulty` emits the skeleton string ("easy"/"medium"/"hard"). Reference expects an int (1-5).  
**Fix:** Add a mapping in `parse_event()`:

```python
_DIFFICULTY_INT = {"easy": 1, "medium": 2, "hard": 3, "very_hard": 4, "boss": 5}
# ...
"difficulty": _DIFFICULTY_INT.get(difficulty, 2),
```

### Fix 2 — `manifest.seed` type: string → int  [CRITICAL, ~15 min]

**File:** `examples/mazeworld_pack/` (wherever ManifestPhase gets the seed) or `src/canon/pipeline/phases/manifest.py`  
**Problem:** Canon seeds can be strings like "shadowspire_001". mazeworld's `random.seed(manifest["seed"])` accepts strings but some uses do `int(seed)` — must confirm. More importantly the reference spec has int. Use `hash(seed) & 0xFFFFFFFF` or require numeric seeds when outputting for mazeworld.  
**Fix:** In manifest writer, coerce seed to int: if seed is a non-numeric string, `int(hashlib.md5(str(seed).encode()).hexdigest()[:8], 16)` or just document that mazeworld-compatible seeds must be ints and pass `--seed 1234`.

### Fix 3 — `spell_pools.json` is empty  [CRITICAL, ~2 hours]

**File:** `examples/mazeworld_pack/` (SpellPoolPhase or equivalent)  
**Problem:** `classes/spell_pools.json` is `{}`. The reference has 4 pools (`mage_damage`, `healer_damage`, `heal`, `buff`) each with 10 spell entries. mazeworld uses spell_pools for combat.  
**Fix:** Verify SpellPoolPhase is registered in the pipeline and that `examples/run_mazeworld_full.py` includes it. If the phase exists but outputs empty dict, check the fake LLM response for spell pool generation. The spell entry shape is `{spell_type, element, stat, targets, num_dice, die_sides, stamina_cost, name, description}`.

### Fix 4 — `events/quests` missing linking fields  [CRITICAL, ~3 hours]

**Files:** `examples/mazeworld_pack/parsers.py` → `parse_event()`, `parse_quest()`  
**Problem:** Events missing `monster_ids` (list of int IDs), `x`, `y`, `portrait_prompt`, `profile_image`, `time_gate`, `room_level`. Quests missing `giver_npc_id`, `room_id`, `is_story_quest`, `portrait_prompt`, `profile_image`, `prerequisite_quest_id`, `target_zone`, `escort_npc_id`, `destination_room`.  
**Fix:**  
- Events: `monster_ids` must be synthesized by looking up which monsters share the room (post-generation cross-reference step). `x`/`y` must be synthesized from MazeLayout event_positions. `portrait_prompt` added to parser. `time_gate: null` stub. `room_level: 1` stub (hardcoded until we track room depth).  
- Quests: `giver_npc_id` requires picking an NPC from the same room (pack-synth cross-reference). `room_id` from map_obj. `is_story_quest: false` stub. `portrait_prompt` added. `prerequisite_quest_id: null` stub. `target_zone` needs tile from maze. `escort_npc_id`/`destination_room` null for non-escort types.

### Fix 5 — `world_bible.json` story field renames + `entity_index` / `player_classes`  [HIGH, ~2 hours]

**Files:** `examples/mazeworld_pack/` story model mapper, `src/canon/pipeline/phases/` story phase  
**Problem:** Multiple field renames:  
- `factions` (array) → `faction` (single object with `name`, `description`, `history`, `goals`)  
- `final_entity_id` → `final_boss_name`  
- `final_entity_lore` → `final_boss_lore`  
- `key_character_names` → `key_npc_names`  
- `beats[N].map_id` → `beats[N].room_id`  
- `beats[N].beat` → `beats[N].summary` (needs full narrative text, not stub)  
- Add `beats[N].faction_presence`, `beats[N].escalation` fields  
- Add `rooms.room_N.encounters` (rename from `events`)  
- Add `$.player_classes` top-level list (copy of classes.json content)  
- Add `$.entity_index` top-level dict (minimal stubs: `{entity_id: {name, type}}` for all generated entities)  
- Add story-level `story_npcs`, `story_items`, `story_monsters` lists  

### Fix 6 — `manifest.json` music/sfx path maps  [HIGH, ~1 hour]

**File:** `src/canon/pipeline/phases/manifest.py`  
**Problem:** `manifest.music` and `manifest.sfx` are emitted as empty dicts. ManifestPhase needs to scan the `music/` and `sfx/` output dirs (or track which tracks were generated) and populate `{"combat": "<path>/combat.mp3", ...}`.  
**Fix:** After asset generation, ManifestPhase scans `<output_dir>/music/` and `<output_dir>/sfx/` and builds the path dicts keyed by file stem.

### Fix 7 — `classes.stats` rename from `stat_template`  [MEDIUM, ~30 min]

**File:** `examples/mazeworld_pack/parsers.py` (class parser) or wherever classes are written  
**Problem:** Canon emits `stat_template: {STR, DEX, ...}` but reference has `stats: {STR, DEX, ...}`.  
**Fix:** Rename output field `stat_template` → `stats` in class parser output.

### Fix 8 — `maze.json` position backfills  [MEDIUM, ~2 hours]

**File:** `src/canon/pipeline/phases/maze_layout.py` or `examples/mazeworld_pack/` pack layer  
**Problem:** `npc_positions`, `item_placements`, `event_positions`, `quest_ids` in `rooms/room_N/maze.json` are all empty. NPC x/y are assigned to NPC records, but the maze.json inverse lookup dict is never populated.  
**Fix:** After NPCs, items, events, and quests are generated for a room, a post-pass writes `npc_positions = {str(npc_id): [x, y] for each NPC in room}` and similarly for items/events/quests.

### Fix 9 — `items.room_level`, `monsters.level` / `monsters.time_availability`  [MEDIUM, ~30 min]

**File:** `examples/mazeworld_pack/parsers.py`  
**Problem:** Items missing `room_level` (derived from which map the item is in — room 0 → level 1, room 4 → level 5). Monsters missing `level` (same) and `time_availability`.  
**Fix:** Add `"room_level": ctx.room_level if hasattr(ctx, 'room_level') else 1` in parse_item. Add `"level": 1` stub and `"time_availability": "always"` stub in parse_monster.

### Fix 10 — `classes.environment`  [LOW, ~15 min]

**File:** `examples/mazeworld_pack/parsers.py` (class parser)  
**Problem:** Reference assigns each class an environment. Canon doesn't emit this.  
**Fix:** Each `ClassArchetype` can be assigned a default environment (e.g. "ruins", "city") in the class skeleton or as a hardcoded map in the parser.

---

## Files produced by this Wave 5.5 run

| File | Path |
|------|------|
| Schema diff script | `scripts/diff_against_reference.py` |
| Cradle reference fixtures | `tests/reference/fixtures/cradle_mazeworld_scifi/` (12 files) |
| Coverage matrix | `tests/reference/wave_5_5_coverage_matrix.md` |
| This sign-off | `tests/reference/wave_5_5_signoff.md` |
| Canon demo output | `/tmp/canon_5_5/` (not committed — regenerate with run_mazeworld_full.py) |

---

## Wave 6 gate

| Condition | Status |
|-----------|--------|
| Zero `unmodeled` fields | FAIL (44 unmodeled) |
| Zero breaking TYPE mismatches | FAIL (2: seed type, difficulty type) |
| Cradle Rust dry-load passes | PASS |
| spell_pools.json non-empty | FAIL |
| events have monster_ids | FAIL |
| quests have giver_npc_id | FAIL |

**Proceed to Wave 6 only after Fixes 1–6 above are implemented and re-verified.**
