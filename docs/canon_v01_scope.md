# Canon v0.1 — Project Outline

## Context for downstream instance

This outline defines the scope of canon v0.1, the first public extraction of canon from MazeWorld. **Read `canon_framing.md` first** — it defines what canon is, what it isn't, and what kinds of games it serves. This outline assumes that framing.

Two things to carry in mind while reading:

1. **Canon v0.1 is an extraction, not a greenfield library.** Every abstraction in v0.1 already exists in MazeWorld in some form. v0.1's job is to generalize and ship them. MazeWorld becomes the reference implementation after cutover and will continue to work — byte-identically for the same seed — after importing canon as a dependency.

2. **Cradle is a first-class v0.1 consumer.** Cradle (Tauri GUI) calls canon via a CLI subprocess with JSON over stdout. Every operation cradle needs — load a bible, inspect any entity with its generation trail, re-roll a field, regenerate an entity, re-run validation, run a single phase — must be in the v0.1 CLI.

**Design decisions carried forward from the discovery report (all resolved):**

- Canon's spatial primitive is `Map` (what MazeWorld calls "room"), with optional `Zone` sub-regions.
- Canon's actor primitive is `Character` — unified across NPCs/PCs/followers/merchants/hostiles via a `role` field.
- Canon owns `StoryArc`. Opinionated but not minimal: factions (plural; Destiny has six, MazeWorld has one, canon supports both), escalation arc, beats, climax.
- `PromptSet` ABC is consolidated to ~17 methods with **world-aware defaults** — `DefaultPromptSet` pulls genre/tone/setting from the bible's seed and accumulated context, not from hardcoded fantasy tropes. No subclassing required for basic use.
- Canon v0.1 ships one LLM backend (Anthropic). Additional backends are v0.2+.
- Asset generation (images, music, SFX) is **not** in v0.1 core. Image backends ship as `canon[images]` in v0.2; music and SFX are deferred until new ABCs are designed.
- Cradle integration is CLI subprocess + JSON over stdout, not HTTP, not Python-embed.
- **All phases are opt-in.** There is no default pipeline. Canon ships phases; users compose.

## v0.1 goals

1. Ship a pip-installable `canon` package whose public API is stable enough for cradle to build against on day one.
2. Expose the World Bible, skeleton generation, phase-based pipeline, 3-stage validation, dialogue subsystem, class archetype system, and LLM backend abstractions as reusable library code.
3. Ship world-aware defaults (`DefaultPromptSet`, built-in phases, default Anthropic backend) so a developer can compose a pipeline and generate a validated world without writing a `PromptSet` subclass or a custom phase.
4. Expose a `canon` CLI with every operation cradle needs. Every command emits JSON on stdout; errors are serialized as JSON on stderr; non-zero exit on failure.
5. Support MazeWorld as a reference implementation — post-cutover, MazeWorld imports canon and contains only its domain-specific phases (MapLayoutPhase), prompt overrides, concrete checkers/validators, and placement logic. Post-cutover output is byte-identical to pre-cutover for the same seed.
6. Serve games with RPG-adjacent content regardless of combat or interaction model. A tower defense, a tactics RPG, a visual novel with stats, an action RPG, a farming sim, and a 1v3 asymmetric shooter should all be able to compose canon pipelines that produce their content.

## What's explicitly out of scope for v0.1

