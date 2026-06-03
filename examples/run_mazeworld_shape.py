"""End-to-end canon-ai demo: generate a MazeWorld-shape world.

Two modes (selected by ``--backend``):
    fake       — no API calls, $0 cost, deterministic. Uses canned responses
                 from ``tests/reference/mazeworld_data.py``. Good for shape
                 inspection and CI.
    anthropic  — real Claude calls. Costs ~$0.10–$0.30 per run depending on
                 world size. Requires ``ANTHROPIC_API_KEY`` in the environment.

Output:
    A single canon-format JSON file at ``--output``. This is *not* the same
    shape as MazeWorld's ``data/`` tree — see the README in this directory
    for the schema gap and adapter notes.

Usage:
    python examples/run_mazeworld_shape.py --backend fake --output /tmp/canon_world.json
    ANTHROPIC_API_KEY=sk-... python examples/run_mazeworld_shape.py --backend anthropic
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

# Allow running this script from the repo root without installing the package.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))  # for tests.reference.mazeworld_data

from canon import (  # noqa: E402
    Bible,
    BibleMetadata,
    CanonConfig,
    CharacterPhase,
    ClassPhase,
    DefaultPromptSet,
    DialoguePhase,
    EntityPhase,
    FakeLLMBackend,
    GenerationStats,
    LLMClient,
    Map,
    NarrativePhase,
    PipelineContext,
    StoryPhase,
    ValidationPhase,
    run_pipeline,
)
from tests.reference.mazeworld_data import (  # noqa: E402
    HEALER_ARCHETYPE,
    ITEM_SPEC,
    JESTER_ARCHETYPE,
    MAGE_ARCHETYPE,
    MONSTER_SPEC,
    SPELL_SPEC,
    WARRIOR_ARCHETYPE,
    WEAPON_SPEC,
    build_canned_responses,
)


def build_backend(kind: str):
    if kind == "fake":
        responses = build_canned_responses(
            num_maps=3,
            weapons_per_map=2,
            monsters_per_map=2,
            items_per_map=1,
            characters_per_map=2,
        )
        return FakeLLMBackend(responses)
    if kind == "anthropic":
        from canon.backends.anthropic import AnthropicBackend
        return AnthropicBackend()
    raise SystemExit(f"Unknown --backend: {kind!r}. Use 'fake' or 'anthropic'.")


def build_pre_seeded_bible(seed: str, num_maps: int) -> Bible:
    """Pre-create empty Maps. Canon's StoryPhase generates the StoryArc but
    not the maps themselves — in MazeWorld a separate maze-layout phase
    creates rooms. For this demo we hand-build empty Maps so the per-map
    phases have something to populate."""
    bible = Bible.empty(seed=seed)
    bible.metadata = BibleMetadata()
    environments = ["forest", "manor", "vault", "catacombs", "crypt"]
    for i in range(num_maps):
        bible.maps[f"room_{i}"] = Map(
            map_id=f"room_{i}",
            name=f"Room {i}",
            description="",
            environment=environments[i % len(environments)],
            level=i + 1,
            story_beat=f"beat_{i}",
        )
    return bible


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=["fake", "anthropic"], default="fake")
    parser.add_argument("--seed", default="shadowspire_001")
    parser.add_argument("--num-maps", type=int, default=3)
    parser.add_argument("--output", default="canon_output/world_bible.json")
    args = parser.parse_args()

    print(f"==> Running canon pipeline (backend={args.backend}, seed={args.seed!r}, maps={args.num_maps})")

    stats = GenerationStats()
    backend = build_backend(args.backend)
    llm = LLMClient(backend, stats=stats)
    prompts = DefaultPromptSet()
    bible = build_pre_seeded_bible(args.seed, args.num_maps)

    ctx = PipelineContext(
        bible=bible,
        config=CanonConfig(seed=args.seed, num_maps=args.num_maps),
        rng=random.Random(args.seed),
        llm=llm,
        prompts=prompts,
        stats=stats,
        schemas={
            "weapon": WEAPON_SPEC,
            "spell": SPELL_SPEC,
            "monster": MONSTER_SPEC,
            "item": ITEM_SPEC,
        },
        archetypes={
            "warrior": WARRIOR_ARCHETYPE,
            "mage": MAGE_ARCHETYPE,
            "healer": HEALER_ARCHETYPE,
            "jester": JESTER_ARCHETYPE,
        },
    )

    bible_out = run_pipeline(
        phases=[
            StoryPhase(),
            ClassPhase(archetype_count=4, instantiate_for_roles=()),
            CharacterPhase(per_map=True, count=2, roles=["npc"]),
            EntityPhase(entity_type="weapon", per_map=True, count=2),
            EntityPhase(entity_type="spell", per_map=True, count=2),
            EntityPhase(entity_type="monster", per_map=True, count=2),
            EntityPhase(entity_type="item", per_map=True, count=1),
            DialoguePhase(),
            NarrativePhase(),
            ValidationPhase(),
        ],
        ctx=ctx,
    )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bible_out.persist(out_path)

    print(f"==> Wrote {out_path}")
    print(f"    title:       {bible_out.story.title!r}")
    print(f"    factions:    {len(bible_out.story.factions)}")
    print(f"    maps:        {len(bible_out.maps)}")
    print(f"    archetypes:  {len(bible_out.class_archetypes)}")
    print(f"    characters:  {len(bible_out.characters)}")
    print(f"    dialogues:   {len(bible_out.dialogues)}")
    total_entities = sum(len(m.entities) for m in bible_out.maps.values())
    print(f"    entities:    {total_entities}")
    print(f"    LLM calls:   {stats.llm_calls}")
    if args.backend == "anthropic":
        print(f"    cost (USD):  ${stats.total_cost:.4f}")
        print("    (NB: stats.total_cost is 0 in v0.1; see backend.last_cost for per-call costs)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
