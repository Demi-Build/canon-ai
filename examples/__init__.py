# examples/ — concrete downstream consumers of canon (checkout-only material).
# The two built-in packs moved INTO the package at row P0-4 (canon.packs.*)
# and the pygame play harness followed on 2026-09-01 (canon.packs.platformer.play);
# what stays here is the MazeWorld runner scripts (they drive canon.packs.dungeon), the lava_world acceptance
# fixture, and shims for the documented `python examples/run_platformer_slice.py`
# and `python examples/platformer_play.py` commands. This package marker keeps
# `examples` importable as a package.
