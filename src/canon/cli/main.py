"""Canon CLI — JSON-emitting subprocess interface for cradle and other tooling.

Every command emits JSON on stdout. Errors are emitted as JSON on stderr with
non-zero exit codes. Every response includes a top-level "canon_version" field
so downstream tools can pin to a schema version.

Optional dependency: install with `pip install canon-ai[cli]`.
"""

from __future__ import annotations

import copy
import importlib
import json
import sys
import traceback
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_meta_version
from pathlib import Path
from typing import Any

import typer

from canon.bible.models import Bible

CANON_VERSION = "0.1"
app = typer.Typer(
    name="canon",
    help="Canon — coherence layer for AI-generated structured content.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,  # we emit clean JSON errors ourselves
)


def _emit(data: dict, *, file=sys.stdout) -> None:
    """Emit a JSON object with canon_version stamped."""
    payload = {"canon_version": CANON_VERSION, **data}
    print(json.dumps(payload, default=str, indent=2), file=file)


def _emit_error(msg: str, *, exit_code: int = 1, **extra) -> None:
    payload = {"error": msg, **extra}
    _emit(payload, file=sys.stderr)
    raise typer.Exit(exit_code)


def _resolve_module_attr(spec: str) -> Any:
    """Resolve 'module.path:attr' into the attribute value."""
    if ":" not in spec:
        raise ValueError(f"Invalid module:attr spec: {spec!r}")
    module_path, attr = spec.split(":", 1)
    module = importlib.import_module(module_path)
    return getattr(module, attr)


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    version_flag: bool = typer.Option(False, "--version", help="Print version and exit."),
) -> None:
    if version_flag:
        try:
            package_version = pkg_meta_version("canon-ai")
        except PackageNotFoundError:
            package_version = "unknown"
        _emit({"package_version": package_version})
        raise typer.Exit(0)
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(0)


bible_app = typer.Typer(help="Bible inspection and validation.")
app.add_typer(bible_app, name="bible")


@bible_app.command("load")
def bible_load(path: Path = typer.Argument(..., help="Path to bible JSON file.")) -> None:
    """Load a bible JSON file and dump it to stdout."""
    if not path.exists():
        _emit_error(f"File not found: {path}", path=str(path))
    try:
        bible = Bible.load(path)
    except Exception as e:
        _emit_error(f"Failed to load bible: {e}", path=str(path))
    _emit({"bible": bible.model_dump(mode="json")})


@bible_app.command("validate")
def bible_validate(
    path: Path = typer.Argument(..., help="Path to bible JSON file."),
    checkers: list[str] = typer.Option([], "--checkers", help="module:attr resolving to list[BaseChecker]"),
    validators: list[str] = typer.Option([], "--validators", help="module:attr resolving to list[BaseValidator]"),
) -> None:
    """Run 3-stage validation on a bible and emit the report."""
    try:
        from canon.pipeline.phases import validate_bible
    except ImportError as e:
        _emit_error(f"Failed to import validation: {e}")

    if not path.exists():
        _emit_error(f"File not found: {path}", path=str(path))
    try:
        bible = Bible.load(path)
    except Exception as e:
        _emit_error(f"Failed to load bible: {e}", path=str(path))

    checker_list: list[Any] = []
    for spec in checkers:
        try:
            checker_list.extend(list(_resolve_module_attr(spec)))
        except Exception as e:
            _emit_error(f"Failed to resolve checker spec {spec!r}: {e}")

    validator_list: list[Any] = []
    for spec in validators:
        try:
            validator_list.extend(list(_resolve_module_attr(spec)))
        except Exception as e:
            _emit_error(f"Failed to resolve validator spec {spec!r}: {e}")

    try:
        report = validate_bible(bible, checkers=checker_list, validators=validator_list)
    except Exception as e:
        _emit_error(f"Validation failed: {e}", traceback=traceback.format_exc())

    _emit({"report": report.to_dict()})


def _build_llm() -> Any:
    """Build an LLM client from the registered anthropic backend."""
    try:
        from canon.backends.registry import BackendRegistry
        from canon.llm.client import LLMClient
    except ImportError as e:
        _emit_error(f"Failed to import LLM client: {e}")

    try:
        backend = BackendRegistry.llm("anthropic")
    except KeyError:
        # Try registering anthropic on demand
        try:
            from canon.backends.anthropic import register

            register()
            backend = BackendRegistry.llm("anthropic")
        except Exception as e:
            _emit_error(
                "No LLM backend registered. Install canon-ai[anthropic] and set ANTHROPIC_API_KEY.",
                detail=str(e),
            )

    return LLMClient(backend)  # type: ignore[possibly-unbound]