**Runtime concerns (out of scope indefinitely — not canon's job):**
- Combat systems of any kind (turn-based, real-time, tactics, action, asymmetric).
- Movement, physics, input handling, animation, rendering.
- Game engine integration beyond the JSON contract.
- Runtime state (health, mana, inventory contents, positions, save files) — canon produces content; engines track state.
- Multiplayer networking, matchmaking, session management.
- Runtime LLM dialogue or LLM-driven runtime behavior of any kind.

**Asset generation:**
- Image generation backends (fal.ai, local diffusers) — **v0.2** as `canon[images]`. The `ImageBackend` ABC ships in v0.1 but without concrete backends.
- Music generation (Lyria) — **deferred**. Needs a proper `MusicBackend` ABC designed first.
- SFX generation (ElevenLabs) — **deferred**, same reason.

**Additional LLM backends:**
- HuggingFace / local Llama — **v0.2** as `canon[local-llm]`.
- OpenAI, Ollama, Google Gemini — **v0.2+**. All pluggable via the `LLMBackend` protocol in v0.1; users can add their own.

**MazeWorld-specific behavior (stays in MazeWorld indefinitely):**
- Tile-grid maze generation (`src/models/maze.py`, `src/generate/placement.py`).
- Pygame runtime concerns (screen dimensions, `MAP_COLORS`, viewport logic).
- XYYY entity ID convention (1000-series NPCs, 2000-series items, etc.).
- `data/rooms/room_N/` directory layout convention.
- Concrete checkers/validators for weapons/spells/NPCs/monsters.
- `ClaudePromptSet` and `LlamaPromptSet` (stay as reference implementations).
- Runtime dialogue dispatch (`conversation_utils.py`).
- PDF player guide builder.
- Player class archetype content (warrior/mage/healer/jester) — the *shape* (`ClassArchetype`) is canon-core; the *content* is MazeWorld reference data.

**Library capabilities deferred:**
- Schema migrations across canon versions — **v0.2**. v0.1 stamps `canon_version` in every bible but does not migrate.
- Streaming generation progress (WebSocket/SSE) — **v0.3+**. CLI returns when done; cradle shows a spinner until then.
- Multi-world diffing or version comparison — **deferred indefinitely**; cradle owns this if anyone does.
- Pluggable context-builder strategies (alternate truncation, embedding-based retrieval) — **v0.2**. v0.1 ships MazeWorld's character-count truncation.
- Parallel story arcs (side arcs for Destiny-scale interweaving narratives) — **v0.2**. v0.1 ships a single `escalation_arc` per `StoryArc`; the field shape won't preclude adding parallel arcs later.
- Async-first generation API — **deferred**. v0.1 is sync at the pipeline level; `generate_batch()` handles concurrency for LLM calls via threads.

## Core capabilities

A developer using canon v0.1 can:

1. **Define a world schema.** Declare entity types via `SkeletonSpec` (mechanical pre-rolls) and class archetypes via `ClassArchetype` (stat templates, ability references). Canon makes no assumptions about what entity types or archetypes exist.
2. **Compose a pipeline.** Pick from built-in phases (`StoryPhase`, `CharacterPhase`, `ClassPhase`, `EntityPhase`, `DialoguePhase`, `ValidationPhase`, `NarrativePhase`) or add custom phases. Canon runs them sequentially, building a `Bible` incrementally, applying 3-stage validation with retry-with-feedback at every content-generation step.
3. **Generate dialogue trees.** `DialoguePhase` produces `DialogueTree` objects per character, with entry/terminal nodes, branching choices, and optional gating/effect annotations. Shape is general enough for Destiny, Stardew, Pokémon, and VNs.
4. **Inspect a generated world.** Load a `Bible` from disk, walk its maps, characters, class archetypes, dialogue trees, and entities; read per-entity `GenerationTrail` (prompt, raw response, validation history, retry count, cost).
5. **Mutate a generated world.** Re-roll flavor for a single field, regenerate an entity from scratch, add a new entity to a map, or re-run validation on the current bible state.
6. **Run a single pipeline phase on an existing bible.** Cradle uses this for targeted regeneration (e.g., "re-run `DialoguePhase` on character 1003 after I edited their backstory").
7. **Swap LLM backends.** Anthropic ships; any user-provided `LLMBackend` plugs in via the registry.
8. **Drive canon from a non-Python process.** The `canon` CLI exposes all of the above with JSON in/out.

## Package structure

```
canon/
├── __init__.py              # Public API surface (re-exports)
├── bible/
│   ├── __init__.py
│   ├── models.py            # Bible, Map, Zone, Character, CharacterClass,
│   │                        # ClassArchetype, EntityLore, StoryArc, Faction,
│   │                        # StoryBeat, BibleMetadata, GenerationTrail
│   └── context.py           # Cumulative context builder (character-count truncation)
├── dialogue/
│   ├── __init__.py
│   └── models.py            # DialogueNode, DialogueChoice, DialogueTree
├── skeleton/
│   ├── __init__.py
│   └── core.py              # SkeletonSpec, SkeletonField, roll_skeleton
├── pipeline/
│   ├── __init__.py
│   ├── runner.py            # run_pipeline, PipelineContext, Phase protocol
│   ├── retry.py             # retry_with_feedback
│   ├── phases.py            # StoryPhase, CharacterPhase, ClassPhase,
│   │                        # EntityPhase, DialoguePhase, ValidationPhase,
│   │                        # NarrativePhase — all opt-in, composed by user
│   └── stats.py             # GenerationStats
├── validation/
│   ├── __init__.py
│   ├── checker.py           # BaseChecker, CheckResult
│   ├── validator.py         # BaseValidator, ValidationResult, ValidationReport
│   └── coherence.py         # Generic coherence utilities (ref integrity, cycle detection)
├── llm/
│   ├── __init__.py
│   ├── client.py            # LLMClient (generate, generate_batch)
│   ├── request.py           # LLMRequest
│   └── prompts.py           # PromptSet ABC + DefaultPromptSet (world-aware)
├── backends/
│   ├── __init__.py
│   ├── base.py              # LLMBackend protocol, ImageBackend protocol
│   ├── registry.py          # BackendRegistry
│   └── anthropic.py         # Claude backend (the only backend shipped in v0.1)
├── cli/
│   ├── __init__.py
│   └── main.py              # `canon` entry point (Typer)
└── config.py                # CanonConfig
```

**Public import surface** (`from canon import ...`):

```
# Bible core
Bible, Map, Zone, Character, CharacterClass, ClassArchetype, EntityLore,
StoryArc, Faction, StoryBeat, BibleMetadata, GenerationTrail,

# Dialogue
DialogueNode, DialogueChoice, DialogueTree,

# Skeleton
SkeletonSpec, SkeletonField, roll_skeleton,

# Pipeline
Phase, PipelineContext, run_pipeline, retry_with_feedback, GenerationStats,
StoryPhase, CharacterPhase, ClassPhase, EntityPhase, DialoguePhase,
ValidationPhase, NarrativePhase,

# Validation
BaseChecker, CheckResult, BaseValidator, ValidationResult, ValidationReport,

# LLM
LLMClient, LLMRequest, LLMBackend, PromptSet, DefaultPromptSet,
BackendRegistry,

# Config
CanonConfig,

# Cradle-facing operations
reroll_entity_flavor, regenerate_entity, generate_entity, validate_bible, run_phase,
```

## Extension points

**Pluggable in v0.1:**

- **LLM backends** via the `LLMBackend` protocol. Canon ships Anthropic; users register their own with `BackendRegistry.register_llm()`.
- **Checkers and validators.** Users subclass `BaseChecker` / `BaseValidator` for their entity types and pass them into `PipelineContext`.
- **Pipeline phases.** Users implement the `Phase` protocol and compose their pipeline with `run_pipeline(phases, ctx)`. All built-in phases are themselves optional — a user composes only the phases their game needs.
- **Entity schemas.** Users define `SkeletonSpec` per entity type. Canon makes no assumptions about what entity types exist.
- **Class archetypes.** Users define `ClassArchetype` instances declaratively. Canon knows nothing about "warrior" or "tower defender" or "Titan" — those are user data.
- **Prompt sets.** Users subclass `PromptSet` (or `DefaultPromptSet`) to customize prompts; world-aware defaults work out of the box.

**Fixed in v0.1:**

- **Bible on-disk format:** JSON via Pydantic `model_dump()`. Schema versioning ships via `canon_version` field; migration does not (v0.2).
- **3-stage validation order:** Checker → Validator → World Editor. Individual checks within each stage are pluggable; the stage ordering is not.
- **Retry-with-feedback loop shape:** accepts `list[str]` feedback; re-invokes generator with feedback as a kwarg. `max_retries` is configurable; the loop structure is not.
- **`LLMRequest` shape:** `system`, `user_message`, `examples`, `max_tokens`. Adding new fields is a breaking change.
- **Context builder strategy:** character-count truncation. Pluggability is v0.2.
- **Dialogue tree shape:** `{nodes: dict[node_id, DialogueNode], entry_node_id, tree_id, character_id}`. Generalized from MazeWorld's format. Custom dialogue shapes (Ink-style flow, Yarn-style commands) would be a user-defined alternative entity type, not a subclass of `DialogueTree`.

## Dependencies

**Required:**
- `pydantic` (>=2.0) — core data modeling.
- `anthropic` — default LLM backend.
- `typer` — CLI framework.
- `tqdm` — progress tracking in the pipeline runner.

**Standard library only:**
- `retry_with_feedback` (no external deps).
- `GenerationStats` tracking.
- `SkeletonSpec` / `roll_skeleton` (uses `random.Random` seeded by `CanonConfig.seed`).
- Bible JSON persistence.

**Optional extras (declared in `pyproject.toml`, empty or minimal in v0.1 for forward compatibility):**
- `canon[images]` — fal.ai + local diffusers backends. **v0.2.**
- `canon[local-llm]` — HuggingFace / Llama. **v0.2.**
- `canon[dev]` — pytest, ruff, mypy.

**Python version:** `>=3.11` (matches MazeWorld's current floor; gains `Self`, `StrEnum`, improved typing).

## Success criteria

- A developer unfamiliar with MazeWorld can `pip install canon`, read the quickstart, define two entity types, compose a pipeline, and generate a validated 3-map world in under 30 minutes.
- A developer can compose a pipeline that skips dialogue entirely (e.g., for a tower defense) and canon runs cleanly without complaint.
- A developer can compose a pipeline that generates only characters and dialogue (e.g., for a visual novel) and canon runs cleanly without complaint.
- MazeWorld, post-cutover, imports canon and contains zero duplicated canon code. The MazeWorld pipeline produces byte-identical output to the pre-cutover pipeline for the same seed.
- Cradle can load any canon-produced bible, walk every entity, surface every piece of generation metadata, and invoke re-roll/regenerate/validate/run-phase via the `canon` CLI.
- Every documented CLI command returns well-formed JSON on stdout on success and exits non-zero on failure with errors as JSON on stderr.
- The 3-stage validation round-trips: a bible loaded, modified, and re-validated produces a report consistent in structure with the initial report.
- `DefaultPromptSet` generates coherent, usable output for at least two distinct RPG-adjacent game shapes (e.g., MazeWorld itself and one of: a scifi CRPG, a tactics RPG, or a farming-sim-with-stats) without subclassing. "Coherent" means: story, characters, and entities produced in one pipeline run are thematically consistent with the seed, and validation passes without retries on a majority of entities.

## Risks and open questions for downstream iteration

**R1. The 2,200-line `pipeline.py` monolith.** Every phase in MazeWorld is a private function in one file. The `Phase` protocol only works if those functions can be cleanly split into discrete classes. Recommend an early spike extracting one phase (story or classes) to validate the shape before committing to the pattern.

**R2. `config.py` is a gravity well.** Almost every MazeWorld generation file imports from it. Canon v0.1 depends on cleanly splitting generation config from game config. If this split isn't complete, canon either carries MazeWorld's `pygame` constants forward or breaks on import. **This refactor must precede canon v0.1 cutover.**

**R3. Hardcoded entity ID scheme.** MazeWorld's XYYY ID convention (1000-series NPCs, 2000-series items, etc.) is baked into pipeline orchestration code. Canon's pipeline needs an ID allocation strategy. Recommend a pluggable `IDAllocator` with a simple counter default; MazeWorld supplies its own XYYY allocator as a reference implementation.

**R4. `DefaultPromptSet` quality is load-bearing.** The decision to ship world-aware defaults is what makes "pip install and compose" possible for non-MazeWorld games. If the defaults produce garbage on anything other than fantasy RPGs, every user writes a `PromptSet` subclass anyway and the consolidation is a lie. Defaults must be tested against at least two distinct RPG-adjacent game shapes before v0.1 ships. Recommended test domains: MazeWorld (fantasy dungeon-crawler) and one non-fantasy RPG — a scifi CRPG, a tactics RPG in a non-fantasy setting, or a farming-sim-with-stats. The dogfooding schema need not be a full game; a one-shot pipeline that generates a coherent 3-map mini-world is sufficient.

**R5. `StoryArc` field defaults.** Canon ships `factions: list[Faction]` and `escalation_arc: list[str]`. For domains without conflict arcs (slice-of-life, educational, meditative) users leave them empty. All such fields default to `[]` rather than being required — canon stays usable without "fake factions" and without schema subclassing.

**R6. Character unification is a runtime refactor for MazeWorld, not a canon v0.1 concern.** MazeWorld has distinct NPC subtypes (`StaticNPC`, `RandomNPC`, `MerchantNPC`, `AggressiveNPC`) baked into its runtime. Collapsing these into a single `Character` in canon is fine for generation output. **MazeWorld's runtime keeps its subtypes and constructs them from canon's `Character` at load time**; full runtime unification is a MazeWorld v-next concern.

**R7. Cradle's CLI contract is effectively write-once.** Once cradle ships against `canon bible load` returning a specific JSON shape, changing that shape breaks cradle. Stamp `"canon_version": "0.1"` in every CLI JSON response from day one. Document the CLI output schema explicitly. Adopt a semver-adjacent policy: CLI schema changes are major-version changes.

**R8. Faction plurality for Destiny-scale worlds.** Canon ships `factions: list[Faction]` in v0.1, which means MazeWorld (single faction) has a slightly awkward single-element list. Trade-off accepted: MazeWorld pays a tiny awkwardness tax so Destiny-scale worlds work naturally. Alternative considered and rejected: `primary_faction` + `additional_factions` — privileges the single-faction case at the expense of multi-faction clarity.

**R9. Parallel story arcs deferred.** Destiny's story isn't one arc but many interweaving ones. v0.1 ships a single `escalation_arc: list[str]`; parallel arcs are v0.2. Confirm this trade-off. If Destiny-scale interweaving narrative is a v0.1 target, `side_arcs: list[list[str]]` or a graph-based story structure need earlier treatment.

**R10. Dialogue tree shape is opinionated.** Canon ships one shape: `{nodes, choices, next_node_id, entry_node_id}`. Alternatives (Ink-style flow, Yarn-style commands, pure freeform LLM dialogue at runtime) are user-defined entity types if needed, not subclasses. This matches how canon handles everything else — generalize the common case, let users escape via `extra: dict` or custom entity types.

**R11. Temptations to expand scope that should stay resisted.** Noting for future iteration: (a) an agent framework on top of `Phase` — resist, v0.3+ if ever; (b) a generic LLM orchestration layer for non-world-generation use cases — resist, not canon's job; (c) embedding-based context retrieval in place of character-count truncation — tempting, but v0.2 at earliest; (d) a web server mode (`canon serve`) — flagged as a later upgrade path from CLI, not v0.1; (e) runtime dialogue or LLM-driven runtime behavior of any kind — **hard no**, that's the engine's job.

**R12. Test coverage gap inherited from MazeWorld.** The discovery report notes `pyproject.toml` omits `pipeline`, `music_client`, `sfx_client`, `image_client`, and all prompts from coverage. Canon v0.1's extraction brings this gap forward unless new tests are written as part of extraction. Add a "test parity" requirement: every extracted module ships with tests that either existed in MazeWorld or are written during extraction. The pipeline itself deserves integration tests against a fixed seed.
