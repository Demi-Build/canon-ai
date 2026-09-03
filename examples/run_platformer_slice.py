"""Shim — the slice runner moved INTO the package at row P0-4
(``canon.packs.platformer.run_slice``; ``python -m`` it). This file only keeps
the documented ``python examples/run_platformer_slice.py ...`` commands working."""
from canon.packs.platformer.run_slice import main

if __name__ == "__main__":
    raise SystemExit(main())
