"""The agent service — cradle's localhost sidecar (Phase 1 A2; master §3.1 stage 3; A4.5 run manager).

One FastAPI app around ``run_conversation``, served by uvicorn on
``127.0.0.1:<port>``. Cradle spawns it the way it spawns the play harness
(the play-process precedent), reads the FIRST stdout line —
``{"port": N, "pid": P}`` — and talks to that port directly from the
webview (``tauri.conf.json`` has ``csp: null``; no proxy). The sidecar
watches cradle's pid and exits when it is gone, so an orphaned service
never outlives the app.

Routes::

    GET  /health                            {ok, pack, backend, model, tools}
    GET  /models                            [{id, provider, label, prices, available, key_env,
                                              reasoning}] — canon.pricing rows as data      (A4.5)
    GET  /roster                            {loaded, foreman_tools, parallel_cap,
                                             specialists: [{id, label, tools, available, missing, model}]}
    GET  /skills                            {skills, recipes, problems}                  (A4.5)
    POST /conversations        {system?, ui_state?}  {id}   (404 no pack dir · 400 not a pack)
    GET  /conversations                     [{id, created, turns}]
    GET  /conversations/{id}                the transcript lines
    GET  /conversations/{id}/prompt         {system, assembled, source}  the §3.1 prompt (A4.5)
    POST /conversations/{id}/messages {text, mode?, ui_state?}  text/event-stream  (409 while a turn runs)
    POST /conversations/{id}/stop {reason?} ⏹ the reply and every run beneath it        (A4.5)
    GET  /conversations/{id}/permissions    the pending permission requests          (A4)
    POST /conversations/{id}/permissions {request_id, decision, reason?}  the decision (A4)
    GET  /conversations/{id}/plans          the conversation's plans                    (A4.5)
    GET  /conversations/{id}/plans/{plan_id}
    POST /conversations/{id}/plans/{plan_id} {decision: approve|reject|edit, steps?, reason?}
    POST /conversations/{id}/plans/{plan_id}/resume {action: continue|skip|stop}
    POST /conversations/{id}/plans/{plan_id}/undo   restore the plan's writes, reverse order
    GET  /runs · GET /runs/{run_id} · POST /runs/{run_id}/stop {reason?}   run cards + ⏹ (A4.5)
    GET  /packs/permissions?pack=…          the project's grants                     (A4)
    DELETE /packs/permissions?pack=…        revoke all · DELETE …/{index} revoke one (A4)
    POST /shutdown                          {ok, shutting_down}  then a clean exit

The SSE stream relays every ``ChatEvent`` the backend streams — ``event:``
is the event's string ``type`` (``message_start``, ``text_delta``,
``thinking_delta``, ``tool_use_start``, ``tool_input_delta``,
``content_block_done``, ``message_stop``), ``data:`` is
``dataclasses.asdict`` of it — plus the service-level events:
``tool_call`` ``{name, input}``, ``tool_result`` ``{name, is_error}``,
``permission_request`` / ``permission_decision`` (row A4), the run and
plan lifecycle (row A4.5 — ``run_start`` / ``run_progress`` / ``run_end``,
``plan_proposed`` / ``plan_decision`` / ``plan_step`` / ``plan_halted`` /
``plan_resumed`` / ``plan_done`` / ``plan_undone``; see
``canon.agent.runs``), ``cancelled`` ``{conversation, usage, landed, runs,
where, reason}`` when ⏹ Stop ended the turn, and ``done`` ``{stop_reason,
usage, conversation}`` (``error`` ``{message, retryable, conversation}``
when the turn dies instead). Everything the loop appends is also written
to the conversation's transcript (``ConversationStore``) as it happens.

The permission round-trip (row A4; Phase 1 §2.3, §4; agent-panel README
§6): when a tool call classifies as *ask*, the engine emits
``permission_request`` on the stream (and a transcript line) and the
worker thread BLOCKS until ``POST /conversations/{id}/permissions``
``{request_id, decision: accept | always | reject, reason?}`` lands — or
the optional ``--permission-timeout`` (minutes) rejects it with "no
decision" — or ⏹ Stop rejects it. The decision endpoint is served
concurrently with the stream (FastAPI's threadpool), answers ``{ok,
request_id, decision, tool, grant?}``, and is 404 for an unknown request,
409 with the disabled reason when "always" is not available, 422 for an
unknown decision. The ``mode`` on a message (``ask`` | ``plan`` |
``allow``; data) is the header's segmented control at send time.

Row A4.5 — what this service wires (the machinery is ``canon.agent.runs``):

- The system prompt is ASSEMBLED per turn (``canon.agent.prompt``: core
  law + pack context + the message's ``ui_state`` + the foreman's role +
  matched skills) when the conversation was created without ``system``;
  ``GET …/prompt`` shows it read-only.
- The foreman is offered ``roster.foreman.tools ∩ registry`` (reads, UI
  tools, ``propose_plan``, ``delegate``, ``sandbox_level``); without a
  roster (``create_app(roster=None)`` — row A4's tests) the whole registry.
- ``delegate`` runs specialists (parallel cap, write gate, run events);
  ``propose_plan`` blocks for the plan decision; ⏹ Stop is
  ``POST …/stop`` (conversation) and ``POST /runs/{id}/stop`` (one run) —
  the loop closes the provider generator, skips the pending tool call and
  the transcript's ``turn_end`` says ``cancelled`` with what landed and the
  usage so far.
- Concurrency: ONE turn at a time per conversation (a concurrent POST is
  409); delegations inside a turn run in parallel (cap 3); two
  conversations run concurrently over the same service and the same
  write gate.
- Cost: nothing here prices anything; ``usage`` is measured tokens. Row
  A6 meters them against the §3.0-C module and writes the journal rows.
- Pack-less conversations wait for P0-10's project store: a conversation
  REQUIRES an open pack, checked on every create.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import signal
import socket
import sys
import threading
from collections.abc import Callable, Iterator
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from canon.agent.actors import FOREMAN, agent_actor, current_call
from canon.agent.conversations import ConversationStore, record_for
from canon.agent.loop import CANCELLED_STOP, ConversationCancelled, run_conversation
from canon.agent.permissions import MODES, AlwaysNotAllowed, GrantStore, PermissionEngine, PermissionRequest
from canon.agent.providers import list_models, resolve_chat_backend
from canon.agent.registry import ToolRegistry
from canon.agent.roster import Specialist, load_roster
from canon.agent.runs import DELEGATE_TOOL, PARALLEL_CAP, RunManager
from canon.agent.skills import SkillSet, load_skills
from canon.agent.tools_code import register_code_tools
from canon.agent.tools_paid import register_paid_tools
from canon.agent.tools_play import register_play_tools
from canon.agent.tools_read import register_read_tools
from canon.agent.tools_vision import register_vision_tools
from canon.agent.tools_write import register_write_tools
from canon.backends.base import ChatBackend
from canon.backends.registry import BackendRegistry
from canon.backends.testing import FakeChatBackend
from canon.llm.chat import ChatError, ChatEvent, ChatRequest, Usage
from canon.packs import PackTypeError, resolve_pack

log = logging.getLogger("canon.agent.service")

#: The loopback host the sidecar binds; never anything else.
HOST = "127.0.0.1"

#: The chat id that builds a scripted ``FakeChatBackend`` instead of a provider.
FAKE_BACKEND_ID = "fake"

#: Watchdog poll interval (seconds).
WATCHDOG_INTERVAL = 0.5


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class CreateConversation(BaseModel):
    """``POST /conversations`` body. ``system`` pins a system prompt for
    every turn (tests, the eval); ``None`` = the service assembles the
    §3.1 prompt per turn. ``ui_state`` seeds layer 3 before the first
    message."""

    system: str | None = None
    ui_state: dict | None = None


class UserMessage(BaseModel):
    """``POST /conversations/{id}/messages`` body. ``mode`` is the header's
    segmented control at send time (``ask`` | ``plan`` | ``allow`` — data);
    ``None`` keeps the engine's default. ``ui_state`` is cradle's per-message
    copy of the open selection / tab / dirty layers (latest only is kept)."""

    text: str
    mode: str | None = None
    ui_state: dict | None = None


class PermissionDecisionBody(BaseModel):
    """``POST /conversations/{id}/permissions`` body — the chip's answer."""

    request_id: str
    decision: str
    reason: str | None = None