def _build_prompts() -> Any:
    try:
        from canon.llm.prompts import DefaultPromptSet
    except ImportError as e:
        _emit_error(f"Failed to import prompts: {e}")
    return DefaultPromptSet()  # type: ignore[possibly-unbound]


@app.command("reroll")
def reroll(
    path: Path = typer.Argument(..., help="Path to bible JSON file."),
    map_id: str = typer.Option(..., "--map", help="Map ID containing the entity."),
    entity_id: str = typer.Option(..., "--entity", help="Entity ID to reroll."),
) -> None:
    """Re-roll the flavor text of an existing entity."""
    try:
        from canon.ops import reroll_entity_flavor
    except ImportError as e:
        _emit_error(f"Failed to import ops module: {e}")

    if not path.exists():
        _emit_error(f"File not found: {path}", path=str(path))
    try:
        bible = Bible.load(path)
        llm = _build_llm()
        prompts = _build_prompts()
        new_entity = reroll_entity_flavor(bible, map_id, entity_id, llm, prompts)  # type: ignore[possibly-unbound]
        bible.persist(path)
        _emit({"entity": new_entity.model_dump(mode="json")})
    except KeyError as e:
        _emit_error(f"Entity not found: {e}", map_id=map_id, entity_id=entity_id)
    except Exception as e:
        _emit_error(f"Reroll failed: {e}", traceback=traceback.format_exc())


@app.command("regenerate")
def regenerate(
    path: Path = typer.Argument(..., help="Path to bible JSON file."),
    map_id: str = typer.Option(..., "--map", help="Map ID containing the entity."),
    entity_id: str = typer.Option(..., "--entity", help="Entity ID to regenerate."),
    spec: str = typer.Option(..., "--spec", help="module:attr resolving to a SkeletonSpec"),
) -> None:
    """Regenerate an entity with a fresh skeleton + flavor."""
    try:
        from canon.ops import regenerate_entity
    except ImportError as e:
        _emit_error(f"Failed to import ops module: {e}")

    if not path.exists():
        _emit_error(f"File not found: {path}", path=str(path))
    try:
        bible = Bible.load(path)
        skeleton_spec = _resolve_module_attr(spec)
        llm = _build_llm()
        prompts = _build_prompts()
        new_entity = regenerate_entity(bible, map_id, entity_id, llm, prompts, skeleton_spec)  # type: ignore[possibly-unbound]
        bible.persist(path)
        _emit({"entity": new_entity.model_dump(mode="json")})
    except KeyError as e:
        _emit_error(f"Entity not found: {e}", map_id=map_id, entity_id=entity_id)
    except Exception as e:
        _emit_error(f"Regenerate failed: {e}", traceback=traceback.format_exc())


@app.command("generate")
def generate(
    path: Path = typer.Argument(..., help="Path to bible JSON file."),
    map_id: str = typer.Option(..., "--map", help="Map ID to add the entity to."),
    entity_type: str = typer.Option(..., "--entity-type", help="Type of entity to generate."),
    spec: str = typer.Option(..., "--spec", help="module:attr resolving to a SkeletonSpec"),
    entity_id: str | None = typer.Option(None, "--entity-id", help="Optional explicit entity ID."),
) -> None:
    """Generate a new entity and add it to the given map."""
    try:
        from canon.ops import generate_entity
    except ImportError as e:
        _emit_error(f"Failed to import ops module: {e}")

    if not path.exists():
        _emit_error(f"File not found: {path}", path=str(path))
    try:
        bible = Bible.load(path)
        skeleton_spec = _resolve_module_attr(spec)
        llm = _build_llm()
        prompts = _build_prompts()
        new_entity = generate_entity(  # type: ignore[possibly-unbound]
            bible,
            map_id,
            entity_type,
            llm,
            prompts,
            skeleton_spec,
            entity_id=entity_id,
        )
        bible.persist(path)
        _emit({"entity": new_entity.model_dump(mode="json")})
    except Exception as e:
        _emit_error(f"Generate failed: {e}", traceback=traceback.format_exc())


