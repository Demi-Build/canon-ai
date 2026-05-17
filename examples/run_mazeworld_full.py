"""Run canon-ai's full mazeworld-shape pipeline end-to-end.

Three modes:
    --backend fake       — FakeLLMBackend with canned responses; $0; deterministic
    --backend anthropic  — Real Claude calls via canon[anthropic]
    --backend none       — error; one of the above is required

Asset modes:
    --assets fake        — FakeImageBackend / FakeMusicBackend / FakeSFXBackend; $0
    --assets api         — FalImageBackend + LyriaMusicBackend + ElevenLabsSFXBackend (~$30 for 3 maps)
    --assets none        — skip all asset generation; placeholder paths

Output:
    A complete `data/` tree at --output-dir matching mazeworld's expected
    layout. Drop into MazeWorld/data_canon/ and point MazeWorld/config.py
    DATA_DIR there to test loading.

Usage:
    python examples/run_mazeworld_full.py --backend fake --assets fake \\
        --output-dir /tmp/canon_run --num-maps 3
    ANTHROPIC_API_KEY=sk-... FAL_KEY=... GOOGLE_API_KEY=... ELEVENLABS_API_KEY=... \\
        python examples/run_mazeworld_full.py --backend anthropic --assets api
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running from repo root without installing
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from canon import (  # noqa: E402
    AssetPhase,
    FakeImageBackend,
    FakeLLMBackend,
    FakeMusicBackend,
    FakeSFXBackend,
    LLMClient,
    run_pipeline,
)
from examples.mazeworld_pack import compose_pipeline  # noqa: E402


def build_llm_backend(kind: str, num_maps: int):
    """Return an LLM backend instance + (for fake mode) the responder."""
    if kind == "fake":
        responder = _make_fake_responder(num_maps)
        return FakeLLMBackend(responder)
    if kind == "anthropic":
        from canon.backends.anthropic import AnthropicBackend  # noqa: E402

        return AnthropicBackend()
    raise SystemExit(f"Unknown --backend: {kind!r}")


def build_asset_backends(kind: str):
    """Return (image, music, sfx) backend instances or (None, None, None)."""
    if kind == "none":
        return None, None, None
    if kind == "fake":
        return FakeImageBackend(), FakeMusicBackend(), FakeSFXBackend()
    if kind == "api":
        from canon.backends.image_fal import FalImageBackend  # noqa: E402
        from canon.backends.music_lyria import LyriaMusicBackend  # noqa: E402
        from canon.backends.sfx_elevenlabs import ElevenLabsSFXBackend  # noqa: E402

        return FalImageBackend(), LyriaMusicBackend(), ElevenLabsSFXBackend()
    raise SystemExit(f"Unknown --assets: {kind!r}")


def _make_fake_responder(num_maps: int):
    """Callable for FakeLLMBackend that returns shape-correct JSON for any
    prompt the pipeline sends. Inspects request.user_message keywords.
    """
    counters = {
        "npc": 0,
        "item": 0,
        "monster": 0,
        "event": 0,
        "quest": 0,
        "character": 0,
        "class": 0,
        "dialogue": 0,
    }

    def respond(request):  # noqa: PLR0911,PLR0912
        msg_lower = request.user_message.lower()

        # Story (look for specific cues)
        if (
            "overarching story" in msg_lower
            or "world synopsis" in msg_lower
            or ("title" in msg_lower and "factions" in msg_lower)
        ):
            return json.dumps({
                "title": "The Convergence",
                "synopsis": "A test world.",
                "factions": [
                    {
                        "faction_id": "f1",
                        "name": "The Order",
                        "description": "test",
                        "history": "old",
                        "leader": "boss",
                        "threat_level": 5,
                    }
                ],
                "escalation_arc": ["arrive", "explore", "confront"],
                "climax": "Final battle.",
                "beats": [
                    {"map_id": f"room_{i}", "beat": f"beat {i}", "boss_name": "Boss", "boss_lore": "lore"}
                    for i in range(num_maps)
                ],
                "key_character_names": ["Hero", "Mentor"],
            })

        # Class archetype
        if "archetype" in msg_lower or ("class" in msg_lower and "stats" in msg_lower):
            n = counters["class"]
            counters["class"] += 1
            archetypes = ["warrior", "mage", "healer", "jester"]
            class_archetype = archetypes[n % 4]
            # Populate spell_pool for archetypes that cast spells
            spell_pool: list[dict] = []
            if class_archetype == "mage":
                spell_pool = [
                    {
                        "name": "Firebolt",
                        "spell_type": "damage_single",
                        "element": "fire",
                        "stat": "INT",
                        "targets": "single",
                        "num_dice": 1,
                        "die_sides": 6,
                        "stamina_cost": 4,
                        "description": "A bolt of flame.",
                    },
                    {
                        "name": "Frost Spike",
                        "spell_type": "damage_single",
                        "element": "frost",
                        "stat": "INT",
                        "targets": "single",
                        "num_dice": 1,
                        "die_sides": 6,
                        "stamina_cost": 4,
                        "description": "A shard of ice.",
                    },
                    {
                        "name": "Mass Shock",
                        "spell_type": "damage_multi",
                        "element": "light",
                        "stat": "INT",
                        "targets": "multi",
                        "num_dice": 1,
                        "die_sides": 4,
                        "stamina_cost": 6,
                        "description": "Lightning fans out.",
                    },
                ]
            elif class_archetype == "healer":
                spell_pool = [
                    {
                        "name": "Mend",
                        "spell_type": "heal",
                        "element": "light",
                        "stat": "WIS",
                        "targets": "single",
                        "num_dice": 1,
                        "die_sides": 8,
                        "stamina_cost": 3,
                        "heal_amount": 8,
                        "description": "Restore HP.",
                    },
                    {
                        "name": "Bless",
                        "spell_type": "buff_stat",
                        "element": "light",
                        "stat": "WIS",
                        "targets": "single",
                        "num_dice": 0,
                        "die_sides": 0,
                        "stamina_cost": 4,
                        "buff_stat": "STR",
                        "buff_value": 2,
                        "buff_duration": 3,
                        "description": "Boost an ally's strength.",
                    },
                    {
                        "name": "Solar Flare",
                        "spell_type": "damage_single",
                        "element": "light",
                        "stat": "WIS",
                        "targets": "single",
                        "num_dice": 1,
                        "die_sides": 6,
                        "stamina_cost": 5,
                        "description": "Searing light damage.",
                    },
                ]
            return json.dumps({
                "archetype_id": class_archetype,
                "archetype": class_archetype,
                "name": f"Class {class_archetype.title()}",
                "description": "A test class.",
                "flavor_text": "Test flavor.",
                "starting_weapon": "Test Weapon",
                "stat_template": {
                    "STR": 14, "DEX": 12, "CON": 13, "INT": 10, "WIS": 10, "CHA": 11, "LUCK": 10,
                },
                "stat_roles": {"primary": ["STR"], "secondary": ["CON"], "dump": ["INT"]},
                "abilities": [{"name": "Strike", "description": "A blow.", "stat": "STR", "stamina_cost": 5}],
                "spells": [],
                "ability_pool": [{"name": "Block", "description": "Defend.", "stat": "STR", "stamina_cost": 4}],
                "spell_pool": spell_pool,
                "portrait_prompt": "a test class portrait",
            })

        # Character
        if "character" in msg_lower and "role" in msg_lower:
            n = counters["character"]
            counters["character"] += 1
            return json.dumps({
                "name": f"Char {n}",
                "lore": "A test character.",
                "personality": "stoic",
                "job": "wanderer",
                "hobby": "watching",
                "opening_greeting": "Hello.",
                "portrait_prompt": "a person",
                "personality_notes": ["quiet"],
                "exhausted_dialogue": "Goodbye.",
            })

        # NPC (DatabasePhase)
        if "npc" in msg_lower or "named character" in msg_lower:
            n = counters["npc"]
            counters["npc"] += 1
            return json.dumps({
                "name": f"NPC {n}",
                "job": "merchant",
                "hobby": "trading",
                "personality": "wary",
                "backstory": "Sells things.",
                "opening_greeting": "Welcome.",
                "portrait_prompt": "a merchant",
            })

        # Item / weapon
        if "item" in msg_lower or "weapon" in msg_lower:
            n = counters["item"]
            counters["item"] += 1
            return json.dumps({
                "name": f"Item {n}",
                "desc": "A test item.",
                "category": "weapon",
                "weapon_type": "heavy",
                "damage_type": "slashing",
                "weapon_category": "martial",
                "item_stats": {"attack_dice": "1d8", "stat_modifier": "STR", "price": 25},
            })

        # Monster
        if "monster" in msg_lower or "creature" in msg_lower:
            n = counters["monster"]
            counters["monster"] += 1
            return json.dumps({
                "name": f"Monster {n}",
                "species": "creature",
                "description": "A test monster.",
                "backstory": "Lurks.",
                "hp_range": [10, 20],
                "ac_range": [10, 12],
                "damage_type": "physical",
                "physical_type": "bludgeoning",
                "abilities": [{"name": "Bite", "effect_type": "damage", "damage_dice": "1d6", "chance": 0.5}],
                "is_boss": False,
                "portrait_prompt": "a monster",
            })

        # Event
        if "event" in msg_lower or "encounter" in msg_lower:
            n = counters["event"]
            counters["event"] += 1
            return json.dumps({
                "name": f"Event {n}",
                "description": "A test event.",
                "difficulty": 1,
                "money_drop": [5, 10],
                "loot_table": [],
                "choices": [
                    {
                        "text": "Try",
                        "stat_check": "STR",
                        "dc": 12,
                        "auto_success": False,
                        "success_text": "ok",
                    }
                ],
            })

        # Quest
        if "quest" in msg_lower or "task" in msg_lower:
            n = counters["quest"]
            counters["quest"] += 1
            return json.dumps({
                "title": f"Quest {n}",
                "description": "Do a thing.",
                "reward": {"xp": 100, "item_id": None},
                "failure_penalty": {"hp_damage": 5},
                "success_dialogue": "Well done.",
                "failure_dialogue": "Too bad.",
            })

        # Dialogue tree
        if "dialogue" in msg_lower or "conversation" in msg_lower:
            counters["dialogue"] += 1
            return json.dumps({
                "entry_node_id": "start",
                "nodes": {
                    "start": {
                        "prompt": "Hello.",
                        "choices": [
                            {"text": "Hi", "next_node_id": "end"},
                            {"text": "Bye", "next_node_id": None},
                        ],
                    },
                    "end": {"prompt": "Goodbye.", "choices": []},
                },
            })

        # Narrative prose (synopsis, victory, defeat, room intros)
        if "synopsis" in msg_lower:
            return "A test synopsis. Heroes face a great challenge."
        if "victory" in msg_lower:
            return "Victory! You have prevailed."
        if "defeat" in msg_lower or "game over" in msg_lower:
            return "Defeat. You have fallen."
        if "intro" in msg_lower or "introduction" in msg_lower:
            return "You enter a new place."

        # Fallback: empty JSON
        return "{}"

    return respond


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=["fake", "anthropic"], default="fake")
    parser.add_argument("--assets", choices=["none", "fake", "api"], default="fake")
    parser.add_argument("--seed", default="shadowspire_001")
    parser.add_argument("--num-maps", type=int, default=3)
    parser.add_argument("--output-dir", default="/tmp/canon_run")
    args = parser.parse_args()

    print("==> Running canon mazeworld pipeline")
    print(f"    backend={args.backend}, assets={args.assets}, seed={args.seed!r}, maps={args.num_maps}")
    print(f"    output={args.output_dir}")

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    phases, ctx = compose_pipeline(seed=args.seed, num_maps=args.num_maps, output_dir=output_dir)

    # Wire LLM
    llm_backend = build_llm_backend(args.backend, args.num_maps)
    ctx.llm = LLMClient(llm_backend, stats=ctx.stats)
    ctx.stats.llm_backend = args.backend

    # Wire assets — set them on ctx so AssetPhase finds them via getattr
    img, mus, sfx = build_asset_backends(args.assets)
    if img is not None:
        ctx.image_backend = img
        ctx.stats.image_backend = "fake" if args.assets == "fake" else "api"
    if mus is not None:
        ctx.music_backend = mus
        ctx.stats.music_backend = "fake" if args.assets == "fake" else "api"
    if sfx is not None:
        ctx.sfx_backend = sfx
        ctx.stats.sfx_backend = "fake" if args.assets == "fake" else "api"

    # If assets are wired, replace the AssetPhase that compose_pipeline added
    # (which defaults to skip-all) with a non-skipping one.
    if args.assets != "none":
        phases = [p if p.name != "assets" else AssetPhase() for p in phases]

    # Run
    run_pipeline(phases, ctx)

    print()
    print(f"==> Wrote {output_dir}/")
    print(f"    title:       {ctx.bible.story.title!r}")
    print(f"    factions:    {len(ctx.bible.story.factions)}")
    print(f"    maps:        {len(ctx.bible.maps)}")
    print(f"    archetypes:  {len(ctx.bible.class_archetypes)}")
    print(f"    characters:  {len(ctx.bible.characters)}")
    print(f"    dialogues:   {len(ctx.bible.dialogues)}")
    total_entities = sum(len(m.entities) for m in ctx.bible.maps.values())
    print(f"    entities:    {total_entities}")
    print(f"    LLM calls:   {ctx.stats.llm_calls}")
    print(f"    Image gen:   {ctx.stats.image_attempts} attempts, {ctx.stats.image_successes} succeeded")
    print(f"    Music gen:   {getattr(ctx.stats, 'music_attempted', 0)} attempts")
    print(f"    SFX gen:     {getattr(ctx.stats, 'sfx_attempted', 0)} attempts")
    if args.backend == "anthropic":
        print(f"    LLM cost:    ${ctx.stats.llm_cost_usd:.4f}")
    if args.assets == "api":
        print(f"    Image cost:  ${ctx.stats.image_cost_usd:.4f}")
        print(f"    Audio cost:  ${ctx.stats.audio_cost_usd:.4f}")
        print(f"    Total cost:  ${ctx.stats.total_cost_usd:.4f}")

    print()
    print("==> Files produced:")
    for p in sorted(output_dir.rglob("*")):
        if p.is_file():
            rel = p.relative_to(output_dir)
            print(f"    {rel}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
