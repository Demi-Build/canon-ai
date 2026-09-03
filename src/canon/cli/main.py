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


def _emit_not_yet_or_error(exc: Exception, **extra) -> None:
    """A ``NotYetError`` (doctrine 4: disabled-with-a-reason) emits its
    structured payload (``not_yet``, ``row``, …) beside the message; any
    other error emits the message alone.

    The two dicts are MERGED, not double-splatted: both carry ``type`` on the
    db verbs (the caller's ``type=entity_type`` and the payload's own), and
    ``**a, **b`` on a shared key is a ``TypeError`` — which would have handed
    cradle/the agent a Python traceback instead of the structured not-yet
    they dispatch on. The payload wins, being the closer description."""
    payload = getattr(exc, "payload", None)
    if isinstance(payload, dict):
        _emit_error(str(exc), **{**extra, **payload})
    _emit_error(str(exc), **extra)


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
        "the would-run nodes (e.g. canon.packs.platformer.estimate:"
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
# Whole-project scaffolding — start a fresh platformer from cradle.
# ---------------------------------------------------------------------------

world_app = typer.Typer(help="Whole-project scaffolding.")
app.add_typer(world_app, name="world")


#: Row P0-10 — ``world new`` count flags are the UNION across templates (the
#: same idiom ``world estimate`` already uses): one signature, and each
#: template's ``PackSpec.runner["counts"]`` says which flags it reads and what
#: it calls them. A flag that belongs to another template is refused with a
#: reason (doctrine 4), never silently dropped. Adding a third template adds
#: rows here and in its seed — never a branch on the template id.
_NEW_COUNT_FLAGS: tuple[str, ...] = (
    "stages", "levels", "enemies", "items",
    "rooms", "npcs", "monsters", "events", "quests", "classes",
)

#: Every count flag also accepts the template's own count-KEY name as an alias
#: (``--npc`` beside ``--npcs``, ``--item`` beside ``--items``) so cradle can
#: send the names `pack templates` gave it verbatim — the wizard's fields are a
#: 1:1 map onto these flags, with no translation table on the cradle side.
#:
#: ``world new`` flag → the count key(s) it may name, in preference order.
#: Plural CLI flags, singular row kinds — the dungeon's entity kinds are
#: singular on disk, and ``--items`` legitimately means ``items`` (the
#: platformer's item pool) on one template and ``item`` (items per room) on
#: the other. The resolver below picks the first key the TEMPLATE declares,
#: so which name a flag carries is template data, not a branch here.
_NEW_FLAG_COUNT_KEYS: dict[str, tuple[str, ...]] = {
    "stages": ("stages",),
    "levels": ("levels",),
    "enemies": ("enemies",),
    "items": ("items", "item"),
    "rooms": ("rooms",),
    "npcs": ("npc", "npcs"),
    "monsters": ("monster", "monsters"),
    "events": ("event", "events"),
    "quests": ("quest", "quests"),
    "classes": ("class", "classes"),
}


def _count_key(spec: Any, flag: str) -> str:
    """The count key *flag* names on *spec* — the first candidate the
    template's ``runner["counts"]`` declares, else the first candidate (which
    ``_runner_argv`` then refuses by name, doctrine 4)."""
    declared = (spec.runner or {}).get("counts", {})
    candidates = _NEW_FLAG_COUNT_KEYS.get(flag, (flag,))
    for candidate in candidates:
        if candidate in declared:
            return candidate
    return candidates[0]


def _runner_argv(
    spec: Any,
    *,
    output_dir: Path,
    seed: str,
    model: str | None,
    counts: dict[str, int],
    backends: dict[str, str],
    orchestrate: bool | None,
) -> tuple[list[str], list[str]]:
    """``(argv, warnings)`` for *spec*'s create runner, built from
    ``PackSpec.runner`` — the registry dispatch that replaced the hardcoded
    platformer runner (row P0-10). Everything the runner cannot take is a
    NAMED warning, never a silent drop (doctrine 4).

    ``orchestrate`` is master §8 Q6's flip: ``None`` (the default) turns the
    DAG scheduler ON for every template that declares one, so a fresh create
    is resumable/regen-able from day one; its only sanctioned fixture delta
    is the additive ``bible.json``. ``False`` restores the sequential run;
    ``True`` on a template with no DAG says so.
    """
    runner = spec.runner or {}
    module = runner.get("module")
    if not module:
        raise ValueError(
            f"template {spec.pack_type!r} declares no create runner "
            "(PackSpec.runner['module']) — it cannot be created from the wizard yet"
        )
    warnings: list[str] = []
    argv: list[str] = [sys.executable, "-m", str(module), *[str(a) for a in runner.get("extra", [])]]

    count_flags: dict[str, str] = runner.get("counts", {})
    for key, value in counts.items():
        flag = count_flags.get(key)
        if flag is None:
            warnings.append(
                f"--{key} ignored: not a {spec.pack_type} count "
                f"({', '.join(sorted(count_flags)) or 'this template takes none'})"
            )
            continue
        argv += [flag, str(value)]

    backend_flags: dict[str, str] = runner.get("backends", {})
    for kind, value in backends.items():
        flag = backend_flags.get(kind)
        if flag is None:
            # A generator this template has no lane for (the dungeon has no
            # animation pass). Anything but off is worth saying out loud.
            if value not in ("", "none"):
                warnings.append(
                    f"--{kind}-backend {value!r} ignored: {spec.pack_type} has no {kind} generator"
                )
            continue
        argv += [flag, value]

    if runner.get("output"):
        argv += [runner["output"], str(output_dir)]
    if runner.get("seed"):
        argv += [runner["seed"], seed]
    if model and runner.get("model"):
        argv += [runner["model"], model]
    elif model:
        warnings.append(f"--model {model!r} ignored: {spec.pack_type}'s runner takes no model flag")

    orchestrate_flag = runner.get("orchestrate")
    if orchestrate_flag and orchestrate is not False:
        argv.append(orchestrate_flag)
    elif orchestrate is True and not orchestrate_flag:
        warnings.append(
            f"--orchestrate ignored: {spec.pack_type} has one linear pipeline, no DAG scheduler"
        )
    return argv, warnings


@world_app.command("new")
def world_new(
    output_dir: Path = typer.Argument(..., help="Where to create the new pack (must be empty/new)."),
    template: str = typer.Option(
        "platformer", "--template",
        help="Which template to create: any registered pack id (platformer | dungeon). "
        "`canon pack templates` lists them with their counts, ranges and labels.",
    ),
    name: str = typer.Option("My Platformer", "--name", help="World display name."),
    stages: int | None = typer.Option(None, "--stages", help="platformer: biome stages (default 1)."),
    levels: int | None = typer.Option(None, "--levels", help="platformer: levels per stage (default 2)."),
    enemies: int | None = typer.Option(None, "--enemies", help="platformer: enemy roster (default 4)."),
    items: int | None = typer.Option(
        None, "--items", "--item",
        help="platformer: item pool (default 4) | dungeon: items per room (default 3).",
    ),
    rooms: int | None = typer.Option(None, "--rooms", help="dungeon: rooms (default 3)."),
    npcs: int | None = typer.Option(None, "--npcs", "--npc", help="dungeon: NPCs per room (default 2)."),
    monsters: int | None = typer.Option(
        None, "--monsters", "--monster", help="dungeon: monsters per room (default 2)."
    ),
    events: int | None = typer.Option(None, "--events", "--event", help="dungeon: events per room (default 4)."),
    quests: int | None = typer.Option(None, "--quests", "--quest", help="dungeon: quests per room (default 2)."),
    classes: int | None = typer.Option(None, "--classes", "--class", help="dungeon: player classes (default 4)."),
    seed: str | None = typer.Option(None, "--seed", help="Pin for reproducibility (default: varied)."),
    llm_backend: str = typer.Option("fake", "--llm-backend", help="Design text: fake ($0) | anthropic (paid)."),
    image_backend: str = typer.Option(
        "fake",
        "--image-backend",
        help="Art: none | fake | fal | retro | pixellab | local.",
    ),
    music_backend: str = typer.Option("none", "--music-backend", help="Music: none | fake | lyria."),
    sfx_backend: str = typer.Option("none", "--sfx-backend", help="SFX: none | fake | elevenlabs."),
    vlm_backend: str = typer.Option("none", "--vlm-backend", help="Animation authoring: none | fake | anthropic."),
    model: str | None = typer.Option(None, "--model", help="LLM model (anthropic only)."),
    orchestrate: bool | None = typer.Option(
        None, "--orchestrate/--no-orchestrate",
        help="Run through the DAG scheduler (DEFAULT on every template that has one, "
        "master §8 Q6): persists bible.json so resume/regen and per-step re-runs work "
        "from the first create. --no-orchestrate restores the sequential run.",
    ),
    actor: str = typer.Option("user", "--actor"),
    env_file: Path | None = typer.Option(None, "--env-file", help="KEY=VALUE file for the paid backends."),
) -> None:
    """Scaffold a fresh, PLAYABLE project from a template — a populated starter.

    ``--template`` dispatches through the pack registry (row P0-10): the
    platformer runs its slice runner, ``--template dungeon`` runs the dungeon
    compose pipeline, and a third template is a registry entry, not a branch
    here. Every generator is a separate backend so you can go free or fully
    paid: all defaults = a $0 preview (canned text, placeholder art). Turn any
    dial up for a real run — ``--llm-backend anthropic`` (Claude authors the
    world), ``--image-backend fal`` (real sprites/backdrops),
    ``--music-backend lyria``, ``--sfx-backend elevenlabs``, ``--vlm-backend
    anthropic`` (animation). Paid backends need their keys via --env-file.

    Three things land after the run, in order: the world's NAME through the
    journaled write core (R13, per-template field), the pack's
    ``.canon/registry.json`` — the first registry any verb writes, stamping
    the template's engines-block entry (§3.0-H: ``godot`` on a platformer,
    ``pygame`` on a dungeon) — and the summary this prints. This is what
    cradle's "new project" shells to.
    """
    import secrets
    import subprocess

    from canon.packs import PACKS
    from canon.packs.spec import PackSpec

    _load_env_file(env_file)
    spec: PackSpec | None = PACKS.get(template)
    if spec is None:
        _emit_error(
            f"unknown template {template!r}; installed templates are {sorted(PACKS)} "
            "(`canon pack templates` describes them)"
        )
    if output_dir.exists() and any(output_dir.iterdir()):
        _emit_error(f"target already exists and is not empty: {output_dir}")
    # The runners ship inside the package (row P0-4) — spawn one as a module
    # under THIS interpreter from whatever cwd we were given; the output dir is
    # made absolute so the child never depends on cwd. Still a subprocess
    # (StepLog / dies-with-parent semantics unchanged).
    output_dir = output_dir.resolve()
    eff_seed = seed or f"cradle-{secrets.token_hex(4)}"
    flags = {
        "stages": stages, "levels": levels, "enemies": enemies, "items": items,
        "rooms": rooms, "npcs": npcs, "monsters": monsters,
        "events": events, "quests": quests, "classes": classes,
    }
    # Unset flags fall back to the TEMPLATE's defaults (``PackSpec.counts`` =
    # the P.4.4 wizard defaults), not the runner's own argparse defaults: the
    # wizard, `pack templates` and `world new` then agree on one set of
    # numbers, and the platformer's create keeps its historical 1 stage / 2
    # levels / 4 enemies / 4 items exactly.
    counts = dict(spec.counts)
    counts.update({
        _count_key(spec, flag): value
        for flag, value in flags.items()
        if value is not None
    })
    backends = {
        "llm": llm_backend, "image": image_backend,
        "music": music_backend, "sfx": sfx_backend, "vlm": vlm_backend,
    }
    try:
        cmd, warnings = _runner_argv(
            spec, output_dir=output_dir, seed=eff_seed, model=model,
            counts=counts, backends=backends, orchestrate=orchestrate,
        )
    except ValueError as e:
        _emit_error(str(e))
    # The subprocess inherits os.environ, so _load_env_file above puts any keys
    # in reach for the paid backends.
    #
    # ⏹ (row A4.5, master §3.0-D — *start nothing new, keep what landed, say
    # what it cost*): the runners exit ``EXIT_CANCELLED`` (3) after a cancel
    # file stopped them at an item boundary. That is NOT a failure — swallowing
    # it as one left the tree un-named and un-registered (so nothing that
    # landed was openable) and made cradle's clean-stop branch unreachable for
    # creates. A stopped create still names + registers the partial tree, emits
    # ``cancelled: true``, and exits 3 so the worker reports a stop, not a crash.
    from canon.pipeline.steplog import EXIT_CANCELLED

    cancelled = False
    try:
        subprocess.run(cmd, check=True, capture_output=True)  # type: ignore[possibly-unbound]
    except subprocess.CalledProcessError as e:
        if e.returncode != EXIT_CANCELLED:
            tail = (e.stderr or b"").decode(errors="replace")[-800:]
            _emit_error(f"world new failed:\n{tail}")
        cancelled = True
    # R13: the chosen world name lands through the journaled write core
    # (the template's title field + its mirrors, one event each) — the
    # un-journaled `_set_world_name` bypass is gone (P0-6).
    from canon.registry_ops import ensure_registry
    from canon.world_ops import set_world_title

    # After a stop the tree is PARTIAL: naming or registry synthesis may have
    # nothing to work with. That is reported as a warning on the cancelled
    # document — it is not a create failure, and erroring here would throw away
    # the very thing §3.0-D promises to keep.
    warnings = list(warnings or [])  # type: ignore[possibly-unbound]
    registry: dict[str, Any] = {}
    try:
        set_world_title(output_dir, name, actor=actor)
    except Exception as e:
        if not cancelled:
            _emit_error(f"world new: naming the world failed: {e}", pack_dir=str(output_dir))
        warnings.append(f"stopped before the world could be named: {e}")
    # §3.0-H — the FIRST registry any verb writes: the create stamps the pack's
    # effective registry (P0-6's synthesis, journaled as a `create` on
    # `registry`), including the template's one engines-block entry. Every verb
    # from here on resolves through the pack's own file, not the code seed.
    try:
        registry, _resolved, _event = ensure_registry(output_dir, actor=actor)
    except Exception as e:
        if not cancelled:
            _emit_error(f"world new: stamping the registry failed: {e}", pack_dir=str(output_dir))
        warnings.append(f"stopped before the registry could be stamped: {e}")
    result = {
        "pack_dir": str(output_dir),
        "template": template,
        "world": name,
        "seed": eff_seed,
        "backends": backends,
        "orchestrated": bool(spec.runner.get("orchestrate")) and orchestrate is not False,  # type: ignore[union-attr]
        "engines": [e.get("id") for e in registry.get("engines", [])],
        "registry": str(Path(output_dir) / ".canon" / "registry.json"),
    }
    if warnings:
        result["warnings"] = warnings
    if cancelled:
        # The step log's cancel-aware `run_end` carries WHAT landed (`kept`);
        # this document says only that the run stopped and where the partial
        # tree is, so the two never disagree.
        result["cancelled"] = True
        _emit(result)
        raise typer.Exit(EXIT_CANCELLED)
    _emit(result)