@app.command("phase")
def run_single_phase(
    phase_name: str = typer.Argument(..., help="Phase class name (e.g. EntityPhase)."),
    path: Path = typer.Argument(..., help="Path to bible JSON file."),
    phase_args: str = typer.Option("{}", "--phase-args", help="JSON dict of constructor kwargs for the phase."),
    pipeline: str | None = typer.Option(
        None,
        "--pipeline",
        help="module:attr resolving to a PipelineContext factory callable(bible) -> PipelineContext.",
    ),
) -> None:
    """Run a single named phase on an existing bible."""
    try:
        from canon.pipeline.runner import run_phase
    except ImportError as e:
        _emit_error(f"Failed to import pipeline runner: {e}")

    if not path.exists():
        _emit_error(f"File not found: {path}", path=str(path))

    try:
        args = json.loads(phase_args)
    except json.JSONDecodeError as e:
        _emit_error(f"Invalid JSON in --phase-args: {e}", phase_args=phase_args)

    if not pipeline:
        _emit_error("--pipeline is required to provide a PipelineContext")

    try:
        bible = Bible.load(path)
    except Exception as e:
        _emit_error(f"Failed to load bible: {e}", path=str(path))

    try:
        ctx_factory = _resolve_module_attr(pipeline)  # type: ignore[arg-type]
    except Exception as e:
        _emit_error(f"Failed to resolve --pipeline spec {pipeline!r}: {e}")

    try:
        ctx = ctx_factory(bible)  # ctx_factory takes the loaded bible
    except Exception as e:
        _emit_error(f"Failed to construct PipelineContext from factory: {e}")

    # Find the phase class: look up by name on canon.pipeline.phases
    try:
        from canon.pipeline import phases as _phases_module
    except ImportError as e:
        _emit_error(f"Failed to import phases module: {e}")

    phase_cls = getattr(_phases_module, phase_name, None)  # type: ignore[possibly-unbound]
    if phase_cls is None:
        available = [n for n in dir(_phases_module) if n.endswith("Phase")]  # type: ignore[possibly-unbound]
        _emit_error(
            f"Unknown phase: {phase_name!r}",
            available=available,
        )

    try:
        phase = phase_cls(**args) if args else phase_cls()  # type: ignore[possibly-unbound]
        run_phase(phase, ctx)  # type: ignore[possibly-unbound]
    except Exception as e:
        _emit_error(f"Phase run failed: {e}", traceback=traceback.format_exc())

    try:
        bible.persist(path)  # type: ignore[possibly-unbound]
    except Exception as e:
        _emit_error(f"Failed to persist bible after phase: {e}")

    _emit({"phase": phase_name, "result": "ok"})


# ---------------------------------------------------------------------------
# Orchestrator verbs (PRD §7.5): run / resume / status
# ---------------------------------------------------------------------------


def _orchestrated_run(
    path: Path,
    pipeline: str | None,
    phases_spec: str | None,
    max_concurrency: int | None,
    skip_edit_check: bool,
    extra_payload: dict | None = None,
) -> None:
    """Shared body of `canon run` and `canon resume` — resume IS run:
    completed nodes are skipped from bible.metadata.node_status."""
    try:
        from canon.pipeline.orchestrator import detect_edits, orchestrate
    except ImportError as e:
        _emit_error(f"Failed to import orchestrator: {e}")

    if not path.exists():
        _emit_error(f"File not found: {path}", path=str(path))
    if not pipeline:
        _emit_error("--pipeline is required (module:attr -> ctx factory)")
    if not phases_spec:
        _emit_error("--phases is required (module:attr -> phases factory)")

    try:
        bible = Bible.load(path)
    except Exception as e:
        _emit_error(f"Failed to load bible: {e}", path=str(path))

    try:
        ctx_factory = _resolve_module_attr(pipeline)  # type: ignore[arg-type]
        ctx = ctx_factory(bible)  # type: ignore[possibly-unbound]
    except Exception as e:
        _emit_error(f"Failed to build PipelineContext from --pipeline: {e}")

    try:
        phases_factory = _resolve_module_attr(phases_spec)  # type: ignore[arg-type]
        phases = phases_factory(ctx) if callable(phases_factory) else phases_factory  # type: ignore[possibly-unbound]
    except Exception as e:
        _emit_error(f"Failed to build phases from --phases: {e}")

    edit_report = None
    if not skip_edit_check:
        try:
            edits = detect_edits(  # type: ignore[possibly-unbound]
                bible, getattr(ctx.config, "output_dir", ".")  # type: ignore[possibly-unbound]
            )
            edit_report = edits.to_dict()
        except Exception as e:
            _emit_error(f"Edit detection failed: {e}", traceback=traceback.format_exc())

    try:
        report = orchestrate(  # type: ignore[possibly-unbound]
            phases, ctx, max_concurrency=max_concurrency, persist_path=path,  # type: ignore[possibly-unbound]
        )
    except Exception as e:
        _emit_error(f"Orchestrated run failed: {e}", traceback=traceback.format_exc())

    _emit(
        {
            "result": "ok" if report.ok else "incomplete",  # type: ignore[possibly-unbound]
            "report": report.to_dict(),  # type: ignore[possibly-unbound]
            "edit_detection": edit_report,
            "bible": str(path),
            **(extra_payload or {}),
        }
    )
    if not report.ok:  # type: ignore[possibly-unbound]
        raise typer.Exit(3)


