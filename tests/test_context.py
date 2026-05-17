"""Unit tests for canon.bible.context.

Covers section ordering, graceful degradation on missing data, prior-map
accumulation, per-entity-type lore truncation, and the soft-cap with
truncation marker. The tests intentionally use small handcrafted Bibles
rather than fixtures from the model tests — context formatting is its own
concern and shouldn't share fate with model-shape tests.
"""

from __future__ import annotations

from canon.bible.context import (
    DEFAULT_CONTEXT_LIMIT_CHARS,
    ITEM_MONSTER_LORE_SNIPPET_CHARS,
    NPC_LORE_SNIPPET_CHARS,
    get_context,
    get_cumulative_context,
)
from canon.bible.models import (
    Bible,
    EntityLore,
    Faction,
    Map,
    StoryArc,
    StoryBeat,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bible(
    *,
    title: str = "Test World",
    synopsis: str = "A small synopsis.",
    factions: list[Faction] | None = None,
    beats: list[StoryBeat] | None = None,
    key_character_names: list[str] | None = None,
    maps: dict[str, Map] | None = None,
) -> Bible:
    bible = Bible.empty(seed="seed_test")
    bible.story = StoryArc(
        title=title,
        synopsis=synopsis,
        seed="seed_test",
        factions=factions or [],
        beats=beats or [],
        key_character_names=key_character_names or [],
    )
    if maps:
        for key, value in maps.items():
            bible.maps[key] = value
    return bible


def _faction(
    fid: str = "f1",
    *,
    name: str = "The Order",
    description: str = "A faction.",
    history: str = "",
    leader: str = "",
) -> Faction:
    return Faction(
        faction_id=fid,
        name=name,
        description=description,
        history=history,
        leader=leader,
    )


def _beat(
    map_id: str,
    beat_text: str = "Opening beat.",
    *,
    boss_name: str | None = None,
    boss_lore: str | None = None,
) -> StoryBeat:
    return StoryBeat(
        map_id=map_id,
        beat=beat_text,
        boss_name=boss_name,
        boss_lore=boss_lore,
    )


def _map(
    map_id: str,
    *,
    name: str | None = None,
    level: int | None = 1,
    entities: list[EntityLore] | None = None,
) -> Map:
    return Map(
        map_id=map_id,
        name=name or map_id,
        level=level,
        entities=entities or [],
    )


def _entity(
    entity_id: str,
    entity_type: str,
    *,
    name: str | None = None,
    lore: str = "",
) -> EntityLore:
    return EntityLore(
        entity_id=entity_id,
        entity_type=entity_type,
        name=name or entity_id,
        lore=lore,
    )


# ---------------------------------------------------------------------------
# get_context
# ---------------------------------------------------------------------------


def test_get_context_emits_sections_in_order_for_full_bible() -> None:
    bible = _make_bible(
        title="Spike Saga",
        synopsis="Heroes converge.",
        factions=[
            _faction(
                name="Order of Tests",
                description="They write the suite.",
                history="Long.",
                leader="Pytest",
            )
        ],
        beats=[_beat("m1", "Heroes meet.", boss_name="Bug", boss_lore="Crashes.")],
        key_character_names=["Alyx", "Borin"],
        maps={"m1": _map("m1")},
    )

    text = get_context(bible, "m1")

    # Verify every expected line shows up exactly.
    assert "Title: Spike Saga" in text
    assert "Synopsis: Heroes converge." in text
    assert "Faction: Order of Tests — They write the suite." in text
    assert "History: Long." in text
    assert "Leader: Pytest" in text
    assert "Beat: Heroes meet." in text
    assert "Boss: Bug — Crashes." in text
    assert "Key characters: Alyx, Borin" in text

    # Order: title -> synopsis -> faction -> history -> leader -> beat -> boss -> key chars.
    indexes = [
        text.index("Title:"),
        text.index("Synopsis:"),
        text.index("Faction:"),
        text.index("History:"),
        text.index("Leader:"),
        text.index("Beat:"),
        text.index("Boss:"),
        text.index("Key characters:"),
    ]
    assert indexes == sorted(indexes)


def test_get_context_with_no_factions_or_beats_returns_title_and_synopsis() -> None:
    bible = _make_bible(
        title="Bare World",
        synopsis="Nothing else.",
        maps={"m1": _map("m1")},
    )

    text = get_context(bible, "m1")
    assert text == "Title: Bare World\nSynopsis: Nothing else."


def test_get_context_emits_each_faction() -> None:
    bible = _make_bible(
        factions=[
            _faction("f1", name="Alpha", description="First."),
            _faction("f2", name="Beta", description="Second."),
            _faction("f3", name="Gamma", description="Third."),
        ],
        maps={"m1": _map("m1")},
    )

    text = get_context(bible, "m1")
    assert "Faction: Alpha — First." in text
    assert "Faction: Beta — Second." in text
    assert "Faction: Gamma — Third." in text


def test_get_context_with_unknown_map_id_does_not_raise() -> None:
    bible = _make_bible(
        title="World",
        synopsis="Story.",
        factions=[_faction()],
        beats=[_beat("m1")],
        key_character_names=["Alyx"],
        maps={"m1": _map("m1")},
    )

    text = get_context(bible, "no_such_map")

    # Beat / boss should be omitted but story-level content remains.
    assert "Title: World" in text
    assert "Synopsis: Story." in text
    assert "Faction: The Order" in text
    assert "Key characters: Alyx" in text
    assert "Beat:" not in text
    assert "Boss:" not in text


def test_get_context_skips_boss_when_only_one_field_set() -> None:
    bible = _make_bible(
        beats=[_beat("m1", boss_name="Bug", boss_lore=None)],
        maps={"m1": _map("m1")},
    )
    text = get_context(bible, "m1")
    assert "Beat:" in text
    assert "Boss:" not in text


def test_get_context_omits_empty_history_and_leader() -> None:
    bible = _make_bible(
        factions=[_faction(history="", leader="")],
        maps={"m1": _map("m1")},
    )
    text = get_context(bible, "m1")
    assert "History:" not in text
    assert "Leader:" not in text


# ---------------------------------------------------------------------------
# get_cumulative_context
# ---------------------------------------------------------------------------


def test_cumulative_context_with_no_prior_maps_equals_single_map_context() -> None:
    bible = _make_bible(
        title="World",
        synopsis="Synopsis.",
        beats=[_beat("m1")],
        maps={"m1": _map("m1"), "m2": _map("m2")},
    )

    assert get_cumulative_context(bible, "m1") == get_context(bible, "m1")


def test_cumulative_context_appends_prior_map_entities() -> None:
    bible = _make_bible(
        title="World",
        synopsis="Synopsis.",
        beats=[_beat("m1"), _beat("m2")],
        maps={
            "m1": _map(
                "m1",
                name="Mossy Hall",
                level=1,
                entities=[
                    _entity("npc1", "npc", name="Alyx", lore="Lives here."),
                    _entity("itm1", "item", name="Mossblade", lore="Old sword."),
                    _entity("mon1", "monster", name="Slime", lore="Drips."),
                ],
            ),
            "m2": _map("m2", name="Crypt", level=2),
        },
    )

    text = get_cumulative_context(bible, "m2")

    assert "Prior map: Mossy Hall (level 1)" in text
    assert "- npc Alyx: Lives here." in text
    assert "- item Mossblade: Old sword." in text
    assert "- monster Slime: Drips." in text


def test_cumulative_context_truncates_npc_lore_to_120_chars() -> None:
    long = "x" * 500
    bible = _make_bible(
        maps={
            "m1": _map(
                "m1",
                entities=[_entity("n", "npc", name="LongName", lore=long)],
            ),
            "m2": _map("m2"),
        },
    )

    text = get_cumulative_context(bible, "m2")
    expected_snippet = "x" * NPC_LORE_SNIPPET_CHARS

    assert f"- npc LongName: {expected_snippet}\n" in text or text.endswith(
        f"- npc LongName: {expected_snippet}"
    )
    # The next character after the snippet must NOT be 'x' — i.e. truncation took effect.
    line = next(
        line for line in text.splitlines() if line.startswith("- npc LongName: ")
    )
    snippet = line[len("- npc LongName: ") :]
    assert len(snippet) == NPC_LORE_SNIPPET_CHARS


def test_cumulative_context_truncates_item_and_monster_lore_to_80_chars() -> None:
    long = "y" * 500
    bible = _make_bible(
        maps={
            "m1": _map(
                "m1",
                entities=[
                    _entity("i", "item", name="Sword", lore=long),
                    _entity("m", "monster", name="Beast", lore=long),
                ],
            ),
            "m2": _map("m2"),
        },
    )

    text = get_cumulative_context(bible, "m2")
    for label in ("- item Sword: ", "- monster Beast: "):
        line = next(line for line in text.splitlines() if line.startswith(label))
        snippet = line[len(label) :]
        assert len(snippet) == ITEM_MONSTER_LORE_SNIPPET_CHARS


def test_cumulative_context_skips_empty_prior_maps() -> None:
    """A prior map with no entities contributes nothing — keeps prompts compact."""

    bible = _make_bible(
        maps={
            "m_empty": _map("m_empty", name="Empty"),
            "m_target": _map("m_target", name="Target"),
        },
    )

    text = get_cumulative_context(bible, "m_target")
    assert "Empty" not in text


def test_cumulative_context_groups_npcs_first() -> None:
    bible = _make_bible(
        maps={
            "m1": _map(
                "m1",
                entities=[
                    _entity("i1", "item", name="Sword", lore="Sharp."),
                    _entity("n1", "npc", name="Bard", lore="Sings."),
                ],
            ),
            "m2": _map("m2"),
        },
    )
    text = get_cumulative_context(bible, "m2")
    npc_idx = text.index("- npc Bard")
    item_idx = text.index("- item Sword")
    assert npc_idx < item_idx, "npcs should appear before items in prior-map blocks"


def test_cumulative_context_soft_cap_truncates_with_marker() -> None:
    # Build many prior maps, each carrying lots of entities, so total length
    # blows past a small `max_chars`.
    big_lore = "z" * 200
    maps_dict: dict[str, Map] = {}
    for i in range(20):
        entities = [
            _entity(f"n{i}_{j}", "npc", name=f"Npc{i}_{j}", lore=big_lore)
            for j in range(10)
        ]
        maps_dict[f"m{i}"] = _map(f"m{i}", name=f"Map{i}", level=i, entities=entities)
    maps_dict["m_target"] = _map("m_target")

    bible = _make_bible(maps=maps_dict)

    text = get_cumulative_context(bible, "m_target", max_chars=2_000)

    assert len(text) <= 2_000
    assert text.endswith("[...earlier maps truncated]")


def test_cumulative_context_default_cap_is_double_default_limit() -> None:
    """When no max_chars is passed, the soft cap defaults to
    DEFAULT_CONTEXT_LIMIT_CHARS * 2 (= 40_000)."""

    big_lore = "q" * 500
    maps_dict: dict[str, Map] = {}
    # Generate enough content to exceed 40_000 chars (50 maps x 100 npcs).
    for i in range(50):
        entities = [
            _entity(f"n{i}_{j}", "npc", name=f"N{i}_{j}", lore=big_lore)
            for j in range(100)
        ]
        maps_dict[f"m{i}"] = _map(f"m{i}", name=f"Map{i}", level=i, entities=entities)
    maps_dict["m_target"] = _map("m_target")
    bible = _make_bible(maps=maps_dict)

    text = get_cumulative_context(bible, "m_target")

    expected_cap = DEFAULT_CONTEXT_LIMIT_CHARS * 2
    assert len(text) <= expected_cap
    assert text.endswith("[...earlier maps truncated]")


def test_cumulative_context_under_cap_is_unchanged() -> None:
    bible = _make_bible(
        title="World",
        synopsis="Synopsis.",
        maps={
            "m1": _map(
                "m1",
                name="Hall",
                level=1,
                entities=[_entity("n", "npc", name="Bard", lore="Sings.")],
            ),
            "m2": _map("m2"),
        },
    )

    text = get_cumulative_context(bible, "m2", max_chars=10_000)
    assert not text.endswith("[...earlier maps truncated]")
    assert "Prior map: Hall (level 1)" in text


# ---------------------------------------------------------------------------
# Bible method delegation smoke test
# ---------------------------------------------------------------------------


def test_bible_method_delegates_to_module_function() -> None:
    bible = _make_bible(
        title="Delegated",
        synopsis="Method routing.",
        beats=[_beat("m1")],
        maps={"m1": _map("m1")},
    )

    assert bible.get_context("m1") == get_context(bible, "m1")
    assert bible.get_cumulative_context("m1") == get_cumulative_context(bible, "m1")
    assert bible.get_cumulative_context("m1", max_chars=500) == get_cumulative_context(
        bible, "m1", max_chars=500
    )
