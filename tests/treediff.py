"""Shared tree-diff helper for the byte-determinism bar.

``DEFAULT_EXCLUDES`` are the observability artifacts documented as
OUTSIDE the byte-determinism contract, matched by basename anywhere in
the tree:

- ``bible.json`` — ``generated_at`` + scheduler state (the original,
  sole exemption). Row P0-10 makes the orchestrated scheduler the create
  DEFAULT (master §8 Q6), so a fresh create tree now carries one: the
  amended doctrine 7 reads "existing artifacts byte-identical; bible
  additive."
- ``generation_stats.json`` — call ordering is scheduler-shaped, and
  wired runs carry token/cost actuals.

``EXCLUDED_DIRS`` is the whole ``.canon/`` directory (P0 paper P.9 R14,
decided 2026-09-01), replacing the by-basename ``log.jsonl`` exemption it
subsumes. The instance registry (``.canon/registry.json``, stamped at
create by P0-10), the journal + its CAS objects and the step log are
per-instance observability and provenance — timestamped, id-bearing, and
outside canon's byte-determinism contract by the same reasoning that
always exempted the step log. The fixtures compare the EMITTED PACK TREE:
what the generator wrote, which is what a byte-identical claim is about.

Everything else in an output tree must be byte-identical across
same-command runs and across schedulers.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_EXCLUDES: tuple[str, ...] = (
    "bible.json",
    "log.jsonl",
    "generation_stats.json",
)

#: Directory names excluded wholesale, matched as a path component (P.9 R14).
EXCLUDED_DIRS: frozenset[str] = frozenset({".canon"})


def tree_files(
    root: Path,
    exclude: tuple[str, ...] = DEFAULT_EXCLUDES,
    exclude_dirs: frozenset[str] = EXCLUDED_DIRS,
) -> list[Path]:
    """Every file under *root* (relative paths), minus excluded basenames and
    anything under an excluded directory."""
    return sorted(
        p.relative_to(root)
        for p in root.rglob("*")
        if p.is_file()
        and p.name not in exclude
        and not (exclude_dirs & set(p.relative_to(root).parts[:-1]))
    )


def assert_trees_byte_identical(
    a: Path, b: Path, exclude: tuple[str, ...] = DEFAULT_EXCLUDES
) -> None:
    """Same file list, same bytes, modulo the excluded basenames."""
    files_a, files_b = tree_files(a, exclude), tree_files(b, exclude)
    assert files_a == files_b, (
        f"tree file lists differ:\n  only in {a}: "
        f"{sorted(set(files_a) - set(files_b))}\n  only in {b}: "
        f"{sorted(set(files_b) - set(files_a))}"
    )
    for rel in files_a:
        assert (a / rel).read_bytes() == (b / rel).read_bytes(), (
            f"{rel} differs"
        )
