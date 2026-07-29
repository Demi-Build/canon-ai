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


@level_app.command("apply-edit")
def level_apply_edit(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
    level_id: str = typer.Option(..., "--level", help="Level id to edit (e.g. l1)."),
    json_str: str | None = typer.Option(
        None, "--json", help="Inline edit JSON (partial level: entities/items/triggers/spawn/exit)."
    ),
    from_file: Path | None = typer.Option(
        None, "--from", help="Path to a JSON file with the edit (alternative to --json)."
    ),
    actor: str = typer.Option("user", "--actor", help="Who made the edit (journalled)."),
    session: str | None = typer.Option(None, "--session", help="Session id (journalled)."),
) -> None:
    """Apply a sparse-layer hand-edit (moved placements / spawn / exit).

    Rewrites the affected layer files, recomputes hashes, updates level.json,
    stamps the level ``user_edited``, and journals the before/after mutation to
    ``.canon/journal.jsonl`` + the content-addressed object store.
    """
    try:
        from canon.adapters.platformer_write import apply_level_edit
    except ImportError as e:
        _emit_error(f"Failed to import platformer writer: {e}")

    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    if json_str and from_file:
        _emit_error("Pass only one of --json / --from.")
    if not json_str and not from_file:
        _emit_error("One of --json / --from is required.")
    try:
        raw = json_str if json_str else Path(from_file).read_text(encoding="utf-8")
        edit = json.loads(raw)
    except json.JSONDecodeError as e:
        _emit_error(f"Invalid edit JSON: {e}")
    try:
        result = apply_level_edit(  # type: ignore[possibly-unbound]
            pack_dir, level_id, edit, actor=actor, session=session
        )
    except FileNotFoundError as e:
        _emit_error(str(e), pack_dir=str(pack_dir), level=level_id)
    except Exception as e:
        _emit_error(f"Apply-edit failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


@level_app.command("baseline")
def level_baseline(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
    level_id: str = typer.Option(..., "--level", help="Level id to baseline (e.g. l1)."),
    actor: str = typer.Option("cradle", "--actor", help="Who imported the generation."),
    session: str | None = typer.Option(None, "--session", help="Session id (journalled)."),
) -> None:
    """Record ``generate`` events for a level's as-generated artifacts.

    Cradle calls this when it imports a fresh generation, snapshotting each step
    artifact into the object store. Idempotent — safe to call on every open.
    """
    try:
        from canon.adapters.platformer_write import baseline_level
    except ImportError as e:
        _emit_error(f"Failed to import platformer writer: {e}")

    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    try:
        result = baseline_level(pack_dir, level_id, actor=actor, session=session)  # type: ignore[possibly-unbound]
    except FileNotFoundError as e:
        _emit_error(str(e), pack_dir=str(pack_dir), level=level_id)
    except Exception as e:
        _emit_error(f"Baseline failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


@level_app.command("history")
def level_history(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
    level_id: str | None = typer.Option(None, "--level", help="Filter to one level id."),
) -> None:
    """Dump the provenance journal (optionally filtered to a level)."""
    try:
        from canon.provenance import journal_path
    except ImportError as e:
        _emit_error(f"Failed to import provenance: {e}")

    jp = journal_path(pack_dir)  # type: ignore[possibly-unbound]
    if not jp.is_file():
        _emit({"events": []})
        return
    events = []
    with jp.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if level_id and f"/{level_id}/" not in e.get("artifact_id", ""):
                continue
            events.append(e)
    _emit({"events": events, "count": len(events)})


@level_app.command("import-grids")
def level_import_grids(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
    level_id: str = typer.Option(..., "--level", help="Level id to update (e.g. l1)."),
    json_str: str | None = typer.Option(
        None, "--json", help='Inline JSON: {"collision": [[...int rows...]]}'
    ),
    from_file: Path | None = typer.Option(None, "--from", help="JSON file (same shape)."),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """Apply a painted/resized collision grid (terrain paint write-back).

    Re-derives terrain (autotile/water-deep), background, and the hazards
    layer exactly as canon's own phases do, rehashes, stamps ``user_edited``,
    and journals the edit with before/after grid snapshots.
    """
    try:
        from canon.adapters.platformer_write import import_level_grids
    except ImportError as e:
        _emit_error(f"Failed to import platformer writer: {e}")

    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    if bool(json_str) == bool(from_file):
        _emit_error("Exactly one of --json / --from is required.")
    try:
        raw = json_str if json_str else Path(from_file).read_text(encoding="utf-8")
        payload = json.loads(raw)
        rows = payload["collision"] if isinstance(payload, dict) else payload
    except (json.JSONDecodeError, KeyError) as e:
        _emit_error(f"Invalid grid JSON: {e}")
    try:
        result = import_level_grids(  # type: ignore[possibly-unbound]
            pack_dir, level_id, rows, actor=actor, session=session
        )
    except (FileNotFoundError, ValueError) as e:
        _emit_error(str(e), pack_dir=str(pack_dir), level=level_id)
    except Exception as e:
        _emit_error(f"Grid import failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


@level_app.command("create")
def level_create(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
    stage_id: str = typer.Option(..., "--stage", help="Stage the level belongs to."),
    width: int = typer.Option(60, "--width"),
    height: int = typer.Option(16, "--height"),
    level_id: str | None = typer.Option(None, "--id", help="Explicit id (default: next lN)."),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """Scaffold a new hand-built DRAFT level (flat floor, spawn/exit).

    Drafts live on disk and open in cradle, but stay out of the manifest /
    world map until `canon level publish` inserts them at a position.
    """
    try:
        from canon.adapters.platformer_write import create_level
    except ImportError as e:
        _emit_error(f"Failed to import platformer writer: {e}")

    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    try:
        result = create_level(  # type: ignore[possibly-unbound]
            pack_dir, stage_id, width, height, level_id, actor=actor, session=session
        )
    except (FileNotFoundError, ValueError) as e:
        _emit_error(str(e), pack_dir=str(pack_dir), stage=stage_id)
    except Exception as e:
        _emit_error(f"Create failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


@level_app.command("publish")
def level_publish(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
    level_id: str = typer.Option(..., "--level", help="Level id to (un)publish."),
    position: int | None = typer.Option(
        None, "--position", help="1-based slot within the stage (default: append)."
    ),
    remove: bool = typer.Option(False, "--remove", help="Unpublish back to draft."),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """Insert a level into the playable progression (or pull it back out).

    Publishing at --position 2 makes it X-2 and renumbers the rest of the
    stage; the manifest level order and world map are rebuilt.
    """
    try:
        from canon.adapters.platformer_write import publish_level
    except ImportError as e:
        _emit_error(f"Failed to import platformer writer: {e}")

    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    try:
        result = publish_level(  # type: ignore[possibly-unbound]
            pack_dir, level_id, position, remove, actor=actor, session=session
        )
    except (FileNotFoundError, ValueError) as e:
        _emit_error(str(e), pack_dir=str(pack_dir), level=level_id)
    except Exception as e:
        _emit_error(f"Publish failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


@level_app.command("validate")
def level_validate(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
    level_id: str = typer.Option(..., "--level", help="Level id (e.g. l1)."),
) -> None:
    """Run generation's REAL validation suite on a level as it sits on disk
    (hand edits included): spawn→exit/checkpoint reachability under the
    level's own physics (jump-arc simulation, run-up momentum, swim), enemy
    placement rules (water policy, footprint, variant/rarity caps), and item
    collectibility. Pure read — no repairs, no writes, no journal."""
    ops = _pack_ops()
    try:
        result = ops.validate_level(pack_dir, level_id)
    except FileNotFoundError as e:
        _emit_error(str(e), pack_dir=str(pack_dir), level=level_id)
    except Exception as e:
        _emit_error(f"level validate failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


@level_app.command("versions")
def level_versions(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
    level_id: str = typer.Option(..., "--level", help="Level id (e.g. l1)."),
    step: str = typer.Option(..., "--step", help="Step artifact (entities/items/triggers/…)."),
) -> None:
    """List the version chain for a level step (the restore picker's source)."""
    try:
        from canon.adapters.platformer_write import level_artifact_id
        from canon.provenance import artifact_versions
    except ImportError as e:
        _emit_error(f"Failed to import provenance: {e}")

    try:
        aid = level_artifact_id(pack_dir, level_id, step)  # type: ignore[possibly-unbound]
    except FileNotFoundError as e:
        _emit_error(str(e), pack_dir=str(pack_dir), level=level_id)
    versions = artifact_versions(pack_dir, aid)  # type: ignore[possibly-unbound]
    _emit({"artifact_id": aid, "versions": versions, "count": len(versions)})  # type: ignore[possibly-unbound]


@level_app.command("restore")
def level_restore(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
    level_id: str = typer.Option(..., "--level", help="Level id (e.g. l1)."),
    step: str = typer.Option(..., "--step", help="Step to revert (entities/items/triggers/…)."),
    to_hash: str = typer.Option(..., "--to", help="Target version hash (from `level versions`)."),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """Revert a level step to a stored version (original or any prior edit).

    The version being left behind stays in the object store — nothing is lost.
    """
    try:
        from canon.adapters.platformer_write import restore_level_step
    except ImportError as e:
        _emit_error(f"Failed to import platformer writer: {e}")

    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    try:
        result = restore_level_step(  # type: ignore[possibly-unbound]
            pack_dir, level_id, step, to_hash, actor=actor, session=session
        )
    except (FileNotFoundError, ValueError) as e:
        _emit_error(str(e), pack_dir=str(pack_dir), level=level_id, step=step)
    except Exception as e:
        _emit_error(f"Restore failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


# ---------------------------------------------------------------------------
# Generation plumbing shared by the db/asset verbs
# ---------------------------------------------------------------------------


def _load_env_file(path: Path | None) -> None:
    """Load KEY=VALUE lines into the environment (setdefault semantics).

    Canon never auto-reads ``.env``; generation verbs accept ``--env-file``
    (or the ``CANON_ENV_FILE`` env var) so hosts like cradle can supply
    provider keys without exporting them shell-wide.
    """
    import os

    p = path or (
        Path(os.environ["CANON_ENV_FILE"])
        if os.environ.get("CANON_ENV_FILE")
        else None
    )
    if not p or not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _pack_ops():
    """Import the platformer pack's ops module.

    The pack code lives under ``examples/`` (not inside the installed canon
    package); with the editable install the repo root is two levels above the
    package, so put it on sys.path before importing.
    """
    import canon as _canon

    root = Path(_canon.__file__).resolve().parents[2]
    if (root / "examples").is_dir() and str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from examples.platformer_pack import ops
    except ImportError as e:  # pragma: no cover — env-specific
        _emit_error(
            f"Failed to import the platformer pack ops ({e}); "
            f"looked for examples/ under {root}."
        )
    return ops


db_app = typer.Typer(help="Generic database rows: create / LLM-complete (anchored).")
app.add_typer(db_app, name="db")


@db_app.command("types")
def db_types_cmd(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
) -> None:
    """The entity-type registry + field specs (drives editor form UIs)."""
    ops = _pack_ops()
    try:
        _emit({"types": ops.db_types(pack_dir)})
    except Exception as e:
        _emit_error(f"db types failed: {e}", traceback=traceback.format_exc())


@db_app.command("new")
def db_new_cmd(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
    entity_type: str = typer.Option(..., "--type", help="enemy | item"),
    fields_json: str | None = typer.Option(
        None, "--fields",
        help='Anchors: JSON of user-set fields (e.g. \'{"archetype":"flyer"}\'). '
        "Locked constraints — the skeleton rolls AROUND them.",
    ),
    complete: bool = typer.Option(
        False, "--complete", help="LLM-author the text fields (name/flavor)."
    ),
    llm_backend: str = typer.Option("fake", "--llm-backend", help="fake | anthropic"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    env_file: Path | None = typer.Option(None, "--env-file"),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """Create ONE new row: anchored skeleton roll + optional LLM completion,
    exactly as pipeline generation would (same prompts, rng streams, retry)."""
    _load_env_file(env_file)
    ops = _pack_ops()
    try:
        fields = json.loads(fields_json) if fields_json else {}
    except json.JSONDecodeError as e:
        _emit_error(f"Invalid --fields JSON: {e}")
    try:
        llm = ops.build_llm(llm_backend if complete else None, llm_model)
        result = ops.new_db_row(
            pack_dir, entity_type, fields,
            complete=complete, llm=llm, actor=actor, session=session,
        )
    except (FileNotFoundError, ValueError, KeyError) as e:
        _emit_error(str(e), pack_dir=str(pack_dir), type=entity_type)
    except Exception as e:
        _emit_error(f"db new failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


@db_app.command("complete")
def db_complete_cmd(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
    entity_type: str = typer.Option(..., "--type", help="enemy | item"),
    entity_id: str = typer.Option(..., "--id"),
    locked: str | None = typer.Option(
        None, "--locked", help="Comma-separated field names preserved as constraints."
    ),
    reroll: bool = typer.Option(
        False, "--reroll", help="Also re-roll unlocked mechanical fields."
    ),
    llm_backend: str = typer.Option("fake", "--llm-backend", help="fake | anthropic"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    env_file: Path | None = typer.Option(None, "--env-file"),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """LLM-complete an existing row, anchored by its locked fields."""
    _load_env_file(env_file)
    ops = _pack_ops()
    try:
        llm = ops.build_llm(llm_backend, llm_model)
        result = ops.complete_db_row(
            pack_dir, entity_type, entity_id,
            [s.strip() for s in locked.split(",")] if locked else [],
            reroll=reroll, llm=llm, actor=actor, session=session,
        )
    except (FileNotFoundError, ValueError, KeyError) as e:
        _emit_error(str(e), pack_dir=str(pack_dir), id=entity_id)
    except Exception as e:
        _emit_error(f"db complete failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


@db_app.command("update")
def db_update_cmd(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
    entity_type: str = typer.Option(..., "--type", help="enemy | item | tile"),
    entity_id: str = typer.Option(
        ..., "--id",
        help="Row id — or <stage>/<tile_name> with --type tile.",
    ),
    set_json: str = typer.Option(
        ..., "--set",
        help='JSON of field: value changes (e.g. \'{"hp": 9, "name": "Grub"}\'; '
        "null deletes a nested knob). Values land verbatim — no rerolls.",
    ),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """DIRECT human edit of an existing row (no LLM, no rerolls): enemy/item
    fields, or collision/params for a tile type. Rehashes, stamps
    ``user_edited``, journals ``op:"edit"`` with the field diff."""
    ops = _pack_ops()
    try:
        changes = json.loads(set_json)
    except json.JSONDecodeError as e:
        _emit_error(f"Invalid --set JSON: {e}")
    try:
        if entity_type == "tile":
            result = ops.update_tile_slots(
                pack_dir, entity_id, changes, actor=actor, session=session
            )
        else:
            result = ops.update_db_row(
                pack_dir, entity_type, entity_id, changes,
                actor=actor, session=session,
            )
    except (FileNotFoundError, ValueError, KeyError) as e:
        _emit_error(str(e), pack_dir=str(pack_dir), type=entity_type, id=entity_id)
    except Exception as e:
        _emit_error(f"db update failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


@db_app.command("schema")
def db_schema_cmd(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
    entity_type: str = typer.Option(..., "--type", help="enemy | item"),
    set_json: str | None = typer.Option(
        None, "--set",
        help='Edit: {"fields": {<name>: <field entry>|null, ...}} — each named '
        "field is replaced wholesale. Omit to just read the effective schema.",
    ),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """Read (default) or edit (--set) the roll-table schema bounding
    generation for one entity type. Edits are validated fail-closed (loader +
    lookup coverage + smoke roll) and land as a PACK-LOCAL override."""
    changes = None
    if set_json is not None:
        # Parsed OUTSIDE the op try: _emit_error's Exit must not be re-caught
        # below as a second "db schema failed" payload.
        try:
            changes = json.loads(set_json)
        except json.JSONDecodeError as e:
            _emit_error(f"Invalid --set JSON: {e}")
    ops = _pack_ops()
    try:
        if changes is None:
            result = ops.read_db_schema(pack_dir, entity_type)
        else:
            result = ops.update_db_schema(
                pack_dir, entity_type, changes, actor=actor, session=session
            )
    except (FileNotFoundError, ValueError, KeyError) as e:
        _emit_error(str(e), pack_dir=str(pack_dir), type=entity_type)
    except Exception as e:
        _emit_error(f"db schema failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


asset_app = typer.Typer(help="Pack asset replacement (user art entering the pack).")
app.add_typer(asset_app, name="asset")


@asset_app.command("generate")
def asset_generate_cmd(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
    target: str = typer.Option(
        ..., "--target",
        help="enemy:<id> | item:<id> | player | backdrop:<stage> | audio:<stage>",
    ),
    image_backend: str | None = typer.Option(None, "--image-backend"),
    image_model: str | None = typer.Option(None, "--image-model"),
    image_edit_model: str | None = typer.Option(None, "--image-edit-model"),
    image_edit_backend: str | None = typer.Option(None, "--image-edit-backend"),
    music_backend: str | None = typer.Option(None, "--music-backend"),
    sfx_backend: str | None = typer.Option(None, "--sfx-backend"),
    env_file: Path | None = typer.Option(None, "--env-file"),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """(Re)generate ONE asset via the real art/audio phases (single-image
    path). Explicit backends only; paid keys via --env-file / CANON_ENV_FILE."""
    _load_env_file(env_file)
    ops = _pack_ops()
    try:
        result = ops.generate_asset(
            pack_dir, target,
            image_backend=image_backend, image_model=image_model,
            image_edit_model=image_edit_model,
            image_edit_backend=image_edit_backend,
            music_backend=music_backend, sfx_backend=sfx_backend,
            actor=actor, session=session,
        )
    except (FileNotFoundError, ValueError) as e:
        _emit_error(str(e), pack_dir=str(pack_dir), target=target)
    except Exception as e:
        _emit_error(f"asset generate failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


@asset_app.command("animate")
def asset_animate_cmd(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
    target: str = typer.Option(..., "--target", help="enemy:<id> | player"),
    image_backend: str | None = typer.Option(None, "--image-backend"),
    image_model: str | None = typer.Option(None, "--image-model"),
    image_edit_model: str | None = typer.Option(None, "--image-edit-model"),
    image_edit_backend: str | None = typer.Option(None, "--image-edit-backend"),
    vlm_backend: str | None = typer.Option(None, "--vlm-backend"),
    vlm_model: str | None = typer.Option(None, "--vlm-model"),
    reuse_spec: bool = typer.Option(
        False, "--reuse-spec",
        help="Skip VLM authoring and reuse the stored motion spec.",
    ),
    env_file: Path | None = typer.Option(None, "--env-file"),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """Animate ONE actor (the multi-image path): VLM-authored motion spec →
    one img2img sheet per state → strips + frames.json + packed atlas."""
    _load_env_file(env_file)
    ops = _pack_ops()
    try:
        result = ops.animate_asset(
            pack_dir, target,
            image_backend=image_backend, image_model=image_model,
            image_edit_model=image_edit_model,
            image_edit_backend=image_edit_backend,
            vlm_backend=vlm_backend, vlm_model=vlm_model,
            reuse_spec=reuse_spec, actor=actor, session=session,
        )
    except (FileNotFoundError, ValueError) as e:
        _emit_error(str(e), pack_dir=str(pack_dir), target=target)
    except Exception as e:
        _emit_error(f"asset animate failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


@asset_app.command("replace")
def asset_replace(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
    target: str = typer.Option(
        ..., "--target",
        help="enemy:<id> | item:<id> | player | tile:<stage>/<name> | backdrop:<stage>/<index>",
    ),
    from_file: Path = typer.Option(..., "--from", help="PNG file to install."),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """Replace an asset's bytes with an uploaded PNG.

    Rehashes every reference, protects the artifact from regen (bible pin when
    a bible exists, ``user_edited`` otherwise), and journals ``op:"import"``
    with before/after snapshots. Tile targets re-skin every slot of the type —
    physics untouched (types-vs-skin).
    """
    try:
        from canon.adapters.platformer_write import replace_asset
    except ImportError as e:
        _emit_error(f"Failed to import platformer writer: {e}")

    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    if not from_file.is_file():
        _emit_error(f"Source file not found: {from_file}")
    try:
        result = replace_asset(  # type: ignore[possibly-unbound]
            pack_dir, target, from_file, actor=actor, session=session
        )
    except (FileNotFoundError, ValueError) as e:
        _emit_error(str(e), pack_dir=str(pack_dir), target=target)
    except Exception as e:
        _emit_error(f"Asset replace failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


@asset_app.command("versions")
def asset_versions_cmd(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
    target: str = typer.Option(
        ..., "--target", help="Artifact id (enemy:<id>, tileset:<stage>, …)."
    ),
) -> None:
    """The compact version chain for ANY journaled artifact (the restore
    picker's source) — level steps keep `level versions`."""
    try:
        from canon.provenance import artifact_versions
    except ImportError as e:
        _emit_error(f"Failed to import provenance: {e}")
    versions = artifact_versions(pack_dir, target)  # type: ignore[possibly-unbound]
    _emit({"artifact_id": target, "versions": versions, "count": len(versions)})


@asset_app.command("lineage")
def asset_lineage_cmd(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
    target: str = typer.Option(..., "--target", help="Artifact id."),
    max_nodes: int = typer.Option(500, "--max-nodes"),
) -> None:
    """The artifact's FAMILY TREE from the journal + object store: nodes =
    versions (content hashes, with facet/actor/model/prompt), edges = the
    events between them; shared bytes connect artifacts across the pack."""
    try:
        from canon.adapters.platformer_read import asset_lineage
    except ImportError as e:
        _emit_error(f"Failed to import platformer reader: {e}")
    try:
        result = asset_lineage(pack_dir, target, max_nodes=max_nodes)  # type: ignore[possibly-unbound]
    except FileNotFoundError as e:
        _emit_error(str(e), pack_dir=str(pack_dir), target=target)
    except Exception as e:
        _emit_error(f"asset lineage failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


@asset_app.command("restore")
def asset_restore_cmd(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
    target: str = typer.Option(
        ..., "--target",
        help="enemy:<id> | item:<id> (row JSON or sprite PNG by bytes) | "
        "player | tilesheet:<stage> | backdrop:<stage>/<index>",
    ),
    to: str = typer.Option(..., "--to", help="Version hash (sha256:…)."),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """Make a historic version current again (op:"restore"). Nothing is
    deleted — the lineage grows a new branch from the chosen node."""
    try:
        from canon.adapters.platformer_write import restore_asset
    except ImportError as e:
        _emit_error(f"Failed to import platformer writer: {e}")
    try:
        result = restore_asset(  # type: ignore[possibly-unbound]
            pack_dir, target, to, actor=actor, session=session
        )
    except (FileNotFoundError, ValueError) as e:
        _emit_error(str(e), pack_dir=str(pack_dir), target=target)
    except Exception as e:
        _emit_error(f"asset restore failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


@asset_app.command("assign")
def asset_assign_cmd(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
    source: str = typer.Option(..., "--source", help="enemy:<id> | item:<id>"),
    to: str = typer.Option(..., "--to", help="enemy:<id> | item:<id>"),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """Use one row's art on another: copies the WHOLE bundle (sprite +
    animation) with a cross-artifact provenance edge — the lineage tree
    shows both rows sharing the same nodes afterward."""
    try:
        from canon.adapters.platformer_write import assign_asset
    except ImportError as e:
        _emit_error(f"Failed to import platformer writer: {e}")
    try:
        result = assign_asset(  # type: ignore[possibly-unbound]
            pack_dir, source, to, actor=actor, session=session
        )
    except (FileNotFoundError, ValueError) as e:
        _emit_error(str(e), pack_dir=str(pack_dir), source=source, to=to)
    except Exception as e:
        _emit_error(f"asset assign failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


library_app = typer.Typer(
    help="The GLOBAL cross-project asset library ($CANON_LIBRARY, "
    "default ~/.canon/library)."
)
app.add_typer(library_app, name="library")


@library_app.command("publish")
def library_publish_cmd(
    pack_dir: Path = typer.Argument(..., help="Source pack root."),
    target: str = typer.Option(
        ..., "--target",
        help="enemy:<id> | item:<id> | player | tile:<stage>/<name> | "
        "backdrop:<stage>/<i> | audio:<stage>",
    ),
    name: str | None = typer.Option(None, "--name"),
    tags: str | None = typer.Option(None, "--tags", help="Comma-separated."),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """Publish an asset into the library (composite assets travel whole;
    identical re-publishes dedup). Journals op:"keep" in the source pack."""
    try:
        from canon import library
    except ImportError as e:
        _emit_error(f"Failed to import library: {e}")
    try:
        result = library.publish(  # type: ignore[possibly-unbound]
            pack_dir, target, name=name,
            tags=[t.strip() for t in tags.split(",")] if tags else (),
            actor=actor, session=session,
        )
    except (FileNotFoundError, ValueError) as e:
        _emit_error(str(e), pack_dir=str(pack_dir), target=target)
    except Exception as e:
        _emit_error(f"library publish failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


@library_app.command("list")
def library_list_cmd(
    kind: str | None = typer.Option(None, "--kind"),
    tag: str | None = typer.Option(None, "--tag"),
    query: str | None = typer.Option(None, "--query"),
    project: str | None = typer.Option(
        None, "--project",
        help="Filter by source pack path/world name (the project view).",
    ),
) -> None:
    """Browse the library index (newest first)."""
    try:
        from canon import library
    except ImportError as e:
        _emit_error(f"Failed to import library: {e}")
    entries = library.list_entries(kind=kind, tag=tag, query=query, project=project)  # type: ignore[possibly-unbound]
    _emit({"entries": entries, "count": len(entries), "root": str(library.library_root())})  # type: ignore[possibly-unbound]


@library_app.command("import")
def library_import_cmd(
    pack_dir: Path = typer.Argument(..., help="Destination pack root."),
    library_id: str = typer.Option(..., "--id"),
    new_id: str | None = typer.Option(
        None, "--as", help="Preferred id (a fresh one is minted on collision)."
    ),
    into: str | None = typer.Option(
        None, "--into",
        help="tile:<stage>/<name> | backdrop:<stage>/<i> | audio:<stage> "
        "(required for those kinds).",
    ),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """Import a library entry: bytes copy in (packs stay self-contained),
    ids are always fresh, and the artifact carries a durable library_ref."""
    try:
        from canon import library
    except ImportError as e:
        _emit_error(f"Failed to import library: {e}")
    try:
        result = library.import_entry(  # type: ignore[possibly-unbound]
            pack_dir, library_id, new_id=new_id, into=into,
            actor=actor, session=session,
        )
    except (FileNotFoundError, ValueError) as e:
        _emit_error(str(e), pack_dir=str(pack_dir), id=library_id)
    except Exception as e:
        _emit_error(f"library import failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


@library_app.command("cat")
def library_cat_cmd(
    content_hash: str = typer.Argument(..., help="sha256:<hex> object hash."),
) -> None:
    """Fetch library object bytes as base64 (previews for the browser UI)."""
    try:
        from canon import library
    except ImportError as e:
        _emit_error(f"Failed to import library: {e}")
    try:
        data = library.read_object(content_hash)  # type: ignore[possibly-unbound]
    except FileNotFoundError:
        _emit_error(f"object {content_hash} not in the library store")
    import base64

    _emit({
        "hash": content_hash,
        "size": len(data),  # type: ignore[possibly-unbound]
        "bytes_b64": base64.b64encode(data).decode("ascii"),  # type: ignore[possibly-unbound]
    })


object_app = typer.Typer(help="Content-addressed object store reads.")
app.add_typer(object_app, name="object")


@object_app.command("cat")
def object_cat_cmd(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
    content_hash: str = typer.Argument(..., help="sha256:<hex> version hash."),
    out: Path | None = typer.Option(
        None, "--out", help="Write raw bytes to a file instead of emitting JSON."
    ),
) -> None:
    """Fetch a stored version's bytes (thumbnails of history live only in
    the CAS). Without --out, emits base64 JSON small enough for UI pipes."""
    try:
        from canon.provenance import read_object
    except ImportError as e:
        _emit_error(f"Failed to import provenance: {e}")
    try:
        data = read_object(pack_dir, content_hash)  # type: ignore[possibly-unbound]
    except FileNotFoundError:
        _emit_error(f"object {content_hash} not in the store", pack_dir=str(pack_dir))
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)  # type: ignore[possibly-unbound]
        out.write_bytes(data)  # type: ignore[possibly-unbound]
        _emit({"hash": content_hash, "size": len(data), "out": str(out)})  # type: ignore[possibly-unbound]
    else:
        import base64

        _emit({
            "hash": content_hash,
            "size": len(data),  # type: ignore[possibly-unbound]
            "bytes_b64": base64.b64encode(data).decode("ascii"),  # type: ignore[possibly-unbound]
        })


def main() -> None:
    app()


if __name__ == "__main__":
    main()