@app.command("run")
def run_pipeline_cmd(
    path: Path = typer.Argument(..., help="Path to bible JSON file (state lives here)."),
    pipeline: str | None = typer.Option(
        None, "--pipeline",
        help="module:attr resolving to a PipelineContext factory callable(bible) -> ctx.",
    ),
    phases: str | None = typer.Option(
        None, "--phases",
        help="module:attr resolving to a phase list, or a callable(ctx) -> phase list.",
    ),
    max_concurrency: int | None = typer.Option(
        None, "--max-concurrency", help="Override config.max_concurrency for this run.",
    ),
    skip_edit_check: bool = typer.Option(
        False, "--skip-edit-check", help="Skip on-disk hash recompute / stale cascade.",
    ),
) -> None:
    """Run a pipeline through the DAG orchestrator (resume-aware)."""
    _orchestrated_run(path, pipeline, phases, max_concurrency, skip_edit_check)


@app.command("regen")
def regen_cmd(
    path: Path = typer.Argument(..., help="Path to bible JSON file."),
    targets: list[str] = typer.Argument(
        ...,
        help="What to regenerate: a level id (l2 — every step of that "
        "level), a step artifact id (level:<stage>/<lid>/entities), or a "
        "phase node id (phase:plat:style). Descendants re-run via the "
        "stale cascade; user-edited artifacts are never destroyed by the "
        "cascade (only an explicit target overrides an edit).",
    ),
    pipeline: str | None = typer.Option(None, "--pipeline"),
    phases: str | None = typer.Option(None, "--phases"),
    max_concurrency: int | None = typer.Option(None, "--max-concurrency"),
    mark_only: bool = typer.Option(
        False, "--mark-only",
        help="Mark targets stale and persist, but don't run — inspect "
        "with `canon status`, then `canon resume`.",
    ),
    field_ops: str | None = typer.Option(
        None, "--field-ops",
        help="module:attr resolving to a callable(ctx, target) -> dict "
        "for FIELD targets (parts of rows, '<artifact_id>#<field>' — "
        "e.g. enemy:ashwalker#flavor).",
    ),
) -> None:
    """Re-roll specific artifacts (mark stale + resume — only they
    re-run, PRD §7.5) or FIELDS within one ('#' targets, via --field-ops)."""
    try:
        from canon.pipeline.orchestrator import mark_stale
    except ImportError as e:
        _emit_error(f"Failed to import orchestrator: {e}")

    if not path.exists():
        _emit_error(f"File not found: {path}", path=str(path))
    try:
        bible = Bible.load(path)
    except Exception as e:
        _emit_error(f"Failed to load bible: {e}", path=str(path))

    node_targets = [t for t in targets if "#" not in t]
    field_targets = [t for t in targets if "#" in t]

    field_results: list[dict] = []
    if field_targets:
        if not pipeline:
            _emit_error("Field targets need --pipeline (LLM + adapter).")
        if not field_ops:
            _emit_error(
                "Field targets need --field-ops (module:attr -> "
                "callable(ctx, target) -> dict).",
                field_targets=field_targets,
            )
        try:
            handler = _resolve_module_attr(field_ops)  # type: ignore[arg-type]
            ctx = _resolve_module_attr(pipeline)(bible)  # type: ignore[arg-type,misc]
        except Exception as e:
            _emit_error(f"Failed to build field-regen context: {e}")
        for target in field_targets:
            try:
                field_results.append(handler(ctx, target))  # type: ignore[possibly-unbound]
            except KeyError as e:
                _emit_error(
                    str(e.args[0]) if e.args else str(e), target=target
                )
            except Exception as e:
                _emit_error(
                    f"Field regen failed for {target!r}: {e}",
                    traceback=traceback.format_exc(),
                )

    plan = None
    if node_targets:
        try:
            plan = mark_stale(bible, node_targets)  # type: ignore[possibly-unbound]
        except KeyError as e:
            _emit_error(
                str(e.args[0]) if e.args else str(e), targets=node_targets
            )
    try:
        bible.persist(path)  # type: ignore[possibly-unbound]
    except Exception as e:
        _emit_error(f"Failed to persist regen state: {e}")

    regen_payload = {
        "regen": plan.to_dict() if plan else None,
        "fields": field_results or None,
    }
    if mark_only or not node_targets:
        result = "marked" if (mark_only and node_targets) else "ok"
        _emit({"result": result, **regen_payload, "bible": str(path)})
        return
    _orchestrated_run(
        path, pipeline, phases, max_concurrency, skip_edit_check=False,
        extra_payload=regen_payload,
    )


