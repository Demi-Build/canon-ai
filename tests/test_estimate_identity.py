"""Row P0-7's identity gate — the platformer estimates are output-identical
across the estimator-core extraction.

``tests/fixtures/estimate_pre_p07/*.json`` were captured from the PRE-P0-7
``canon.packs.platformer.estimate`` (``world estimate`` / ``level estimate``
via the CLI, ``estimate_cradle`` / ``estimate_run`` via the API) for a fixed
parameter set. Each case replays through the NEW engine with the OLD unit
prices injected (image $0.04 flat, music $0.10, sfx $0.05, the old per-token
rows, the old model tiers — ``_old_prices.json``) and asserts byte-identical
JSON minus the additive §3.0-E keys. The live run (new prices) is checked
separately: every figure that moved is listed in ``test_live_prices_moved``.
"""

from __future__ import annotations

import json
import random
import tempfile
from pathlib import Path

import pytest

from canon import pricing
from canon.bible.models import Bible
from canon.config import CanonConfig
from canon.estimator import strip_additive
from canon.packs.platformer.estimate import estimate_cradle, estimate_run
from canon.pipeline.runner import PipelineContext
from tests.test_estimate import _build_tree

FIXTURES = Path(__file__).parent / "fixtures" / "estimate_pre_p07"
OLD = json.loads((FIXTURES / "_old_prices.json").read_text())


def _cases(kind: str) -> list[dict]:
    return [
        json.loads(p.read_text())
        for p in sorted(FIXTURES.glob("*.json"))
        if not p.name.startswith("_") and json.loads(p.read_text())["kind"] == kind
    ]


def _canon(d: dict) -> str:
    return json.dumps(d, sort_keys=True, indent=2)


@pytest.fixture
def old_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject the pre-P0-7 unit prices into canon.pricing's tables and the
    old model tiers into the platformer's models.json."""
    for row in pricing.IMAGE.values():
        monkeypatch.setitem(row, "usd", OLD["image_usd_per_call"])
        monkeypatch.setitem(row, "usd_high", OLD["image_usd_per_call"])
    monkeypatch.setitem(pricing.MUSIC["lyria-3-pro-preview"], "usd", OLD["music_usd_per_track"])
    monkeypatch.setitem(pricing.SFX["elevenlabs"], "usd", OLD["sfx_usd_per_event"])
    for model, per_token in OLD["llm_per_token"].items():
        row = pricing.LLM[model]
        monkeypatch.setitem(row, "input_per_1m", per_token["input"] * 1_000_000)
        monkeypatch.setitem(row, "output_per_1m", per_token["output"] * 1_000_000)
    models = {
        "model_tiers": OLD["model_tiers"],
        "agent_tiers": json.loads(
            (Path(__file__).parents[1] / "src/canon/packs/platformer/models.json").read_text()
        )["agent_tiers"],
    }
    tmp = Path(tempfile.mkdtemp()) / "models.json"
    tmp.write_text(json.dumps(models))
    monkeypatch.setenv("CANON_PLAT_MODELS", str(tmp))
    monkeypatch.delenv("CANON_PLAT_VLM_MODEL", raising=False)
    monkeypatch.delenv("CANON_PLAT_COST_MODEL", raising=False)


def _world(case: dict) -> dict:
    a = case["args"]
    return estimate_cradle(
        "world",
        counts={
            "num_stages": a["stages"], "num_levels": a["levels"],
            "num_enemies": a["enemies"], "num_items": a["items"],
        },
        backends={
            "llm": a["llm"], "image": a["image"], "music": a["music"],
            "sfx": a["sfx"], "vlm": a["vlm"],
        },
    )


@pytest.mark.parametrize("case", _cases("world"), ids=lambda c: c["case"])
def test_world_identity(old_prices: None, case: dict) -> None:
    assert _canon(strip_additive(_world(case))) == _canon(case["estimate"])


