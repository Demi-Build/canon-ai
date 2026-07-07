"""Synthetic DAG pipeline for orchestrator tests (in-process + CLI).

Graph: a -> (b, c) -> d   — a diamond, all nodes file-counter writers so
tests (and the CLI subprocess crash test) can count executions across
process boundaries.

Env knobs (subprocess tests):
- CANON_DAG_OUT: counter/output directory (ctx.config.output_dir)
- CANON_DAG_FAIL_B=1: node b raises (escalation path)
- CANON_DAG_KILL_B=1: node b hard-kills the process (crash-resume path)
"""

from __future__ import annotations

import os
import random
from pathlib import Path

from canon.config import CanonConfig
from canon.pipeline.orchestrator import Node
from canon.pipeline.runner import PipelineContext


def ctx_factory(bible) -> PipelineContext:
    out = os.environ.get("CANON_DAG_OUT", ".")
    return PipelineContext(
        bible=bible,
        config=CanonConfig(seed="dag", output_dir=Path(out)),
        rng=random.Random(0),
    )


def _bump(out: Path, node_id: str) -> None:
    path = out / f"{node_id}.count"
    count = int(path.read_text()) if path.exists() else 0
    path.write_text(str(count + 1))


class DiamondPhase:
    name = "diamond"

    def expand(self, ctx) -> list[Node]:
        out = Path(ctx.config.output_dir)
        out.mkdir(parents=True, exist_ok=True)

        def make(node_id: str, requires: list[str]) -> Node:
            def run(_ctx, _nid=node_id) -> None:
                _bump(out, _nid)
                if _nid == "b" and os.environ.get("CANON_DAG_KILL_B"):
                    os._exit(9)  # simulate a crash mid-run
                if _nid == "b" and os.environ.get("CANON_DAG_FAIL_B"):
                    raise RuntimeError("node b exploded")

            return Node(node_id=node_id, run=run, requires=requires)

        return [
            make("a", []),
            make("b", ["a"]),
            make("c", ["a"]),
            make("d", ["b", "c"]),
        ]


def phases_factory(ctx) -> list:
    return [DiamondPhase()]
