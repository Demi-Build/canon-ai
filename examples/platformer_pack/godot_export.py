"""GodotExportPhase — drops the Godot project template into the output tree.

The generated data tree IS the Godot project: ``project.godot`` lands at
the output root (``res://`` cannot escape the project root, so this puts
every generated artifact in reach), and the game scene/script live under
``godot/``. Static template files are pack data
(``examples/platformer_pack/godot_template/``) copied through the adapter
like any other artifact.

Composed only when the runner is invoked with ``--engine godot``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from canon.bible.models import BibleMetadata

TEMPLATE_DIR = Path(__file__).parent / "godot_template"

logger = logging.getLogger(__name__)


class GodotExportPhase:
    name = "plat:godot_export"

    def run(self, ctx: Any) -> None:
        written = 0
        for source in sorted(TEMPLATE_DIR.rglob("*")):
            if not source.is_file():
                continue
            rel = source.relative_to(TEMPLATE_DIR).as_posix()
            ctx.adapter.write_binary(rel, source.read_bytes())
            written += 1

        logger.info(
            "GodotExportPhase wrote %d project files — open the output "
            "directory in Godot 4.3+ and press Play.", written,
        )
        if not isinstance(getattr(ctx.bible, "metadata", None), BibleMetadata):
            ctx.bible.metadata = BibleMetadata()
        ctx.bible.metadata.phases_run.append(self.name)