@pytest.mark.parametrize(
    "case", [c for c in _cases("level") if "width" in c["args"]], ids=lambda c: c["case"]
)
def test_level_width_identity(old_prices: None, case: dict, tmp_path: Path) -> None:
    a = case["args"]
    got = estimate_cradle(
        a["op"], pack_dir=tmp_path, level_id="__preview__", width=a["width"],
        axis=a["axis"], backends={"llm": a["llm"]},
    )
    assert _canon(strip_additive(got)) == _canon(case["estimate"])


@pytest.mark.parametrize("case", _cases("music"), ids=lambda c: c["case"])
def test_music_identity(old_prices: None, case: dict) -> None:
    got = estimate_cradle("music", backends={"music": case["args"]["music"]})
    assert _canon(strip_additive(got)) == _canon(case["estimate"])


def test_run_fresh_identity(old_prices: None, tmp_path: Path) -> None:
    case = next(c for c in _cases("run") if c["args"].get("mode") == "fresh")
    ctx = PipelineContext(
        bible=Bible.empty(seed="est"),
        config=CanonConfig(seed="est", output_dir=tmp_path),
        rng=random.Random(0),
    )
    got = estimate_run(ctx, [], Bible.empty(seed="est"))
    assert _canon(strip_additive(got)) == _canon(case["estimate"])


