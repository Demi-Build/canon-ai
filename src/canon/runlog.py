"""Per-project run log — ``<pack>/.canon/runlog.jsonl``: everything one canon
process did, so a failed or surprising run can be read back and turned into
a debug report (``canon debug report``) without re-running it.

Why another file. The pack already keeps four records, each owned by one
concern: the provenance JOURNAL (artifact mutations), the SPEND ledger
(dollars), the JOB ledger (one status line per queued job) and the STEP log
(``.canon/log.jsonl``, the phase progress cradle tails). None of them says
what happened *inside* a run: which provider was called, how long it took,
what it answered, how many retries it burned, what the Python log said
while it ran. Pass 4 lost its reasons exactly there — 26 assets failed and
the causes (a wrong Google key, impossible SFX durations, a rate-limit
burst) were only recoverable by guessing, and every warning a runner logged
went to a stderr that ``world new`` trimmed to its last 800 bytes.

This file is that missing record. Observability only, like the step log:
nothing may read it back to make a decision, and it sits outside the
byte-determinism contract (wall-clock timestamps throughout).

One JSON object per line, every line carrying ``schema``, ``ts`` (UTC ISO
8601, ms), ``run`` (the run id) and ``event``:

- ``run_start`` — the command (secrets redacted), cwd, pid, versions, the
  ``parent`` run when a canon process spawned this one (``world new`` spawns
  its template runner).
- ``env`` — which provider keys were visible when the first paid call was
  made, and under which variable; names only, never a value
  (:func:`canon.providers.key_status`), plus any key an ``--env-file`` could
  NOT set because the shell already exported a different value (the Pass 4
  "shell is shadowing your fresh keys" finding).
- ``call`` — ONE provider call: ``lane`` (llm / vlm / chat / asset),
  ``provider``, ``label``, ``model``, ``attempt``, ``ok``, ``duration_ms``,
  tokens and ``cost_usd`` when known, and on failure the classified
  ``error`` (the :class:`canon.backends.failures.AssetError` record shape:
  kind, status, message, retryable, request_id, hint).
- ``cost`` — money a lane credited to the run's stats outside a ``call``
  line (asset phases meter their spend after the call returns).
- ``asset_failed`` — the final word on an asset that did not land.
- ``step`` — a mirror of each step-log event (phase starts, items, ends).
- ``log`` — a Python log record (``canon.*`` at INFO and above, everything
  else at WARNING and above), with the traceback when there was one.
- ``run_end`` — exit code, duration, the uncaught exception and its
  traceback if any, and the run's totals.

When a run writes. A process that makes no provider call, logs nothing at
WARNING or above and exits 0 (``level export``, ``spend list`` — the reads
cradle makes constantly) writes nothing: its events are buffered and
dropped. The first "interesting" event (a call, a warning, a failure)
flushes the buffer and the rest of the run streams to disk line by line, so
a run killed mid-way (cradle's Stop kills after a grace period) keeps
everything up to the kill. ``CANON_RUNLOG=all`` logs every command;
``CANON_RUNLOG=off`` logs nothing.

Where it writes. A run binds to a pack the first time one is known: a
``StepLog`` opened on an output dir binds it (every pipeline run makes
one), and otherwise the command line is scanned for a directory holding a
``.canon/`` folder (every pack verb names its pack). A run that never finds
a pack writes nothing.

Secrets. Command-line values after a flag naming a key/token/secret are
replaced, and every string written is scrubbed of the values of the
process's key-like environment variables and of common key shapes, so an
error message that echoes a key never lands on disk with it.

The file rotates at :data:`MAX_BYTES` to ``runlog.1.jsonl`` (one
generation kept); the report reads both.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import secrets
import sys
import threading
import time
import traceback
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = [
    "ENV_MODE",
    "ENV_PARENT",
    "FILENAME",
    "ROTATED_FILENAME",
    "SCHEMA",
    "RunLog",
    "active",
    "bind",
    "end",
    "event",
    "log_path",
    "note_cost",
    "note_env_shadowed",
    "provider_of",
    "read_runlog",
    "record_call",
    "run_main",
    "session",
    "start",
    "timed_call",
]

SCHEMA = "canon-runlog/v1"
DIRNAME = ".canon"
FILENAME = "runlog.jsonl"
ROTATED_FILENAME = "runlog.1.jsonl"

#: Rotate the log once it passes this size (one older generation is kept).
MAX_BYTES = 20 * 1024 * 1024

#: ``off`` = never write · ``all`` = write every run · anything else = write
#: runs that did something worth reading (see the module doc).
ENV_MODE = "CANON_RUNLOG"

#: Set for child processes so a spawned runner names the run that spawned it.
ENV_PARENT = "CANON_PARENT_RUN"

#: Longest string kept in any field; longer ones are cut and marked.
MAX_FIELD_CHARS = 4000

#: Events a run holds in memory before anything makes it worth writing.
MAX_QUIET_BUFFER = 500

#: Longest traceback kept on a ``log`` / ``run_end`` line.
MAX_TRACE_CHARS = 12000

_SECRET_FLAG = re.compile(r"(key|token|secret|password|passwd|credential)", re.IGNORECASE)
_SECRET_ENV = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)", re.IGNORECASE)
_SECRET_SHAPES = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{16,}"),
    re.compile(r"(?i)([?&](?:key|api_key|apikey|token|access_token)=)[^&\s\"']+"),
)
REDACTED = "[redacted]"


# ---------------------------------------------------------------------------
# Scrubbing
# ---------------------------------------------------------------------------


def _secret_values() -> list[str]:
    """Values of the environment's key-like variables (and every variable the
    provider table names), longest first so a key containing another is
    replaced whole. Read fresh on every call: ``--env-file`` loads keys
    after the run has started."""
    names: set[str] = {name for name in os.environ if _SECRET_ENV.search(name)}
    try:
        from canon.providers import env_vars

        names.update(env_vars())
    except Exception:  # noqa: BLE001 — scrubbing must never fail a write
        pass
    values = {os.environ.get(name, "") for name in names}
    return sorted((v for v in values if len(v) >= 8), key=len, reverse=True)


def scrub_text(text: str, values: Sequence[str] | None = None) -> str:
    """``text`` with every secret value and key-shaped token replaced."""
    for value in values if values is not None else _secret_values():
        if value in text:
            text = text.replace(value, REDACTED)
    for pattern in _SECRET_SHAPES:
        if pattern.groups:
            text = pattern.sub(lambda m: m.group(1) + REDACTED, text)
        else:
            text = pattern.sub(REDACTED, text)
    return text


def _clip(text: str, limit: int = MAX_FIELD_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"… [{len(text) - limit} more chars]"


def _scrub(obj: Any, values: Sequence[str]) -> Any:
    if isinstance(obj, str):
        return scrub_text(obj, values)
    if isinstance(obj, dict):
        return {str(k): _scrub(v, values) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_scrub(v, values) for v in obj]
    return obj


def redact_argv(argv: Sequence[str]) -> list[str]:
    """The command line with the value after any secret-named flag replaced
    (``--api-key X`` and ``--api-key=X``) and overlong values clipped."""
    out: list[str] = []
    hide_next = False
    for raw in argv:
        token = str(raw)
        if hide_next:
            out.append(REDACTED)
            hide_next = False
            continue
        if token.startswith("--") and _SECRET_FLAG.search(token.split("=", 1)[0]):
            if "=" in token:
                out.append(token.split("=", 1)[0] + "=" + REDACTED)
            else:
                out.append(token)
                hide_next = True
            continue
        out.append(_clip(token, 2000))
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)


def _mode() -> str:
    return os.environ.get(ENV_MODE, "").strip().lower() or "auto"


def log_path(pack_dir: str | Path) -> Path:
    """Where a pack's run log lives."""
    return Path(pack_dir) / DIRNAME / FILENAME