@app.command("estimate")
def estimate_cmd(
    path: Path = typer.Argument(
        ..., help="Path to bible JSON file (may not exist yet — a missing "
        "file forecasts a full run from scratch).",
    ),
    targets: list[str] = typer.Argument(
        None,
        help="Optional regen targets (same grammar as `canon regen`) — "
        "the estimate then prices only the would-run subgraph.",
    ),
    pipeline: str | None = typer.Option(
        None, "--pipeline",
        help="module:attr resolving to a PipelineContext factory "
        "callable(bible) -> ctx.",
    ),
    phases: str | None = typer.Option(
        None, "--phases",
        help="module:attr resolving to a phase list, or a callable(ctx) "
        "-> phase list.",
    ),
    estimator: str | None = typer.Option(
        None, "--estimator",
        help="module:attr -> callable(ctx, nodes, bible) -> dict pricing "
        "the would-run nodes (e.g. examples.platformer_pack.estimate:"
        "estimate_run). Omitted: node counts only, no dollars.",
    ),
) -> None:
    """Forecast what a run would execute — and what it would cost —
    without generating anything (PRD §9.2). Never writes: the bible on
    disk is untouched (stale-marking for targets happens on a copy)."""
    try:
        from canon.pipeline.orchestrator import (
            build_nodes,
            detect_edits,
            initial_skips,
            mark_stale,
            pinned_ids,
        )
    except ImportError as e:
        _emit_error(f"Failed to import orchestrator: {e}")
    if not pipeline:
        _emit_error("--pipeline is required (module:attr -> ctx factory)")
    if not phases:
        _emit_error("--phases is required (module:attr -> phases factory)")

    if path.exists():
        try:
            bible = Bible.load(path)
        except Exception as e:
            _emit_error(f"Failed to load bible: {e}", path=str(path))
        # Estimate must never mutate run state — regen-target marking and
        # DAG expansion happen on a deep copy, and nothing persists.
        work = copy.deepcopy(bible)  # type: ignore[possibly-unbound]
    else:
        work = Bible.empty(seed="estimate")

    plan = None
    if targets:
        try:
            plan = mark_stale(work, list(targets))  # type: ignore[possibly-unbound]
        except KeyError as e:
            _emit_error(
                str(e.args[0]) if e.args else str(e), targets=list(targets)
            )

    try:
        ctx = _resolve_module_attr(pipeline)(work)  # type: ignore[arg-type,misc]
        phases_factory = _resolve_module_attr(phases)  # type: ignore[arg-type]
        phase_list = (
            phases_factory(ctx) if callable(phases_factory) else phases_factory
        )
        nodes = build_nodes(phase_list, ctx)  # type: ignore[possibly-unbound]
    except Exception as e:
        _emit_error(
            f"Failed to build the DAG for estimation: {e}",
            traceback=traceback.format_exc(),
        )

    # Every actual run path (run/resume/regen and the runner's resume)
    # runs edit detection before orchestrating — the forecast must see
    # the same hand-edit stale cascade or it under-prices edited trees.
    # detect_edits only mutates the in-memory copy; still no writes.
    edit_report = None
    if path.exists():
        try:
            edits = detect_edits(  # type: ignore[possibly-unbound]
                work, getattr(ctx.config, "output_dir", ".")  # type: ignore[possibly-unbound]
            )
            edit_report = edits.to_dict()
        except Exception as e:
            _emit_error(
                f"Edit detection failed: {e}",
                traceback=traceback.format_exc(),
            )

    node_map = {n.node_id: n for n in nodes}  # type: ignore[possibly-unbound]
    status = getattr(work.metadata, "node_status", {}) or {}
    skips = initial_skips(node_map, status, pinned_ids(work))  # type: ignore[possibly-unbound]
    to_run = [n for n in nodes if n.node_id not in skips]  # type: ignore[possibly-unbound]

    payload: dict = {
        "result": "estimate",
        "bible": str(path),
        "nodes": {
            "total": len(node_map),
            "to_run": len(to_run),
            "skipped": len(skips),
        },
        "regen": plan.to_dict() if plan else None,
        "edit_detection": edit_report,
    }
    if estimator:
        try:
            payload["estimate"] = _resolve_module_attr(estimator)(
                ctx, to_run, work
            )
        except Exception as e:
            _emit_error(
                f"Estimator failed: {e}", traceback=traceback.format_exc()
            )
    _emit(payload)


