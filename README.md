# canon-ai

**Coherence layer for AI-generated structured content.**

Canon is a Python library for generating structured, internally-consistent content using LLMs. It provides the infrastructure that turns a story seed into a fully realized world: a World Bible as single source of truth, skeleton-driven generation (mechanical properties pre-rolled deterministically, LLM fills name + flavor), and a validation pipeline with retry-on-failure feedback.

Canon is engine-agnostic and usable by anyone generating structured LLM content — game devs, interactive fiction authors, worldbuilders, tabletop creators.

## Core Concepts

- **World Bible** — A living lore document that accumulates context as content is generated. Every generator reads from it; every generator writes back to it. Ensures narrative coherence across maps, characters, and items.

- **Skeleton-Driven Generation** — Mechanical properties (stats, dice, costs, scaling) are pre-rolled deterministically from tables. The LLM adds only name, description, and flavor. This guarantees balanced, consistent game mechanics while letting the LLM be creative where it matters.

- **Validation Pipeline** — Three-stage quality assurance: Checkers validate structural correctness, Validators enforce business logic, and Coherence Audits catch cross-entity inconsistencies. Failed content is retried with failure reasons fed back to the LLM.

- **Maps & Zones** — Content is organized spatially. A Map is a discrete area (a dungeon room, a town, a chapter). Zones are sub-regions within maps for encounter placement and event distribution.

- **Unified Character Model** — NPCs, player characters, companions, merchants, and hostiles are all the same `Character` type with a `role` field. An NPC can be promoted to a player character by assigning a class. No conversion logic needed.

## Status

Canon is in early development. It is being extracted from [MazeWorld](https://github.com/wolfgangjblack/MazeWorld), which serves as the reference implementation.

## Installation

```bash
pip install canon-ai
```

Optional extras:

```bash
pip install canon-ai[anthropic]      # Claude LLM backend
pip install canon-ai[huggingface]    # Local LLM backend (Llama, etc.)
pip install canon-ai[images]         # Image generation (fal.ai)
pip install canon-ai[images-local]   # Local image generation (FLUX, SDXL)
pip install canon-ai[audio]          # Music (Lyria) + SFX (ElevenLabs)
pip install canon-ai[cli]            # CLI for use with Cradle / external tools
```

## Quick Start

```python
from canon import Bible, LLMClient, PromptSet

# Load or create a world bible
bible = Bible.load("world_bible.json")

# Browse the world
for map_id, map_data in bible.maps.items():
    print(f"{map_data.name}: {len(map_data.characters)} characters")

# Re-run validation
from canon.validation import validate_bible
report = validate_bible(bible)
print(report.status)
```

## Architecture

```
canon/
├── bible/          # World Bible models (Bible, Map, EntityLore, StoryArc)
├── skeleton/       # Deterministic pre-roll specs and rolling
├── pipeline/       # Phase-based generation orchestrator
├── validation/     # Checker, Validator, Coherence audit framework
├── llm/            # LLM client, batch executor, request types
├── prompts/        # PromptSet ABC with default implementations
├── backends/       # Pluggable LLM and image backends
└── config.py       # Canon-specific configuration
```

## Companion Projects

- **[MazeWorld](https://github.com/wolfgangjblack/MazeWorld)** — Reference implementation. A procedurally generated RPG that uses canon for world generation.
- **Cradle** (coming soon) — A Tauri-based GUI for inspecting and editing canon-generated worlds.

## License

[Apache License 2.0](LICENSE)
