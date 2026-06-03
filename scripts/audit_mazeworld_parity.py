#!/usr/bin/env python3
"""Parity audit — load canon-generated data through MazeWorld's *real* models.

This is the rigorous version of the manual spot-checks: for every generated
file it (1) constructs MazeWorld's actual model/loader from the canon record,
and (2) cross-references a real MazeWorld dataset (the captured fixtures) to
distinguish "canon under-populates a field MazeWorld normally has" from
"optional field that's fine to default".

It reports three gap classes:

  ERROR    — the model/loader raises on the canon record (hard incompatibility).
  IGNORED  — canon emits a key the model has no field for (likely a rename gap,
             e.g. classes emit ``stat_template`` but PlayerClass wants ``stats``).
  UNDERPOP — a field the reference dataset populates broadly but canon leaves
             defaulted (e.g. PlayerClass.stats falling back to all-10s).

Read-only against both repos. MazeWorld is imported via --mazeworld on sys.path;
nothing in MazeWorld is modified.

Usage:
    python scripts/audit_mazeworld_parity.py \
        --data-dir /tmp/canon_run \
        --mazeworld /Users/you/Documents/projects/MazeWorld
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


def setup_mazeworld(mazeworld: Path) -> None:
    """Put MazeWorld on sys.path so `import config` / `import src...` resolve.

    Headless SDL drivers are set defensively in case any import path touches
    pygame (the model modules don't, but loaders pull siblings).
    """
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    sys.path.insert(0, str(mazeworld))


def prime_registry(data_dir: Path) -> None:
    """Populate the bits of MazeWorld's global registry that loaders read.

    ``create_event_from_data`` resolves combat monsters via
    ``registry.monster_registry``; without this the audit would report
    spurious AttributeErrors that aren't canon data problems.
    """
    try:
        from src.registry import registry
    except Exception:  # noqa: BLE001
        return
    registry.monster_registry = {}
    registry.item_registry = {}
    mpath = data_dir / "monsters" / "monsters.json"
    if mpath.exists():
        data = json.loads(mpath.read_text(encoding="utf-8"))
        registry.monster_registry = {int(k): v for k, v in data.items()}


def load_records(path: Path, kind: str) -> list[dict]:
    """Load a data file into a flat list of record dicts.

    kind: "array" | "single" | "keyed" | "pools".
    """
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if kind == "array":
        return [r for r in data if isinstance(r, dict)]
    if kind == "single":
        return [data] if isinstance(data, dict) else []
    if kind == "keyed":
        return [v for v in data.values() if isinstance(v, dict)]
    if kind == "pools":
        out: list[dict] = []
        for v in data.values():
            if isinstance(v, list):
                out.extend(r for r in v if isinstance(r, dict))
        return out
    return []


# ---------------------------------------------------------------------------
# Per-record introspection
# ---------------------------------------------------------------------------


@dataclass
class RecordStats:
    n: int = 0
    errors: list[str] = field(default_factory=list)
    set_counts: Counter = field(default_factory=Counter)  # model field -> # records that set it
    ignored_counts: Counter = field(default_factory=Counter)  # canon key -> # records with it
    model_fields: set[str] = field(default_factory=set)


def audit_records(records: list[dict], construct: Callable[[dict], Any]) -> RecordStats:
    """Construct each record through `construct` and collect field-set stats."""
    rs = RecordStats()
    for record in records:
        rs.n += 1
        raw_keys = set(record.keys())
        try:
            inst = construct(record)
        except Exception as exc:  # noqa: BLE001 — we want every failure reason
            msg = f"{type(exc).__name__}: {exc}"
            rs.errors.append(msg[:300])
            continue
        mfields = set(getattr(type(inst), "model_fields", {}).keys())
        rs.model_fields |= mfields
        set_fields = set(getattr(inst, "__pydantic_fields_set__", set()))
        rs.set_counts.update(set_fields & mfields)
        # Canon keys the model has no field for (rename / dead-data gaps).
        rs.ignored_counts.update(raw_keys - mfields)
    return rs


# ---------------------------------------------------------------------------
# Construct-function builders (lazy imports so one missing loader is non-fatal)
# ---------------------------------------------------------------------------


def build_constructors() -> dict[str, Callable[[dict], Any]]:
    ctor: dict[str, Callable[[dict], Any]] = {}

    from src.models.player import PlayerClass
    from src.models.story import OverarchingStory

    ctor["classes"] = lambda r: PlayerClass(**r)
    ctor["story"] = lambda r: OverarchingStory(**r)

    from src.models.npc import NPC, AggressiveNPC, MerchantNPC, RandomNPC, StaticNPC

    npc_map = {
        "StaticNPC": StaticNPC,
        "RandomNPC": RandomNPC,
        "AggressiveNPC": AggressiveNPC,
        "MerchantNPC": MerchantNPC,
    }

    def make_npc(r: dict) -> Any:
        cls = npc_map.get(r.get("type", "StaticNPC"), NPC)
        kwargs = {k: v for k, v in r.items() if k != "selected"}
        return cls(**kwargs)

    ctor["npcs"] = make_npc

    from src.models.encounter import create_event_from_data

    ctor["events"] = lambda r: create_event_from_data(r)

    from src.models.quest import create_quest_from_data

    ctor["quests"] = lambda r: create_quest_from_data(r)

    from src.models.monster import Monster

    ctor["monsters"] = lambda r: Monster(**r)

    from src.models.spell import Spell

    ctor["spell_pools"] = lambda r: Spell(**r)

    return ctor


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


@dataclass
class Check:
    name: str
    rel_path: str
    kind: str


CHECKS = [
    Check("classes", "classes/classes.json", "array"),
    Check("story", "story/story.json", "single"),
    Check("npcs", "npcs/npcs.json", "array"),
    Check("events", "events/events.json", "array"),
    Check("quests", "quests/quests.json", "array"),
    Check("monsters", "monsters/monsters.json", "keyed"),
    Check("spell_pools", "classes/spell_pools.json", "pools"),
]


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

_UNDERPOP_REF_MIN = 0.5   # field must be populated in >=50% of reference records
_UNDERPOP_GAP = 0.3       # ...and canon must lag the reference rate by >=30pts


def compare_and_report(name: str, canon: RecordStats, ref: RecordStats, ref_keys: set[str]) -> dict:
    """Print a per-file report. Return a summary dict for the final verdict.

    ``ref_keys`` is the union of keys seen across reference records.  An ignored
    key that the reference *also* emits isn't a canon gap (canon is matching the
    real data shape; the model simply doesn't store it), so we only flag ignored
    keys canon invents that the reference never emits.
    """
    print(f"\n=== {name} ===")
    print(f"  canon records: {canon.n}   reference records: {ref.n}")

    errors = list(canon.errors)
    invented = sorted(
        ((k, c) for k, c in canon.ignored_counts.items() if k not in ref_keys),
        key=lambda kv: -kv[1],
    )

    underpop: list[tuple[str, float, float]] = []
    if ref.n:
        for fname in sorted(ref.model_fields):
            ref_rate = ref.set_counts.get(fname, 0) / ref.n
            canon_rate = (canon.set_counts.get(fname, 0) / canon.n) if canon.n else 0.0
            if ref_rate >= _UNDERPOP_REF_MIN and (ref_rate - canon_rate) >= _UNDERPOP_GAP:
                underpop.append((fname, ref_rate, canon_rate))

    if errors:
        uniq = Counter(errors)
        print(f"  ERROR  ({len(errors)} records failed to construct):")
        for msg, cnt in uniq.most_common(5):
            print(f"    [{cnt}x] {msg}")
    if invented:
        print("  CANON-ONLY KEYS (canon emits, model ignores, reference never emits):")
        for key, cnt in invented:
            print(f"    {key}  ({cnt}/{canon.n} records)")
    if underpop:
        print("  UNDERPOP (reference populates, canon defaults — likely a real gap):")
        for fname, rr, cr in underpop:
            print(f"    {fname}: reference {rr:.0%} vs canon {cr:.0%}")
    if not (errors or invented or underpop):
        print("  OK — constructs cleanly, matches reference field coverage.")

    return {
        "name": name,
        "errors": len(errors),
        "ignored": [k for k, _ in invented],
        "underpop": [f for f, _, _ in underpop],
    }


def audit_maze(data_dir: Path, ref_dir: Path) -> dict:
    """Maze isn't a per-record Pydantic model; check load + key parity + that
    placement arrays are non-empty (the bug we just fixed)."""
    print("\n=== maze (rooms/*/maze.json) ===")
    from src.models.maze import Maze

    canon_rooms = sorted((data_dir / "rooms").glob("*/maze.json"))
    if not canon_rooms:
        print("  (no rooms found)")
        return {"name": "maze", "errors": 1, "ignored": [], "underpop": []}

    ref_rooms = sorted((ref_dir / "rooms").glob("*/maze.json"))
    ref_keys: set[str] = set()
    for rp in ref_rooms:
        ref_keys |= set(json.loads(rp.read_text()).keys())

    errors = 0
    empty_placements = 0
    missing_keys_total: set[str] = set()
    for rp in canon_rooms:
        try:
            Maze.load_from_json(str(rp))
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR loading {rp.parent.name}: {type(exc).__name__}: {exc}")
            errors += 1
            continue
        data = json.loads(rp.read_text())
        if ref_keys:
            missing_keys_total |= (ref_keys - set(data.keys()))
        placed = (
            len(data.get("npc_positions", {}))
            + len(data.get("event_positions", []))
            + len(data.get("item_placements", []))
        )
        if placed == 0:
            empty_placements += 1

    if errors:
        print(f"  ERROR — {errors} maze files failed Maze.load_from_json")
    if missing_keys_total:
        print(f"  MISSING KEYS (reference has, canon omits): {sorted(missing_keys_total)}")
    if empty_placements:
        print(f"  EMPTY PLACEMENTS — {empty_placements}/{len(canon_rooms)} rooms have no npc/event/item placements")
    if not (errors or missing_keys_total or empty_placements):
        print(f"  OK — {len(canon_rooms)} rooms load, keys match reference, placements present.")

    return {
        "name": "maze",
        "errors": errors + empty_placements,
        "ignored": [],
        "underpop": sorted(missing_keys_total),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, help="canon-generated data dir (e.g. /tmp/canon_run)")
    parser.add_argument("--mazeworld", required=True, help="path to the MazeWorld repo (for importing its models)")
    parser.add_argument(
        "--reference",
        default=str(repo_root / "tests/reference/fixtures/cradle_mazeworld_scifi"),
        help="a real MazeWorld dataset to compare field coverage against",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    ref_dir = Path(args.reference)
    setup_mazeworld(Path(args.mazeworld))

    print(f"Auditing canon data : {data_dir}")
    print(f"Against MazeWorld    : {args.mazeworld}")
    print(f"Reference dataset    : {ref_dir}")

    try:
        ctor = build_constructors()
    except Exception as exc:  # noqa: BLE001
        print(f"\nFATAL: could not import MazeWorld models: {type(exc).__name__}: {exc}")
        return 2

    prime_registry(data_dir)

    summaries: list[dict] = []
    for check in CHECKS:
        construct = ctor.get(check.name)
        if construct is None:
            continue
        canon_records = load_records(data_dir / check.rel_path, check.kind)
        ref_records = load_records(ref_dir / check.rel_path, check.kind)
        ref_keys = {k for r in ref_records for k in r.keys()}
        canon = audit_records(canon_records, construct)
        ref = audit_records(ref_records, construct)
        summaries.append(compare_and_report(check.name, canon, ref, ref_keys))

    summaries.append(audit_maze(data_dir, ref_dir))

    # Verdict
    print("\n" + "=" * 64)
    total_err = sum(s["errors"] for s in summaries)
    total_ignored = sorted({k for s in summaries for k in s["ignored"]})
    total_underpop = [(s["name"], f) for s in summaries for f in s["underpop"]]
    print("PARITY SUMMARY")
    print(f"  files with hard errors / empty placements : {sum(1 for s in summaries if s['errors'])}")
    print(f"  rename gaps (ignored keys)                : {total_ignored or 'none'}")
    print(f"  under-populated fields                    : {total_underpop or 'none'}")
    print("=" * 64)
    return 1 if (total_err or total_ignored or total_underpop) else 0


if __name__ == "__main__":
    raise SystemExit(main())