@app.command("resume")
def resume_cmd(
    path: Path = typer.Argument(..., help="Path to bible JSON file."),
    pipeline: str | None = typer.Option(None, "--pipeline"),
    phases: str | None = typer.Option(None, "--phases"),
    max_concurrency: int | None = typer.Option(None, "--max-concurrency"),
    skip_edit_check: bool = typer.Option(False, "--skip-edit-check"),
) -> None:
    """Alias for `run` — resume after a failure, edit, or gate pause."""
    _orchestrated_run(path, pipeline, phases, max_concurrency, skip_edit_check)


@app.command("pin")
def pin_cmd(
    path: Path = typer.Argument(..., help="Path to bible JSON file."),
    artifact_ids: list[str] | None = typer.Argument(
        None,
        help="ART artifact ids to protect (tileset:<stage>, enemy:<id>, "
        "backdrop:<stage>, player). Pinned content is skipped by regen "
        "cascades AND by the art phases — a paid asset you like stays "
        "exactly as it is. Level steps are not pinnable: hand-edit the "
        "layer file instead (USER_EDITED protection).",
    ),
    list_only: bool = typer.Option(
        False, "--list", help="Show what's pinned and what's pinnable.",
    ),
) -> None:
    """Protect artifacts from regeneration (`canon unpin` reverses)."""
    try:
        from canon.bible.artifacts import ArtifactStatus
        from canon.pipeline.orchestrator import pinnable_ids, pinned_ids
    except ImportError as e:
        _emit_error(f"Failed to import orchestrator: {e}")

    if not path.exists():
        _emit_error(f"File not found: {path}", path=str(path))
    try:
        bible = Bible.load(path)
    except Exception as e:
        _emit_error(f"Failed to load bible: {e}", path=str(path))

    pinned = pinned_ids(bible)  # type: ignore[possibly-unbound]
    pinnable = pinnable_ids(bible)  # type: ignore[possibly-unbound]
    if list_only or not artifact_ids:
        _emit({
            "pinned": sorted(pinned),
            "pinnable": sorted(pinnable),
            "bible": str(path),
        })
        return

    unknown = sorted(set(artifact_ids) - pinnable)
    if unknown:
        # Atomic: reject the whole request before any mutation.
        _emit_error(
            f"not pinnable: {unknown} — pinnable ids are the hash-tracked "
            "artifacts (see `canon pin <bible> --list`).",
            pinnable=sorted(pinnable),
        )

    added = sorted(set(artifact_ids) - pinned)
    already = sorted(set(artifact_ids) & pinned)
    stale_cleared: list[str] = []
    status = bible.metadata.node_status  # type: ignore[possibly-unbound]
    for aid in added:
        # A stale mark would still reschedule the owning phase via `owns`
        # and defeat the pin — pinning an already-marked artifact clears it.
        if status.get(aid) is ArtifactStatus.STALE:  # type: ignore[possibly-unbound]
            status[aid] = ArtifactStatus.DONE  # type: ignore[possibly-unbound]
            stale_cleared.append(aid)
    bible.metadata.pinned = sorted(pinned | set(added))  # type: ignore[possibly-unbound]
    try:
        bible.persist(path)  # type: ignore[possibly-unbound]
    except Exception as e:
        _emit_error(f"Failed to persist pins: {e}")
    _emit({
        "result": "pinned",
        "pinned": added,
        "already_pinned": already,
        "stale_cleared": stale_cleared,
        "bible": str(path),
    })


