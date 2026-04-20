# Canon — Project Framing

## What canon is

Canon is a Python library for generating rich game worlds: the characters, factions, story arcs, maps, items, abilities, classes, dialogue, and quests that make a game feel inhabited. It generates, validates, and persists this content as a canonical "World Bible" plus a set of engine-ready JSON databases.

Three ideas do most of the work:

- **World Bible** — single source of truth, accumulated incrementally, passed as context to every subsequent LLM call. Nothing canon generates can contradict anything else canon has already generated.
- **Skeleton-driven generation** — mechanical properties (dice, stats, numeric values) are pre-rolled deterministically; the LLM fills only names and flavor; skeleton values always win at merge time. Generative AI cannot break mechanical balance.
- **3-stage validation with retry-with-feedback** — per-entity structural checks, cross-reference integrity, world-level coherence. Failures feed back into the generator as retry context.

## What canon is not

Canon is not a game engine. It has no opinion about:

- combat (turn-based, tactical, real-time action, asymmetric, tower defense, capture-based, none)
- movement (grid, free, scrolling, teleport, none)
- dimensionality (2D, 3D, text-only, VR)
- platform (singleplayer, multiplayer, co-op, MMO)
- rendering, physics, animation, audio playback, input handling

Your game engine — pygame, Godot, Unity, Ren'Py, Bevy, your own — reads canon's output and does whatever it does.

## The target

Any game where the **world and characters matter**. Canon is tuned for games with RPG-adjacent content: stats, classes or archetypes, abilities/skills, items, progression, factions, characters with lore, quests.

A sampler across wildly different combat models, all served by the same canon primitives:

- **Destiny** — factions (Cabal, Fallen, Hive, Vex), character archetypes (Titan/Hunter/Warlock with subclasses), named NPCs with arcs (Zavala, Cayde), a weapon database where Gjallarhorn has both stats and a legend. Shooter is runtime; world is canon's.
- **Star Ocean 3/4** — action combat, but the party, alien species, skill trees, and crafting systems are rich content with lore.
- **Growlanser** — tactics-grid combat reading from a character DB, class archetypes, branching story arcs.
- **Pokémon** — trainer types, creature database, route structure, gym leader arcs. The battle system reads from them.
- **Tower defense** — tower archetypes, enemy wave definitions, map progression structure.
- **Stardew Valley** — villagers with relationships, festival arcs, item database, map zones. Lighter story than Destiny, same content primitives.
- **Asymmetric shooter** (1v3) — factional lore, character archetypes, ability sets, mapped zones.
- **MazeWorld** — the reference implementation.

These games differ dramatically at runtime. Their **content shapes** are nearly identical. Canon targets the shared shape and leaves everything else to the engine.

## Composition over prescription

Canon ships phases: `StoryPhase`, `CharacterPhase`, `ClassPhase`, `EntityPhase`, `DialoguePhase`, `ValidationPhase`, `NarrativePhase`. **Every phase is opt-in.** Users compose the pipeline they need:

- A tower defense developer composes `StoryPhase + ClassPhase(for tower types) + EntityPhase(for enemy waves) + ValidationPhase`. No dialogue, no named NPCs.
- A visual novel developer composes `StoryPhase + CharacterPhase + DialoguePhase + ValidationPhase`. No class archetypes, no monsters.
- A Destiny-shaped game composes all of them.
- MazeWorld composes all of them plus its own custom `MapLayoutPhase` (tile-grid, lives in MazeWorld).

There is no default pipeline. Canon doesn't assume anything about what your game needs.

## Who consumes canon's output

1. **The game engine**, at runtime. Reads JSON databases to populate runtime state — characters, inventories, encounter tables, dialogue trees.
2. **Cradle**, the companion GUI. Inspects the bible, surfaces generation trails, and re-invokes canon operations (re-roll, regenerate, re-validate) via a CLI subprocess.
3. **Canon itself**, on subsequent LLM calls. The bible is canon's own context memory — every generation after the first uses accumulated bible content as context, which is what keeps the world coherent.