class PlanDecisionBody(BaseModel):
    """``POST /conversations/{id}/plans/{plan_id}`` — approve | reject | edit (+ steps)."""

    decision: str
    steps: list[dict] | None = None
    reason: str | None = None


class PlanResumeBody(BaseModel):
    """``POST …/plans/{plan_id}/resume`` — continue | skip | stop."""

    action: str


class StopBody(BaseModel):
    """``POST …/stop`` bodies (optional reason for the transcript)."""

    reason: str | None = None


# ---------------------------------------------------------------------------
# Pack + backend resolution
# ---------------------------------------------------------------------------


def pack_problem(pack_dir: Path) -> tuple[int, str] | None:
    """``None`` when ``pack_dir`` is an open-able pack; else ``(http status,
    reason)`` — 404 when the directory is missing, 400 when it is there but
    ``resolve_pack`` (the one resolver, P0-4) does not recognise it."""
    if not pack_dir.is_dir():
        return 404, f"no such pack directory: {pack_dir}"
    try:
        resolve_pack(pack_dir)
    except PackTypeError as exc:
        return 400, str(exc)
    return None


def _load_fake_script(path: Path | None) -> list:
    """The turns list of a ``--fake-script`` file: a bare JSON list of turns
    or ``{"turns": [...]}`` (each turn a block list, or a dict with
    ``content`` / ``stop_reason`` / ``stop_details`` — ``FakeChatBackend``'s
    list-mode shapes)."""
    if path is None:
        return []
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    turns = document.get("turns") if isinstance(document, dict) else document
    if not isinstance(turns, list):
        raise ValueError(f"{path}: expected a JSON list of turns or {{'turns': [...]}}")
    return list(turns)


def _last_user_text(request: ChatRequest) -> str:
    for message in reversed(request.messages):
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            return message["content"]
    return ""


def fake_backend(script: Path | None = None, model: str | None = None) -> FakeChatBackend:
    """``FakeChatBackend`` in callable mode: plays ``script``'s turns in
    order across requests, then echoes the latest user text — so the $0
    demo never hits list mode's ``IndexError`` mid-conversation."""
    turns = _load_fake_script(script)
    state = {"index": 0}

    def next_turn(request: ChatRequest) -> list | dict:
        if state["index"] < len(turns):
            turn = turns[state["index"]]
            state["index"] += 1
            return turn
        return [{"type": "text", "text": f"(fake backend, $0) you said: {_last_user_text(request)}"}]

    return FakeChatBackend(next_turn, model=model or "fake-chat")