@app.command("unpin")
def unpin_cmd(
    path: Path = typer.Argument(..., help="Path to bible JSON file."),
    artifact_ids: list[str] = typer.Argument(
        ..., help="Pinned artifact ids to release (idempotent).",
    ),
) -> None:
    """Release pinned artifacts. Status is untouched — nothing re-rolls
    until explicitly targeted or reached by a future cascade."""
    try:
        from canon.pipeline.orchestrator import pinned_ids
    except ImportError as e:
        _emit_error(f"Failed to import orchestrator: {e}")

    if not path.exists():
        _emit_error(f"File not found: {path}", path=str(path))
    try:
        bible = Bible.load(path)
    except Exception as e:
        _emit_error(f"Failed to load bible: {e}", path=str(path))

    pinned = pinned_ids(bible)  # type: ignore[possibly-unbound]
    removed = sorted(pinned & set(artifact_ids))
    not_pinned = sorted(set(artifact_ids) - pinned)
    bible.metadata.pinned = sorted(pinned - set(removed))  # type: ignore[possibly-unbound]
    try:
        bible.persist(path)  # type: ignore[possibly-unbound]
    except Exception as e:
        _emit_error(f"Failed to persist pins: {e}")
    _emit({
        "result": "unpinned",
        "unpinned": removed,
        "not_pinned": not_pinned,
        "bible": str(path),
    })


@app.command("status")
def status_cmd(
    path: Path = typer.Argument(..., help="Path to bible JSON file."),
) -> None:
    """Print the state-machine summary from the bible (per-node + coarse)."""
    if not path.exists():
        _emit_error(f"File not found: {path}", path=str(path))
    try:
        bible = Bible.load(path)
    except Exception as e:
        _emit_error(f"Failed to load bible: {e}", path=str(path))

    node_status = {
        k: str(v) for k, v in bible.metadata.node_status.items()  # type: ignore[possibly-unbound]
    }
    counts: dict[str, int] = {}
    for value in node_status.values():
        counts[value] = counts.get(value, 0) + 1
    pinned = set(getattr(bible.metadata, "pinned", None) or ())  # type: ignore[possibly-unbound]
    _emit(
        {
            "phases_run": bible.metadata.phases_run,  # type: ignore[possibly-unbound]
            "phase_status": {
                k: str(v) for k, v in bible.metadata.phase_status.items()  # type: ignore[possibly-unbound]
            },
            "node_counts": counts,
            "node_status": node_status,
            "pinned": sorted(pinned),
            # Pinned ids are deliberate state, not attention items —
            # unless the file was hand-edited under the pin.
            "attention": sorted(
                nid for nid, s in node_status.items()
                if s in ("escalated", "stale", "user_edited", "awaiting_review")
                and (nid not in pinned or s == "user_edited")
            ),
        }
    )


# ---------------------------------------------------------------------------
# Platformer read/export verbs — the read half external tooling (Cradle)
# shells out to instead of re-implementing .npz decoding + the tileset registry.
# ---------------------------------------------------------------------------

level_app = typer.Typer(help="Platformer level inspection / export.")
app.add_typer(level_app, name="level")


@level_app.command("export")
def level_export(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root (holds manifest.json)."),
    level_id: str = typer.Option(..., "--level", help="Level id to export (e.g. l1)."),
) -> None:
    """Emit a render-ready JSON bundle for one level.

    Decodes the three dense ``.npz`` grids to nested int lists, inlines the
    tileset slots + palette, resolves enemy placements against their global
    definitions, and rewrites asset refs to absolute paths. This is the
    contract a viewer renders from without needing numpy.
    """
    try:
        from canon.adapters.platformer_read import export_level_bundle
    except ImportError as e:
        _emit_error(f"Failed to import platformer reader: {e}")

    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    try:
        bundle = export_level_bundle(pack_dir, level_id)  # type: ignore[possibly-unbound]
    except FileNotFoundError as e:
        _emit_error(str(e), pack_dir=str(pack_dir), level=level_id)
    except Exception as e:
        _emit_error(f"Level export failed: {e}", traceback=traceback.format_exc())
    _emit({"level": bundle})  # type: ignore[possibly-unbound]


def main() -> None:
    app()


if __name__ == "__main__":
    main()
