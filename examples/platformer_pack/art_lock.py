"""ArtLock — the FROZEN generation config of the sample→lock→batch loop.

Sample mode (art_sampling.py) explores one exemplar across backends;
when the human approves one, the winning configuration is written down
HERE — backend, model, seed, the lane file, frozen prompt tokens and
palette — with ``status: "approved"``. Batch generation then runs
against the lock instead of ad-hoc flags: everything downstream
generates the way the approved exemplar did, and a batch run is
reproducible because the config is data and the seed is pinned.

The lock is a hand-editable JSON (``--art-lock <path>`` on the runner).
It is NOT re-litigated per asset; changing it is an explicit edit.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ArtLock(BaseModel):
    model_config = ConfigDict(extra="allow")

    #: The human's verdict on the locked exemplar. Batch generation
    #: REFUSES a lock that isn't "approved" — locking is the approval.
    status: Literal["draft", "approved", "rejected", "needs-rework"] = "draft"
    #: The chosen image backend name (fal / retro / pixellab / local).
    backend: str = ""
    #: Model id override ("" = the backend's default).
    model: str = ""
    #: img2img (animation-sheet) model override ("" = backend default).
    edit_model: str = ""
    #: Pinned generation seed — backends declaring the "seeds"
    #: capability re-run identically; others ignore it (recorded in
    #: provenance either way).
    seed: int | None = None
    #: The lane spec this lock froze (path, informational — the runner
    #: still takes --graphics; a mismatch is warned, not fatal).
    graphics: str = ""
    #: Frozen aesthetic tokens (override the lane's at batch time when
    #: non-empty — the sampled prompt is what was approved).
    prompt_tokens: list[str] = Field(default_factory=list)
    #: Frozen role→hex palette (override the style agent's when
    #: non-empty — cross-asset color coherence pinned to the exemplar).
    palette: dict[str, str] = Field(default_factory=dict)
    #: Stamped at lock time (informational provenance).
    pipeline_version: str = ""


def load_art_lock(path: str | Path) -> ArtLock:
    """Load a lock file. The caller decides how strictly to treat a
    non-approved status (the runner refuses batch generation on it)."""
    return ArtLock.model_validate(json.loads(Path(path).read_text()))
