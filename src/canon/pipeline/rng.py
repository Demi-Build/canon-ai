"""Deterministic RNG derivation for pipeline phases.

Phases that generate per-map (or, later, per-DAG-node) content must not
seed their sub-RNGs from ``hash()`` or from draws on a shared RNG:

- ``hash(str)`` is salted per process (PYTHONHASHSEED), so the same config
  seed produces different output on every run.
- Draws on a shared RNG consume state in iteration order, so reordering
  maps (or running nodes in parallel — Phase 2) changes every seed.

``derive_seed`` is a pure function of its inputs: same parts → same seed,
across processes, platforms, and call orders. Phase 2's orchestrator should
derive every parallel node's RNG this way — ``derive_rng(config.seed,
phase_name, node_id)``.
"""

from __future__ import annotations

import hashlib
import random


def derive_seed(*parts: object) -> int:
    """Stable 64-bit seed derived from *parts* via SHA-256.

    Parts are joined with ``:`` after ``str()`` conversion, so pass distinct
    discriminators (e.g. the phase name) to keep unrelated streams apart.
    """
    key = ":".join(str(p) for p in parts)
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def derive_rng(*parts: object) -> random.Random:
    """A ``random.Random`` seeded by ``derive_seed(*parts)``."""
    return random.Random(derive_seed(*parts))