@world_app.command("map")
def world_map_read(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
) -> None:
    """The render-ready world map: nodes (position + display name + stage),
    typed edges, and the AREAS levels cluster under. Pure read."""
    try:
        from canon.adapters.platformer_write import read_world_map

        _emit(read_world_map(pack_dir))
    except (FileNotFoundError, ValueError, KeyError) as e:
        _emit_error(str(e), pack_dir=str(pack_dir))
    except Exception as e:
        _emit_error(f"world map failed: {e}", traceback=traceback.format_exc())


@world_app.command("map-edit")
def world_map_edit(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
    edit_json: str = typer.Option(
        ..., "--json",
        help='Any subset of {"nodes":{"l1":{"pos":[x,y]}},"edges":[...],"locked":bool}. '
        "A null node value hands that node back to the generator.",
    ),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """Hand-author the world map: place nodes, type the connections, lock the
    layout.

    The map is recomputed from the seed on every resume, so these are stored as
    DURABLE OVERRIDES on the World bible and layered back on at compose time —
    without that, the next run silently reverts your layout."""
    try:
        edit = json.loads(edit_json)
    except json.JSONDecodeError as e:
        _emit_error(f"--json is not valid JSON: {e}")
    try:
        from canon.adapters.platformer_write import apply_world_map_edit

        _emit(
            apply_world_map_edit(
                pack_dir, edit, actor=actor, session=session  # type: ignore[possibly-unbound]
            )
        )
    except (FileNotFoundError, ValueError, KeyError) as e:
        _emit_error(str(e), pack_dir=str(pack_dir))
    except Exception as e:
        _emit_error(f"world map-edit failed: {e}", traceback=traceback.format_exc())


#: ``world estimate`` count flags → the count key each template's count
#: function reads (row P0-7). A third template adds an entry — a data row,
#: never a branch on the template id. Flags left unset fall to the
#: template's ``cost_model.json`` ``fresh_plan`` (the P.4.4 wizard defaults).
_ESTIMATE_COUNT_FLAGS: dict[str, dict[str, str]] = {
    "platformer": {"stages": "num_stages", "levels": "num_levels", "enemies": "num_enemies", "items": "num_items"},
    "dungeon": {
        "rooms": "rooms", "npcs": "npc", "monsters": "monster", "items": "item",
        "events": "event", "quests": "quest", "classes": "class",
    },
}


@world_app.command("update")
def world_update_cmd(
    pack_dir: Path = typer.Argument(..., help="Pack root (platformer or dungeon)."),
    set_json: str = typer.Option(
        ..., "--set",
        help='JSON of world field: value — dotted keys from the pack\'s `world_fields` table '
        '(platformer: title, unlock_rules.<key>; dungeon: story.*, story.beats.<room_id>.*, narrative.*).',
    ),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """World/bible-level edits on the reusable protected-wall write core (P0
    paper P.7): resolve the key in the pack's ``world_fields`` table → wall →
    fail-closed validate → write the file and every mirror in one batch →
    one journal event per file (``world`` + ``mirror_of`` events). Numeric
    list indices are refused; ``<list>[<key>=<value>]`` addresses items.
    """
    from canon.packs import PackTypeError
    from canon.world_ops import update_world

    try:
        changes = json.loads(set_json)
    except json.JSONDecodeError as e:
        _emit_error(f"Invalid --set JSON: {e}")
    try:
        result = update_world(pack_dir, changes, actor=actor, session=session)
    except (FileNotFoundError, ValueError, PackTypeError) as e:
        _emit_not_yet_or_error(e, pack_dir=str(pack_dir))
    except Exception as e:
        _emit_error(f"world update failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


@world_app.command("estimate")
def world_estimate(
    template: str = typer.Option(
        "platformer", "--template",
        help="Template to price: any registered pack id (platformer | dungeon).",
    ),
    stages: int | None = typer.Option(None, "--stages", help="platformer: biome stages (default 3)."),
    levels: int | None = typer.Option(None, "--levels", help="platformer: levels (default 9)."),
    enemies: int | None = typer.Option(None, "--enemies", help="platformer: enemy roster (default 7)."),
    items: int | None = typer.Option(
        None, "--items", "--item",
        help="platformer: item pool (default 5) | dungeon: items per room (default 3).",
    ),
    rooms: int | None = typer.Option(None, "--rooms", help="dungeon: rooms (default 3)."),
    npcs: int | None = typer.Option(None, "--npcs", "--npc", help="dungeon: NPCs per room (default 2)."),
    monsters: int | None = typer.Option(
        None, "--monsters", "--monster", help="dungeon: monsters per room (default 2)."
    ),
    events: int | None = typer.Option(None, "--events", "--event", help="dungeon: events per room (default 4)."),
    quests: int | None = typer.Option(None, "--quests", "--quest", help="dungeon: quests per room (default 2)."),
    classes: int | None = typer.Option(None, "--classes", "--class", help="dungeon: player classes (default 4)."),
    llm_backend: str = typer.Option("fake", "--llm-backend"),
    image_backend: str = typer.Option("fake", "--image-backend"),
    music_backend: str = typer.Option("none", "--music-backend"),
    sfx_backend: str = typer.Option("none", "--sfx-backend"),
    vlm_backend: str = typer.Option("none", "--vlm-backend"),
    model: str | None = typer.Option(
        None, "--model",
        help="LLM model id every task prices at, for templates without a per-agent "
        "model table (dungeon; default: the anthropic backend's DEFAULT_MODEL).",
    ),
) -> None:
    """Forecast the cost of a NEW project (`world new`) at these counts +
    backends, WITHOUT running anything. fake/none categories price at $0 (the
    counts still show, so you can see what turning a backend on would cost).
    ``--template`` picks the pack (the estimate carries ``template``); every
    template answers the same JSON shape (cradle's CostEstimate + the §3.0-E
    ``low/high/backend/model/unitCount`` keys)."""
    try:
        from dataclasses import replace

        from canon.estimator import estimate
        from canon.packs import PACKS
    except ImportError as e:  # pragma: no cover — env-specific
        _emit_error(f"Failed to import the estimator: {e}")
    spec = PACKS.get(template)  # type: ignore[possibly-unbound]
    if spec is None or spec.estimator is None:
        _emit_error(
            f"no estimator for template {template!r}; templates with one: "
            f"{sorted(t for t, s in PACKS.items() if s.estimator is not None)}"  # type: ignore[possibly-unbound]
        )
    flags = {
        "stages": stages, "levels": levels, "enemies": enemies, "items": items,
        "rooms": rooms, "npcs": npcs, "monsters": monsters, "events": events,
        "quests": quests, "classes": classes,
    }
    count_keys = _ESTIMATE_COUNT_FLAGS.get(template, {})
    counts = {count_keys[flag]: value for flag, value in flags.items() if value is not None and flag in count_keys}
    # Doctrine 4 — a flag that belongs to another template is disabled WITH a
    # reason, never silently dropped (the same treatment `--model` gets below).
    foreign = [flag for flag, value in flags.items() if value is not None and flag not in count_keys]
    count_note: str | None = None
    if foreign:
        count_note = (
            f"{', '.join('--' + f for f in foreign)} ignored: not a {template} count flag; "
            f"{template} counts are {', '.join('--' + f for f in count_keys) or '(none)'}"
        )
    backends = {
        "llm": llm_backend, "image": image_backend,
        "music": music_backend, "sfx": sfx_backend, "vlm": vlm_backend,
    }
    est_spec = spec.estimator  # type: ignore[union-attr]
    model_note: str | None = None
    if model:
        if est_spec.models_path is None:
            est_spec = replace(est_spec, default_model=model)  # type: ignore[possibly-unbound]
        else:
            model_note = f"--model {model!r} ignored: {template} prices per agent from its models.json"
    try:
        result = estimate(  # type: ignore[possibly-unbound]
            est_spec, {"scope": "world", "counts": counts}, None, backends=backends, template=template,
        )
    except Exception as e:
        _emit_error(f"world estimate failed: {e}", traceback=traceback.format_exc())
    est = {"scope": "world", "backends": backends, **result}  # type: ignore[possibly-unbound]
    if count_note:
        est["warnings"].append(count_note)
    if model_note:
        est["warnings"].append(model_note)
    _emit({"result": "estimate", "estimate": est})


# ---------------------------------------------------------------------------
# Platformer read/export verbs — the read half external tooling (Cradle)
# shells out to instead of re-implementing .npz decoding + the tileset registry.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Pack registry — what kind of pack a directory is, and what it declares.
# ---------------------------------------------------------------------------

pack_app = typer.Typer(help="Pack registry: type, capabilities, entity kinds (P0 paper P.4.6).")
app.add_typer(pack_app, name="pack")


@pack_app.command("info")
def pack_info_cmd(
    pack_dir: Path = typer.Argument(..., help="Pack root (the directory holding manifest.json)."),
) -> None:
    """Describe a pack from its registry seed: ``pack_type`` (cradle's
    ``world_kind`` verbatim), label, capabilities, vocab, every entity kind
    with its on-disk row count / placeability / schema source, grids and
    their placements, the dialogue vocabulary when declared, the primary
    engine's evaluability blocks, engines, template provenance, and which
    resolve tier answered (``registry`` | ``manifest`` | ``shape``).

    Resolution is the P.4.1 read-both shim — a legacy pack with no stamp
    answers by shape; nothing is written. ``pack templates`` (the wizard
    cards) is the sibling verb below.
    """
    from canon.packs import PackTypeError, pack_info

    try:
        _emit(pack_info(pack_dir))
    except PackTypeError as e:
        _emit_error(str(e))


@pack_app.command("templates")
def pack_templates_cmd() -> None:
    """The installed templates + their wizard metadata (P0 paper P.4.4, row
    P0-10) — what cradle's create wizard RENDERS, in card order.

    Per template: ``id`` (= ``pack_type``), ``label``, ``description``,
    ``vocab``, ``defaults`` (the count fields and their starting values),
    ``ranges`` (their numeric bands, or ``null`` when a template has not
    authored any), ``advanced`` (the counts that live under Advanced —
    W2.1.1's primary/Advanced split), ``engine`` / ``dimension`` /
    ``distribution`` (W2.4's future axes; ``distribution`` is DERIVED from
    the engines block's ``exports``, never authored), ``beta``, and
    ``phase_labels`` — the §3.0-E phase-id → label map every progress surface
    renders, so no build hardcodes a second label list (master S5).

    Template-side and pack-less by design: this is read BEFORE a pack exists.
    Pure read; nothing is written."""
    from canon.packs import pack_templates

    _emit({"result": "templates", "templates": pack_templates()})


# ---------------------------------------------------------------------------
# Provider rows — the key screen's data (row P0-12, master §6 S6).
# ---------------------------------------------------------------------------

providers_app = typer.Typer(help="Provider rows as data: `providers list`, `providers test` (row P0-12).")
app.add_typer(providers_app, name="providers")


@providers_app.command("list")
def providers_list_cmd() -> None:
    """The provider rows cradle's Settings → API keys screen RENDERS — the
    sibling of ``pack templates`` for keys (master §6 S6: rows are DATA, never
    a hardcoded union, so adding a provider is adding a row in
    ``canon.providers``).

    Per row: ``id``, ``label``, ``env_var`` (canon's canonical name),
    ``aliases`` (other names the backend accepts — ``PIXELLAB_API_KEY`` for
    ``PIXELLAB_SECRET``), ``unlocks``, ``backends`` (``{kind: [backend id]}``,
    the missing-key precheck's map), ``docs``, ``note``, and ``test`` (the
    cheapest authenticated ping, or ``null`` when the provider publishes none).
    ``key_status`` reports, for THIS process's environment, whether each row's
    key is visible and under which NAME — never a value, never a length.

    Pack-less and pure read: this is asked before a pack exists, and nothing
    here contacts a provider."""
    from canon import providers

    _emit(
        {
            "result": "providers",
            "providers": providers.provider_rows(),
            "backend_key_vars": providers.backend_key_vars(),
            "key_status": providers.key_status(),
        }
    )


@providers_app.command("test")
def providers_test_cmd(
    provider_id: str = typer.Argument(..., help="Provider id from `providers list` (e.g. anthropic)."),
) -> None:
    """USER-INITIATED key check: one free, read-only, authenticated call to
    ``provider_id`` — never a generation (doctrine 3 keeps paid legs user-run,
    and a key test that billed would be a paid leg cradle started).

    **This contacts the provider.** It is only ever reached from an explicit
    click on the key screen's Test button. The key is read from this process's
    environment (cradle injects it from the keychain), rides in a request
    HEADER, and is never logged, echoed, or put in the URL. A provider with no
    free endpoint answers ``ran: false`` with the reason, so the button renders
    disabled-with-a-reason (doctrine 4)."""
    from canon import providers

    _emit({"result": "provider_test", **providers.test_provider(provider_id)})


registry_app = typer.Typer(help="The pack registry as data: `registry set` (P0 paper P.7.4).")
app.add_typer(registry_app, name="registry")


@registry_app.command("set")
def registry_set_cmd(
    pack_dir: Path = typer.Argument(..., help="Pack root."),
    set_json: str = typer.Option(
        ..., "--set",
        help='JSON deep-merged to the leaf into .canon/registry.json — e.g. \'{"capabilities": {"dialogue": true}}\' '
        "(map form: append), '{\"label\": \"…\"}', '{\"tuning\": {\"keys\": {\"<k>\": {\"min\": 0}}}}' "
        "(min/max/choices only). entities / engines / template / pack_type are refused.",
    ),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """Edit the pack's own registry (`db schema --set`'s idiom against
    `.canon/registry.json`): the FIRST registry-writing verb synthesizes the
    file from the template seed (P.4.1), then merges. Enabling `dialogue`
    seeds the DialogueSpec block + empty engine evaluability blocks and
    refuses when no npc-like kind exists. Journaled on artifact `registry`
    with a per-path diff (`registry_set` / `capability_set`).
    """
    from canon.packs import PackTypeError
    from canon.registry_ops import registry_set

    try:
        changes = json.loads(set_json)
    except json.JSONDecodeError as e:
        _emit_error(f"Invalid --set JSON: {e}")
    try:
        result = registry_set(pack_dir, changes, actor=actor, session=session)
    except (FileNotFoundError, ValueError, PackTypeError) as e:
        _emit_not_yet_or_error(e, pack_dir=str(pack_dir))
    except Exception as e:
        _emit_error(f"registry set failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


# ---------------------------------------------------------------------------
# Grid export — ONE verb over every GridKind (P0 paper P.6.3a; row P0-5).
# `canon grid export <pack> --level <id>` dispatches on the resolved pack's
# registry `grids`; `canon level export` keeps its signature as the alias
# (Phase 0 §6: "generalize level *; level stays as alias").
# ---------------------------------------------------------------------------

grid_app = typer.Typer(
    help="Grid read verbs — export / describe over every GridKind (platformer level / dungeon room)."
)
app.add_typer(grid_app, name="grid")

#: The GridKind → verb maps live in ``canon.adapters`` since row A3 (one map,
#: two consumers: this CLI and the agent's in-process read tools); the
#: ``module:attr`` targets resolve lazily so ``--help`` never pays for numpy.
#: A third template registers its verbs there — a data entry, never a branch
#: on ``pack_type``.
_WINDOW_HELP = (
    "Slice to a window: x0,y0,w,h in level cells (grids cut to the region, placements filtered, "
    "grid_width/grid_height stay the full dims). Omit for the whole grid."
)


def _parse_window(text: str | None) -> tuple[int, int, int, int] | None:
    """``--window x0,y0,w,h`` → the tuple ``export_level_bundle`` takes;
    ``None`` when the flag is absent. Malformed text is the structured
    error (the reader validates the numbers against the grid itself)."""
    if text is None:
        return None
    parts = [p.strip() for p in text.split(",")]
    try:
        if len(parts) != 4:
            raise ValueError
        x0, y0, w, h = (int(p) for p in parts)
    except ValueError:
        _emit_error(f"--window must be four integers x0,y0,w,h (got {text!r})")
    return x0, y0, w, h  # type: ignore[possibly-unbound]


def _grid_verb_for(pack_dir: Path, table: dict[str, str], what: str, absent: str | None = None):
    """``(resolved pack, first GridKind id, the verb ``table`` serves it
    with)`` — the shared dispatch of every ``canon grid …`` verb. Resolves the
    pack (P.4.1's read-both shim), picks the first ``GridKind`` the seed
    declares, and turns every failure into the structured error every read
    verb emits. A kind the table has no entry for is a structured refusal
    naming what serves it instead (*absent*), or the row that brings it
    (``GRID_ROOM_ROW``) when nothing does yet — doctrine 4."""
    from canon.adapters import GRID_ROOM_ROW, grid_verb
    from canon.packs import PackTypeError, resolve_pack

    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    try:
        resolved = resolve_pack(pack_dir)
    except PackTypeError as e:
        _emit_error(str(e), pack_dir=str(pack_dir))
    kind = next(iter(resolved.spec.grids), None)  # type: ignore[possibly-unbound]
    if kind is None:
        _emit_error(
            f"pack type {resolved.pack_type!r} declares no grid",  # type: ignore[possibly-unbound]
            pack_dir=str(pack_dir),
        )
    try:
        verb = grid_verb(table, kind)  # type: ignore[arg-type]
    except ImportError as e:
        _emit_error(f"Failed to import the {kind!r} grid {what}: {e}")
    if verb is None:  # type: ignore[possibly-unbound]
        _emit_error(
            absent
            or f"grid {what} is not yet available for {kind!r} grids — row {GRID_ROOM_ROW} brings it",
            pack_dir=str(pack_dir),
            grid=kind,
            **({} if absent else {"row": GRID_ROOM_ROW}),
        )
    return resolved, kind, verb  # type: ignore[possibly-unbound]


def _export_grid_bundle(pack_dir: Path, grid_id: str, window: tuple[int, int, int, int] | None = None) -> dict:
    """The render-ready bundle for one grid of *pack_dir* — the platformer
    ``LevelBundle`` shape for both pack types (P.6.3a), optionally sliced to
    ``window`` (row A3; only readers that take a ``window`` keyword — the
    room reader gains it at row P0-8). Pure read: nothing is written."""
    import inspect

    from canon.adapters import GRID_READERS, GRID_ROOM_ROW

    _, kind, reader = _grid_verb_for(pack_dir, GRID_READERS, "export")
    kwargs: dict[str, Any] = {}
    if window is not None:
        if "window" not in inspect.signature(reader).parameters:
            _emit_error(
                f"--window is not yet supported for {kind!r} grids — row {GRID_ROOM_ROW} brings it",
                pack_dir=str(pack_dir),
                grid=kind,
                row=GRID_ROOM_ROW,
            )
        kwargs["window"] = window
    try:
        return reader(pack_dir, grid_id, **kwargs)
    except FileNotFoundError as e:
        _emit_error(str(e), pack_dir=str(pack_dir), level=grid_id)
    except ValueError as e:
        _emit_error(str(e), pack_dir=str(pack_dir), level=grid_id)
    except Exception as e:
        _emit_error(f"Level export failed: {e}", traceback=traceback.format_exc())
    return {}  # pragma: no cover — every branch above exits


def _describe_grid(pack_dir: Path, grid_id: str) -> dict:
    """The compact summary of one grid — ``describe_level`` for the
    platformer (row A3), ``describe_room`` for a dungeon room (row P0-8).
    Pure read: nothing is written."""
    from canon.adapters import GRID_DESCRIBERS

    _, _, describer = _grid_verb_for(pack_dir, GRID_DESCRIBERS, "describe")
    try:
        return describer(pack_dir, grid_id)
    except FileNotFoundError as e:
        _emit_error(str(e), pack_dir=str(pack_dir), level=grid_id)
    except Exception as e:
        _emit_error(f"Level describe failed: {e}", traceback=traceback.format_exc())
    return {}  # pragma: no cover — every branch above exits


def _read_edit_payload(json_str: str | None, from_file: Path | None) -> Any:
    """``--json`` / ``--from`` (exactly one) parsed, or the structured error."""
    if json_str and from_file:
        _emit_error("Pass only one of --json / --from.")
    if not json_str and not from_file:
        _emit_error("One of --json / --from is required.")
    try:
        raw = json_str if json_str else Path(from_file).read_text(encoding="utf-8")  # type: ignore[arg-type]
        return json.loads(raw)
    except json.JSONDecodeError as e:
        _emit_error(f"Invalid edit JSON: {e}")
    return None  # pragma: no cover — every branch above exits


def _apply_grid_edit(pack_dir: Path, grid_id: str, edit: Any, *, actor: str, session: str | None) -> dict:
    """``canon grid apply-edit`` — the sparse-layer writer the pack's
    GridKind registers in ``GRID_EDITORS`` (platformer: ``apply_level_edit``
    untouched; dungeon rooms: ``apply_room_edit``, row P0-8)."""
    from canon.adapters import GRID_EDITORS

    _, _, writer = _grid_verb_for(pack_dir, GRID_EDITORS, "apply-edit")
    try:
        return writer(pack_dir, grid_id, edit, actor=actor, session=session)
    except FileNotFoundError as e:
        _emit_error(str(e), pack_dir=str(pack_dir), level=grid_id)
    except ValueError as e:
        _emit_not_yet_or_error(e, pack_dir=str(pack_dir), level=grid_id)
    except Exception as e:
        _emit_error(f"Apply-edit failed: {e}", traceback=traceback.format_exc())
    return {}  # pragma: no cover — every branch above exits


def _import_grid(pack_dir: Path, grid_id: str, payload: Any, *, actor: str, session: str | None) -> dict:
    """``canon grid import-grids`` — the dense-grid writer the pack's
    GridKind registers in ``GRID_IMPORTERS`` (platformer:
    ``import_level_grids`` untouched; dungeon rooms: ``import_room_grids``,
    row P0-8 — cells 0/1, no resize, placements re-stamped)."""
    from canon.adapters import GRID_IMPORTERS

    _, _, writer = _grid_verb_for(pack_dir, GRID_IMPORTERS, "import-grids")
    try:
        rows = payload["collision"] if isinstance(payload, dict) else payload
    except KeyError as e:
        _emit_error(f"Invalid grid JSON: {e}")
    try:
        return writer(pack_dir, grid_id, rows, actor=actor, session=session)  # type: ignore[possibly-unbound]
    except (FileNotFoundError, ValueError) as e:
        _emit_not_yet_or_error(e, pack_dir=str(pack_dir), level=grid_id)
    except Exception as e:
        _emit_error(f"Grid import failed: {e}", traceback=traceback.format_exc())
    return {}  # pragma: no cover — every branch above exits


@grid_app.command("apply-edit")
def grid_apply_edit(
    pack_dir: Path = typer.Argument(..., help="Pack root (the directory holding manifest.json)."),
    level_id: str = typer.Option(..., "--level", help="Grid id: a level id (l1) or a room id (room_0)."),
    json_str: str | None = typer.Option(
        None, "--json", help="Inline edit JSON (partial grid: entities/items/triggers/spawn/exit)."
    ),
    from_file: Path | None = typer.Option(
        None, "--from", help="Path to a JSON file with the edit (alternative to --json)."
    ),
    actor: str = typer.Option("user", "--actor", help="Who made the edit (journalled)."),
    session: str | None = typer.Option(None, "--session", help="Session id (journalled)."),
) -> None:
    """Apply a sparse-layer hand-edit (moved placements / spawn / exit) to one
    grid — dispatching on the pack's registry ``grids`` (row P0-6). The
    platformer path is ``level apply-edit`` unchanged; a dungeon room maps the
    same sparse keys onto ``maze.json`` (row P0-8), plus the additive
    ``encounters`` key that places monsters through a combat event (P.9 G4)."""
    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    edit = _read_edit_payload(json_str, from_file)
    _emit(_apply_grid_edit(pack_dir, level_id, edit, actor=actor, session=session))


@grid_app.command("import-grids")
def grid_import_grids(
    pack_dir: Path = typer.Argument(..., help="Pack root (the directory holding manifest.json)."),
    level_id: str = typer.Option(..., "--level", help="Grid id: a level id (l1) or a room id (room_0)."),
    json_str: str | None = typer.Option(
        None, "--json", help='Inline JSON: {"collision": [[...int rows...]]}'
    ),
    from_file: Path | None = typer.Option(None, "--from", help="JSON file (same shape)."),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """Apply a painted/resized dense grid to one grid — dispatching on the
    pack's registry ``grids`` (row P0-6). Platformer: ``level import-grids``
    unchanged; dungeon room: the maze cells 0/1 only, no resize, placements
    re-stamped after the paint (row P0-8)."""
    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    payload = _read_edit_payload(json_str, from_file)
    _emit(_import_grid(pack_dir, level_id, payload, actor=actor, session=session))


@grid_app.command("export")
def grid_export(
    pack_dir: Path = typer.Argument(..., help="Pack root (the directory holding manifest.json)."),
    level_id: str = typer.Option(..., "--level", help="Grid id: a level id (l1) or a room id (room_0)."),
    window: str | None = typer.Option(None, "--window", help=_WINDOW_HELP),
) -> None:
    """Emit a render-ready JSON bundle for one grid — a platformer level or a
    dungeon room — in the one ``LevelBundle`` shape cradle's level canvas
    renders (P0 paper P.6.3a). Dispatches on the pack's registry ``grids``;
    ``canon level export`` is the same verb under its original name.
    ``--window x0,y0,w,h`` (row A3) slices a level to a region.
    """
    _emit({"level": _export_grid_bundle(pack_dir, level_id, _parse_window(window))})


def _restore_grid_step(
    pack_dir: Path, grid_id: str, step: str, to_hash: str, *, actor: str, session: str | None
) -> dict:
    """``canon grid restore`` — one stored version of one grid step made
    current again through the writer the pack's GridKind registers in
    ``GRID_RESTORERS`` (platformer: ``restore_level_step`` unchanged; dungeon
    rooms: ``restore_room_step``, row P0-8). Nothing is deleted: the restore
    is a new version (doctrine 6)."""
    from canon.adapters import GRID_RESTORERS

    _, _, restorer = _grid_verb_for(pack_dir, GRID_RESTORERS, "restore")
    try:
        return restorer(pack_dir, grid_id, step, to_hash, actor=actor, session=session)
    except (FileNotFoundError, ValueError) as e:
        _emit_not_yet_or_error(e, pack_dir=str(pack_dir), level=grid_id, step=step)
    except Exception as e:
        _emit_error(f"Restore failed: {e}", traceback=traceback.format_exc())
    return {}  # pragma: no cover — every branch above exits


@grid_app.command("roll")
def grid_roll(
    pack_dir: Path = typer.Argument(..., help="Pack root (the directory holding manifest.json)."),
    level_id: str = typer.Option(..., "--level", help="Grid id: a room id (room_0)."),
    step: str = typer.Option(
        ..., "--step",
        help="What to re-roll: whole | layout | npcs | events | items | monsters.",
    ),
    encounter: str | None = typer.Option(
        None, "--encounter",
        help="For --step monsters: the combat event whose monster_ids get re-rolled.",
    ),
    seed: str | None = typer.Option(
        None, "--seed", help="Pin the roll (reproducible); omitted, the pack seed is salted."
    ),
    actor: str = typer.Option("user", "--actor", help="Who rolled (journalled)."),
    session: str | None = typer.Option(None, "--session", help="Session id (journalled)."),
) -> None:
    """Re-roll ONE step of a grid — code-only and $0 (no LLM, no provider, no
    spend: P0 paper P.6.3's per-step rolls, doctrine 3). Dispatches on the
    pack's registry ``grids``: a dungeon room takes ``whole`` (layout +
    placement + the gate), ``layout``, ``npcs``, ``events``, ``items`` and
    ``monsters`` (one encounter's roster — P.9 G4). Every roll journals; a
    platformer level rolls through its own per-step generation verbs
    (``level generate-terrain`` / ``place-enemies`` / ``place-items``).
    """
    from canon.adapters import GRID_ROLLERS

    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    _, kind, roller = _grid_verb_for(
        pack_dir, GRID_ROLLERS, "roll",
        absent=(
            "this grid has no single code-only roll verb — its per-step generation is "
            "LLM-backed and lives on `level generate-terrain` / `level place-enemies` / "
            "`level place-items`"
        ),
    )
    try:
        result = roller(
            pack_dir, level_id, step, encounter_id=encounter, seed=seed,
            actor=actor, session=session,
        )
    except (FileNotFoundError, ValueError) as e:
        _emit_not_yet_or_error(e, pack_dir=str(pack_dir), level=level_id, step=step, grid=kind)
    except Exception as e:
        _emit_error(f"Grid roll failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


@grid_app.command("restore")
def grid_restore(
    pack_dir: Path = typer.Argument(..., help="Pack root (the directory holding manifest.json)."),
    level_id: str = typer.Option(..., "--level", help="Grid id: a level id (l1) or a room id."),
    step: str = typer.Option(..., "--step", help="Step to revert (entities/items/grid/…)."),
    to_hash: str = typer.Option(..., "--to", help="Target version hash (sha256:…)."),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """Revert one grid step to a stored version — the platformer's ``level
    restore`` under the grid verb, and the room's History restore (row P0-8).
    The version being left behind stays in the object store: nothing is lost,
    and the restore writes a NEW version (doctrine 6).
    """
    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    _emit(_restore_grid_step(pack_dir, level_id, step, to_hash, actor=actor, session=session))


@grid_app.command("describe")
def grid_describe(
    pack_dir: Path = typer.Argument(..., help="Pack root (the directory holding manifest.json)."),
    level_id: str = typer.Option(..., "--level", help="Grid id: a level id (l1)."),
) -> None:
    """Emit a compact summary of one grid — dims, spawn/exit, a tile histogram
    by collision category, platform bands (run-length spans per row band, not
    the grid), placements by archetype/kind with positions, trigger and
    hazard counts, per-level overrides, and the validation verdict (row A3;
    Phase 1 §3.4 "describe first"). A dungeon room answers the same shape
    projected from its room bundle (row P0-8). ``canon level describe`` is the
    same verb under the level alias. Pure read.
    """
    _emit({"level": _describe_grid(pack_dir, level_id)})


level_app = typer.Typer(help="Platformer level inspection / export.")
app.add_typer(level_app, name="level")

level_music_app = typer.Typer(help="Per-level / per-section music.")
level_app.add_typer(level_music_app, name="music")


@level_music_app.command("list")
def level_music_list(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
) -> None:
    """List every music track file in the pack (for 'assign an existing track')."""
    ops = _pack_ops()
    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    try:
        result = ops.list_music_tracks(pack_dir)
    except Exception as e:
        _emit_error(f"level music list failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


@level_music_app.command("generate")
def level_music_generate(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
    level_id: str = typer.Option(..., "--level", help="Level (or secret room) id."),
    brief: str = typer.Option("", "--brief", help="What the track should feel like."),
    section: int | None = typer.Option(
        None, "--section", help="Index into the level's music_sections (default: the level track)."
    ),
    music_backend: str = typer.Option("lyria", "--music-backend", help="fake ($0) | lyria (paid)."),
    seconds: int | None = typer.Option(None, "--seconds", help="Track length (default 30s clip)."),
    prompt: str | None = typer.Option(
        None, "--prompt",
        help="Override the WHOLE music prompt for this call (wins over --brief; "
        "see `canon prompt show --kind music`).",
    ),
    prompt_file: Path | None = typer.Option(
        None, "--prompt-file", help="Read the prompt override from a file."
    ),
    env_file: Path | None = typer.Option(None, "--env-file", help="KEY=VALUE file (GOOGLE_API_KEY for lyria)."),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """Generate ONE music track for a level or one of its user-defined music
    sections (Lyria, paid — GOOGLE_API_KEY via --env-file; fake is $0), repoint
    the level to it (journaled), and report the actual cost."""
    _load_env_file(env_file)
    ops = _pack_ops()
    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    override = _prompt_text(prompt, prompt_file, "--prompt")
    try:
        result = ops.generate_level_music(
            pack_dir, level_id=level_id, brief=brief, section=section,
            backend=music_backend, music_seconds=seconds,
            prompt_override=override, actor=actor, session=session,
        )
    except (FileNotFoundError, ValueError, KeyError) as e:
        _emit_error(str(e), pack_dir=str(pack_dir), level=level_id)
    except Exception as e:
        _emit_error(f"level music generate failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


@level_app.command("export")
def level_export(
    pack_dir: Path = typer.Argument(..., help="Pack root (the directory holding manifest.json)."),
    level_id: str = typer.Option(..., "--level", help="Level id to export (e.g. l1)."),
    window: str | None = typer.Option(None, "--window", help=_WINDOW_HELP),
) -> None:
    """Emit a render-ready JSON bundle for one level.

    Decodes the three dense ``.npz`` grids to nested int lists, inlines the
    tileset slots + palette, resolves enemy placements against their global
    definitions, and rewrites asset refs to absolute paths. This is the
    contract a viewer renders from without needing numpy.

    Since row P0-5 this is the alias of ``canon grid export`` — the same
    dispatch, so a dungeon pack's room exports through it too. ``--window
    x0,y0,w,h`` (row A3) slices the level to a region: grids cut, placements
    filtered (absolute coordinates kept), ``grid_width``/``grid_height``
    the full dims, and the bundle gains ``window``.
    """
    _emit({"level": _export_grid_bundle(pack_dir, level_id, _parse_window(window))})


@level_app.command("describe")
def level_describe(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root (holds manifest.json)."),
    level_id: str = typer.Option(..., "--level", help="Level id to describe (e.g. l1)."),
) -> None:
    """Emit a compact summary of one level — the alias of ``canon grid
    describe`` (row A3): dims, spawn/exit, tile histogram by collision
    category, platform bands, placements with positions, trigger/hazard
    counts, overrides, the validation verdict and the revision. Pure read.
    """
    _emit({"level": _describe_grid(pack_dir, level_id)})


@level_app.command("apply-edit")
def level_apply_edit(
    pack_dir: Path = typer.Argument(..., help="Pack root (level or room grids)."),
    level_id: str = typer.Option(..., "--level", help="Level id (l1) or room id (room_0)."),
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
    ``.canon/journal.jsonl`` + the content-addressed object store. The
    ``level`` form is the alias of ``canon grid apply-edit`` (Phase 0 §6).
    """
    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    edit = _read_edit_payload(json_str, from_file)
    _emit(_apply_grid_edit(pack_dir, level_id, edit, actor=actor, session=session))


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
    and journals the edit with before/after grid snapshots. The ``level``
    form is the alias of ``canon grid import-grids`` (Phase 0 §6).
    """
    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    if bool(json_str) == bool(from_file):
        _emit_error("Exactly one of --json / --from is required.")
    payload = _read_edit_payload(json_str, from_file)
    _emit(_import_grid(pack_dir, level_id, payload, actor=actor, session=session))


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


@level_app.command("sandbox")
def level_sandbox(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
    stage_id: str | None = typer.Option(
        None, "--stage", help="Stage to borrow tiles from (default: the first)."
    ),
    width: int = typer.Option(40, "--width"),
    height: int = typer.Option(16, "--height"),
    level_id: str | None = typer.Option(
        None, "--level",
        help="Sandbox an EXISTING level instead of the reserved draft room (nothing is created).",
    ),
    spawn: str | None = typer.Option(
        None, "--spawn",
        help="Start cell 'x,y' for the launched harness (rides as PLAT_SPAWN; default: the level's spawn).",
    ),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """Create-or-reuse the flat DRAFT room the movement sandbox plays in.

    Idempotent: the room has a reserved id, so repeat launches reuse it and
    journal nothing. Play it with `PLAT_SANDBOX=1` for no win condition and a
    HUD naming the animation state the game picked and why.

    Row P1-A4.5 (C19): `--level` sandboxes an existing level (a read — the
    room is looked up, never created) and `--spawn x,y` names the start cell;
    both come back on the result as `level_id` / `spawn` plus a `launch`
    block (`PLAT_SANDBOX` + `PLAT_SPAWN`) for whoever launches the harness —
    launching stays the existing play path (cradle's `play_level`, W2.0's
    session runtime).
    """
    try:
        from canon.adapters.platformer_write import ensure_sandbox_level
    except ImportError as e:
        _emit_error(f"Failed to import platformer writer: {e}")

    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    spawn_cell: list[int] | None = None
    if spawn is not None:
        try:
            x_s, y_s = spawn.split(",")
            spawn_cell = [int(x_s), int(y_s)]
        except ValueError:
            _emit_error(f"--spawn must be 'x,y' cells, got {spawn!r}", spawn=spawn)
    try:
        if level_id is not None:
            # The lookup lives beside the agent's tool contract (row A4.5's
            # ``canon.agent.tools_play``) so the CLI and the tool answer one
            # shape; a read — a missing id is an error, never a scaffold.
            from canon.agent.tools_play import sandbox_existing_level

            result = sandbox_existing_level(pack_dir, level_id)
        else:
            result = ensure_sandbox_level(  # type: ignore[possibly-unbound]
                pack_dir, stage_id, width=width, height=height,
                actor=actor, session=session,
            )
    except (FileNotFoundError, ValueError) as e:
        _emit_error(str(e), pack_dir=str(pack_dir), stage=stage_id or "", level=level_id or "")
    except Exception as e:
        _emit_error(f"Sandbox failed: {e}", traceback=traceback.format_exc())
    result = dict(result)  # type: ignore[possibly-unbound]
    result["spawn"] = spawn_cell
    launch = {"PLAT_SANDBOX": "1"}
    if spawn_cell is not None:
        launch["PLAT_SPAWN"] = f"{spawn_cell[0]},{spawn_cell[1]}"
    result["launch"] = {"env": launch}
    _emit(result)


@level_app.command("generate")
def level_generate(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
    stage_id: str = typer.Option(..., "--stage", help="Stage the level belongs to."),
    brief: str = typer.Option("", "--brief", help="Design brief the layout agent honors."),
    difficulty: int | None = typer.Option(None, "--difficulty", help="1..3 (default: rolled by world position)."),
    width: int | None = typer.Option(None, "--width"),
    height: int | None = typer.Option(None, "--height"),
    axis: str | None = typer.Option(None, "--axis", help="horizontal | vertical (default: rolled)."),
    enemies: int = typer.Option(4, "--enemies", help="Max enemy placements."),
    items: int = typer.Option(12, "--items", help="Max item placements."),
    seed: str | None = typer.Option(None, "--seed", help="Pin for reproducibility (default: varied)."),
    llm_backend: str = typer.Option("fake", "--llm-backend", help="fake | anthropic"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    system_prompt: str | None = typer.Option(
        None, "--system-prompt",
        help="Override the LAYOUT agent's SYSTEM prompt for this call "
        "(placements keep their defaults; see `canon prompt show --kind layout`).",
    ),
    system_prompt_file: Path | None = typer.Option(
        None, "--system-prompt-file", help="Read the system override from a file."
    ),
    env_file: Path | None = typer.Option(None, "--env-file"),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """Generate a WHOLE new DRAFT level — terrain + enemies + items.

    Draft: stays out of the manifest/world map until `canon level publish`.
    Full-control pins (difficulty/width/height/axis) constrain the roll. Fake
    backend is $0 (canned DSL); anthropic is paid (key via --env-file)."""
    _load_env_file(env_file)
    ops = _pack_ops()
    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    override = _prompt_text(system_prompt, system_prompt_file, "--system-prompt")
    try:
        result = ops.generate_level(
            pack_dir, stage_id=stage_id, brief=brief, backend=llm_backend,
            model=llm_model, difficulty=difficulty, width=width, height=height,
            axis=axis, max_enemies=enemies, max_items=items, seed=seed,
            system_override=override, actor=actor, session=session,
        )
    except (FileNotFoundError, ValueError, KeyError) as e:
        _emit_error(str(e), pack_dir=str(pack_dir), stage=stage_id)
    except Exception as e:
        _emit_error(f"level generate failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


@level_app.command("estimate")
def level_estimate(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
    level_id: str = typer.Option(
        "__preview__", "--level",
        help="Existing level the op runs on (omit + pass --width to price a NEW level).",
    ),
    op: str = typer.Option(
        "generate", "--op",
        help="generate | layout | enemies | items (which per-level op to price).",
    ),
    width: int | None = typer.Option(
        None, "--width",
        help="Price a NEW level of this width instead of loading a level from disk.",
    ),
    axis: str | None = typer.Option(None, "--axis"),
    llm_backend: str = typer.Option("fake", "--llm-backend", help="fake | anthropic"),
) -> None:
    """Forecast the cost of ONE per-level op (generate / regenerate-layout /
    place-enemies / place-items) on an existing level, backend-aware (fake =
    $0). LLM-only — these ops author no art/audio."""
    try:
        from canon.packs.platformer.estimate import estimate_cradle
    except ImportError as e:  # pragma: no cover — env-specific
        _emit_error(f"Failed to import platformer estimator: {e}")
    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    try:
        est = estimate_cradle(  # type: ignore[possibly-unbound]
            op, pack_dir=pack_dir, level_id=level_id, width=width, axis=axis,
            backends={"llm": llm_backend},
        )
    except (FileNotFoundError, ValueError, KeyError) as e:
        _emit_error(str(e), pack_dir=str(pack_dir), level=level_id, op=op)
    except Exception as e:
        _emit_error(f"level estimate failed: {e}", traceback=traceback.format_exc())
    _emit({"result": "estimate", "estimate": est})  # type: ignore[possibly-unbound]


@level_app.command("generate-terrain")
def level_generate_terrain(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
    stage_id: str = typer.Option(..., "--stage", help="Stage the level belongs to."),
    brief: str = typer.Option("", "--brief", help="Design brief the layout agent honors."),
    difficulty: int | None = typer.Option(None, "--difficulty", help="1..3 (default: rolled)."),
    width: int | None = typer.Option(None, "--width"),
    height: int | None = typer.Option(None, "--height"),
    axis: str | None = typer.Option(None, "--axis", help="horizontal | vertical (default: rolled)."),
    seed: str | None = typer.Option(None, "--seed"),
    llm_backend: str = typer.Option("fake", "--llm-backend", help="fake | anthropic"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    system_prompt: str | None = typer.Option(
        None, "--system-prompt",
        help="Override the layout agent's SYSTEM prompt for this call "
        "(see `canon prompt show --kind layout`).",
    ),
    system_prompt_file: Path | None = typer.Option(
        None, "--system-prompt-file", help="Read the system override from a file."
    ),
    env_file: Path | None = typer.Option(None, "--env-file"),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """Generate ONLY a new DRAFT level's TERRAIN (no placements) — then paint
    it or run `level place-enemies`/`place-items` onto it."""
    _load_env_file(env_file)
    ops = _pack_ops()
    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    override = _prompt_text(system_prompt, system_prompt_file, "--system-prompt")
    try:
        result = ops.generate_terrain(
            pack_dir, stage_id=stage_id, brief=brief, backend=llm_backend,
            model=llm_model, difficulty=difficulty, width=width, height=height,
            axis=axis, seed=seed, system_override=override,
            actor=actor, session=session,
        )
    except (FileNotFoundError, ValueError, KeyError) as e:
        _emit_error(str(e), pack_dir=str(pack_dir), stage=stage_id)
    except Exception as e:
        _emit_error(f"level generate-terrain failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


@level_app.command("place-enemies")
def level_place_enemies(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
    level_id: str = typer.Option(..., "--level", help="Level id (generated or hand-painted)."),
    enemies: int = typer.Option(4, "--enemies", help="Max enemy placements."),
    seed: str | None = typer.Option(None, "--seed"),
    llm_backend: str = typer.Option("fake", "--llm-backend", help="fake | anthropic"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    env_file: Path | None = typer.Option(None, "--env-file"),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """Place enemies onto an EXISTING level, (re)writing entities.json against
    its on-disk grid — works on generated OR hand-painted terrain."""
    _load_env_file(env_file)
    ops = _pack_ops()
    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    try:
        result = ops.place_enemies(
            pack_dir, level_id=level_id, backend=llm_backend, model=llm_model,
            max_enemies=enemies, seed=seed, actor=actor, session=session,
        )
    except (FileNotFoundError, ValueError, KeyError) as e:
        _emit_error(str(e), pack_dir=str(pack_dir), level=level_id)
    except Exception as e:
        _emit_error(f"level place-enemies failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


@level_app.command("place-items")
def level_place_items(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
    level_id: str = typer.Option(..., "--level", help="Level id (generated or hand-painted)."),
    items: int = typer.Option(12, "--items", help="Max item placements."),
    seed: str | None = typer.Option(None, "--seed"),
    llm_backend: str = typer.Option("fake", "--llm-backend", help="fake | anthropic"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    env_file: Path | None = typer.Option(None, "--env-file"),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """Place items onto an EXISTING level, (re)writing items.json against its
    on-disk grid + enemy roster — generated OR hand-painted terrain."""
    _load_env_file(env_file)
    ops = _pack_ops()
    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    try:
        result = ops.place_items(
            pack_dir, level_id=level_id, backend=llm_backend, model=llm_model,
            max_items=items, seed=seed, actor=actor, session=session,
        )
    except (FileNotFoundError, ValueError, KeyError) as e:
        _emit_error(str(e), pack_dir=str(pack_dir), level=level_id)
    except Exception as e:
        _emit_error(f"level place-items failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


@level_app.command("regenerate")
def level_regenerate(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
    level_id: str = typer.Option(..., "--level", help="Existing level to redesign (draft or published)."),
    brief: str = typer.Option("", "--brief", help="What the redesigned level should be like."),
    difficulty: int | None = typer.Option(None, "--difficulty", help="1..3 (default: rolled)."),
    width: int | None = typer.Option(None, "--width", help="Override (default: keep current)."),
    height: int | None = typer.Option(None, "--height", help="Override (default: keep current)."),
    axis: str | None = typer.Option(None, "--axis", help="horizontal | vertical (default: rolled)."),
    seed: str | None = typer.Option(None, "--seed"),
    llm_backend: str = typer.Option("fake", "--llm-backend", help="fake | anthropic"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    system_prompt: str | None = typer.Option(
        None, "--system-prompt",
        help="Override the layout agent's SYSTEM prompt for this call "
        "(see `canon prompt show --kind layout`).",
    ),
    system_prompt_file: Path | None = typer.Option(
        None, "--system-prompt-file", help="Read the system override from a file."
    ),
    env_file: Path | None = typer.Option(None, "--env-file"),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """Regenerate an EXISTING level's TERRAIN in place from a brief — a flat
    draft becomes a designed level, or an existing one is redesigned. Keeps the
    current size unless --width/--height given; CLEARS placements (re-run
    place-enemies/place-items); leaves manifest untouched (stays published)."""
    _load_env_file(env_file)
    ops = _pack_ops()
    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    override = _prompt_text(system_prompt, system_prompt_file, "--system-prompt")
    try:
        result = ops.regenerate_terrain(
            pack_dir, level_id=level_id, brief=brief, backend=llm_backend,
            model=llm_model, difficulty=difficulty, width=width, height=height,
            axis=axis, seed=seed, system_override=override,
            actor=actor, session=session,
        )
    except (FileNotFoundError, ValueError, KeyError) as e:
        _emit_error(str(e), pack_dir=str(pack_dir), level=level_id)
    except Exception as e:
        _emit_error(f"level regenerate failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


@level_app.command("improve")
def level_improve(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
    level_id: str = typer.Option(..., "--level", help="Existing level to improve in place."),
    instruction: str = typer.Option(..., "--instruction", help="What to change (the LLM sees the current level)."),
    fix_problems: bool = typer.Option(
        False,
        "--fix-problems",
        help="Also feed the level's validation problems to the LLM.",
    ),
    reroll_placements: bool = typer.Option(
        False,
        "--reroll-placements",
        help="Re-roll enemies/items onto the improved terrain (default: keep).",
    ),
    seed: str | None = typer.Option(None, "--seed"),
    llm_backend: str = typer.Option("fake", "--llm-backend", help="fake | anthropic"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    system_prompt: str | None = typer.Option(
        None, "--system-prompt",
        help="Override the layout agent's SYSTEM prompt for this call "
        "(see `canon prompt show --kind improve`).",
    ),
    system_prompt_file: Path | None = typer.Option(
        None, "--system-prompt-file", help="Read the system override from a file."
    ),
    env_file: Path | None = typer.Option(None, "--env-file"),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """CONTEXT-AWARE improve: the layout LLM SEES the current level + your
    instruction and re-authors it in place (keeps dims/axis). Unlike
    `regenerate` this is not blind and does NOT clear placements (kept by
    default; --reroll-placements re-adapts them). Journals op=regenerate."""
    _load_env_file(env_file)
    ops = _pack_ops()
    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    override = _prompt_text(system_prompt, system_prompt_file, "--system-prompt")
    try:
        result = ops.improve_terrain(
            pack_dir, level_id=level_id, instruction=instruction,
            fix_problems=fix_problems, reroll_placements=reroll_placements,
            backend=llm_backend, model=llm_model, seed=seed,
            system_override=override, actor=actor, session=session,
        )
    except (FileNotFoundError, ValueError, KeyError) as e:
        _emit_error(str(e), pack_dir=str(pack_dir), level=level_id)
    except Exception as e:
        _emit_error(f"level improve failed: {e}", traceback=traceback.format_exc())
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
    pack_dir: Path = typer.Argument(..., help="Pack root (level or room grids)."),
    level_id: str = typer.Option(..., "--level", help="Level id (e.g. l1) or room id."),
    step: str = typer.Option(..., "--step", help="Step to revert (entities/items/triggers/grid/…)."),
    to_hash: str = typer.Option(..., "--to", help="Target version hash (from `level versions`)."),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """Revert a grid step to a stored version (original or any prior edit).

    Since row P0-8 this is the alias of ``canon grid restore`` — the same
    dispatch on the pack's registry ``grids``, so a dungeon room restores
    through its own writer. The version being left behind stays in the object
    store: nothing is lost.
    """
    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    _emit(_restore_grid_step(pack_dir, level_id, step, to_hash, actor=actor, session=session))


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


def _pack_ops(pack_dir: Path | None = None):
    """The ops module a verb dispatches to — through ``resolve_pack`` (row
    P0-6) when a pack is given: the platformer's wrapper module for a
    platformer pack (it also carries the platformer-only verbs — tile
    slots, level generation, assets), ``canon.db_ops`` — the core with the
    pack's own registry — for every other pack type. Without a pack (the
    level/asset verbs, platformer-only until their rows) the platformer
    module. Imports are deferred so ``--help`` never pays for numpy/Pillow.
    """
    if pack_dir is not None:
        from canon.packs import PackTypeError, resolve_pack

        try:
            resolved = resolve_pack(pack_dir)
        except PackTypeError as e:
            _emit_error(str(e), pack_dir=str(pack_dir))
        if resolved.pack_type != "platformer":  # type: ignore[possibly-unbound]
            from canon import db_ops

            return db_ops
    try:
        from canon.packs.platformer import ops
    except ImportError as e:  # pragma: no cover — env-specific
        _emit_error(f"Failed to import the platformer pack ops ({e}).")
    return ops


def _prompt_text(text: str | None, path: Path | None, flag: str) -> str | None:
    """Resolve a prompt override from an inline string or a file. Returns None
    when neither is given (→ the built-in default runs, byte-for-byte)."""
    if text and path:
        _emit_error(f"Pass either {flag} or {flag}-file, not both.")
    if path:
        if not path.is_file():
            _emit_error(f"Prompt file not found: {path}")
        return path.read_text()
    return text or None


prompt_app = typer.Typer(
    help="Inspect the prompts the generators send (and override them per call)."
)
app.add_typer(prompt_app, name="prompt")


@prompt_app.command("show")
def prompt_show(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
    kind: str = typer.Option(
        ..., "--kind",
        help="layout | improve | enemy | item | sprite | animate | music",
    ),
    level_id: str | None = typer.Option(
        None, "--level", help="Use this level's real data (layout/improve/music)."
    ),
    target: str | None = typer.Option(
        None, "--target",
        help="Row id for enemy/item, enemy:<id>|item:<id>|player for sprite, "
        "or enemy:<id>|player for animate.",
    ),
    instruction: str = typer.Option(
        "", "--instruction", help="Preview an improve with this instruction."
    ),
    brief: str = typer.Option("", "--brief", help="Brief for layout/music previews."),
) -> None:
    """Print the DEFAULT prompt a generator would send, WITHOUT generating.

    LLM kinds emit ``system`` (the editable standing instructions) plus the
    ``user_message`` for context; image/audio/vlm kinds emit a single
    ``prompt``. Feed an edited ``system`` back via --system-prompt on the gen
    verb (or --prompt for sprite/animate/music). Pure read: no LLM call, no
    cost, no journal."""
    ops = _pack_ops()
    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    try:
        result = ops.preview_prompt(
            pack_dir, kind, level_id=level_id, target=target,
            instruction=instruction, brief=brief,
        )
    except (FileNotFoundError, ValueError, KeyError) as e:
        _emit_error(str(e), pack_dir=str(pack_dir), kind=kind)
    except Exception as e:
        _emit_error(f"prompt show failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


journal_app = typer.Typer(help="The provenance journal — the cost dashboard's ONE source (row P1-A6).")
app.add_typer(journal_app, name="journal")


@journal_app.command("list")
def journal_list(
    pack_dir: Path = typer.Argument(..., help="Pack root whose journal to read."),
    identity: str | None = typer.Option(
        None, "--identity", help="user | agent:<conversation>/<specialist> (exact match)."
    ),
    session: str | None = typer.Option(None, "--session", help="Conversation id (§3.0-D)."),
    gen_kind: str | None = typer.Option(
        None, "--gen-kind", help="image | animation | video | code | audio | text | tokens | … (open)."
    ),
    since: str | None = typer.Option(None, "--since", help="ISO-8601; keeps events at or after it."),
    artifact_prefix: str | None = typer.Option(
        None, "--artifact-prefix", help="e.g. level:s1/ or conversation: — a startswith on artifact_id."
    ),
    limit: int | None = typer.Option(None, "--limit", help="Keep only the newest N events."),
    summary: bool = typer.Option(
        False, "--summary", help="Also emit the by-kind / by-identity / by-conversation roll-up."
    ),
) -> None:
    """Read the pack's journal with P.8.7's read-time defaults applied.

    Emits ``{"result": "journal_list", "events": [...]}``; ``--summary`` swaps
    the event list for ``"summary"`` — the tiles, the you/agent split and the
    three tables the cost dashboard renders, every figure a sum of the SAME
    ``costCents`` field, so they reconcile by construction. That swap is the
    point of the flag (BUILD 2: the roll-up computed server-side *instead of*
    shipping every event to the client); pass ``--limit`` alongside it to get
    both, with the summary computed over those same N events. Pure read: writes
    nothing, ever.
    """
    from canon.provenance import list_events, summarize_events

    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    try:
        events = list_events(
            pack_dir, identity=identity, session=session, gen_kind=gen_kind,
            since=since, artifact_prefix=artifact_prefix, limit=limit,
        )
    except Exception as e:
        _emit_error(f"journal list failed: {e}", traceback=traceback.format_exc())
    out: dict = {"result": "journal_list"}
    if summary:
        out["summary"] = summarize_events(events)  # type: ignore[possibly-unbound]
    # The roll-up REPLACES the event list unless the caller bounded the read
    # itself: shipping both would make `--summary` strictly more expensive than
    # not passing it, which is the opposite of what the flag is for.
    if not summary or limit is not None:
        out["events"] = events  # type: ignore[possibly-unbound]
    _emit(out)


spend_app = typer.Typer(help="Per-project spend ledger (what paid ops cost).")
app.add_typer(spend_app, name="spend")


@spend_app.command("record")
def spend_record(
    pack_dir: Path = typer.Argument(..., help="Pack root the spend belongs to."),
    json_str: str = typer.Option(
        ..., "--json",
        help='Spend entry, e.g. {"op":"generate","scope":"level","level_id":'
        '"l1","backends":{"llm":"anthropic"},"actual_usd":0.07}.',
    ),
) -> None:
    """Append one paid-op spend entry to <pack>/.canon/spend.jsonl. Cradle
    calls this after each op it fires (it never writes pack files itself)."""
    from canon.spend import record_spend

    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    try:
        entry = json.loads(json_str)
    except json.JSONDecodeError as e:
        _emit_error(f"--json is not valid JSON: {e}")
    if not isinstance(entry, dict):  # type: ignore[possibly-unbound]
        _emit_error("--json must be a JSON object.")
    try:
        stored = record_spend(pack_dir, entry)  # type: ignore[arg-type]
    except Exception as e:
        _emit_error(f"spend record failed: {e}", traceback=traceback.format_exc())
    _emit({"result": "spend_record", "entry": stored})  # type: ignore[possibly-unbound]


@spend_app.command("list")
def spend_list(
    pack_dir: Path = typer.Argument(..., help="Pack root to summarize."),
) -> None:
    """Emit the pack's spend ledger + a roll-up (total actual, per-op)."""
    from canon.spend import summarize

    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    try:
        summary = summarize(pack_dir)
    except Exception as e:
        _emit_error(f"spend list failed: {e}", traceback=traceback.format_exc())
    _emit({"result": "spend_list", "spend": summary})  # type: ignore[possibly-unbound]


anim_app = typer.Typer(
    help="Animation frames: inspect the geometry, correct the playback.",
)
app.add_typer(anim_app, name="anim")


@anim_app.command("inspect")
def anim_inspect_cmd(
    pack_dir: Path = typer.Argument(..., help="Pack root."),
    target: str = typer.Option(
        ..., "--target", help="player | enemy:<id> | item:<id>."
    ),
) -> None:
    """Every measurable fact about one actor's animation.

    Per state: the shared frame square, playback timing, authored offsets, and
    the measured content box of every frame — plus the two defects that are
    invisible frame by frame (states sized independently, feet wandering
    between frames of one state). Pure read.
    """
    from canon.adapters.platformer_read import read_animation

    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    try:
        anim = read_animation(pack_dir, target)
    except ValueError as e:
        _emit_error(str(e))
    except Exception as e:
        _emit_error(f"anim inspect failed: {e}", traceback=traceback.format_exc())
    _emit({"result": "anim_inspect", "animation": anim})  # type: ignore[possibly-unbound]


@anim_app.command("edit")
def anim_edit_cmd(
    pack_dir: Path = typer.Argument(..., help="Pack root."),
    target: str = typer.Option(..., "--target", help="player | enemy:<id> | item:<id>."),
    state: str = typer.Option(..., "--state", help="Animation state, e.g. fall."),
    json_str: str = typer.Option(
        ..., "--json",
        help='Playback patch, e.g. {"offsets":[[0,-2],[0,-2],[0,-1]],'
        '"durations_ms":[120,120,90],"loop":"loop"}. offsets:null clears them.',
    ),
    actor: str = typer.Option("user", "--actor", help="Who is editing (journalled)."),
    session: str | None = typer.Option(None, "--session", help="Session id."),
) -> None:
    """Correct one animation state's playback by hand — per-frame offsets,
    per-frame durations, loop mode.

    Fixes a badly-seated or badly-timed animation without paying to regenerate
    it. Frame GEOMETRY is generation's output and is not editable here; these
    are corrections layered on top, which is why re-animating clears them.
    """
    from canon.adapters.platformer_write import apply_frames_edit

    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    try:
        edit = json.loads(json_str)
    except json.JSONDecodeError as e:
        _emit_error(f"--json is not valid JSON: {e}")
    if not isinstance(edit, dict):  # type: ignore[possibly-unbound]
        _emit_error("--json must be a JSON object.")
    try:
        result = apply_frames_edit(
            pack_dir, target, state, edit, actor=actor, session=session  # type: ignore[arg-type]
        )
    except (ValueError, FileNotFoundError) as e:
        _emit_error(str(e))
    except Exception as e:
        _emit_error(f"anim edit failed: {e}", traceback=traceback.format_exc())
    _emit({"result": "anim_edit", **result})  # type: ignore[possibly-unbound]


engine_app = typer.Typer(
    help="The game runtime inside a pack (Godot project files).",
)
app.add_typer(engine_app, name="engine")


def _pack_engine():
    """Import the platformer pack's engine-export module (deferred like
    ``_pack_ops``; the pack ships inside the package since row P0-4)."""
    try:
        from canon.packs.platformer import godot_export
    except ImportError as e:  # pragma: no cover — env-specific
        _emit_error(f"Failed to import the platformer pack engine export ({e}).")
    return godot_export


@engine_app.command("status")
def engine_status_cmd(
    pack_dir: Path = typer.Argument(..., help="Pack root to check."),
) -> None:
    """Is this pack's game runtime current with canon's template?

    The runtime is COPIED into a pack when it is generated, so a pack keeps
    whatever engine code existed that day — every engine fix shipped since is
    invisible to it. Pure read; nothing is written.
    """
    engine = _pack_engine()

    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    try:
        status = engine.engine_status(pack_dir)
    except Exception as e:
        _emit_error(f"engine status failed: {e}", traceback=traceback.format_exc())
    _emit({"result": "engine_status", "status": status})  # type: ignore[possibly-unbound]


@engine_app.command("sync")
def engine_sync_cmd(
    pack_dir: Path = typer.Argument(..., help="Pack root to refresh."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report what would change; write nothing."
    ),
    force: bool = typer.Option(
        False, "--force",
        help="Overwrite hand-edited runtime files too (they are refused by default).",
    ),
    actor: str = typer.Option("user", "--actor", help="Who is syncing (journalled)."),
    session: str | None = typer.Option(None, "--session", help="Session id (journalled)."),
) -> None:
    """Refresh a pack's game runtime from canon's current template.

    A file that differs from its OWN stamp was hand-edited, so it is REFUSED
    by name rather than silently overwritten; pass --force to overwrite it
    anyway. Only the runtime is touched — generated content is never rewritten.
    """
    engine = _pack_engine()

    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    try:
        result = engine.engine_sync(
            pack_dir, dry_run=dry_run, force=force, actor=actor, session=session
        )
    except FileNotFoundError as e:
        _emit_error(str(e), pack_dir=str(pack_dir))
    except Exception as e:
        _emit_error(f"engine sync failed: {e}", traceback=traceback.format_exc())
    _emit({"result": "engine_sync", **result})  # type: ignore[possibly-unbound]


@engine_app.command("edit")
def engine_edit_cmd(
    pack_dir: Path = typer.Argument(..., help="Pack root whose engine copy is edited."),
    path: str = typer.Argument(..., help="Pack-relative file inside the engine copy, e.g. godot/main.gd."),
    diff_file: Path = typer.Option(
        ..., "--diff", help="File holding the unified diff to apply ('-' reads stdin)."
    ),
    actor: str = typer.Option("user", "--actor", help="Who is editing (journalled)."),
    session: str | None = typer.Option(None, "--session", help="Session id (journalled)."),
) -> None:
    """Apply a unified diff to a file in THIS project's own engine copy (row P1-A7.5).

    The verb behind the agent's ``edit_project_code``. It reaches ``godot/**``
    and nothing else — canon's source, the shared engine template and every
    other pack are refused by name. The file is stamped ``modified`` (so
    ``engine sync`` refuses to overwrite it), the change is journalled with
    before/after hashes, and ``canon engine restore`` undoes it.
    """
    from canon.engine_ops import CodeEditRefused, edit_project_code

    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    try:
        diff = sys.stdin.read() if str(diff_file) == "-" else diff_file.read_text(encoding="utf-8")
    except OSError as e:
        _emit_error(f"cannot read the diff: {e}")
    try:
        result = edit_project_code(pack_dir, path, diff, actor=actor, session=session)  # type: ignore[possibly-unbound]
    except CodeEditRefused as e:
        # The refusal is already a JSON document (the _emit_error shape).
        typer.echo(str(e))
        raise typer.Exit(1) from None
    except Exception as e:
        _emit_error(f"engine edit failed: {e}", traceback=traceback.format_exc())
    _emit({"result": "engine_edit", **result})  # type: ignore[possibly-unbound]


@engine_app.command("restore")
def engine_restore_cmd(
    pack_dir: Path = typer.Argument(..., help="Pack root whose engine copy is restored."),
    path: str = typer.Argument(..., help="Pack-relative file inside the engine copy, e.g. godot/main.gd."),
    version_hash: str = typer.Argument(..., help="sha256:<hex> from `canon history` / the edit's before_hash."),
    actor: str = typer.Option("user", "--actor", help="Who is restoring (journalled)."),
    session: str | None = typer.Option(None, "--session", help="Session id (journalled)."),
) -> None:
    """Put a stored version of an engine-copy file back (the one-click undo).

    Nothing is deleted: this writes a NEW version and journals ``restore``.
    When the restored bytes match what canon wrote, the ``modified`` stamp is
    CLEARED and ``engine sync`` manages the file again.
    """
    from canon.engine_ops import CODE_NAMESPACE, CodeEditRefused, restore_code_file

    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    try:
        result = restore_code_file(
            pack_dir, f"{CODE_NAMESPACE}:{path}", version_hash, actor=actor, session=session
        )
    except CodeEditRefused as e:
        typer.echo(str(e))
        raise typer.Exit(1) from None
    except Exception as e:
        _emit_error(f"engine restore failed: {e}", traceback=traceback.format_exc())
    _emit({"result": "engine_restore", **result})  # type: ignore[possibly-unbound]


@engine_app.command("gate")
def engine_gate_cmd(
    pack_dir: Path = typer.Argument(..., help="Pack root to run the gate ladder against."),
    path: list[str] = typer.Option(
        None, "--path", help="Engine-copy file that changed (repeatable); drives the syntax rung."
    ),
    level: list[str] = typer.Option(
        None, "--level", help="Level to smoke and validate (repeatable); default: the pack's first."
    ),
) -> None:
    """Run the §7.1 gate ladder: syntax, headless boot, scripted smoke, validate.

    The same harness the agent runs automatically after a code edit. Godot's
    exit code lies, so the boot/smoke verdicts are the ``SCRIPT ERROR`` count
    and the trajectory the run produced. With no Godot on this machine the
    engine rungs report ``unproven`` by name — never a false green.
    """
    from canon.agent.gates import ladder_summary, run_ladder, smoke_levels

    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    levels = list(level or []) or smoke_levels(pack_dir)
    try:
        ladder = run_ladder(pack_dir, paths=list(path or []), levels=levels)
    except Exception as e:
        _emit_error(f"engine gate failed: {e}", traceback=traceback.format_exc())
    _emit({"result": "engine_gate", "summary": ladder_summary(ladder), **ladder})  # type: ignore[possibly-unbound]


jobs_app = typer.Typer(help="Per-project job ledger (background generation runs).")
app.add_typer(jobs_app, name="jobs")


@jobs_app.command("record")
def jobs_record(
    pack_dir: Path = typer.Argument(..., help="Pack root the job belongs to."),
    json_str: str = typer.Option(
        ..., "--json",
        help='Job entry, e.g. {"job_id":"ab12","op":"improve","target":"l1",'
        '"status":"ok","changed":true,"duration_ms":8200}.',
    ),
) -> None:
    """Append one background-job entry to <pack>/.canon/jobs.jsonl. Cradle's
    worker calls this on job completion (it never writes pack files itself)."""
    from canon.jobs import record_job

    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    try:
        entry = json.loads(json_str)
    except json.JSONDecodeError as e:
        _emit_error(f"--json is not valid JSON: {e}")
    if not isinstance(entry, dict):  # type: ignore[possibly-unbound]
        _emit_error("--json must be a JSON object.")
    try:
        stored = record_job(pack_dir, entry)  # type: ignore[arg-type]
    except Exception as e:
        _emit_error(f"jobs record failed: {e}", traceback=traceback.format_exc())
    _emit({"result": "jobs_record", "entry": stored})  # type: ignore[possibly-unbound]


@jobs_app.command("list")
def jobs_list(
    pack_dir: Path = typer.Argument(..., help="Pack root to summarize."),
) -> None:
    """Emit the pack's job ledger + a roll-up (count, per-op, per-status)."""
    from canon.jobs import summarize

    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    try:
        summary = summarize(pack_dir)
    except Exception as e:
        _emit_error(f"jobs list failed: {e}", traceback=traceback.format_exc())
    _emit({"result": "jobs_list", "jobs": summary})  # type: ignore[possibly-unbound]


db_app = typer.Typer(help="Generic database rows: create / LLM-complete (anchored).")
app.add_typer(db_app, name="db")


@db_app.command("types")
def db_types_cmd(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
) -> None:
    """The entity-type registry + field specs (drives editor form UIs) —
    every kind the pack's registry declares, with the P.1 field lists."""
    ops = _pack_ops(pack_dir)
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
    system_prompt: str | None = typer.Option(
        None, "--system-prompt",
        help="Override the authoring agent's SYSTEM prompt for this call "
        "(see `canon prompt show --kind enemy|item`).",
    ),
    system_prompt_file: Path | None = typer.Option(
        None, "--system-prompt-file", help="Read the system override from a file."
    ),
    env_file: Path | None = typer.Option(None, "--env-file"),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """Create ONE new row: anchored skeleton roll + optional LLM completion,
    exactly as pipeline generation would (same prompts, rng streams, retry).
    A kind without a per-row generation body (dungeon kinds, `db define`d
    kinds) rolls its skeleton only; `--complete` is a structured not-yet."""
    _load_env_file(env_file)
    ops = _pack_ops(pack_dir)
    try:
        fields = json.loads(fields_json) if fields_json else {}
    except json.JSONDecodeError as e:
        _emit_error(f"Invalid --fields JSON: {e}")
    override = _prompt_text(system_prompt, system_prompt_file, "--system-prompt")
    try:
        llm = ops.build_llm(llm_backend if complete else None, llm_model)
        result = ops.new_db_row(
            pack_dir, entity_type, fields,
            complete=complete, llm=llm, system_override=override,
            actor=actor, session=session,
        )
    except (FileNotFoundError, ValueError, KeyError) as e:
        _emit_not_yet_or_error(e, pack_dir=str(pack_dir), type=entity_type)
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
    system_prompt: str | None = typer.Option(
        None, "--system-prompt",
        help="Override the authoring agent's SYSTEM prompt for this call "
        "(see `canon prompt show --kind enemy|item`).",
    ),
    system_prompt_file: Path | None = typer.Option(
        None, "--system-prompt-file", help="Read the system override from a file."
    ),
    env_file: Path | None = typer.Option(None, "--env-file"),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """LLM-complete an existing row, anchored by its locked fields (a kind
    without a per-row completion body answers a structured not-yet)."""
    _load_env_file(env_file)
    ops = _pack_ops(pack_dir)
    override = _prompt_text(system_prompt, system_prompt_file, "--system-prompt")
    try:
        llm = ops.build_llm(llm_backend, llm_model)
        result = ops.complete_db_row(
            pack_dir, entity_type, entity_id,
            [s.strip() for s in locked.split(",")] if locked else [],
            reroll=reroll, llm=llm, system_override=override,
            actor=actor, session=session,
        )
    except (FileNotFoundError, ValueError, KeyError) as e:
        _emit_not_yet_or_error(e, pack_dir=str(pack_dir), id=entity_id)
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
    """DIRECT human edit of an existing row (no LLM, no rerolls): any
    registered kind's fields (list containers as `<c>[<i>].<key>`), or
    collision/params for a platformer tile type. Rehashes, stamps
    ``user_edited``, journals ``op:"edit"`` with the field diff."""
    ops = _pack_ops(pack_dir)
    try:
        changes = json.loads(set_json)
    except json.JSONDecodeError as e:
        _emit_error(f"Invalid --set JSON: {e}")
    if entity_type == "tile" and not hasattr(ops, "update_tile_slots"):
        # Outside the op try: _emit_error's Exit must not be re-caught below.
        _emit_error(
            "--type tile edits a platformer tileset; this pack declares no tilesets",
            pack_dir=str(pack_dir), type=entity_type,
        )
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
        _emit_not_yet_or_error(e, pack_dir=str(pack_dir), type=entity_type, id=entity_id)
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
    ops = _pack_ops(pack_dir)
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


@db_app.command("define")
def db_define_cmd(
    pack_dir: Path = typer.Argument(..., help="Pack root."),
    kind: str = typer.Option(..., "--type", help="The net-new kind id (e.g. player_ability)."),
    set_json: str = typer.Option(
        ..., "--set",
        help='Partial EntityKind JSON — minimum label, layout, id_field; optional id_alloc, llm_fields, '
        'user_fields, …, and an inline "schema": {"fields": {...}} (P0 paper P.7.5).',
    ),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """Define a net-new row type in THIS pack's registry — the
    project-evolution verb (Phase 0 §5.1a, success criterion 6): writes
    schemas/<kind>.json, the empty collection file, and the registry entry
    (synthesizing .canon/registry.json from the template seed on first
    use); journals `db_define` on `registry` + a `create` per file. Every
    generic verb serves the kind from then on with zero code changes."""
    from canon.db_ops import db_define
    from canon.packs import PackTypeError

    try:
        payload = json.loads(set_json)
    except json.JSONDecodeError as e:
        _emit_error(f"Invalid --set JSON: {e}")
    try:
        result = db_define(pack_dir, kind, payload, actor=actor, session=session)
    except (FileNotFoundError, ValueError, PackTypeError) as e:
        _emit_not_yet_or_error(e, pack_dir=str(pack_dir), type=kind)
    except Exception as e:
        _emit_error(f"db define failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


@db_app.command("evolve")
def db_evolve_cmd(
    pack_dir: Path = typer.Argument(..., help="Pack root."),
    kind: str = typer.Option(..., "--type", help="The kind to evolve."),
    rename_field: str | None = typer.Option(None, "--rename-field", help="old:new — a mechanical field rename."),
    rename_type: str | None = typer.Option(None, "--rename-type", help="v1.1 — answers a structured not-yet."),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """Mechanical, journaled field rename across a kind's rows + registry
    entry (code applies it; no LLM): rewrites every row, updates the
    `renames` map and every registry list naming the field, journals
    `db_evolve` on `registry` + one `edit` per rewritten file, and warns
    loudly that the engine must follow. Type renames are v1.1."""
    from canon.db_ops import db_evolve
    from canon.packs import PackTypeError

    try:
        result = db_evolve(
            pack_dir, kind, rename_field=rename_field, rename_type=rename_type, actor=actor, session=session,
        )
    except (FileNotFoundError, ValueError, PackTypeError) as e:
        _emit_not_yet_or_error(e, pack_dir=str(pack_dir), type=kind)
    except Exception as e:
        _emit_error(f"db evolve failed: {e}", traceback=traceback.format_exc())
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
    prompt: str | None = typer.Option(
        None, "--prompt",
        help="Override the image prompt (sprite targets) or music prompt "
        "(audio targets) for this call — see `canon prompt show --kind sprite`.",
    ),
    prompt_file: Path | None = typer.Option(
        None, "--prompt-file", help="Read the prompt override from a file."
    ),
    env_file: Path | None = typer.Option(None, "--env-file"),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """(Re)generate ONE asset via the real art/audio phases (single-image
    path). Explicit backends only; paid keys via --env-file / CANON_ENV_FILE."""
    _load_env_file(env_file)
    ops = _pack_ops()
    override = _prompt_text(prompt, prompt_file, "--prompt")
    try:
        result = ops.generate_asset(
            pack_dir, target,
            image_backend=image_backend, image_model=image_model,
            image_edit_model=image_edit_model,
            image_edit_backend=image_edit_backend,
            music_backend=music_backend, sfx_backend=sfx_backend,
            prompt_override=override, actor=actor, session=session,
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
    renormalize: bool = typer.Option(
        False, "--renormalize",
        help="REFRAME ONLY, $0, no backends: re-seat the frames already on "
        "disk on one shared square, giving every state equal headroom and "
        "repacking the atlas. It canNOT restore cross-state proportions — "
        "those were lost when each state was scaled to fill its own cell; "
        "re-animate for that.",
    ),
    prompt: str | None = typer.Option(
        None, "--prompt",
        help="Override the VLM's motion-spec authoring prompt for this call — "
        "see `canon prompt show --kind animate`. Inert with --reuse-spec / "
        "--renormalize, which never author.",
    ),
    prompt_file: Path | None = typer.Option(
        None, "--prompt-file", help="Read the prompt override from a file."
    ),
    env_file: Path | None = typer.Option(None, "--env-file"),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """Animate ONE actor (the multi-image path): VLM-authored motion spec →
    one img2img sheet per state → strips + frames.json + packed atlas.

    With --renormalize it instead repairs the existing frames in place (free)."""
    _load_env_file(env_file)
    override = _prompt_text(prompt, prompt_file, "--prompt")
    ops = _pack_ops()
    try:
        result = ops.animate_asset(
            pack_dir, target,
            image_backend=image_backend, image_model=image_model,
            image_edit_model=image_edit_model,
            image_edit_backend=image_edit_backend,
            vlm_backend=vlm_backend, vlm_model=vlm_model,
            reuse_spec=reuse_spec, renormalize=renormalize,
            prompt_override=override,
            actor=actor, session=session,
        )
    except (FileNotFoundError, ValueError) as e:
        _emit_error(str(e), pack_dir=str(pack_dir), target=target)
    except Exception as e:
        _emit_error(f"asset animate failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


@asset_app.command("estimate")
def asset_estimate(
    pack_dir: Path = typer.Argument(..., help="Platformer pack root."),
    target: str = typer.Option(..., "--target", help="enemy:<id> | player"),
    op: str = typer.Option("animate", "--op", help="animate (the only op today)."),
    reuse_spec: bool = typer.Option(
        False, "--reuse-spec", help="Price a run that skips the VLM authoring call."
    ),
    image_backend: str = typer.Option("fake", "--image-backend", help="fal | fake"),
    vlm_backend: str = typer.Option("none", "--vlm-backend", help="anthropic | none"),
) -> None:
    """Forecast the cost of animating ONE actor, backend-aware (fake/none =
    $0). Priced BY STATES — one img2img edit per state per facing — plus one
    VLM authoring call unless --reuse-spec."""
    try:
        from canon.packs.platformer.estimate import estimate_cradle
    except ImportError as e:  # pragma: no cover — env-specific
        _emit_error(f"Failed to import platformer estimator: {e}")
    if not pack_dir.exists():
        _emit_error(f"Pack directory not found: {pack_dir}", pack_dir=str(pack_dir))
    try:
        est = estimate_cradle(  # type: ignore[possibly-unbound]
            op, pack_dir=pack_dir, target=target, reuse_spec=reuse_spec,
            backends={"image": image_backend, "vlm": vlm_backend},
        )
    except (FileNotFoundError, ValueError, KeyError) as e:
        _emit_error(str(e), pack_dir=str(pack_dir), target=target, op=op)
    except Exception as e:
        _emit_error(f"asset estimate failed: {e}", traceback=traceback.format_exc())
    _emit({"result": "estimate", "estimate": est})  # type: ignore[possibly-unbound]


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


agent_app = typer.Typer(help="The agent service — cradle's localhost sidecar (Phase 1 A2).")
app.add_typer(agent_app, name="agent")


@agent_app.command("serve")
def agent_serve_cmd(
    pack_dir: Path = typer.Option(..., "--pack", help="Pack root the conversations belong to (required)."),
    backend: str = typer.Option("fake", "--backend", help="Chat backend id (data): fake | anthropic | openai | kimi."),
    model: str | None = typer.Option(None, "--model", help="Model id for the backend (a plain string)."),
    port: int = typer.Option(0, "--port", help="Port on 127.0.0.1; 0 = a free port picked by the OS."),
    parent_pid: int | None = typer.Option(
        None, "--parent-pid", help="Exit when this process is gone (cradle passes its own pid)."
    ),
    fake_script: Path | None = typer.Option(
        None, "--fake-script", help="JSON turns file the fake backend plays (fake only; $0)."
    ),
) -> None:
    """Serve the agent over HTTP+SSE on 127.0.0.1.

    The bound port and pid are printed as the FIRST stdout line
    (``{"port": N, "pid": P}``), then uvicorn runs until SIGTERM,
    ``POST /shutdown``, or the ``--parent-pid`` process dies. Same
    ``main()`` as ``python -m canon.agent.service``; needs the ``agent``
    extra (``pip install canon-ai[agent]``).
    """
    try:
        from canon.agent.service import main as serve_main
    except ImportError as e:
        _emit_error(f"the agent service needs the `agent` extra (pip install canon-ai[agent]): {e}")
    argv = ["--pack", str(pack_dir), "--backend", backend, "--port", str(port)]
    if model is not None:
        argv += ["--model", model]
    if parent_pid is not None:
        argv += ["--parent-pid", str(parent_pid)]
    if fake_script is not None:
        argv += ["--fake-script", str(fake_script)]
    raise typer.Exit(serve_main(argv))  # type: ignore[possibly-unbound]


dialogue_app = typer.Typer(
    help="NPC dialogue: selector-model trees, gates, the tester and the selector "
    "(Phase 0 §7.2; row P0-9).",
)
app.add_typer(dialogue_app, name="dialogue")

scene_app = typer.Typer(
    help="Group scenes — the `type: \"scene\"` event rows (P0 paper P.1.5 / P.9 S7).",
)
app.add_typer(scene_app, name="scene")


def _dialogue_payload(raw: str | None, what: str) -> Any:
    """A JSON payload given inline, as ``-`` for stdin, or as a file path —
    the tester tests the UNSAVED buffer, so the payload never comes from a
    pack lookup (`PLAN.md:256`)."""
    if raw is None:
        return None
    text = raw
    if raw == "-":
        text = sys.stdin.read()
    elif not raw.lstrip().startswith(("{", "[")):
        path = Path(raw)
        if not path.is_file():
            _emit_error(f"{what}: {raw!r} is neither JSON, '-' (stdin), nor a readable file")
        text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        _emit_error(f"Invalid {what} JSON: {e}")


@dialogue_app.command("show")
def dialogue_show_cmd(
    pack_dir: Path = typer.Argument(..., help="Pack root."),
    npc: str = typer.Option(..., "--npc", help="NPC row id."),
) -> None:
    """The NPC's trees, their selectors, ranks and gates, plus each token's
    engine-evaluability against the primary engine's block — the data the
    navigator rail and the gate ribbon render. Writes nothing."""
    from canon.dialogue.verbs import dialogue_show
    from canon.packs import PackTypeError

    try:
        result = dialogue_show(pack_dir, npc)
    except (FileNotFoundError, ValueError, PackTypeError) as e:
        _emit_not_yet_or_error(e, pack_dir=str(pack_dir), npc=npc)
    except Exception as e:
        _emit_error(f"dialogue show failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


@dialogue_app.command("update")
def dialogue_update_cmd(
    pack_dir: Path = typer.Argument(..., help="Pack root."),
    npc: str = typer.Option(..., "--npc", help="NPC row id."),
    ops_json: str = typer.Option(
        ..., "--ops",
        help="The EditOp list as JSON (inline, a file path, or '-' for stdin) — "
        "the design package's op union, applied as ONE batch.",
    ),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """Apply an EditOp list to one NPC's dialogue as one batch: fail-closed
    validation, `dialogue_trees` + the legacy four rewritten together, one CAS
    snapshot, one journal event carrying a per-op diff."""
    from canon.dialogue.verbs import dialogue_update
    from canon.packs import PackTypeError

    ops = _dialogue_payload(ops_json, "--ops")
    try:
        result = dialogue_update(pack_dir, npc, ops, actor=actor, session=session)
    except (FileNotFoundError, ValueError, PackTypeError) as e:
        _emit_not_yet_or_error(e, pack_dir=str(pack_dir), npc=npc)
    except Exception as e:
        _emit_error(f"dialogue update failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


@dialogue_app.command("validate")
def dialogue_validate_cmd(
    pack_dir: Path = typer.Argument(..., help="Pack root."),
    npc: str = typer.Option(..., "--npc", help="NPC row id."),
) -> None:
    """`{errors[], warnings[]}` for one NPC. Unreachable nodes, dangling
    targets, uncoverable selector rows and engine lag are WARNINGS and never
    block a save (doctrine 10)."""
    from canon.dialogue.verbs import dialogue_validate
    from canon.packs import PackTypeError

    try:
        result = dialogue_validate(pack_dir, npc)
    except (FileNotFoundError, ValueError, PackTypeError) as e:
        _emit_not_yet_or_error(e, pack_dir=str(pack_dir), npc=npc)
    except Exception as e:
        _emit_error(f"dialogue validate failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


@dialogue_app.command("test")
def dialogue_test_cmd(
    pack_dir: Path | None = typer.Argument(None, help="Pack root (optional: gives operand tables + the engine block)."),
    tree: str | None = typer.Option(
        None, "--tree", help="The tree payload — inline JSON, a file, or '-' for stdin (the UNSAVED buffer)."
    ),
    npc: str | None = typer.Option(None, "--npc", help="Read a STORED tree instead of a payload."),
    tree_id: str | None = typer.Option(None, "--tree-id", help="With --npc: which stored tree."),
    state: str | None = typer.Option(None, "--state", help="Simulated state JSON (inline, file or '-')."),
    node: str | None = typer.Option(None, "--node", help="Start at this node instead of the entry."),
    choose: int | None = typer.Option(None, "--choose", help="Take this choice: fires its effects."),
) -> None:
    """Walk a tree against a simulated state: per-choice pass / fail /
    unevaluable with the FAILING CONDITION NAMED, the effect ledger, and the
    post-effect state. ONE evaluator — the UI never reimplements gating."""
    from canon.dialogue.verbs import dialogue_test, load_tree
    from canon.packs import PackTypeError

    payload = _dialogue_payload(tree, "--tree")
    sim = _dialogue_payload(state, "--state") or {}
    try:
        if payload is None:
            if npc is None or pack_dir is None:
                _emit_error("dialogue test needs --tree <payload>, or a pack root + --npc")
            payload = load_tree(pack_dir, npc, tree_id)  # type: ignore[arg-type]
        result = dialogue_test(payload, sim, pack_dir=pack_dir, node_id=node, choose=choose)
    except (FileNotFoundError, ValueError, PackTypeError) as e:
        _emit_not_yet_or_error(e, npc=npc)
    except Exception as e:
        _emit_error(f"dialogue test failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


@dialogue_app.command("select")
def dialogue_select_cmd(
    pack_dir: Path = typer.Argument(..., help="Pack root."),
    npc: str = typer.Option(..., "--npc", help="NPC row id."),
    state: str | None = typer.Option(None, "--state", help="Simulated state JSON (inline, file or '-')."),
) -> None:
    """Which tree the state selects, and why each other tree did not — the
    rail's would-play / blocked grouping, plus the engine's own pick when it
    diverges (the selector-level engine-lag case)."""
    from canon.dialogue.verbs import dialogue_select
    from canon.packs import PackTypeError

    sim = _dialogue_payload(state, "--state") or {}
    try:
        result = dialogue_select(pack_dir, npc, sim)
    except (FileNotFoundError, ValueError, PackTypeError) as e:
        _emit_not_yet_or_error(e, pack_dir=str(pack_dir), npc=npc)
    except Exception as e:
        _emit_error(f"dialogue select failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


@dialogue_app.command("improve")
def dialogue_improve_cmd(
    pack_dir: Path = typer.Argument(..., help="Pack root."),
    npc: str = typer.Option(..., "--npc", help="NPC row id."),
    instruction: str = typer.Option("", "--instruction", help="What to improve."),
    tree_id: str | None = typer.Option(None, "--tree-id", help="Which tree (default: the first)."),
    scope: str = typer.Option("tree", "--scope", help="tree | npc"),
    backend: str = typer.Option("none", "--backend", help="none | fake ($0, deterministic) | a chat provider id."),
    model: str | None = typer.Option(None, "--model"),
    keep_structure: bool = typer.Option(True, "--keep-structure/--allow-structure"),
    actor: str = typer.Option("user", "--actor"),
    env_file: Path | None = typer.Option(None, "--env-file"),
) -> None:
    """Propose per-field rewrites. NEVER a write: accepted rows land in the
    caller's unsaved buffer and ship through `dialogue update`. `none`/`fake`
    run the built-in deterministic proposer at $0; any other backend id is a
    real, user-run provider call."""
    from canon.dialogue.improve import dialogue_improve
    from canon.packs import PackTypeError

    _load_env_file(env_file)
    try:
        result = dialogue_improve(
            pack_dir, npc, instruction=instruction, tree_id=tree_id, scope=scope,
            backend=backend, model=model, keep_structure=keep_structure, actor=actor,
        )
    except (FileNotFoundError, ValueError, KeyError, PackTypeError) as e:
        _emit_not_yet_or_error(e, pack_dir=str(pack_dir), npc=npc)
    except Exception as e:
        _emit_error(f"dialogue improve failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


@scene_app.command("update")
def scene_update_cmd(
    pack_dir: Path = typer.Argument(..., help="Pack root."),
    scene: str | None = typer.Option(None, "--scene", help="Scene (event) id; omit with --create to allocate one."),
    ops_json: str | None = typer.Option(
        None, "--ops", help="The scene EditOp list as JSON (inline, a file, or '-')."
    ),
    create: bool = typer.Option(False, "--create", help="Create the scene row if it does not exist."),
    title: str = typer.Option("", "--title", help="With --create: the scene title."),
    actor: str = typer.Option("user", "--actor"),
    session: str | None = typer.Option(None, "--session"),
) -> None:
    """Apply scene EditOps to one `type: "scene"` event row. Scene writes go
    through the event kind's row path and NEVER touch `event_positions`, so
    the engine is never handed a scene to trigger (P.9 S7)."""
    from canon.dialogue.verbs import scene_update
    from canon.packs import PackTypeError

    ops = _dialogue_payload(ops_json, "--ops") if ops_json else []
    try:
        result = scene_update(
            pack_dir, scene, ops, actor=actor, session=session, create=create, title=title,
        )
    except (FileNotFoundError, ValueError, PackTypeError) as e:
        _emit_not_yet_or_error(e, pack_dir=str(pack_dir), scene=scene)
    except Exception as e:
        _emit_error(f"scene update failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


@scene_app.command("validate")
def scene_validate_cmd(
    pack_dir: Path = typer.Argument(..., help="Pack root."),
    scene: str = typer.Option(..., "--scene", help="Scene (event) id."),
) -> None:
    """`{errors[], warnings[]}` for one scene row."""
    from canon.dialogue.verbs import scene_validate
    from canon.packs import PackTypeError

    try:
        result = scene_validate(pack_dir, scene)
    except (FileNotFoundError, ValueError, PackTypeError) as e:
        _emit_not_yet_or_error(e, pack_dir=str(pack_dir), scene=scene)
    except Exception as e:
        _emit_error(f"scene validate failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


@scene_app.command("test")
def scene_test_cmd(
    pack_dir: Path | None = typer.Argument(None, help="Pack root (optional: gives the engine block)."),
    scene_payload: str | None = typer.Option(
        None, "--scene-payload", help="The scene document — inline JSON, a file, or '-' (the UNSAVED buffer)."
    ),
    scene: str | None = typer.Option(None, "--scene", help="Read the STORED scene row instead."),
    state: str | None = typer.Option(
        None, "--state", help="Simulated state JSON — carries `actors` presence for scenes."
    ),
) -> None:
    """Play a scene against a simulated state that carries ACTOR PRESENCE.
    An absent required actor cancels the scene; an absent optional actor's
    lines are skipped and named, never silently dropped."""
    from canon.dialogue.verbs import load_scene, scene_test
    from canon.packs import PackTypeError

    payload = _dialogue_payload(scene_payload, "--scene-payload")
    sim = _dialogue_payload(state, "--state") or {}
    try:
        if payload is None:
            if scene is None or pack_dir is None:
                _emit_error("scene test needs --scene-payload <payload>, or a pack root + --scene")
            payload = load_scene(pack_dir, scene)  # type: ignore[arg-type]
        result = scene_test(payload, sim, pack_dir=pack_dir)
    except (FileNotFoundError, ValueError, PackTypeError) as e:
        _emit_not_yet_or_error(e, scene=scene)
    except Exception as e:
        _emit_error(f"scene test failed: {e}", traceback=traceback.format_exc())
    _emit(result)  # type: ignore[possibly-unbound]


def main() -> None:
    app()


if __name__ == "__main__":
    main()
