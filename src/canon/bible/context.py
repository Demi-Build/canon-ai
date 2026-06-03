"""Context-assembly helpers — turn a Bible into LLM-ready context strings.

Two functions are exposed:

* `get_context(bible, map_id)` — single-map context. Title, synopsis,
  faction(s), this map's beat, and key character names. No truncation.
  Used for the first generation in a map.
* `get_cumulative_context(bible, map_id, max_chars=None)` — single-map
  context plus a summary of every prior map's generated entities (name +
  truncated lore). Soft-capped at `max_chars` (default
  `DEFAULT_CONTEXT_LIMIT_CHARS * 2 = 40_000`); excess content is hard-cut
  with a `[...earlier maps truncated]` marker.

The truncation lengths and section ordering are taken from MazeWorld's
existing `world_bible.get_story_context` / `get_cumulative_context`
behavior (see `docs/api_verification_and_spike.md` §1.7).

TODO(v0.2): pluggable context strategies. Today this is a single
opinionated formatter that mirrors MazeWorld; future revisions may expose
a Protocol or registry so games can swap in domain-specific framings
without subclassing `Bible`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from canon.bible.models import Bible, Map, StoryBeat


# Default soft cap used by `get_cumulative_context` — generalized from
# MazeWorld's `STORY_CONTEXT_LIMIT * 2`. Canon does not import any
# MazeWorld config; this constant is the single source of truth here.
DEFAULT_CONTEXT_LIMIT_CHARS = 20_000

# Per-entity lore snippet lengths used when summarizing prior maps.
# NPCs get more characters because their lore is what most influences
# downstream character generation; items and monsters carry less context.
NPC_LORE_SNIPPET_CHARS = 120
ITEM_MONSTER_LORE_SNIPPET_CHARS = 80

_TRUNCATION_MARKER = "\n[...earlier maps truncated]"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_context(bible: Bible, map_id: str) -> str:
    """Build the single-map context string for `map_id`.

    The output is a newline-separated set of sections, emitted only when
    they have content. Missing fields and missing maps are tolerated
    silently — the caller gets back what canon could assemble. This is
    intentional: context strings are inputs to LLM prompts, not contracts.
    """

    sections: list[str] = []

    story = bible.story
    if story.title:
        sections.append(f"Title: {story.title}")
    if story.synopsis:
        sections.append(f"Synopsis: {story.synopsis}")

    for faction in story.factions:
        sections.append(f"Faction: {faction.name} — {faction.description}")
        if faction.history:
            sections.append(f"History: {faction.history}")
        if faction.leader:
            sections.append(f"Leader: {faction.leader}")

    beat = _find_beat(story.beats, map_id)
    if beat is not None:
        sections.append(f"Beat: {beat.beat}")
        if beat.boss_name and beat.boss_lore:
            sections.append(f"Boss: {beat.boss_name} — {beat.boss_lore}")

    if story.key_character_names:
        sections.append(
            "Key characters: " + ", ".join(story.key_character_names)
        )

    return "\n".join(sections)


def get_cumulative_context(
    bible: Bible,
    map_id: str,
    max_chars: int | None = None,
) -> str:
    """Build the cumulative context for `map_id` — single-map context
    plus every prior map's entities, soft-capped at `max_chars`.

    "Prior" is determined by `bible.maps` insertion order. If `map_id` is
    the first key, or absent, no prior maps are included. The function
    never raises on a missing `map_id` — it just falls back to the
    single-map context.
    """

    cap = max_chars if max_chars is not None else DEFAULT_CONTEXT_LIMIT_CHARS * 2

    base = get_context(bible, map_id)
    prior_maps = _prior_maps(bible, map_id)
    if not prior_maps:
        return _apply_cap(base, cap)

    parts: list[str] = [base] if base else []
    for prior in prior_maps:
        section = _format_prior_map(prior)
        if section:
            parts.append(section)

    full = "\n".join(parts)
    return _apply_cap(full, cap)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _find_beat(beats: list, map_id: str) -> StoryBeat | None:
    """Return the first StoryBeat whose `map_id` matches, or None."""

    for beat in beats:
        if beat.map_id == map_id:
            return beat
    return None


def _prior_maps(bible: Bible, map_id: str) -> list:
    """Return the list of `Map` objects that come before `map_id` in
    `bible.maps` insertion order. If `map_id` is not present, returns an
    empty list (no map is a "prior" of an unknown map)."""

    prior: list = []
    for key, value in bible.maps.items():
        if key == map_id:
            return prior
        prior.append(value)
    # Reaching here means `map_id` was never found — bail with [] rather
    # than implicitly treating every map as prior.
    return []


def _format_prior_map(the_map: Map) -> str:
    """Format one prior map's header + grouped entity snippets."""

    header_bits = [f"Prior map: {the_map.name}"]
    if the_map.level is not None:
        header_bits.append(f"(level {the_map.level})")
    lines: list[str] = [" ".join(header_bits)]

    # Group by entity_type so the LLM sees npcs first, then items, then
    # monsters — matching MazeWorld's grouping. We keep stable ordering
    # within a group via the source list order.
    grouped: dict[str, list] = {}
    for entity in the_map.entities:
        grouped.setdefault(entity.entity_type, []).append(entity)

    # Stable iteration order: npc first, then everything else in
    # first-seen order. NPCs are highlighted because their snippet is
    # longer and they tend to anchor narrative continuity.
    type_order: list[str] = []
    if "npc" in grouped:
        type_order.append("npc")
    for entity_type in grouped:
        if entity_type not in type_order:
            type_order.append(entity_type)

    appended_any = False
    for entity_type in type_order:
        snippet_len = (
            NPC_LORE_SNIPPET_CHARS
            if entity_type == "npc"
            else ITEM_MONSTER_LORE_SNIPPET_CHARS
        )
        for entity in grouped[entity_type]:
            snippet = entity.lore[:snippet_len] if entity.lore else ""
            lines.append(f"- {entity_type} {entity.name}: {snippet}")
            appended_any = True

    if not appended_any:
        # A prior map with no entities at all carries no useful context;
        # skip it entirely so the prompt stays compact.
        return ""

    return "\n".join(lines)


def _apply_cap(text: str, cap: int) -> str:
    """Hard-cut `text` to `cap` characters, replacing the tail with a
    truncation marker so downstream prompts know content was elided.

    The marker itself is included inside the cap budget — so the returned
    string is always `<= cap` characters long. If `text` already fits, it
    is returned unchanged.
    """

    if len(text) <= cap:
        return text

    # Pathological case: cap is smaller than the marker itself. Hard-cut
    # the text to `cap` and skip the marker — there's no room for it.
    if cap <= len(_TRUNCATION_MARKER):
        return text[:cap]

    keep = cap - len(_TRUNCATION_MARKER)
    return text[:keep] + _TRUNCATION_MARKER