@pytest.fixture(scope="module")
def tree(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("identity") / "game"
    _build_tree(out)
    return out


def test_tree_cases_identity(old_prices: None, tree: Path) -> None:
    """The on-disk cases (asset animate, per-level ops on real levels, the
    targeted run hook) against the same deterministic fake tree the
    fixtures were captured from."""
    checked = 0
    for case in _cases("asset"):
        a = case["args"]
        got = estimate_cradle(
            "animate", pack_dir=tree, target=a["target"],
            backends={"image": a["image"], "vlm": a["vlm"]},
            reuse_spec=bool(a.get("reuse_spec")),
        )
        assert _canon(strip_additive(got)) == _canon(case["estimate"]), case["case"]
        checked += 1
    for case in [c for c in _cases("level") if "level_id" in c["args"]]:
        a = case["args"]
        got = estimate_cradle(
            a["op"], pack_dir=tree, level_id=a["level_id"], backends={"llm": a["llm"]},
        )
        assert _canon(strip_additive(got)) == _canon(case["estimate"]), case["case"]
        checked += 1
    assert checked >= 4


def test_run_tree_identity(old_prices: None, tree: Path) -> None:
    """The targeted ``canon estimate <bible> l2`` case through the hook: mark
    l2 stale on a copy, price the would-run subgraph."""
    import copy

    from canon.packs.platformer.dag import cli_ctx_factory, cli_phases_factory
    from canon.pipeline.orchestrator import build_nodes, detect_edits, initial_skips, mark_stale, pinned_ids

    case = next(c for c in _cases("run") if c["args"].get("targets") == ["l2"])
    bible = Bible.load(tree / "bible.json")
    work = copy.deepcopy(bible)
    mark_stale(work, ["l2"])
    import os

    os.environ["CANON_PLAT_OUT"] = str(tree)
    os.environ["CANON_PLAT_SEED"] = "emberfall_001"
    try:
        ctx = cli_ctx_factory(work)
        nodes = build_nodes(cli_phases_factory(ctx), ctx)
        detect_edits(work, ctx.config.output_dir)
        node_map = {n.node_id: n for n in nodes}
        skips = initial_skips(node_map, work.metadata.node_status, pinned_ids(work))
        to_run = [n for n in nodes if n.node_id not in skips]
        got = estimate_run(ctx, to_run, work)
    finally:
        os.environ.pop("CANON_PLAT_OUT", None)
        os.environ.pop("CANON_PLAT_SEED", None)
    assert _canon(strip_additive(got)) == _canon(case["estimate"])


# ---------------------------------------------------------------------------
# The live run — the NEW prices. Every figure that moved, and why.
# ---------------------------------------------------------------------------


def test_additive_keys_present() -> None:
    got = estimate_cradle(
        "world", counts={}, backends={"llm": "anthropic", "image": "fal", "music": "lyria",
                                      "sfx": "elevenlabs", "vlm": "anthropic"},
    )
    for key in ("low", "high", "backend", "model", "unitCount", "accuracy", "template"):
        assert key in got, key
    assert got["low"] == got["total_usd"]["best"]
    assert got["high"] == got["total_usd"]["worst"]
    assert got["backend"] == "anthropic"
    assert got["template"] == "platformer"
    assert got["accuracy"] == "estimated"
    for block in ("images", "music", "sfx", "vlm"):
        for key in ("low", "high", "backend", "unitCount", "accuracy"):
            assert key in got["assets"][block], (block, key)
    assert got["assets"]["images"]["model"] == "fal-ai/nano-banana"
    assert got["assets"]["music"]["model"] == "lyria-3-pro-preview"
    assert got["assets"]["sfx"]["model"] == "elevenlabs"
    assert got["unitCount"] == (
        round(got["llm"]["calls"]) + got["assets"]["images"]["count"]
        + got["assets"]["music"]["count"] + got["assets"]["sfx"]["count"]
        + got["assets"]["vlm"]["unitCount"]
    )


def test_live_prices_moved(monkeypatch: pytest.MonkeyPatch) -> None:
    """The sanctioned estimate-output changes (user decision 2026-09-01), on
    the default paid world case (3/9/7/5, anthropic/fal/lyria/elevenlabs/
    anthropic):

    - images: 109 × $0.04 = $4.36 → 109 × $0.039 (fal nano-banana) = $4.251
    - music:    3 × $0.10 = $0.30 →   3 × $0.08 (Lyria 3 Pro)      = $0.24
    - sfx:      4 × $0.05 = $0.20 →   4 × $0.04 (ElevenLabs)        = $0.16
    - mid-tier tasks (world/stage/style/layout) claude-sonnet-4-6 ($3/$15)
      → claude-sonnet-5 ($2/$10): llm best $1.212 → cheaper; cheap-tier
      (haiku) and the VLM judge (DEFAULT_MODEL sonnet-4-6) unchanged.
    """
    monkeypatch.delenv("CANON_PLAT_MODELS", raising=False)
    monkeypatch.delenv("CANON_PLAT_VLM_MODEL", raising=False)
    case = next(c for c in _cases("world") if c["case"] == "w_default_paid")
    old = case["estimate"]
    new = _world(case)
    assert new["assets"]["images"]["count"] == old["assets"]["images"]["count"] == 109
    assert new["assets"]["images"]["usd"] == pytest.approx(109 * 0.039)
    assert new["assets"]["music"]["usd"] == pytest.approx(3 * 0.08)
    assert new["assets"]["sfx"]["usd"] == pytest.approx(4 * 0.04)
    assert old["assets"]["images"]["usd"] == 4.36
    assert old["assets"]["music"]["usd"] == 0.3
    assert old["assets"]["sfx"]["usd"] == 0.2
    # model tier bump: mid tasks now price at sonnet-5, cheaper than 4-6
    for task in ("plat:world", "plat:stage", "plat:style", "plat:layout"):
        assert new["llm"]["by_task"][task]["model"] == "claude-sonnet-5"
        assert new["llm"]["by_task"][task]["usd"] < old["llm"]["by_task"][task]["usd"]
    for task in ("plat:enemies", "plat:items", "plat:placement", "plat:item_placement", "plat:decorator"):
        assert new["llm"]["by_task"][task] == old["llm"]["by_task"][task]
    assert new["assets"]["vlm"]["model"] == old["assets"]["vlm"]["model"] == "claude-sonnet-4-6"
    assert new["assets"]["vlm"]["usd"] == old["assets"]["vlm"]["usd"]
    # the call counts and every count are untouched by a price change
    assert new["llm"]["calls"] == old["llm"]["calls"]
    assert new["total_usd"]["best"] < old["total_usd"]["best"]
