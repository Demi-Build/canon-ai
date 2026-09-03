"""Arc-2 Chunk 0 data knobs: coyote time + enemy death-linger.

Both are play-feel numbers the consumers read from the manifest; the
mechanics are input-layer forgiveness / presentation-layer linger, so
the suite pins the DATA contract (spec fields, defaults, manifest
carriage) — behavioral parity is proven by PLAT_TRAJ/PLAT_CAPTURE runs
per the verification bar (coyote makes play MORE forgiving than the
reachability sim, never less, so validation is untouched by design).
"""

from __future__ import annotations

from canon.packs.platformer.movement import DEFAULT_MOVEMENT
from canon.packs.platformer.rules import DEFAULT_RULES, load_rules


class TestCoyoteKnob:
    def test_spec_default(self) -> None:
        assert DEFAULT_MOVEMENT.coyote_s == 0.08

    def test_manifest_carries_it(self) -> None:
        # Both consumers read manifest["movement"]["coyote_s"] with a 0.0
        # fallback — old manifests keep the no-coyote behavior.
        assert DEFAULT_MOVEMENT.model_dump()["coyote_s"] == 0.08


class TestDeathLingerKnob:
    def test_rules_default_and_template(self) -> None:
        assert DEFAULT_RULES.death_linger_s == 0.5
        assert load_rules().model_dump()["death_linger_s"] == 0.5