def build_backend(backend_id: str, model: str | None, fake_script: Path | None = None) -> ChatBackend:
    """``"fake"`` → a scripted fake; anything else → the shared registrar
    map + registry (``KeyError`` unknown id, ``ImportError`` missing extra)."""
    if backend_id == FAKE_BACKEND_ID:
        return fake_backend(fake_script, model)
    if fake_script is not None:
        log.warning("--fake-script is ignored for backend %r", backend_id)
    return resolve_chat_backend(backend_id, model)


# ---------------------------------------------------------------------------
# SSE + the turn runner
# ---------------------------------------------------------------------------


def sse(event: str, data: Any) -> str:
    """One ``text/event-stream`` frame."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


class TurnLocks:
    """One in-flight turn per conversation. Delegations inside a turn run
    in parallel (the run manager); a second user message while one runs
    is a 409 — the conversation is one thread of talk."""

    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def try_acquire(self, conversation_id: str) -> bool:
        with self._guard:
            lock = self._locks.setdefault(conversation_id, threading.Lock())
        return lock.acquire(blocking=False)

    def release(self, conversation_id: str) -> None:
        self._locks[conversation_id].release()

    def busy(self, conversation_id: str) -> bool:
        lock = self._locks.get(conversation_id)
        return lock is not None and lock.locked()


def journal_turn_tokens(
    pack_dir: str | Path,
    conversation_id: str,
    usage: dict,
    *,
    model: str | None,
    backend_id: str,
    turn: int = 0,
    cancelled: bool = False,
    specialist: str = FOREMAN,
    run_id: str | None = None,
    batch_id: str | None = None,
) -> dict | None:
    """Journal ONE conversation turn's (or ONE delegated run's) token cost
    (row P1-A6; paper P.8.6).

    The token lane is a JOURNAL event, not a spend-ledger-only row — README §12
    is explicit that "every row is one journal entry, so the two tables always
    reconcile", and a spend-only token row would force the dashboard back onto
    two sources. The event is:

    - ``artifact_id: conversation:<id>`` — the transcript IS a pack-resident
      artifact (``.canon/agent/``), so this names a real thing;
    - **hash-less** — a turn is not a new version of anything, and P.8.5's rule
      makes hash-less events invisible to ``artifact_versions`` / lineage /
      restore by construction;
    - ``genKind: tokens``, priced from the MEASURED provider-reported counts by
      ``canon.provenance.token_gen_block`` → ``canon.pricing``. **Nothing here
      prices anything**: this module still carries no rate, no table and no
      arithmetic, which is the property row A1 fixed and A6 keeps;
    - ``detail.cancelled: true`` for a stopped turn (P.8.5: a cancelled turn's
      token burn is still a token row).

    A DELEGATED run is metered through the same function, with its own
    ``specialist`` (so the row lands under ``agent:<conversation>/<specialist>``
    and README §12's by-identity table can nest it) and ``run_id``. Its usage is
    the ``RunManager``'s own ``run.usage`` — the specialist's ``run_conversation``
    is a separate loop from the foreman's, so without this call its burn reached
    the run card and nothing else, and every specialist's ``tokens`` column read
    $0 while cradle's panel counted the same tokens from ``run_end``.

    Best-effort by design: a metering failure must never turn a completed turn
    into a failed one. Returns the event, or ``None`` when the turn burned
    nothing or the write failed.
    """
    from canon import provenance

    priced = provenance.token_gen_block(backend_id, model, usage)
    if priced is None:
        return None
    detail: dict[str, Any] = (
        {"kind": "run", "run_id": run_id} if run_id else {"kind": "turn", "turn": turn}
    )
    if cancelled:
        detail["cancelled"] = True
    try:
        return provenance.record(
            pack_dir,
            artifact_id=f"conversation:{conversation_id}",
            op="generate",
            source="llm",
            actor=agent_actor(conversation_id, specialist),
            session=conversation_id,
            detail=detail,
            gen=priced["gen"],
            gen_kind=provenance.TOKENS_GEN_KIND,
            accuracy=priced["accuracy"],
            cost_error=priced["cost_error"],
            batch_id=batch_id,
        )
    except Exception:  # noqa: BLE001 — metering never fails a completed turn or run
        log.warning("token row not journalled for %s", conversation_id, exc_info=True)
        return None


def run_turn(
    *,
    backend: ChatBackend,
    registry: ToolRegistry,
    store: ConversationStore,
    conversation_id: str,
    text: str,
    emit: Callable[[str, Any], None],
    max_tool_rounds: int = 8,
    mode: str | None = None,
    manager: RunManager | None = None,
    ui_state: dict | None = None,
) -> None:
    """Run ONE user message through ``run_conversation`` on top of the
    stored history; every event/message goes to ``emit`` (SSE) and the
    transcript. Never raises: a dead turn emits ``error`` and journals it;
    a stopped turn emits ``cancelled`` and journals ``turn_end`` with
    ``stop_reason: cancelled`` (row A4.5).

    Row A4: the turn runs as ``agent_actor(conversation, FOREMAN)`` in
    ``mode`` (the engine's default when ``None``); the call context is
    bound around every ``registry.execute`` so write tools attribute their
    verbs, and the engine's requests/decisions for this conversation are
    routed to the stream and the transcript for the turn's duration.

    Row A4.5: the manager binds the turn (cancel flag, stream, UI state),
    assembles the prompt when the conversation pinned none, offers the
    foreman's tool subset, executes every call through its write gate /
    plan attribution, and runs ``delegate`` calls of one turn in parallel.
    """
    meta = store.meta(conversation_id)
    history = store.messages(conversation_id)
    actor = agent_actor(conversation_id, FOREMAN)
    engine: PermissionEngine = registry.permissions
    engine.set_mode(conversation_id, mode if mode is not None else engine.default_mode)
    if manager is None:
        manager = RunManager(pack_dir=store.pack_dir, registry=registry, backend=backend, store=store)

    def execute(name: str, tool_input: dict) -> Any:
        emit("tool_call", {"name": name, "input": tool_input})
        try:
            result = manager.execute(name, tool_input, actor=actor, conversation=conversation_id)
        except Exception as exc:
            emit("tool_result", {"name": name, "is_error": True, "error": f"{type(exc).__name__}: {exc}"})
            raise
        emit("tool_result", {"name": name, "is_error": False})
        return result

    def on_event(event: ChatEvent) -> None:
        emit(event.type, asdict(event))

    def on_message(message: dict) -> None:
        store.append(conversation_id, record_for(message))

    def on_request(request: PermissionRequest) -> None:
        payload = request.payload()
        store.append(conversation_id, {"type": "permission_request", **payload})
        emit("permission_request", payload)

    def on_decision(request: PermissionRequest, record: dict) -> None:
        store.append(conversation_id, {"type": "permission_decision", **record})
        emit("permission_decision", record)

    def meter(usage: dict, *, cancelled: bool = False) -> None:
        """Row P1-A6: the turn's MEASURED tokens as their own journal event
        (P.8.6's conversation row). The service does not price anything itself
        — ``journal_turn_tokens`` goes through ``canon.pricing``."""
        journal_turn_tokens(
            store.pack_dir,
            conversation_id,
            usage,
            model=meta.get("model"),
            backend_id=str(meta.get("backend") or getattr(backend, "id", "") or ""),
            turn=len([line for line in store.load(conversation_id) if line.get("type") == "turn_end"]),
            cancelled=cancelled,
        )

    started_runs: list[str] = []

    def emit_tracking(event: str, data: Any) -> None:
        if event == "run_start" and isinstance(data, dict) and data.get("run_id"):
            started_runs.append(str(data["run_id"]))
        emit(event, data)

    try:
        with (
            manager.turn(conversation_id, emit=emit_tracking, ui_state=ui_state, mode=mode) as turn,
            engine.listen(conversation_id, on_request=on_request, on_decision=on_decision),
        ):
            system = meta.get("system")
            if system is None:
                system = manager.foreman_prompt(conversation_id, text=text)
            try:
                result = run_conversation(
                    backend,
                    system=system,
                    tools=manager.specs_for(manager.foreman_tool_names()),
                    tool_executor=execute,
                    user_messages=[text],
                    model=meta.get("model"),
                    max_tool_rounds=max_tool_rounds,
                    on_event=on_event,
                    history=history,
                    on_message=on_message,
                    cancel=turn.cancel,
                    parallel=lambda name: name == DELEGATE_TOOL,
                )
            except ConversationCancelled as cancelled:
                usage = asdict(cancelled.result.usage)
                landed = [{"tool": s["tool"], "is_error": s["is_error"]} for s in cancelled.result.steps]
                runs = [manager.runs[r].payload() for r in started_runs if r in manager.runs]
                record = {
                    "stop_reason": CANCELLED_STOP,
                    "usage": usage,
                    "landed": landed,
                    "runs": [{"run_id": r["run_id"], "specialist": r["specialist"], "status": r["status"],
                              "usage": r["usage"], "artifacts": r["artifacts"]} for r in runs],
                    "where": cancelled.where,
                    "reason": turn.cancel_reason,
                }
                store.append(conversation_id, {"type": "turn_end", **record})
                # Row P1-A6: a cancelled turn still burned tokens — meter it,
                # with detail.cancelled (P.8.5), before the stream closes.
                meter(usage, cancelled=True)
                emit("cancelled", {"conversation": conversation_id, **record})
                return
    except Exception as exc:  # noqa: BLE001 — a dead turn must reach the client and the transcript as data
        retryable = bool(getattr(exc, "retryable", False)) if isinstance(exc, ChatError) else False
        message = f"{type(exc).__name__}: {exc}" if not isinstance(exc, ChatError) else str(exc)
        log.exception("turn failed in %s", conversation_id)
        store.append(conversation_id, {"type": "error", "message": message, "retryable": retryable})
        store.append(conversation_id, {"type": "turn_end", "stop_reason": "error", "usage": asdict(Usage())})
        emit("error", {"message": message, "retryable": retryable, "conversation": conversation_id})
        return

    stop_reason = result.stop_reasons[-1] if result.stop_reasons else ""
    usage = asdict(result.usage)
    store.append(conversation_id, {"type": "turn_end", "stop_reason": stop_reason, "usage": usage})
    meter(usage)
    emit("done", {"stop_reason": stop_reason, "usage": usage, "conversation": conversation_id})


# ---------------------------------------------------------------------------
# The app
# ---------------------------------------------------------------------------


def create_app(
    pack_dir: str | Path,
    backend_id: str,
    model: str | None,
    registry: ToolRegistry,
    store: ConversationStore,
    *,
    backend: ChatBackend | None = None,
    fake_script: Path | None = None,
    on_shutdown: Callable[[], None] | None = None,
    permission_timeout: float | None = None,
    roster: dict[str, Specialist] | None = None,
    skills: SkillSet | None = None,
    parallel_cap: int = PARALLEL_CAP,
    max_tool_rounds: int = 8,
    manager: RunManager | None = None,
) -> FastAPI:
    """Build the service app for one pack.

    Args:
        pack_dir: The open pack every conversation belongs to.
        backend_id: Chat backend id (data): ``"fake"`` or a registry id.
        model: Model id for the backend (``None`` = the backend's own).
        registry: The tool registry (A3 fills the reads, A4 the writes,
            A4.5 the play tool; ``delegate`` / ``propose_plan`` are
            registered here by the run manager unless already present).
            Its ``PermissionEngine`` is attached to ``pack_dir``'s grants
            file when it was built without a pack.
        store: The transcript store for ``pack_dir``.
        backend: A pre-built backend (tests inject a scripted fake);
            ``None`` builds one from ``backend_id`` / ``fake_script``.
        fake_script: ``--fake-script`` turns file for the fake backend.
        on_shutdown: What ``POST /shutdown`` calls (the sidecar sets it to
            stop uvicorn); ``None`` only records the request.
        permission_timeout: Seconds a pending permission request waits
            before it is rejected with "no decision"; ``None`` keeps the
            engine's own (default: wait until decided).
        roster: ``load_roster()``; ``None`` = no specialist layer (the
            foreman is the whole registry; ``delegate`` refuses).
        skills: ``load_skills(...)``; ``None`` = none.
        parallel_cap: Concurrent delegations (§5.5: 3).
        max_tool_rounds: Per turn and per specialist run.
        manager: A pre-built ``RunManager`` (tests); ``None`` builds one.
    """
    pack = Path(pack_dir)
    chat = backend if backend is not None else build_backend(backend_id, model, fake_script)
    shown_model = model if model is not None else getattr(chat, "model", None)
    engine: PermissionEngine = registry.permissions
    engine.attach(pack)
    if permission_timeout is not None:
        engine.timeout = permission_timeout
    runs = manager if manager is not None else RunManager(
        pack_dir=pack,
        registry=registry,
        backend=chat,
        store=store,
        roster=roster,
        skills=skills,
        parallel_cap=parallel_cap,
        model=shown_model,
        max_tool_rounds=max_tool_rounds,
    )
    # The specialist layer is a roster: without one there is nobody to
    # delegate to and no plan to run, so the two foreman tools stay
    # unregistered and the registry is exactly what the caller built (row
    # A2/A4's shape). The sidecar's ``main`` always loads the roster.
    if roster is not None and DELEGATE_TOOL not in registry.names():
        runs.register_tools(registry)
    for entry in runs.roster_report():
        if entry["missing"]:
            log.warning("roster %s: not registered in this service (dropped loudly): %s", entry["id"], entry["missing"])
    unreachable = runs.unreachable_tools()
    if unreachable:
        # The inverse direction: registered verbs no specialist lists, which a
        # loaded roster silently takes away from every agent (doctrine 4).
        log.warning("registered but in no roster allowlist — no agent can call: %s", unreachable)
    for problem in runs.skills.problems:
        log.warning("skill refused: %s", problem)

    app = FastAPI(title="canon agent service", docs_url=None, redoc_url=None)
    app.state.pack_dir = pack
    app.state.backend_id = backend_id
    app.state.model = shown_model
    app.state.backend = chat
    app.state.registry = registry
    app.state.store = store
    app.state.locks = TurnLocks()
    app.state.on_shutdown = on_shutdown
    app.state.shutdown_requested = False
    app.state.manager = runs

    @app.get("/health")
    def health() -> dict:
        return {
            "ok": True,
            "pack": str(pack),
            "backend": backend_id,
            "model": shown_model,
            "tools": registry.names(),
        }

    # -- Row A4.5: models, roster, skills (data for A5's picker + the inspect views) --

    @app.get("/models")
    def models() -> list[dict]:
        return list_models()

    @app.get("/roster")
    def roster_view() -> dict:
        return {
            "loaded": runs.roster is not None,
            "foreman_tools": runs.foreman_tool_names(),
            "parallel_cap": runs.parallel_cap,
            "specialists": runs.roster_report(),
            "unreachable": runs.unreachable_tools(),
        }

    @app.get("/skills")
    def skills_view() -> dict:
        return {
            "skills": [
                {
                    "id": s.id, "specialist": s.specialist, "allowlist": list(s.allowlist) if s.allowlist else None,
                    "model": s.model, "trigger": s.trigger, "source": s.source, "path": s.path,
                    "routable": s.routable,
                }
                for s in runs.skills.skills.values()
            ],
            "recipes": [
                {"id": r.id, "family": r.family, "parameters": r.parameters, "gates": r.gates,
                 "source": r.source, "path": r.path}
                for r in runs.skills.recipes.values()
            ],
            "problems": list(runs.skills.problems),
        }

    @app.post("/conversations", status_code=201)
    def create_conversation(body: CreateConversation | None = None) -> dict:
        problem = pack_problem(pack)
        if problem is not None:
            status, reason = problem
            raise HTTPException(status_code=status, detail=reason)
        system = body.system if body is not None else None
        conversation_id = store.create(backend=backend_id, model=shown_model, system=system)
        if body is not None and body.ui_state is not None:
            runs.latest_ui_state[conversation_id] = body.ui_state
        return {"id": conversation_id}

    @app.get("/conversations")
    def list_conversations() -> list[dict]:
        return store.list()

    @app.get("/conversations/{conversation_id}")
    def get_conversation(conversation_id: str) -> list[dict]:
        try:
            return store.load(conversation_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def _known_conversation(conversation_id: str) -> dict:
        try:
            return store.meta(conversation_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/conversations/{conversation_id}/prompt")
    def get_prompt(conversation_id: str) -> dict:
        meta = _known_conversation(conversation_id)
        pinned = meta.get("system")
        assembled = runs.foreman_prompt(conversation_id)
        return {
            "conversation": conversation_id,
            "source": "pinned" if pinned is not None else "assembled",
            "system": pinned if pinned is not None else assembled,
            "assembled": assembled,
            "ui_state": runs.latest_ui_state.get(conversation_id),
            "tools": runs.foreman_tool_names(),
        }

    @app.post("/conversations/{conversation_id}/messages")
    def post_message(conversation_id: str, body: UserMessage) -> StreamingResponse:
        _known_conversation(conversation_id)
        if body.mode is not None and body.mode not in MODES:
            # A typo'd mode is a REQUEST error, not a mid-turn tool error: the
            # engine would fail closed on every ask/paid call and the user
            # would only see it inside a failed tool card.
            raise HTTPException(
                status_code=422,
                detail=f"mode must be one of {list(MODES)} (got {body.mode!r})",
            )
        locks: TurnLocks = app.state.locks
        if not locks.try_acquire(conversation_id):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"conversation {conversation_id} already has a turn in flight — one turn at a time per "
                    "conversation (row A4.5: stop it with POST …/stop, or wait for done)"
                ),
            )

        frames: queue.Queue[str | None] = queue.Queue()

        def emit(event: str, data: Any) -> None:
            frames.put(sse(event, data))

        def worker() -> None:
            try:
                run_turn(
                    backend=chat,
                    registry=registry,
                    store=store,
                    conversation_id=conversation_id,
                    text=body.text,
                    emit=emit,
                    mode=body.mode,
                    manager=runs,
                    ui_state=body.ui_state,
                    max_tool_rounds=max_tool_rounds,
                )
            finally:
                locks.release(conversation_id)
                frames.put(None)

        threading.Thread(target=worker, name=f"turn-{conversation_id}", daemon=True).start()

        def stream() -> Iterator[str]:
            while (frame := frames.get()) is not None:
                yield frame

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # -- Row A4.5: ⏹ Stop --

    @app.post("/conversations/{conversation_id}/stop")
    def stop_conversation(conversation_id: str, body: StopBody | None = None) -> dict:
        _known_conversation(conversation_id)
        reason = (body.reason if body is not None else None) or "stopped by the user"
        return runs.stop_conversation(conversation_id, reason)

    @app.get("/runs")
    def list_runs(conversation: str | None = None) -> list[dict]:
        return [r.payload() for r in runs.runs.values() if conversation is None or r.conversation == conversation]

    @app.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        run = runs.runs.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"no run {run_id!r}")
        return run.payload()

    @app.post("/runs/{run_id}/stop")
    def stop_run(run_id: str, body: StopBody | None = None) -> dict:
        reason = (body.reason if body is not None else None) or "stopped by the user"
        try:
            return runs.stop_run(run_id, reason)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc

    # -- Row A4.5: plans --

    @app.get("/conversations/{conversation_id}/plans")
    def list_plans(conversation_id: str) -> list[dict]:
        _known_conversation(conversation_id)
        return [p.payload() for p in runs.plans.values() if p.conversation == conversation_id]

    def _plan(conversation_id: str, plan_id: str):
        _known_conversation(conversation_id)
        plan = runs.plans.get(plan_id)
        if plan is None or plan.conversation != conversation_id:
            raise HTTPException(status_code=404, detail=f"no plan {plan_id!r} in conversation {conversation_id}")
        return plan

    @app.get("/conversations/{conversation_id}/plans/{plan_id}")
    def get_plan(conversation_id: str, plan_id: str) -> dict:
        return _plan(conversation_id, plan_id).payload()

    @app.post("/conversations/{conversation_id}/plans/{plan_id}")
    def decide_plan(conversation_id: str, plan_id: str, body: PlanDecisionBody) -> dict:
        _plan(conversation_id, plan_id)
        try:
            record = runs.decide_plan(conversation_id, plan_id, body.decision, steps=body.steps, reason=body.reason)
        except ValueError as exc:
            raise HTTPException(status_code=409 if "not proposed" in str(exc) else 422, detail=str(exc)) from exc
        return {"ok": True, **record}

    @app.post("/conversations/{conversation_id}/plans/{plan_id}/resume")
    def resume_plan(conversation_id: str, plan_id: str, body: PlanResumeBody) -> dict:
        _plan(conversation_id, plan_id)
        try:
            record = runs.resume_plan(conversation_id, plan_id, body.action)
        except ValueError as exc:
            raise HTTPException(status_code=409 if "not halted" in str(exc) else 422, detail=str(exc)) from exc
        return {"ok": True, **record}

    @app.post("/conversations/{conversation_id}/plans/{plan_id}/undo")
    def undo_plan(conversation_id: str, plan_id: str) -> dict:
        _plan(conversation_id, plan_id)
        try:
            record = runs.undo_plan(conversation_id, plan_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True, **record}

    # -- Row A4: the permission round-trip + the grants (Settings → Permissions reads these) --

    @app.get("/conversations/{conversation_id}/permissions")
    def pending_permissions(conversation_id: str) -> list[dict]:
        _known_conversation(conversation_id)
        return engine.pending(conversation_id)

    @app.post("/conversations/{conversation_id}/permissions")
    def decide_permission(conversation_id: str, body: PermissionDecisionBody) -> dict:
        _known_conversation(conversation_id)
        try:
            record = engine.decide(body.request_id, body.decision, body.reason, conversation=conversation_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc
        except AlwaysNotAllowed as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"ok": True, **record}

    def _grant_store(pack_query: str | None) -> GrantStore:
        if pack_query is None:
            assert engine.grants is not None  # attached above
            return engine.grants
        other = Path(pack_query)
        problem = pack_problem(other)
        if problem is not None:
            status, reason = problem
            raise HTTPException(status_code=status, detail=reason)
        return GrantStore(other)

    def _grants_document(grants: GrantStore) -> dict:
        try:
            listed = grants.grants()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "pack": str(grants.pack_dir),
            "path": str(grants.path),
            "grants": [{"index": index, **grant} for index, grant in enumerate(listed)],
        }

    @app.get("/packs/permissions")
    def list_grants(pack: str | None = None) -> dict:
        return _grants_document(_grant_store(pack))

    @app.delete("/packs/permissions")
    def revoke_all_grants(pack: str | None = None) -> dict:
        grants = _grant_store(pack)
        revoked = grants.revoke_all()
        return {"revoked": revoked, **_grants_document(grants)}

    @app.delete("/packs/permissions/{index}")
    def revoke_grant(index: int, pack: str | None = None) -> dict:
        grants = _grant_store(pack)
        try:
            removed = grants.revoke(index)
        except IndexError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"revoked": removed, **_grants_document(grants)}

    @app.post("/shutdown")
    def shutdown() -> dict:
        app.state.shutdown_requested = True
        if app.state.on_shutdown is not None:
            app.state.on_shutdown()
        return {"ok": True, "shutting_down": True}

    return app


# ---------------------------------------------------------------------------
# Sidecar lifecycle: port line, watchdog, clean exit
# ---------------------------------------------------------------------------


def pid_alive(pid: int) -> bool:
    """Is a process with ``pid`` running? POSIX: ``os.kill(pid, 0)``
    semantics (``EPERM`` still means alive). Windows: ``OpenProcess`` +
    ``WaitForSingleObject(0)`` — never ``os.kill``, which would TERMINATE
    the process there (psutil-free)."""
    if sys.platform == "win32":  # pragma: no cover - not exercised on this box
        import ctypes

        synchronize = 0x00100000
        wait_timeout = 0x102
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == wait_timeout
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def parent_alive(parent_pid: int) -> Callable[[], bool]:
    """The watchdog's probe for ``parent_pid``. When the parent is our own
    parent process, a reparent (``os.getppid()`` changing) also counts as
    dead — that catches an exited-but-unreaped parent that ``kill 0``
    would still see."""
    was_our_parent = os.getppid() == parent_pid

    def alive() -> bool:
        if not pid_alive(parent_pid):
            return False
        return not was_our_parent or os.getppid() == parent_pid

    return alive


def watch_parent(
    alive: Callable[[], bool],
    on_dead: Callable[[], None],
    *,
    interval: float = WATCHDOG_INTERVAL,
    stop: threading.Event | None = None,
) -> bool:
    """Poll ``alive()`` every ``interval`` seconds until it is ``False``
    (then call ``on_dead`` once and return ``True``) or ``stop`` is set
    (return ``False``). Runs on the watchdog thread; pure enough to unit
    test with a fake ``alive``."""
    stop = stop if stop is not None else threading.Event()
    while not stop.is_set():
        if not alive():
            on_dead()
            return True
        stop.wait(interval)
    return False


def bind(host: str, port: int) -> socket.socket:
    """Bind a listening socket (``port`` 0 → OS-assigned); uvicorn serves on it."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(128)
    sock.set_inheritable(True)
    return sock


def serve(
    app: FastAPI,
    *,
    port: int = 0,
    parent_pid: int | None = None,
    out=None,
    sock: socket.socket | None = None,
) -> int:
    """Run uvicorn on ``127.0.0.1:port`` after printing ``{"port", "pid"}``
    as the first line of ``out`` (stdout). Returns 0 after a graceful stop
    from SIGTERM, ``POST /shutdown``, or — with ``parent_pid`` — that
    process going away.

    ``sock`` is an already-bound listener (``bind``); ``main`` binds first
    so a busy port is a JSON usage error BEFORE the port line, never a
    traceback after it. ``None`` binds ``port`` here.

    SIGTERM and the exit status: uvicorn's ``capture_signals`` handles
    SIGTERM for the graceful stop, then restores whatever handler was
    installed before ``run`` and RE-RAISES the signal — with Python's
    default handler that kills the process with status 143 after the
    shutdown finished. The no-op SIGTERM handler installed here is what the
    re-raise lands on, so ``main`` returns 0 and cradle's spawner (the
    play-process precedent) sees the same clean exit on every path.
    Installed only on the main thread (signals cannot be set elsewhere;
    tests run ``uvicorn.Server`` on a worker thread without it)."""
    import uvicorn

    sock = sock if sock is not None else bind(HOST, port)
    bound_port = sock.getsockname()[1]
    config = uvicorn.Config(app, host=HOST, port=bound_port, log_config=None, access_log=False)
    server = uvicorn.Server(config)

    def request_shutdown(*_: Any) -> None:
        server.should_exit = True

    app.state.on_shutdown = request_shutdown

    previous_sigterm = None
    if threading.current_thread() is threading.main_thread():
        previous_sigterm = signal.signal(signal.SIGTERM, request_shutdown)

    stop = threading.Event()
    if parent_pid is not None:

        def on_parent_dead() -> None:
            log.info("parent pid %s is gone — shutting down", parent_pid)
            request_shutdown()

        threading.Thread(
            target=watch_parent,
            args=(parent_alive(parent_pid), on_parent_dead),
            kwargs={"interval": WATCHDOG_INTERVAL, "stop": stop},
            name="parent-watchdog",
            daemon=True,
        ).start()

    stream = out if out is not None else sys.stdout
    print(json.dumps({"port": bound_port, "pid": os.getpid()}), file=stream, flush=True)
    try:
        server.run(sockets=[sock])
    finally:
        stop.set()
        sock.close()
        if previous_sigterm is not None:
            signal.signal(signal.SIGTERM, previous_sigterm)
    return 0


def _error(message: str, **extra: Any) -> int:
    print(json.dumps({"error": message, **extra}), file=sys.stderr, flush=True)
    return 2


def main(argv: list[str] | None = None) -> int:
    """Sidecar entry (``python -m canon.agent.service`` and ``canon agent serve``).

    Returns 0 after a clean exit, 2 for a usage error (missing / non-pack
    directory, unknown backend id, missing extra, bad ``--fake-script``, a
    ``--port`` that cannot be bound) — reported as one JSON line on stderr,
    never a traceback, and never after the port line.
    """
    parser = argparse.ArgumentParser(
        prog="python -m canon.agent.service",
        description="Serve the cradle agent over HTTP+SSE on 127.0.0.1; the first stdout line is "
        '{"port": N, "pid": P}. Default backend "fake" is $0; any other id is a real, paid provider.',
    )
    parser.add_argument("--pack", required=True, help="pack root the conversations belong to")
    parser.add_argument("--backend", default=FAKE_BACKEND_ID, help="chat backend id (default: fake)")
    parser.add_argument("--model", default=None, help="model id for the backend (a plain string; ids are data)")
    parser.add_argument("--port", type=int, default=0, help="port on 127.0.0.1; 0 = a free port (default)")
    parser.add_argument("--parent-pid", type=int, default=None, help="exit when this pid is gone")
    parser.add_argument("--fake-script", type=Path, default=None, help="JSON turns the fake backend plays")
    parser.add_argument(
        "--permission-timeout",
        type=float,
        default=None,
        help="minutes a permission chip waits for a decision before the tool is rejected with "
        "'no decision' (default: wait until decided)",
    )
    parser.add_argument(
        "--project-store",
        type=Path,
        default=None,
        help="project-store root whose .cradle/skills/ loads (default: $CRADLE_PROJECT_STORE or ~/CradleProjects)",
    )
    parser.add_argument(
        "--parallel-cap", type=int, default=PARALLEL_CAP, help=f"concurrent specialist runs (default {PARALLEL_CAP})"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    pack = Path(args.pack)
    problem = pack_problem(pack)
    if problem is not None:
        status, reason = problem
        return _error(reason, status=status, pack=str(pack))

    try:
        backend = build_backend(args.backend, args.model, args.fake_script)
    except KeyError:
        return _error(
            f"unknown chat backend {args.backend!r}",
            known=[*BackendRegistry.chat_ids(), FAKE_BACKEND_ID],
        )
    except ImportError as exc:
        return _error(f"chat backend {args.backend!r} is not installed: {exc}")
    except (OSError, ValueError) as exc:
        return _error(f"--fake-script: {exc}")

    # Row A3: the auto-tier read tools, in-process over the canon verbs; row
    # A4: the ask-tier write tools behind the permission engine (grants at
    # <pack>/.canon/agent/permissions.json); row A4.5: the sandbox tool,
    # the roster + skills, and the run manager's delegate / propose_plan
    # (registered by create_app). Paid tools (A6) register after.
    if args.permission_timeout is not None and args.permission_timeout <= 0:
        return _error("--permission-timeout must be a positive number of minutes", value=args.permission_timeout)
    if args.parallel_cap <= 0:
        return _error("--parallel-cap must be a positive integer", value=args.parallel_cap)
    timeout = args.permission_timeout * 60 if args.permission_timeout is not None else None
    registry = ToolRegistry(PermissionEngine(pack, timeout=timeout))
    register_read_tools(registry, pack)
    register_write_tools(registry, pack, actor_for=current_call)
    register_play_tools(registry, pack, actor_for=current_call)
    # Row A7: the auto-tier vision tools (headless capture / trajectory /
    # view_asset). Their demote-to-ask flag is registry DATA, read per call.
    register_vision_tools(registry, pack)
    # Row A7.5: game_coder's engine-copy tools — engine_status (auto),
    # engine_sync (ask, never Always-allowable) and edit_project_code (ask,
    # followed by the gate ladder inside the run's own verify loop).
    register_code_tools(registry, pack, actor_for=current_call)
    # Row A6: the $-tier tools, their estimators and their free-selection tier
    # resolvers, all on the same registry + engine (no second gate).
    register_paid_tools(registry, pack, actor_for=current_call)
    store = ConversationStore(pack)
    roster = load_roster()
    skills = load_skills(pack, args.project_store)
    app = create_app(
        pack,
        args.backend,
        args.model,
        registry,
        store,
        backend=backend,
        roster=roster,
        skills=skills,
        parallel_cap=args.parallel_cap,
    )
    try:
        sock = bind(HOST, args.port)
    except OSError as exc:
        return _error(f"cannot bind {HOST}:{args.port}: {exc.strerror or exc}", port=args.port, errno=exc.errno)
    return serve(app, sock=sock, parent_pid=args.parent_pid)


__all__ = [
    "FAKE_BACKEND_ID",
    "HOST",
    "WATCHDOG_INTERVAL",
    "TurnLocks",
    "bind",
    "build_backend",
    "create_app",
    "fake_backend",
    "main",
    "pack_problem",
    "parent_alive",
    "pid_alive",
    "run_turn",
    "serve",
    "sse",
    "watch_parent",
]


if __name__ == "__main__":
    raise SystemExit(main())
