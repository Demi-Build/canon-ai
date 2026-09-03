"""Shim — the pygame play harness moved INTO the package (2026-09-01,
``canon.packs.platformer.play``; ``python -m`` it) so a bundled cradle can
▶ Play a level before W2.0. Extends the row P0-4 promotion the same way
``run_platformer_slice.py`` does. This file only keeps the documented
``python examples/platformer_play.py <data_dir> [level_id]`` commands (and
every ``PLAT_*`` env hook) working — the harness itself lives in the wheel."""
from canon.packs.platformer.play import main

if __name__ == "__main__":
    raise SystemExit(main())
