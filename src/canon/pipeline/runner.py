"""Pipeline orchestrator — Phase protocol, PipelineContext, and run_pipeline."""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from canon.adapters import JsonOutputAdapter, OutputAdapter
from canon.persistence import IDAllocator
from canon.pipeline.stats import GenerationStats

logger = logging.getLogger(__name__)


@runtime_checkable
class Phase(Protocol):
    """A single pipeline stage.

    Any class with ``name: str`` and ``run(ctx: PipelineContext) -> None``
    satisfies this protocol. No inheritance required.
    """

    name: str

    def run(self, ctx: PipelineContext) -> None: ...


@dataclass
class PipelineContext:
    """Shared mutable state passed through all pipeline phases.

    Phases read from and write to ``bible`` (the primary output) and
    ``artifacts`` (inter-phase scratch space). The context also carries
    configuration, the LLM client, prompt set, and RNG.

    Fields typed as ``Any`` are optional integrations that not every
    pipeline needs — a validation-only pipeline needs no LLM client.
    """

    bible: Any  # Will be Bible once bible module exists
    config: Any  # Will be CanonConfig
    rng: random.Random
    stats: GenerationStats = field(default_factory=GenerationStats)
    llm: Any = None  # Will be LLMClient
    prompts: Any = None  # Will be PromptSet
    schemas: dict[str, Any] = field(default_factory=dict)
    archetypes: dict[str, Any] = field(default_factory=dict)
    checkers: list[Any] = field(default_factory=list)
    validators: list[Any] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)
    # Optional StepLog (canon.pipeline.steplog) — when attached, both
    # schedulers append node lifecycle events to .canon/log.jsonl.
    steplog: Any = None
    id_allocator: IDAllocator = field(
        default_factory=lambda: IDAllocator(
            bases={
                "npc": 1000,
                "item": 2000,
                "event": 3000,
                "quest": 4000,
                "monster": 5000,
                "class": 6000,
            }
        )
    )
    adapter: OutputAdapter | None = None

    def __post_init__(self) -> None:
        # Default adapter rooted at config.output_dir, falling back to "."
        # exactly as the phases historically did — contexts constructed
        # without an adapter behave identically to pre-adapter code.
        if self.adapter is None:
            self.adapter = JsonOutputAdapter(
                getattr(self.config, "output_dir", ".")
            )


def run_pipeline(
    phases: list[Phase],
    ctx: PipelineContext,
    persist_after_each: str | Path | None = None,
) -> Any:
    """Run phases sequentially, building a Bible incrementally.

    Phases are opt-in — canon has no default pipeline. Users compose the
    list of phases their game needs.

    If *persist_after_each* is a path, the bible is written to disk after
    every phase for crash-safety.

    Returns the Bible instance attached to *ctx*.
    """
    steplog = getattr(ctx, "steplog", None)
    if steplog is not None:
        steplog.emit(
            "run_start",
            scheduler="sequential",
            seed=str(getattr(ctx.config, "seed", "")),
            phases=len(phases),
        )
    for phase in phases:
        logger.info("=== Phase: %s ===", phase.name)
        if steplog is not None:
            steplog.emit("node_start", node=f"phase:{phase.name}")
        try:
            phase.run(ctx)
        except Exception as error:
            if steplog is not None:
                steplog.emit(
                    "node_failed",
                    node=f"phase:{phase.name}",
                    error=str(error),
                )
                steplog.emit("run_end", scheduler="sequential", ok=False)
            raise
        if steplog is not None:
            steplog.emit("node_done", node=f"phase:{phase.name}")

        if persist_after_each is not None and hasattr(ctx.bible, "persist"):
            ctx.bible.persist(str(persist_after_each))
            logger.info("Bible persisted after phase '%s'.", phase.name)

    if steplog is not None:
        steplog.emit("run_end", scheduler="sequential", ok=True)
    return ctx.bible


def run_phase(phase: Phase, ctx: PipelineContext) -> None:
    """Run a single phase on an existing context.

    Convenience wrapper for cradle's "re-run one phase" operation.
    """
    logger.info("=== Phase (single): %s ===", phase.name)
    phase.run(ctx)
