"""Shared tree-diff helper for the byte-determinism bar.

``DEFAULT_EXCLUDES`` are the observability artifacts documented as
OUTSIDE the byte-determinism contract, matched by basename anywhere in
the tree:

- ``bible.json`` — ``generated_at`` + scheduler state (the original,
  sole exemption).
- ``log.jsonl`` — the ``.canon/`` step log; wall-clock timestamps by
  design.
- ``generation_stats.json`` — call ordering is scheduler-shaped, and
  wired runs carry token/cost actuals.

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


def tree_files(
    root: Path, exclude: tuple[str, ...] = DEFAULT_EXCLUDES
) -> list[Path]:
    """Every file under *root* (relative paths), minus excluded basenames."""
    return sorted(
        p.relative_to(root)
        for p in root.rglob("*")
        if p.is_file() and p.name not in exclude
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
