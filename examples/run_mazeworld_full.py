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
    CanonConfig,
    FakeImageBackend,
    FakeLLMBackend,
    FakeMusicBackend,
    FakeSFXBackend,
    LLMClient,
    run_pipeline,
)
from examples.mazeworld_pack import compose_pipeline  # noqa: E402


def build_llm_backend(kind: str, num_maps: int, model: str | None = None):
    """Return an LLM backend instance + (for fake mode) the responder."""
    if kind == "fake":
        responder = _make_fake_responder(num_maps)
        return FakeLLMBackend(responder)
    if kind == "anthropic":
        from canon.backends.anthropic import AnthropicBackend  # noqa: E402

        return AnthropicBackend(model=model) if model else AnthropicBackend()
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

        # Dialogue trees — checked FIRST. The dialogue prompt embeds the
        # character summary ("Role: npc"), so it would otherwise be caught by
        # the character/npc branches below and never produce a tree.
        if "dialogue" in msg_lower or "conversation" in msg_lower:
            counters["dialogue"] += 1
            return json.dumps({
                "entry_node_id": "start",
                "nodes": {
                    "start": {
                        "prompt": "Hello, traveler. What brings you here?",
                        "choices": [
                            {"text": "Who are you?", "next_node_id": "about"},
                            {"text": "Just passing through.", "next_node_id": "end"},
                            {"text": "Farewell.", "next_node_id": None},
                        ],
                    },
                    "about": {
                        "prompt": "Someone trying to survive in these ruins.",
                        "choices": [
                            {"text": "Good luck.", "next_node_id": "end"},
                        ],
                    },
                    "end": {"prompt": "Safe travels.", "choices": []},
                },
            })

        # Events — routed near the TOP by "money_drop", a marker unique to the
        # event JSON shape. The event prompt embeds the growing cumulative world
        # context, so keyword routing on the whole prompt is unreliable (context
        # words like "npc"/"monster" hijack later event prompts). "money_drop"
        # only appears in the event shape, so it's immune; "stat_check"
        # distinguishes puzzle/skill events (need choices) from combat (doesn't).
        if "money_drop" in msg_lower:
            counters["event"] += 1
            n = counters["event"]
            if "stat_check" in msg_lower:
                return json.dumps({
                    "name": f"Sealed Mechanism {n}",
                    "description": "An ancient device blocks the way, humming with latent power.",
                    "difficulty": "medium",
                    "money_drop": [10, 40],
                    "loot_table": [],
                    "choices": [
                        {
                            "text": "Force the mechanism open",
                            "stat_check": "STR",
                            "dc": 12,
                            "auto_success": False,
                            "success_text": "Metal shrieks and gives way.",
                        },
                        {
                            "text": "Study the glowing runes",
                            "stat_check": "INT",
                            "dc": 12,
                            "auto_success": False,
                            "success_text": "The sequence resolves in your mind.",
                        },
                    ],
                    # Ability/spell solutions that match generated classes
                    # (warrior has Bulwark; mage has Firebolt) so they're usable.
                    "correct_tool": None,
                    "correct_ability": "Bulwark",
                    "correct_spell": "Firebolt",
                    "failure_damage_type": "arcane",
                    "failure_damage_range": [2, 8],
                })
            return json.dumps({
                "name": f"Ambush {n}",
                "description": "Something hostile lunges from the shadows.",
                "difficulty": "medium",
                "money_drop": [10, 40],
                "loot_table": [],
            })

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
            # Per-archetype abilities so classes aren't identical in fake mode.
            def _ab(name, desc, stat, cost):
                return {"name": name, "description": desc, "stat": stat, "stamina_cost": cost}

            ability_sets = {
                "warrior": [
                    _ab("Cleave", "A heavy swing hitting adjacent foes.", "STR", 6),
                    _ab("Shield Bash", "Slam a foe to stun them.", "STR", 4),
                    _ab("Bulwark", "Brace to reduce incoming damage.", "CON", 3),
                ],
                "mage": [
                    _ab("Arcane Bolt", "A focused dart of raw magic.", "INT", 3),
                    _ab("Mana Shield", "Turn stamina into a damage ward.", "INT", 5),
                ],
                "healer": [
                    _ab("Soothing Word", "Calm an ally, clearing fear.", "WIS", 3),
                    _ab("Sanctuary", "Ward an ally from the next blow.", "WIS", 5),
                ],
                "jester": [
                    _ab("Mock", "Taunt a foe into a rash attack.", "CHA", 3),
                    _ab("Sleight of Hand", "Filch an item mid-combat.", "DEX", 4),
                    _ab("Tumble", "Roll clear of danger.", "DEX", 4),
                ],
            }
            stat_templates = {
                "warrior": {"STR": 16, "DEX": 12, "CON": 15, "INT": 8, "WIS": 10, "CHA": 11, "LUCK": 10},
                "mage": {"STR": 8, "DEX": 12, "CON": 10, "INT": 16, "WIS": 13, "CHA": 11, "LUCK": 10},
                "healer": {"STR": 9, "DEX": 11, "CON": 12, "INT": 12, "WIS": 16, "CHA": 12, "LUCK": 10},
                "jester": {"STR": 10, "DEX": 16, "CON": 11, "INT": 12, "WIS": 10, "CHA": 15, "LUCK": 13},
            }
            weapons = {
                "warrior": "Iron Greatsword", "mage": "Oak Staff",
                "healer": "Blessed Mace", "jester": "Twin Daggers",
            }
            abilities = ability_sets.get(class_archetype, ability_sets["warrior"])
            # Casters start knowing their spells, not just a level-up pool.
            spells = spell_pool if class_archetype in ("mage", "healer") else []
            return json.dumps({
                "archetype_id": class_archetype,
                "archetype": class_archetype,
                "name": f"Class {class_archetype.title()}",
                "description": f"A {class_archetype} archetype for testing.",
                "flavor_text": "Test flavor.",
                "starting_weapon": weapons.get(class_archetype, "Test Weapon"),
                "stat_template": stat_templates.get(class_archetype, stat_templates["warrior"]),
                "stat_roles": {"primary": ["STR"], "secondary": ["CON"], "dump": ["INT"]},
                "abilities": abilities,
                "spells": spells,
                "ability_pool": [_ab("Second Wind", "Recover stamina.", "CON", 0)],
                "spell_pool": spell_pool,
                "portrait_prompt": f"a {class_archetype} class portrait",
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

        # Item / weapon — checked LAST among entity types: event and quest
        # prompts also contain "item" (item_id in loot/reward shapes), so their
        # unique "event"/"quest" keywords must match first above. Only true item
        # prompts (which carry "weapon") reach here.
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
    # Surface canon's INFO-level progress logs (per-phase success/retry lines from
    # retry_with_feedback). Mirrors MazeWorld's pipeline.py logging setup; httpx /
    # anthropic are pinned to WARNING so their request chatter doesn't drown it out.
    import logging

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)

    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=["fake", "anthropic"], default="fake")
    parser.add_argument("--assets", choices=["none", "fake", "api"], default="fake")
    parser.add_argument("--config", default=None,
                        help="CanonConfig file (.toml/.json) — counts, model, seed, num_maps")
    parser.add_argument("--model", default=None,
                        help="LLM model id override, e.g. claude-haiku-4-5-20251001")
    # seed/num-maps default None so we can tell "not provided" from a value and
    # apply precedence CLI > --config > default.
    parser.add_argument("--seed", default=None)
    parser.add_argument("--num-maps", type=int, default=None)
    parser.add_argument("--output-dir", default="/tmp/canon_run")
    # Per-entity-type count overrides. These are mazeworld-specific runner flags
    # that populate the GENERIC CanonConfig.counts dict — core canon stays
    # game-agnostic; only the pack/runner knows these names.
    for _flag in ("npcs", "items", "monsters", "events", "quests", "classes"):
        parser.add_argument(f"--{_flag}", type=int, default=None)
    args = parser.parse_args()

    # Optional config file, then CLI overrides.
    # Precedence (highest first): CLI flag > --config file > pack default.
    cfg = None
    if args.config:
        cfg_path = Path(args.config)
        cfg = (
            CanonConfig.from_toml(cfg_path)
            if cfg_path.suffix.lower() == ".toml"
            else CanonConfig.from_json(cfg_path)
        )

    seed = args.seed or (cfg.seed if cfg else None) or "shadowspire_001"
    num_maps = args.num_maps if args.num_maps is not None else (cfg.num_maps if cfg else 3)
    model = args.model or (cfg.model if cfg else None)

    # CLI count flags → generic counts dict (flag name → entity_type key).
    _flag_to_type = {
        "npcs": "npc", "items": "item", "monsters": "monster",
        "events": "event", "quests": "quest", "classes": "class",
    }
    cli_counts = {
        etype: getattr(args, flag)
        for flag, etype in _flag_to_type.items()
        if getattr(args, flag) is not None
    }
    counts = {**(cfg.counts if cfg else {}), **cli_counts}

    print("==> Running canon mazeworld pipeline")
    print(f"    backend={args.backend}, assets={args.assets}, seed={seed!r}, maps={num_maps}")
    if model:
        print(f"    model={model}")
    if counts:
        print(f"    count overrides={counts}")
    print(f"    output={args.output_dir}")

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    phases, ctx = compose_pipeline(
        seed=seed, num_maps=num_maps, output_dir=output_dir, counts=counts, model=model
    )

    # Wire LLM
    llm_backend = build_llm_backend(args.backend, num_maps, model)
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

    if args.assets == "fake":
        print()
        print("    " + "=" * 64)
        print("    TEST ASSETS — generated with fake backends.")
        print("    Portraits are blank and SFX/music are SILENT placeholders;")
        print("    no real audio was generated. The game will load them without")
        print("    error but nothing will play. generation_stats.json records")
        print('    "assets_placeholder": true. Re-run with --assets api for real')
        print("    portraits + audio.")
        print("    " + "=" * 64)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