def _looks_like_pack(path: Path) -> bool:
    try:
        return path.is_dir() and (path / DIRNAME).is_dir()
    except OSError:
        return False


def pack_from_argv(argv: Sequence[str]) -> Path | None:
    """The first argument that names a pack (a directory holding ``.canon/``)
    or a file inside one. ``--flag=value`` values are checked too."""
    for raw in argv:
        token = str(raw)
        if token.startswith("-"):
            if "=" not in token:
                continue
            token = token.split("=", 1)[1]
        if not token or len(token) > 1024 or "\n" in token:
            continue
        try:
            path = Path(token).expanduser()
        except (ValueError, RuntimeError):
            continue
        if _looks_like_pack(path):
            return path.resolve()
        try:
            if path.is_file() and _looks_like_pack(path.parent):
                return path.parent.resolve()
        except OSError:
            continue
    return None


def provider_of(backend: Any) -> str:
    """A backend's provider id: the module's ``PROVIDER_ID`` when declared
    (the asset backends), else a ``provider`` attribute, else the class name.
    A wrapper (``MeteredJudge``, a sheet producer) answers for what it wraps."""
    if backend is None:
        return ""
    inner = getattr(backend, "backend", None)
    if inner is not None and inner is not backend:
        return provider_of(inner)
    module = sys.modules.get(type(backend).__module__)
    declared = getattr(module, "PROVIDER_ID", None)
    if declared:
        return str(declared)
    attr = getattr(type(backend), "provider", None)
    if isinstance(attr, str) and attr:
        return attr
    return type(backend).__name__


def _package_version() -> str:
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("canon-ai")
        except PackageNotFoundError:
            return "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def error_record(exc: BaseException, *, provider: str = "") -> dict[str, Any]:
    """The classified shape of a failure (the asset-failure record without
    its asset fields). Falls back to the bare class + message when the
    classifier itself cannot be imported or raises."""
    try:
        from canon.backends.failures import classify_exception

        err = classify_exception(exc, provider=provider)
        record = err.to_record()
        # ``attempts`` belongs to the retry loop, not to one attempt.
        record.pop("attempts", None)
        if not record.get("error"):
            record["error"] = type(exc).__name__
        return record
    except Exception:  # noqa: BLE001
        return {"error": type(exc).__name__, "provider": provider, "message": str(exc)}


# ---------------------------------------------------------------------------
# The log
# ---------------------------------------------------------------------------


class RunLog:
    """One process's run record. Use the module functions (:func:`start`,
    :func:`record_call`, :func:`end`) rather than holding one directly; a
    ``RunLog`` is public so tests and callers can inspect ``summary()``."""

    def __init__(
        self,
        command: Sequence[str] | None = None,
        *,
        pack_dir: str | Path | None = None,
        mode: str | None = None,
        run_id: str | None = None,
        parent: str | None = None,
    ) -> None:
        self.run_id = run_id or _new_run_id()
        self.mode = mode or _mode()
        self.command = redact_argv(list(command if command is not None else sys.argv))
        self.parent = parent if parent is not None else (os.environ.get(ENV_PARENT) or None)
        self.path: Path | None = None
        self.started = time.monotonic()
        self._lock = threading.RLock()
        self._buffer: list[dict[str, Any]] = []
        self._interesting = self.mode == "all"
        self._env_noted = False
        self._shadowed: list[str] = []
        self.ended = False
        # Totals (the run_end summary).
        self.calls = 0
        self.failed_calls = 0
        self.cost_usd = 0.0
        self.warnings = 0
        self.errors = 0
        self.by_provider: dict[str, dict[str, Any]] = {}
        self.event(
            "run_start",
            command=self.command,
            cwd=os.getcwd(),
            pid=os.getpid(),
            parent=self.parent,
            canon_version=_package_version(),
            python=platform.python_version(),
            platform=platform.platform(terse=True),
        )
        if pack_dir is not None:
            self.bind(pack_dir)

    # -- binding / writing -----------------------------------------------------

    def bind(self, pack_dir: str | Path) -> None:
        """Tie the run to a pack (the first binding wins)."""
        with self._lock:
            if self.path is not None:
                return
            self.path = log_path(Path(pack_dir).resolve())
            self._flush_locked()

    def _flush_locked(self) -> None:
        if self.path is None or not self._interesting or self.mode == "off" or not self._buffer:
            return
        values = _secret_values()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._rotate_locked()
            with self.path.open("a", encoding="utf-8") as fh:
                for record in self._buffer:
                    fh.write(json.dumps(_scrub(record, values), ensure_ascii=False, default=str) + "\n")
        except OSError:
            # Observability must never take a run down: a read-only or vanished
            # pack keeps the buffer and the run carries on.
            return
        self._buffer.clear()

    def _rotate_locked(self) -> None:
        assert self.path is not None
        try:
            if self.path.is_file() and self.path.stat().st_size > MAX_BYTES:
                self.path.replace(self.path.with_name(ROTATED_FILENAME))
        except OSError:
            pass

    def event(self, kind: str, /, *, interesting: bool = False, **fields: Any) -> None:
        """Append one event. ``interesting`` makes the run worth writing."""
        if self.mode == "off":
            return
        record: dict[str, Any] = {"schema": SCHEMA, "ts": _now(), "run": self.run_id, "event": kind}
        for key, value in fields.items():
            if value is None:
                continue
            record[key] = _clip(value) if isinstance(value, str) else value
        with self._lock:
            self._buffer.append(record)
            if interesting:
                self._interesting = True
            elif not self._interesting and len(self._buffer) > MAX_QUIET_BUFFER:
                # A long quiet run (the agent sidecar idling) keeps its start
                # line and its most recent events, not an unbounded backlog.
                del self._buffer[1:len(self._buffer) - MAX_QUIET_BUFFER + 1]
            self._flush_locked()

    # -- typed events ----------------------------------------------------------

    def note_env(self) -> None:
        """Record the provider-key snapshot once (names only)."""
        with self._lock:
            if self._env_noted:
                return
            self._env_noted = True
        try:
            from canon.providers import key_status

            keys = [
                {"id": k["id"], "set": k["set"], "set_via": k["set_via"]}
                for k in key_status()
            ]
        except Exception:  # noqa: BLE001
            keys = []
        self.event(
            "env",
            keys=keys,
            env_file=bool(os.environ.get("CANON_ENV_FILE")),
            shadowed_by_shell=list(self._shadowed) or None,
        )

    def note_env_shadowed(self, names: Sequence[str]) -> None:
        """Keys an env file named but could not set (the shell had them)."""
        names = [n for n in names if n not in self._shadowed]
        if not names:
            return
        self._shadowed.extend(names)
        self.event("env_shadowed", interesting=True, names=list(names))

    def record_call(
        self,
        *,
        lane: str,
        provider: str = "",
        label: str = "",
        model: str | None = None,
        ok: bool = True,
        duration_ms: float | None = None,
        attempt: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: float | None = None,
        error: BaseException | dict[str, Any] | None = None,
        **extra: Any,
    ) -> None:
        self.note_env()
        err: dict[str, Any] | None
        if isinstance(error, BaseException):
            err = error_record(error, provider=provider)
        else:
            err = error
        cost = float(cost_usd) if cost_usd else None
        with self._lock:
            self.calls += 1
            agg = self.by_provider.setdefault(
                provider or "unknown", {"calls": 0, "failed": 0, "ms": 0.0, "cost_usd": 0.0},
            )
            agg["calls"] += 1
            if not ok:
                self.failed_calls += 1
                agg["failed"] += 1
            if duration_ms:
                agg["ms"] += float(duration_ms)
            if cost:
                self.cost_usd += cost
                agg["cost_usd"] += cost
        self.event(
            "call",
            interesting=True,
            lane=lane,
            provider=provider or None,
            label=label or None,
            model=model or None,
            attempt=attempt,
            ok=ok,
            duration_ms=round(float(duration_ms), 1) if duration_ms is not None else None,
            input_tokens=int(input_tokens) if input_tokens else None,
            output_tokens=int(output_tokens) if output_tokens else None,
            cost_usd=round(cost, 6) if cost else None,
            error=err,
            **extra,
        )

    def note_cost(self, *, lane: str, usd: float, provider: str = "", label: str = "") -> None:
        if not usd:
            return
        with self._lock:
            self.cost_usd += float(usd)
            if provider:
                agg = self.by_provider.setdefault(
                    provider, {"calls": 0, "failed": 0, "ms": 0.0, "cost_usd": 0.0},
                )
                agg["cost_usd"] += float(usd)
        self.event(
            "cost", interesting=True, lane=lane, usd=round(float(usd), 6),
            provider=provider or None, label=label or None,
        )

    def note_log(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            message = str(record.msg)
        trace = None
        if record.exc_info and record.exc_info[0] is not None:
            trace = "".join(traceback.format_exception(*record.exc_info))[-MAX_TRACE_CHARS:]
        warn = record.levelno >= logging.WARNING
        with self._lock:
            if record.levelno >= logging.ERROR:
                self.errors += 1
            elif warn:
                self.warnings += 1
        self.event(
            "log",
            interesting=warn,
            level=record.levelname,
            logger=record.name,
            message=message,
            where=f"{record.module}:{record.lineno}",
            traceback=trace,
        )

    def summary(self) -> dict[str, Any]:
        with self._lock:
            return {
                "calls": self.calls,
                "failed_calls": self.failed_calls,
                "cost_usd": round(self.cost_usd, 6),
                "warnings": self.warnings,
                "errors": self.errors,
                "by_provider": {
                    p: {**a, "ms": round(a["ms"], 1), "cost_usd": round(a["cost_usd"], 6)}
                    for p, a in sorted(self.by_provider.items())
                },
            }

    def end(self, exit_code: int | None = None, error: BaseException | None = None) -> None:
        """Close the run (idempotent). A non-zero exit or an exception makes
        it worth writing; a pack named on the command line is bound last."""
        with self._lock:
            if self.ended:
                return
            self.ended = True
        failed = error is not None or (exit_code not in (None, 0))
        # A crash always makes a run worth writing; a clean non-zero exit (a
        # refused read: unknown level, bad flag) only rides along when the run
        # already did something worth reading — reads stay byte-quiet.
        crashed = error is not None
        trace = None
        if error is not None:
            trace = "".join(traceback.format_exception(type(error), error, error.__traceback__))[-MAX_TRACE_CHARS:]
        if self.path is None:
            pack = pack_from_argv(self.command)
            if pack is not None:
                self.bind(pack)
        self.event(
            "run_end",
            interesting=crashed,
            exit_code=exit_code,
            ok=not failed,
            duration_ms=round((time.monotonic() - self.started) * 1000, 1),
            error=f"{type(error).__name__}: {error}" if error is not None else None,
            traceback=trace,
            summary=self.summary(),
        )


# ---------------------------------------------------------------------------
# Logging capture
# ---------------------------------------------------------------------------


class _Handler(logging.Handler):
    """Copies Python log records into the active run: ``canon.*`` at INFO and
    above, every other logger at WARNING and above."""

    def __init__(self, runlog: RunLog) -> None:
        super().__init__(level=logging.INFO)
        self.runlog = runlog
        self._local = threading.local()

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(self._local, "busy", False):
            return  # a log line raised while writing a log line — drop it
        if record.levelno < logging.WARNING and not record.name.startswith("canon"):
            return
        self._local.busy = True
        try:
            self.runlog.note_log(record)
        except Exception:  # noqa: BLE001 — logging must never raise
            pass
        finally:
            self._local.busy = False


# ---------------------------------------------------------------------------
# The process-wide active run
# ---------------------------------------------------------------------------

_active: RunLog | None = None
_handler: _Handler | None = None
_canon_level: int | None = None
_state_lock = threading.Lock()


def active() -> RunLog | None:
    """The run this process is recording, or ``None``."""
    return _active


def start(
    command: Sequence[str] | None = None,
    *,
    pack_dir: str | Path | None = None,
) -> RunLog | None:
    """Start recording this process (one run at a time; a second ``start``
    returns the run already active). ``None`` when ``CANON_RUNLOG=off``.
    Child processes inherit :data:`ENV_PARENT` naming this run."""
    global _active, _handler, _canon_level
    if _mode() == "off":
        return None
    with _state_lock:
        if _active is not None:
            if pack_dir is not None:
                _active.bind(pack_dir)
            return _active
        run = RunLog(command, pack_dir=pack_dir)
        _active = run
        _handler = _Handler(run)
        logging.getLogger().addHandler(_handler)
        canon_logger = logging.getLogger("canon")
        if canon_logger.level == logging.NOTSET or canon_logger.level > logging.INFO:
            _canon_level = canon_logger.level
            canon_logger.setLevel(logging.INFO)
        os.environ[ENV_PARENT] = run.run_id
    if pack_dir is None:
        found = pack_from_argv(run.command)
        if found is not None:
            run.bind(found)
    return run


def end(exit_code: int | None = None, error: BaseException | None = None) -> None:
    """Close the active run and stop recording."""
    global _active, _handler, _canon_level
    with _state_lock:
        run, handler = _active, _handler
        _active = None
        _handler = None
        if handler is not None:
            logging.getLogger().removeHandler(handler)
        if _canon_level is not None:
            logging.getLogger("canon").setLevel(_canon_level)
            _canon_level = None
        if run is not None and os.environ.get(ENV_PARENT) == run.run_id:
            if run.parent:
                os.environ[ENV_PARENT] = run.parent
            else:
                os.environ.pop(ENV_PARENT, None)
    if run is not None:
        run.end(exit_code, error)


@contextmanager
def session(command: Sequence[str] | None = None, *, pack_dir: str | Path | None = None) -> Iterator[RunLog | None]:
    """``start`` … ``end`` around a block; an exception is recorded and
    re-raised, a ``SystemExit`` records its code."""
    run = start(command, pack_dir=pack_dir)
    owns = run is not None
    try:
        yield run
    except SystemExit as exc:
        if owns:
            end(_exit_code(exc.code))
        raise
    except BaseException as exc:
        if owns:
            end(1, exc)
        raise
    else:
        if owns:
            end(0)


def _exit_code(code: Any) -> int:
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    return 1


def run_main(fn: Callable[[], int | None], command: Sequence[str] | None = None) -> int:
    """Run a script entry point inside a session and return its exit code
    (``raise SystemExit(run_main(main))``)."""
    with session(command if command is not None else sys.argv):
        code = fn()
        if code not in (None, 0):
            run = active()
            if run is not None:
                # The session's ``else`` records exit 0; a returned failure
                # code is recorded here first (``end`` is idempotent).
                end(int(code) if isinstance(code, int) else 1)
        return int(code or 0)


# -- module-level forwarding (all no-ops without an active run) ---------------


def bind(pack_dir: str | Path) -> None:
    run = _active
    if run is not None:
        run.bind(pack_dir)


def event(kind: str, /, *, interesting: bool = False, **fields: Any) -> None:
    run = _active
    if run is not None:
        run.event(kind, interesting=interesting, **fields)


def record_call(**fields: Any) -> None:
    """Record one provider call on the active run (see ``RunLog.record_call``).
    Never raises."""
    run = _active
    if run is None:
        return
    try:
        run.record_call(**fields)
    except Exception:  # noqa: BLE001
        pass


def note_cost(*, lane: str, usd: float, provider: str = "", label: str = "") -> None:
    run = _active
    if run is None:
        return
    try:
        run.note_cost(lane=lane, usd=usd, provider=provider, label=label)
    except Exception:  # noqa: BLE001
        pass


def note_env_shadowed(names: Sequence[str]) -> None:
    run = _active
    if run is not None and names:
        run.note_env_shadowed(names)


@contextmanager
def timed_call(*, lane: str, provider: str = "", label: str = "", model: str | None = None, **extra: Any):
    """Time one provider call and record it: ``ok`` when the block returns,
    the classified error when it raises (and the exception propagates).
    The yielded dict takes fields learned during the call (tokens, cost)."""
    fields: dict[str, Any] = {}
    t0 = time.perf_counter()
    try:
        yield fields
    except BaseException as exc:
        if not isinstance(exc, (KeyboardInterrupt, SystemExit, GeneratorExit)):
            record_call(
                lane=lane, provider=provider, label=label, model=model, ok=False,
                duration_ms=(time.perf_counter() - t0) * 1000, error=exc, **extra, **fields,
            )
        raise
    record_call(
        lane=lane, provider=provider, label=label, model=model, ok=True,
        duration_ms=(time.perf_counter() - t0) * 1000, **extra, **fields,
    )


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def read_runlog(pack_dir: str | Path) -> list[dict[str, Any]]:
    """Every run-log record of a pack, oldest first (the rotated file, then
    the live one). Malformed lines are skipped."""
    base = Path(pack_dir) / DIRNAME
    out: list[dict[str, Any]] = []
    for name in (ROTATED_FILENAME, FILENAME):
        path = base / name
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and obj.get("run"):
                out.append(obj)
    return out
