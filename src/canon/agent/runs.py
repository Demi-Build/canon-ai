"""The run manager — delegation, plans, the write gate and ⏹ Stop (Phase 1 §5.1/§5.5; row P1-A4.5).

One loop, many configurations: a specialist run is ``run_conversation``
under a different config (fresh context = the assembled prompt with the
brief + refs, the roster allowlist ∩ registry ∩ skills as its tools, its
own model) — this module never writes a second loop, it wraps the one in
``canon.agent.loop`` per delegation. What is genuinely new (§5.6):

- ``delegate(specialist, task, refs, budget?)`` — a foreman TOOL that
  starts a specialist run and returns its structured result
  ``{run_id, status, summary, artifacts_touched [{id, before, after}],
  cost: {usage}, attachments}``. Parallel cap 3 (a semaphore); the loop's
  ``parallel`` predicate lets one foreman turn fan out several
  delegations at once.
- **The per-pack write gate** — a lock keyed by TARGET (level id /
  artifact id — ``write_target``) taken around every ask/paid tool call,
  so two runs never interleave a target; reads are unrestricted.
- Run lifecycle events on the conversation's stream (A5's run cards):
  ``run_start {run_id, conversation, specialist, task, tools, dropped}``,
  ``run_progress {run_id, specialist, event}`` (the nested ChatEvents and
  tool events tagged with the run), ``run_end {run_id, status: ok | failed
  | cancelled, usage, artifacts, summary}``.
- Errors (§5.5): inside a run one corrected retry is the model's (it sees
  the ``is_error`` result); a SECOND tool failure — or any paid failure —
  stops the run and returns a structured failure to the foreman; a dead
  provider stream retries once when retryable.
- Actors: every specialist call runs as ``agent:<conversation>/<specialist>``
  through A4's ``bind_call`` (I6); grants govern tool names, never actors.
- Plans (§5.5 decomposition; README §7): ``propose_plan(steps)`` emits
  ``plan_proposed {plan_id, steps}`` and BLOCKS until ``decide_plan``
  (``POST /conversations/{id}/plans/{plan_id}`` — approve | reject | edit)
  lands. Approved, the foreman receives the plan as the tool result and
  executes it ONE TOOL CALL PER STEP, in order: the manager attributes the
  foreman's next tool calls to the pending steps (``plan_step {plan_id,
  index, status: running | done | failed | skipped}``), binds ``batchId =
  plan_id`` around every write (``provenance.bind_batch`` — every verb
  journals it; the specialists a step delegates to inherit it), and on a
  failure HALTS: ``plan_halted {plan_id, index, error, options}`` and the
  failed call blocks until ``resume_plan`` (continue | skip | stop) or
  ``undo_plan`` decides — nothing auto-continues past a failed step.
  ``undo_plan`` restores every write's ``before_hash`` in reverse order,
  whoever wrote it, via the ``restore`` tool under one ``undo:<plan_id>``
  batch (one History entry).
- ⏹ Stop (§3.0-D — start nothing new, keep what landed, say what it cost):
  ``stop_conversation`` sets the turn's cancel flag and every run's beneath
  it, rejects the conversation's pending permission chips and wakes its
  pending plans; ``stop_run`` stops one run only. The loop closes the
  provider generator and skips the pending tool call; the service marks
  the transcript turn ``cancelled`` with what landed and the usage so far.

Row P1-A7 adds ONE thing here — the VERIFY LOOP (goal 2, §5.5), which is
the machinery behind the core prompt's "never claim done without the
mandatory post-mutation validation":

- After a delegated run that MUTATED something, the manager runs that
  validation itself (``verify_run``): ``validate_level`` for every level
  artifact touched, the pack's own row validators for every row. What it
  cannot verify is reported as ``skipped`` with a reason, never as a pass.
- A failing verdict spends the run's ONE corrected retry — the SAME budget
  ``RUN_FAILURE_LIMIT`` gives a failed tool call, not a second retry path:
  the specialist is handed the verdict as a follow-up turn on its own
  history and repairs its own break inside the same run.
- A verdict that still fails ends the run ``failed`` with a structured
  error the foreman reports with an opinion. Nothing here claims done.
- VLM QA is OPTIONAL and backend-gated (``RunManager(vlm=…)``): the manager
  never builds a judge — a real one is a paid, user-run leg (doctrine 3) —
  and a judge's answer is advisory, riding the verdict as an ``opinion``
  while the validators alone decide its status.

Row P1-A7.5 adds ONE leg to that same loop — the ENGINE GATE LADDER. A run
that changed a file in the project's own engine copy (a ``code:<path>``
artifact) is verified by ``canon.agent.gates.run_ladder``: syntax, headless
boot (``grep -c 'SCRIPT ERROR'`` — Godot's exit code lies), a scripted smoke
through the ``PLAT_*`` mirror, and ``validate_level`` on the affected levels.
It is a leg of ``verify_run``, not a second verification path, so a failing
ladder spends the SAME one corrected retry and a green claim is impossible
without it. A machine without Godot reports the engine rungs ``unproven``
(``ok: None``) — a skip, loudly named, never a pass.

Deliberately absent, by row ownership: run cards / plan UI / Stop buttons
(A5), the cost journal rows a run's usage becomes (A6), the vision tools
themselves (A7's ``tools_vision``), play-session kill (W2.0 — extends the
same Child retention in cradle's worker), the promoted-pygame ladder and
``game_coder``'s tuning smoke (W2.0 / W2.1).
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from canon import provenance
from canon.agent.actors import FOREMAN, agent_actor, bind_call, current_call, parse_actor, user_actor
from canon.agent.loop import CANCELLED_STOP, ConversationCancelled, run_conversation
from canon.agent.permissions import PermissionEngine
from canon.agent.prompt import assemble, pack_context
from canon.agent.registry import Tool, ToolRefused, ToolRegistry
from canon.agent.roster import FOREMAN_ID, Specialist, core_law, resolve_model
from canon.agent.skills import Skill, SkillSet, intersect
from canon.backends.base import ChatBackend
from canon.llm.chat import ChatError, ChatEvent, ToolSpec, Usage

#: Default parallel delegation cap (§5.5).
PARALLEL_CAP = 3

#: Run / plan id prefixes (distinct id spaces from conversations).
RUN_PREFIX = "run_"
PLAN_PREFIX = "plan_"

#: Run statuses (data).
RUN_STATUSES: tuple[str, ...] = ("running", "ok", "failed", "cancelled")

#: Plan statuses (data; README §7's states plus the decision states).
PLAN_STATUSES: tuple[str, ...] = (
    "proposed",
    "approved",
    "rejected",
    "running",
    "halted",
    "done",
    "stopped",
    "undone",
    "cancelled",
)

#: Plan decisions (``decide_plan``) and halt options (``resume_plan`` + undo).
PLAN_DECISIONS: tuple[str, ...] = ("approve", "reject", "edit")
HALT_OPTIONS: tuple[str, ...] = ("continue", "skip", "undo", "stop")

#: Tool failures a run tolerates before it stops (one corrected retry).
#: Row A7's verify loop spends the SAME budget — a failed post-mutation
#: validation is a run failure, so a run never gets two independent retries.
RUN_FAILURE_LIMIT = 2

#: Row A7: the tool the manager calls to verify a touched level. It is a
#: registered auto-tier read (row A3), so verification never opens a chip and
#: never writes; a registry without it verifies nothing and SAYS so.
VERIFY_LEVEL_TOOL = "validate_level"

#: Verify verdict statuses (data; ``skipped`` is a first-class answer —
#: doctrine 4, a verification we cannot run is never reported as a pass).
VERIFY_STATUSES: tuple[str, ...] = ("ok", "failed", "skipped")

#: Row A7.5: the artifact namespace a code edit journals under
#: (``canon.engine_ops.CODE_NAMESPACE``, restated so this module does not
#: import the verb just to read one string). A run that touched one of these
#: gets the ENGINE GATE LADDER as its post-mutation validation — the same
#: verify loop, one more leg, never a second verification path.
CODE_NAMESPACE = "code"

#: Row A7's OPTIONAL vision-QA leg. A ``VLMBackend`` (``judge(prompt, images)``)
#: handed to ``RunManager(vlm=…)`` looks at frames of a mutated level and gives
#: an OPINION. The manager never constructs one — a real judge is a paid,
#: user-run leg (doctrine 3); tests pass ``FakeVLMBackend``. The opinion is
#: advisory by design: only the validators decide ``status``, because a defect
#: is something a validator can prove and a judgment is something a designer
#: decides (the playtester's own rule).
VLM_VERIFY_PROMPT = (
    "These frames are a headless capture of one level of a 2D platformer, in tick order, after an edit. "
    "Answer ONLY with JSON: {\"passed\": true|false, \"notes\": \"<one sentence>\"}. "
    "passed=false means something is visibly broken — the player is inside geometry, the level did not render, "
    "art is missing or placed impossibly. Style opinions are not failures."
)

#: How many ticks the VLM leg captures (short: this is a look, not a playtest).
VLM_VERIFY_TICKS = 120

#: How often a blocked plan wait re-checks the turn's cancel flag (seconds).
_WAIT_TICK = 0.1

#: The tools the manager registers for the foreman.
DELEGATE_TOOL = "delegate"
PLAN_TOOL = "propose_plan"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _artifact_of(event: dict) -> dict:
    """One journal event as a run card's ``artifacts_touched`` row."""
    return {
        "id": event.get("artifact_id"),
        "before": event.get("before_hash"),
        "after": event.get("after_hash"),
        "op": event.get("op"),
    }


def _delegation_failure(name: str, result: Any) -> Exception | None:
    """The exception a plan step raises when its DELEGATION came back
    failed or cancelled — ``None`` for anything else.

    ``delegate`` answers a dead run with a structured result rather than a
    raise (the foreman must be able to read it), so the plan machinery has
    to look inside to know the step did not land."""
    if name != DELEGATE_TOOL or not isinstance(result, str):
        return None
    try:
        payload = json.loads(result)
    except ValueError:
        return None
    if not isinstance(payload, dict) or payload.get("status") not in ("failed", "cancelled"):
        return None
    return ToolRefused(
        json.dumps(
            {
                "error": f"delegation_{payload.get('status')}",
                "run_id": payload.get("run_id"),
                "specialist": payload.get("specialist"),
                "detail": payload.get("error"),
            },
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        )
    )


# ---------------------------------------------------------------------------
# The verify loop's data (row A7)
# ---------------------------------------------------------------------------


def _verify_targets(artifacts: list[dict]) -> tuple[list[str], list[str], list[str]]:
    """What a run's touched artifacts mean for validation: ``(level ids, row
    artifact ids, engine-copy paths)``.

    The split rides the artifact-id grammar (``canon.bible.artifacts``):
    ``level:<stage>/<level>/<step>`` is a level's step file,
    ``code:<pack-relative path>`` is a file in the project's own engine copy
    (row A7.5 — it gets the GATE LADDER, not a row validator), anything else
    (``enemy:<id>``, ``item:<id>``, ``world``, …) is a row. Unparseable ids
    are dropped rather than guessed at — a namespace this row never heard of
    must not silently become "a level"."""
    levels: list[str] = []
    rows: list[str] = []
    code: list[str] = []
    for artifact in artifacts:
        artifact_id = str(artifact.get("id") or "")
        if not artifact_id:
            continue
        namespace, _, rest = artifact_id.partition(":")
        parts = rest.split("/") if rest else []
        if namespace == "level" and len(parts) >= 2:
            levels.append(parts[1])
        elif namespace == CODE_NAMESPACE and rest:
            code.append(rest)
        elif namespace:
            rows.append(artifact_id)
    return sorted(set(levels)), sorted(set(rows)), sorted(set(code))


def _level_problems(report: dict) -> list[str]:
    """Every problem string a ``validate_level`` report carries, flattened —
    the report shape is the verb's (``checks: [{name, problems}]``), read
    tolerantly so a widened report never breaks the verdict."""
    out: list[str] = []
    for check in report.get("checks") or []:
        if not isinstance(check, dict):
            continue
        name = check.get("name") or "check"
        for problem in check.get("problems") or []:
            out.append(f"{name}: {problem}")
    for finding in report.get("findings") or []:
        out.append(str(finding))
    return out


def _repair_brief(verdict: dict) -> str:
    """The follow-up turn a specialist gets after its own write failed the
    mandatory validation. It is a USER turn on the run's own history — the
    same loop, the same tools, the run's one corrected retry."""
    return (
        "MANDATORY POST-MUTATION VALIDATION FAILED on what you just wrote:\n"
        + json.dumps(verdict, separators=(",", ":"), ensure_ascii=False, default=str)
        + "\n\nRepair it now, in this run. Read the current state first, then make the smallest write that makes "
        "every check pass — do not re-do work that is already correct. This is your ONE corrected retry: if the "
        "validation still fails afterwards the run returns a failure to the foreman. Never report done while the "
        "validation is red."
    )


def _attachment_refs(result: Any) -> list[dict]:
    """The attachment REFERENCES (path + sha256 + bytes) inside one tool
    result, so a delegation's ``attachments`` carries what the specialist
    looked at without carrying the pixels back into the conversation
    (§3.4). Row A7's ``tools_vision`` owns the shape; imported lazily so
    the run manager keeps working in a service where that row is absent."""
    try:
        from canon.agent.tools_vision import attachment_refs
    except ImportError:  # pragma: no cover — the module ships with this row
        return []
    return attachment_refs(result)


# ---------------------------------------------------------------------------
# The write gate
# ---------------------------------------------------------------------------


def _target_of(name: str, i: dict) -> str:
    """The per-pack write target a tool call locks — a level, an artifact,
    a schema, the world; ``tool:<name>`` for anything unmapped (which then
    serializes per tool, never runs unlocked)."""
    try:
        if name in ("apply_level_edit", "import_level_grids", "publish_level", "generate_layout", "improve_layout"):
            return f"level:{i['level_id']}"
        if name in ("place_enemies", "place_items", "generate_music"):
            return f"level:{i.get('level_id') or i.get('stage') or i.get('target')}"
        if name in ("create_level", "generate_level"):
            return f"stage:{i['params']['stage_id']}" if "params" in i else f"stage:{i.get('stage_id')}"
        if name == "edit_world_map":
            return "world"
        if name in ("update_row", "complete_row"):
            return f"{i['type']}:{i['id']}"
        if name == "update_schema":
            return f"schema:{i['type']}"
        if name in ("pin", "unpin"):
            return "bible"
        if name == "sandbox_level":
            return f"level:{i.get('level_id') or 'sandbox'}"
        if name == "edit_project_code":
            return f"code:{i.get('path')}"
        if "target" in i:
            return str(i["target"])
        if "level_id" in i:
            return f"level:{i['level_id']}"
    except (KeyError, TypeError):
        pass
    return f"tool:{name}"


def write_target(name: str, tool_input: dict) -> str:
    """Public form of the target rule (data-shaped; tested)."""
    return _target_of(name, tool_input)


class WriteGate:
    """Per-target locks: ``hold(target)`` serializes every write to one
    level/artifact across runs and conversations (§5.5). ``on_acquire`` /
    ``on_release`` observe the lock trace (the tests assert interleaving is
    impossible from it)."""

    def __init__(
        self,
        on_acquire: Callable[[str], None] | None = None,
        on_release: Callable[[str], None] | None = None,
    ) -> None:
        self._locks: dict[str, threading.RLock] = {}
        self._guard = threading.Lock()
        self.on_acquire = on_acquire
        self.on_release = on_release

    def lock_for(self, target: str) -> threading.RLock:
        with self._guard:
            return self._locks.setdefault(target, threading.RLock())

    @contextmanager
    def hold(self, target: str) -> Iterator[None]:
        lock = self.lock_for(target)
        lock.acquire()
        try:
            if self.on_acquire is not None:
                self.on_acquire(target)
            yield
        finally:
            if self.on_release is not None:
                self.on_release(target)
            lock.release()


# ---------------------------------------------------------------------------
# Runs, turns, plans
# ---------------------------------------------------------------------------


@dataclass
class Run:
    """One delegated specialist run (the run card)."""

    run_id: str
    conversation: str
    specialist: str
    actor: str
    task: str
    refs: list[Any]
    budget: float | None
    tools: list[str]
    dropped: list[str]
    model: str | None
    batch_id: str | None
    status: str = "running"
    cancel: threading.Event = field(default_factory=threading.Event)
    cancel_reason: str | None = None
    failures: int = 0
    usage: Usage = field(default_factory=Usage)
    artifacts: list[dict] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    summary: str = ""
    error: str | None = None
    attachments: list[Any] = field(default_factory=list)
    #: Row A7: the mandatory post-mutation validation's verdict, or ``None``
    #: when the run mutated nothing (there is nothing to validate).
    verify: dict | None = None
    started: str = field(default_factory=_now)
    ended: str | None = None

    def payload(self) -> dict:
        return {
            "run_id": self.run_id,
            "conversation": self.conversation,
            "specialist": self.specialist,
            "actor": self.actor,
            "task": self.task,
            "refs": list(self.refs),
            "budget": self.budget,
            "tools": list(self.tools),
            "dropped": list(self.dropped),
            "model": self.model,
            "batch_id": self.batch_id,
            "status": self.status,
            "usage": asdict(self.usage),
            "artifacts": list(self.artifacts),
            "steps": list(self.steps),
            "summary": self.summary,
            "error": self.error,
            "verify": self.verify,
            "started": self.started,
            "ended": self.ended,
        }

    def result(self) -> dict:
        """What the foreman's ``delegate`` call returns (§5.1)."""
        return {
            "run_id": self.run_id,
            "specialist": self.specialist,
            "status": self.status,
            "summary": self.summary,
            "artifacts_touched": list(self.artifacts),
            "cost": {"usage": asdict(self.usage)},
            "attachments": list(self.attachments),
            "verify": self.verify,
            "error": self.error,
            "tools_dropped": list(self.dropped),
        }


@dataclass
class TurnState:
    """One in-flight turn of a conversation: its cancel flag, stream, UI
    state and the runs beneath it."""

    conversation: str
    emit: Callable[[str, Any], None]
    mode: str | None = None
    ui_state: dict | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    runs: set[str] = field(default_factory=set)
    cancel_reason: str | None = None


@dataclass
class Plan:
    """One proposed plan and its execution state (README §7)."""

    plan_id: str
    conversation: str
    steps: list[dict]
    status: str = "proposed"
    created: str = field(default_factory=_now)
    decision: threading.Event = field(default_factory=threading.Event)
    decided: dict | None = None
    halted: dict | None = None
    resume: threading.Event = field(default_factory=threading.Event)
    resume_action: str | None = None
    undone: list[dict] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def payload(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "conversation": self.conversation,
            "status": self.status,
            "steps": [dict(step) for step in self.steps],
            "created": self.created,
            "decision": self.decided,
            "halted": self.halted,
            "undone": list(self.undone),
        }

    def next_pending(self) -> dict | None:
        for step in self.steps:
            if step.get("status") == "pending":
                return step
        return None

    def all_settled(self) -> bool:
        return all(step.get("status") in ("done", "skipped") for step in self.steps)


def _normalize_steps(steps: Any) -> list[dict]:
    if not isinstance(steps, list) or not steps:
        raise ValueError("a plan needs a non-empty list of steps [{text, tier, specialist?}]")
    out: list[dict] = []
    for index, raw in enumerate(steps, start=1):
        if not isinstance(raw, dict) or not isinstance(raw.get("text"), str) or not raw["text"].strip():
            raise ValueError(f"step {index}: 'text' must be a non-empty string")
        tier = raw.get("tier", "ask")
        if not isinstance(tier, str) or not tier:
            raise ValueError(f"step {index}: 'tier' must be a string (auto | ask | paid)")
        specialist = raw.get("specialist")
        if specialist is not None and not isinstance(specialist, str):
            raise ValueError(f"step {index}: 'specialist' must be a string when given")
        out.append(
            {
                "index": index,
                "text": raw["text"].strip(),
                "tier": tier,
                "specialist": specialist,
                "status": "pending",
                "estimate": raw.get("estimate"),
            }
        )
    return out


# ---------------------------------------------------------------------------
# The delegate tool's spec — ONE definition, two callers
# ---------------------------------------------------------------------------


def delegate_menu(roster: dict[str, Specialist] | None, routable: list[str] | None = None) -> str:
    """The specialists ``delegate`` offers, as the description spells them:
    every roster id but the foreman, then the routable skills."""
    known = [s for s in (roster or {}) if s != FOREMAN_ID]
    return ", ".join([*known, *(routable or [])]) or "(no roster loaded)"


def delegate_spec(menu: str) -> ToolSpec:
    """The ``delegate`` ``ToolSpec``. Extracted at row A7 so the routing eval
    binds to the REAL spec the foreman is offered (the corpus never carries a
    second definition of a tool — doctrine 2, the rule row A1 already applies
    to the read tools)."""
    return ToolSpec(
        name=DELEGATE_TOOL,
        description=(
            "Hand a bounded task to a specialist and wait for its structured result "
            "{run_id, status, summary, artifacts_touched, cost, attachments, verify}. The task decides the "
            f"specialist — choose from: {menu}. Give a one-paragraph task and the ids it needs in refs "
            "(level ids, artifact ids, prior findings). Independent delegations in one turn run in "
            "parallel."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "specialist": {"type": "string", "description": f"One of: {menu}"},
                "task": {"type": "string", "description": "The brief the specialist executes."},
                "refs": {"type": "array", "items": {}, "description": "Ids and findings the task needs."},
                "budget": {"type": "number", "description": "Optional USD ceiling the run should respect."},
            },
            "required": ["specialist", "task"],
            "additionalProperties": False,
        },
    )


# ---------------------------------------------------------------------------
# The manager
# ---------------------------------------------------------------------------


class RunManager:
    """See the module docstring. One per service (per pack).

    Args:
        pack_dir: The open pack.
        registry: The tool registry (reads + writes + play + the two
            foreman tools ``register_tools`` adds).
        backend: The conversation's chat backend (specialists run on it
            with their own model id).
        store: The transcript store (lifecycle lines are journaled there).
        roster: ``load_roster()``; ``None`` = no specialist layer — the
            foreman is the whole registry (row A4's shape) and ``delegate``
            refuses with a reason.
        skills: ``load_skills(...)``; ``None`` = none.
        parallel_cap: Concurrent delegations per service (§5.5: 3).
        model: The conversation's model id (the specialist fallback).
        max_tool_rounds: Per specialist run.
    """

    def __init__(
        self,
        *,
        pack_dir: str | Path,
        registry: ToolRegistry,
        backend: ChatBackend,
        store: Any,
        roster: dict[str, Specialist] | None = None,
        skills: SkillSet | None = None,
        parallel_cap: int = PARALLEL_CAP,
        model: str | None = None,
        max_tool_rounds: int = 8,
        gate: WriteGate | None = None,
        vlm: Any | None = None,
    ) -> None:
        self.pack = Path(pack_dir)
        self.registry = registry
        self.backend = backend
        self.store = store
        self.roster = roster
        self.skills = skills if skills is not None else SkillSet()
        self.parallel_cap = max(1, int(parallel_cap))
        self.model = model
        self.max_tool_rounds = max_tool_rounds
        self.gate = gate if gate is not None else WriteGate()
        #: Optional vision judge for the verify loop (``VLM_VERIFY_PROMPT``).
        #: ``None`` = no VLM QA, which is the shipped default: the manager
        #: never builds a backend, so no run can spend money on an opinion.
        self.vlm = vlm
        self._slots = threading.BoundedSemaphore(self.parallel_cap)
        self._turns: dict[str, TurnState] = {}
        self.runs: dict[str, Run] = {}
        self.plans: dict[str, Plan] = {}
        self.latest_ui_state: dict[str, dict | None] = {}
        self._lock = threading.Lock()
        self._core: str | None = None

    # -- turns -----------------------------------------------------------------

    @contextmanager
    def turn(
        self,
        conversation: str,
        *,
        emit: Callable[[str, Any], None],
        ui_state: dict | None = None,
        mode: str | None = None,
    ) -> Iterator[TurnState]:
        """Bind one in-flight turn (the service does this per POST)."""
        state = TurnState(conversation=conversation, emit=emit, mode=mode, ui_state=ui_state)
        with self._lock:
            self._turns[conversation] = state
            if ui_state is not None:
                self.latest_ui_state[conversation] = ui_state
        try:
            yield state
        finally:
            # A plan the foreman stopped executing must not outlive its turn
            # (it would refuse every later plan in this conversation). Settled
            # while the turn is still bound, so the events reach this stream.
            self.close_plans(
                conversation,
                reason=state.cancel_reason or "the turn ended before the plan finished",
                status="stopped",
            )
            with self._lock:
                if self._turns.get(conversation) is state:
                    del self._turns[conversation]

    def turn_state(self, conversation: str) -> TurnState | None:
        with self._lock:
            return self._turns.get(conversation)

    def busy(self, conversation: str) -> bool:
        return self.turn_state(conversation) is not None

    def _emit(self, conversation: str, event: str, data: dict, *, transcript: bool = False) -> None:
        state = self.turn_state(conversation)
        if state is not None:
            state.emit(event, data)
        if transcript:
            try:
                self.store.append(conversation, {"type": event, **data})
            except KeyError:
                pass

    # -- prompts + subsets -----------------------------------------------------

    def core(self) -> str:
        if self._core is None:
            self._core = core_law()
        return self._core

    def context(self) -> dict:
        """The pack-context layer, probed now (re-probed per turn)."""
        return pack_context(self.pack)

    def specialist(self, specialist_id: str) -> Specialist:
        if self.roster is None:
            raise LookupError("no roster is loaded — this service has no specialist layer (create_app(roster=…))")
        try:
            return self.roster[specialist_id]
        except KeyError:
            known = [s for s in self.roster if s != FOREMAN_ID]
            raise LookupError(
                json.dumps({"error": "unknown_specialist", "specialist": specialist_id, "known": known,
                            "routable_skills": [s.id for s in self.skills.routable()]})
            ) from None

    def resolve_delegate_target(self, specialist_id: str) -> tuple[Specialist, Skill | None]:
        """A roster id, or a routable skill id (its host specialist + the skill)."""
        if self.roster is not None and specialist_id in self.roster:
            return self.roster[specialist_id], None
        skill = self.skills.skills.get(specialist_id)
        if skill is not None and skill.routable:
            host = skill.specialist or FOREMAN_ID
            if host == FOREMAN_ID:
                raise LookupError(f"routable skill {specialist_id!r} names no host specialist")
            return self.specialist(host), skill
        return self.specialist(specialist_id), None

    def subset(self, specialist: Specialist, allowlist: tuple[str, ...] | None = None) -> tuple[list[str], list[str]]:
        """``(kept, dropped)``: the specialist's allowlist (∩ a skill's
        allowlist when given — never widening) ∩ the registry, in roster
        order. ``dropped`` names what is not registered (rows not landed)
        or not in the host's list — reported on ``run_start``, loudly."""
        names = list(specialist.tools)
        dropped: list[str] = []
        if allowlist is not None:
            names, dropped = intersect(allowlist, names)
        registered = set(self.registry.names())
        kept = [n for n in names if n in registered]
        dropped.extend(n for n in names if n not in registered)
        return kept, dropped

    def foreman_tool_names(self) -> list[str]:
        """What the conversation offers the model: the roster foreman's
        allowlist ∩ registry, or the whole registry without a roster."""
        if self.roster is None or FOREMAN_ID not in self.roster:
            return self.registry.names()
        kept, _ = self.subset(self.roster[FOREMAN_ID])
        return kept

    def specs_for(self, names: list[str]) -> list[ToolSpec]:
        return [self.registry.get(name).spec for name in names]

    def foreman_prompt(self, conversation: str, *, text: str | None = None, context: dict | None = None) -> str:
        """The assembled §3.1 prompt for the foreman's next turn."""
        if self.roster is None or FOREMAN_ID not in self.roster:
            foreman = Specialist(
                id=FOREMAN_ID, label="Foreman", actor=FOREMAN, tools=tuple(self.registry.names()),
                model_tier=None, model=None, role_prompt="(no roster loaded — the whole registry is offered)",
                path="",
            )
        else:
            foreman = self.roster[FOREMAN_ID]
        return assemble(
            self.pack,
            foreman,
            ui_state=self.latest_ui_state.get(conversation),
            skills=self.skills.matched(FOREMAN_ID, text),
            core=self.core(),
            context=context,
        )

    def roster_report(self) -> list[dict]:
        """Per specialist: allowlist, what the registry serves, what is missing (loud)."""
        if self.roster is None:
            return []
        out = []
        for specialist in self.roster.values():
            kept, dropped = self.subset(specialist)
            out.append(
                {
                    "id": specialist.id,
                    "label": specialist.label,
                    "actor": specialist.actor,
                    "model_tier": specialist.model_tier,
                    "model": specialist.model or resolve_model(self.pack, specialist),
                    "tools": list(specialist.tools),
                    "available": kept,
                    "missing": dropped,
                }
            )
        return out

    def unreachable_tools(self) -> list[str]:
        """Registered tools NO specialist can reach — the loud direction
        ``roster_report`` misses.

        ``roster_report`` names allowlisted-but-unregistered tools (a row
        that has not landed); this names the inverse, a registered verb no
        roster file lists, which silently removes a capability the moment a
        roster loads. Doctrine 4: disabled with a reason, never hidden."""
        if self.roster is None:
            return []
        reachable = {name for specialist in self.roster.values() for name in self.subset(specialist)[0]}
        return sorted(set(self.registry.names()) - reachable)

    # -- the one executor ------------------------------------------------------

    def execute(
        self,
        name: str,
        tool_input: dict,
        *,
        actor: str,
        conversation: str,
        run: Run | None = None,
    ) -> Any:
        """Run one tool call for the foreman (``run=None``) or a specialist
        run: cancel check → run subset (fail closed) → plan-step attribution
        → write gate (ask/paid) → ``bind_call`` + ``bind_batch`` →
        ``registry.execute`` (the permission engine) → run failure policy.

        Two orderings here are deliberate, not accidental:

        - The target lock is held ACROSS ``registry.execute``, and the
          permission round-trip happens inside it — so a chip awaiting a
          human decision holds its target's lock, and another run wanting
          the same target waits for the answer. That is the §5.5 promise
          (never interleave a target) taken literally: a second write must
          not slip in between "the user was shown this diff" and "the
          write happened". Recovery for an abandoned chip is ⏹ Stop
          (``cancel_pending`` wakes it and releases the lock), and the
          engine's ``--permission-timeout`` bounds it when set.
        - A run's artifacts come from the call context's own journal sink
          (row A4's ``journal_window``), never from a slice of the pack log
          taken around the whole run: two parallel runs of ONE specialist
          share an actor, and a slice hands one of them the other's writes.
        """
        state = self.turn_state(conversation)
        cancel = run.cancel if run is not None else (state.cancel if state is not None else None)
        if cancel is not None and cancel.is_set():
            raise ToolRefused(f"not executed: {CANCELLED_STOP}")
        if run is not None and name not in run.tools:
            refused = ToolRefused(
                json.dumps({"error": "tool_not_in_run", "tool": name, "specialist": run.specialist, "tools": run.tools})
            )
            self._run_failure(run, name, "ask", refused)  # a refused call is a failure the run counts
            raise refused
        tool = self.registry.get(name)
        plan, step = (None, None) if run is not None else self._claim_step(conversation, name)
        batch = run.batch_id if run is not None else (plan.plan_id if plan is not None else provenance.current_batch())
        target = _target_of(name, tool_input) if tool.tier != "auto" else None
        started = time.monotonic()
        if step is not None:
            self._plan_step(plan, step, "running", tool=name)
        try:
            with (
                self.gate.hold(target) if target is not None else nullcontext(),
                bind_call(actor, conversation, run.run_id if run is not None else None) as call,
                provenance.bind_batch(batch),
            ):
                try:
                    result = self.registry.execute(name, tool_input, actor=actor, conversation=conversation)
                finally:
                    # Whatever this ONE call journaled is this run's, failed
                    # call included (a verb can write before it raises).
                    if run is not None and call.journal:
                        with self._lock:
                            run.artifacts.extend(_artifact_of(e) for e in call.journal)
        except Exception as exc:
            if run is not None:
                self._run_failure(run, name, tool.tier, exc)
            if step is not None and plan is not None:
                raise self._halt(plan, step, exc, conversation, tool=name, seconds=time.monotonic() - started) from exc
            raise
        if step is not None and plan is not None:
            failure = _delegation_failure(name, result)
            if failure is not None:
                # §5.5: a step whose specialist run FAILED halts the plan. The
                # delegation itself did not raise (a structured failure is the
                # tool's result), so without this the step would go green with
                # nothing done and the plan would run on to plan_done.
                raise self._halt(
                    plan, step, failure, conversation, tool=name, seconds=time.monotonic() - started
                ) from None
            self._plan_step(plan, step, "done", tool=name, seconds=time.monotonic() - started)
            self._settle(plan, conversation)
        return result

    def _run_failure(self, run: Run, name: str, tier: str, exc: Exception) -> None:
        """§5.5 inside a run: the first tool failure is the model's corrected
        retry; the second — or any paid failure — stops the run."""
        run.failures += 1
        if tier == "paid" or run.failures >= RUN_FAILURE_LIMIT:
            why = (
                "a paid tool failed" if tier == "paid"
                else f"tool failure #{run.failures} (one corrected retry allowed)"
            )
            run.cancel_reason = "failed"
            run.error = f"{why}: {name}: {type(exc).__name__}: {exc}"
            run.cancel.set()

    # -- delegation --------------------------------------------------------------

    def delegate(
        self,
        *,
        conversation: str,
        specialist: str,
        task: str,
        refs: list[Any] | None = None,
        budget: float | None = None,
    ) -> dict:
        """Start a specialist run and block until it ends; returns
        ``Run.result()`` (a structured failure is a result, never a raise —
        the foreman folds it in)."""
        state = self.turn_state(conversation)
        if state is None:
            raise LookupError(f"no turn in flight for conversation {conversation}")
        target, skill = self.resolve_delegate_target(specialist)
        if target.is_foreman():
            raise ValueError("the foreman never delegates to itself — name a specialist")
        allow = skill.allowlist if skill is not None else None
        kept, dropped = self.subset(target, allow)
        matched = list(self.skills.matched(target.id, task))
        if skill is not None and skill not in matched:
            matched.append(skill)
        preferred = skill.model if skill is not None and skill.model else None
        model = preferred or resolve_model(self.pack, target) or self.model
        run = Run(
            run_id=RUN_PREFIX + secrets.token_hex(4),
            conversation=conversation,
            specialist=target.id,
            actor=agent_actor(conversation, target.actor),
            task=task,
            refs=list(refs or []),
            budget=budget,
            tools=kept,
            dropped=dropped,
            model=model,
            batch_id=provenance.current_batch(),
        )
        with self._lock:
            self.runs[run.run_id] = run
            state.runs.add(run.run_id)
        if state.cancel.is_set():
            run.cancel_reason = "stopped"
            run.cancel.set()
        if not kept:
            run.status = "failed"
            run.error = (
                f"no registered tools for {target.id}: {dropped} are not registered in this service "
                "(their rows have not landed, or the skill's allowlist has no overlap with the host's)"
            )
            run.ended = _now()
            self._emit(conversation, "run_start", self._start_payload(run), transcript=True)
            self._emit(conversation, "run_end", self._end_payload(run), transcript=True)
            state.runs.discard(run.run_id)
            return run.result()

        with self._slots:
            self._emit(conversation, "run_start", self._start_payload(run), transcript=True)
            try:
                self._run(run, target, matched)
            finally:
                run.ended = _now()
                self._meter(run)
                self._emit(conversation, "run_end", self._end_payload(run), transcript=True)
                with self._lock:
                    state.runs.discard(run.run_id)
        return run.result()

    def _meter(self, run: Run) -> None:
        """Row P1-A6: journal this run's token burn under the SPECIALIST's own
        identity, the same way the service meters a foreman turn.

        A delegated run drives its own ``run_conversation``, so its usage never
        passed through the turn meter — the run card showed it and the journal
        did not, which made every specialist's ``tokens`` column $0 and let
        cradle's panel (which counts ``run_end``) disagree with the dashboard
        for the same conversation. One journal entry, both tables (README §12).

        The import is function-local: ``service`` imports THIS module, and the
        turn meter is the one that owns the pricing call (nothing here prices
        anything). Best-effort by contract — ``journal_turn_tokens`` swallows
        its own failures, so a run can never fail on its meter. A cancelled run
        is metered too (P.8.5: a stopped run's burn is still a token row).
        """
        from canon.agent.service import journal_turn_tokens

        journal_turn_tokens(
            self.pack,
            run.conversation,
            asdict(run.usage),
            model=run.model,
            backend_id=str(getattr(self.backend, "id", "") or ""),
            cancelled=run.status == "cancelled",
            specialist=run.specialist,
            run_id=run.run_id,
            batch_id=run.batch_id,
        )

    def _start_payload(self, run: Run) -> dict:
        return {
            "run_id": run.run_id,
            "conversation": run.conversation,
            "specialist": run.specialist,
            "actor": run.actor,
            "task": run.task,
            "refs": list(run.refs),
            "budget": run.budget,
            "tools": list(run.tools),
            "dropped": list(run.dropped),
            "model": run.model,
            "batch_id": run.batch_id,
            "started": run.started,
        }

    def _end_payload(self, run: Run) -> dict:
        return {
            "run_id": run.run_id,
            "conversation": run.conversation,
            "specialist": run.specialist,
            "status": run.status,
            "usage": asdict(run.usage),
            "artifacts": list(run.artifacts),
            "attachments": list(run.attachments),
            "summary": run.summary,
            "verify": run.verify,
            "error": run.error,
            "ended": run.ended,
        }

    def _run(self, run: Run, specialist: Specialist, skills: list[Skill]) -> None:
        conversation = run.conversation
        state = self.turn_state(conversation)
        prompt = assemble(
            self.pack,
            specialist,
            ui_state=state.ui_state if state is not None else None,
            task_brief=run.task,
            refs=run.refs,
            skills=skills,
            core=self.core(),
        )
        tools = self.specs_for(run.tools)

        def execute(name: str, tool_input: dict) -> Any:
            self._emit(conversation, "run_progress", {
                "run_id": run.run_id, "specialist": run.specialist,
                "event": {"type": "tool_call", "name": name, "input": tool_input},
            })
            try:
                result = self.execute(name, tool_input, actor=run.actor, conversation=conversation, run=run)
            except Exception as exc:
                self._emit(conversation, "run_progress", {
                    "run_id": run.run_id, "specialist": run.specialist,
                    "event": {"type": "tool_result", "name": name, "is_error": True,
                              "error": f"{type(exc).__name__}: {exc}"},
                })
                raise
            self._emit(conversation, "run_progress", {
                "run_id": run.run_id, "specialist": run.specialist,
                "event": {"type": "tool_result", "name": name, "is_error": False},
            })
            return result

        def on_event(event: ChatEvent) -> None:
            self._emit(conversation, "run_progress", {
                "run_id": run.run_id, "specialist": run.specialist, "event": asdict(event),
            })

        def drive(messages: list[str], history: list[dict] | None) -> Any:
            """One pass of the ONE loop for this run (the retryable provider
            call included). Sets ``run.status`` / ``run.error``; returns the
            partial or complete ``ConversationResult``, or ``None``."""
            attempts = 0
            while True:
                try:
                    outcome = run_conversation(
                        self.backend,
                        system=prompt,
                        tools=tools,
                        tool_executor=execute,
                        user_messages=messages,
                        model=run.model,
                        max_tool_rounds=self.max_tool_rounds,
                        on_event=on_event,
                        history=history,
                        cancel=run.cancel,
                    )
                    run.status = "ok"
                    return outcome
                except ConversationCancelled as cancelled:
                    run.status = "failed" if run.cancel_reason == "failed" else "cancelled"
                    if run.status == "cancelled" and run.error is None:
                        run.error = f"stopped at {cancelled.where}: {run.cancel_reason or 'stopped by the user'}"
                    return cancelled.result
                except ChatError as exc:
                    attempts += 1
                    if attempts < 2 and getattr(exc, "retryable", False):
                        continue
                    run.status = "failed"
                    run.error = f"provider: {exc}"
                    return None
                except Exception as exc:  # noqa: BLE001 — a dead run is a structured failure to the foreman
                    run.status = "failed"
                    run.error = f"{type(exc).__name__}: {exc}"
                    return None

        result = drive([run.task], None)
        self._fold(run, result)
        # ``run.artifacts`` was filled per tool call in ``execute`` from that
        # call's own journal sink — a slice of the pack log taken here would
        # claim a parallel run's writes (same actor, overlapping windows).
        self._verify_leg(run, result, drive)

    def _fold(self, run: Run, result: Any) -> None:
        """Fold one loop pass into the run: usage ACCUMULATES (a repair leg
        costs real tokens and the card must say so), steps append, summary is
        the latest word, attachments keep the REFS a vision tool returned —
        never the bytes (§3.4)."""
        if result is None:
            return
        with self._lock:
            run.usage = run.usage + result.usage
            run.steps.extend({"tool": s["tool"], "is_error": s["is_error"]} for s in result.steps)
            for step in result.steps:
                run.attachments.extend(_attachment_refs(step.get("result")))
        if result.texts:
            run.summary = result.texts[-1]

    # -- the verify loop (row A7; goal 2, §5.5) --------------------------------------

    def verify_run(self, run: Run) -> dict:
        """The mandatory post-mutation validation for what THIS run touched.

        Levels validate through the registered ``validate_level`` read; rows
        through the pack seed's own validators (``PackSpec.validators`` — the
        dungeon's referential-integrity family today; a template that
        declares none is reported ``skipped`` with that reason rather than
        given a free pass). Never raises: a verification that itself fails is
        an ``error`` check, so a broken validator can never be mistaken for a
        green one.

        Row A7.5 adds the CODE leg: a run that changed a file in the
        project's own engine copy is validated by the §7.1 gate ladder
        (``canon.agent.gates``) — syntax, headless boot, scripted smoke,
        ``validate_level`` on the affected levels. The ladder owns the level
        rung for the levels it runs, so they are not validated twice; a
        machine without Godot reports the engine rungs ``unproven``
        (``ok: None``), which is a skip, never a pass.
        """
        levels, rows, code = _verify_targets(run.artifacts)
        ladder_levels = self._ladder_levels(levels) if code else []
        checks: list[dict] = []
        for level_id in levels:
            if level_id in ladder_levels:
                continue  # the ladder's validate rung covers it
            checks.append(self._verify_level(level_id))
        if rows:
            checks.append(self._verify_rows(rows))
        if code:
            checks.append(self._verify_code(code, ladder_levels))
        if not checks:
            return {"status": "skipped", "checks": [], "levels": [], "rows": rows, "code": code,
                    "reason": "nothing this run touched has a validation to run"}
        # Only the VALIDATORS decide the status; the judge's opinion rides
        # beside them (advisory — see VLM_VERIFY_PROMPT).
        status = "failed" if any(check.get("ok") is False for check in checks) else (
            "skipped" if all(check.get("ok") is None for check in checks) else "ok"
        )
        verdict = {"status": status, "checks": checks, "levels": levels, "rows": rows}
        if code:
            verdict["code"] = code
        opinions = [self._vlm_look(level_id) for level_id in levels] if self.vlm is not None else []
        if opinions:
            verdict["opinions"] = opinions
        return verdict

    def _vlm_look(self, level_id: str) -> dict:
        """One advisory look at a mutated level (optional, backend-gated).

        Frames come from the registered ``capture_frames`` (row A7, auto-tier,
        headless, writes nothing); the verdict comes from whatever
        ``VLMBackend`` the CALLER supplied. Never raises, never decides: an
        unparseable answer is ``ok: None`` with the raw text, because a judge
        that mumbles is not a pass and not a failure.
        """
        import base64

        opinion: dict = {"kind": "vlm", "target": level_id, "ok": None,
                         "model": str(getattr(self.vlm, "model", "") or "")}
        payload = {"level_id": level_id, "ticks": VLM_VERIFY_TICKS}
        try:
            tool = self.registry.get("capture_frames")
            engine = self.registry.permissions
            if hasattr(engine, "effective_tier") and engine.effective_tier(tool, payload) != "auto":
                # ASSUMPTION-6a's escape hatch outranks an advisory look: a
                # project that demoted headless capture to ask does not get it
                # fired unasked from inside a verify loop.
                opinion["reason"] = "capture_frames is demoted to ask in this project; the look was skipped"
                return opinion
            blocks = tool.run(payload)
            images = [
                base64.standard_b64decode(block["source"]["data"])
                for block in blocks
                if isinstance(block, dict) and block.get("type") == "image"
            ]
            if not images:
                opinion["reason"] = "the capture produced no frames to look at"
                return opinion
            answer = self.vlm.judge(VLM_VERIFY_PROMPT, images)
            opinion["frames"] = len(images)
        except Exception as exc:  # noqa: BLE001 — an opinion never breaks a verdict
            opinion["reason"] = f"{type(exc).__name__}: {exc}"
            return opinion
        try:
            parsed = json.loads(str(answer).strip())
            opinion["ok"] = bool(parsed["passed"])
            opinion["notes"] = str(parsed.get("notes") or "")
        except (ValueError, KeyError, TypeError):
            opinion["reason"] = "the judge did not answer in the requested JSON shape"
            opinion["raw"] = str(answer)[:400]
        return opinion

    def _verify_level(self, level_id: str) -> dict:
        try:
            tool = self.registry.get(VERIFY_LEVEL_TOOL)
        except LookupError:
            return {
                "kind": "level", "target": level_id, "ok": None,
                "reason": f"{VERIFY_LEVEL_TOOL} is not registered in this service (row A3 brings it)",
            }
        try:
            raw = tool.run({"level_id": level_id})
            report = json.loads(raw) if isinstance(raw, str) else raw
        except Exception as exc:  # noqa: BLE001 — a broken validator is a finding, never a pass
            return {"kind": "level", "target": level_id, "ok": False,
                    "error": f"{type(exc).__name__}: {exc}"}
        ok = bool(report.get("ok")) if isinstance(report, dict) else False
        problems = _level_problems(report) if isinstance(report, dict) else ["validator returned no report"]
        return {"kind": "level", "target": level_id, "tool": VERIFY_LEVEL_TOOL, "ok": ok, "problems": problems}

    def _ladder_levels(self, touched: list[str]) -> list[str]:
        """Which levels the gate ladder runs against (row A7.5): the ones the
        run touched, or — for a pure code edit, which touches none — the
        pack's own first level, so the smoke rung has something to play.
        A pack with no levels answers ``[]`` and the smoke rung says so."""
        if touched:
            return list(touched)
        from canon.agent.gates import smoke_levels

        return smoke_levels(self.pack)

    def _verify_code(self, paths: list[str], levels: list[str]) -> dict:
        """The §7.1 gate ladder as one verify check (row A7.5).

        ``validate_level`` is handed to the ladder as the REGISTERED tool, so
        the validate rung runs exactly what the rest of the loop runs. The
        ladder's own status maps onto the verdict's tri-state: ``failed`` →
        ``ok: False`` (the run spends its one corrected retry), ``unproven``
        (no Godot on this machine) → ``ok: None``, which is a skip and never
        a pass."""
        from canon.agent.gates import ladder_summary, run_ladder

        try:
            registered = self.registry.get(VERIFY_LEVEL_TOOL)
        except LookupError:
            registered = None

        def read_report(level_id: str) -> dict:
            raw = registered.run({"level_id": level_id})  # type: ignore[union-attr]
            return json.loads(raw) if isinstance(raw, str) else raw

        validate = read_report if registered is not None else None
        try:
            ladder = run_ladder(self.pack, paths=paths, levels=levels, validate=validate)
        except Exception as exc:  # noqa: BLE001 — a broken ladder is a finding, never a pass
            return {"kind": "code", "targets": paths, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
        status = ladder.get("status")
        check = {
            "kind": "code",
            "targets": paths,
            "ok": True if status == "ok" else (False if status == "failed" else None),
            "ladder": ladder,
            "summary": ladder_summary(ladder),
        }
        if status != "ok":
            check["reason"] = ladder.get("reason") or f"the ladder stopped at the {ladder.get('failed_rung')} rung"
        return check

    def _verify_rows(self, rows: list[str]) -> dict:
        """The pack validator leg. ``PackSpec.validators`` is a seed callable
        ``(output_dir) -> [BaseValidator]``; each reads the persisted JSON
        (the files the game actually loads), so this validates the tree the
        run just wrote, not an in-memory copy."""
        try:
            from canon.packs import resolve_pack

            spec = resolve_pack(self.pack).spec
            factory = spec.validators
        except Exception as exc:  # noqa: BLE001
            return {"kind": "rows", "targets": rows, "ok": None,
                    "reason": f"the pack did not resolve: {type(exc).__name__}: {exc}"}
        if factory is None:
            return {
                "kind": "rows", "targets": rows, "ok": None,
                "reason": (
                    f"pack type {spec.pack_type!r} declares no row validators — the write verb's own fail-closed "
                    "validation is what guarded this edit"
                ),
            }
        try:
            problems: list[str] = []
            for validator in factory(self.pack):
                outcome = validator.validate([], context={"pack_dir": str(self.pack)})
                if not getattr(outcome, "passed", True) and getattr(outcome, "severity", "") == "error":
                    problems.extend(getattr(outcome, "issues", []) or [type(validator).__name__])
        except Exception as exc:  # noqa: BLE001 — a broken validator is a finding, never a pass
            return {"kind": "rows", "targets": rows, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"kind": "rows", "targets": rows, "ok": not problems, "problems": problems}

    def _verify_leg(self, run: Run, result: Any, drive: Callable[[list[str], list[dict] | None], Any]) -> None:
        """Run the mandatory validation, and spend the run's ONE corrected
        retry on a failure (§5.5's rule, the same ``RUN_FAILURE_LIMIT``
        budget a failed tool call spends — never a second retry path).

        A cancelled run is not verified: ⏹ starts nothing new. A run that
        mutated nothing has nothing to validate.
        """
        if run.status == "cancelled" or not run.artifacts:
            return
        verdict = self.verify_run(run)
        run.verify = verdict
        if verdict["status"] != "failed":
            return
        # The caught break is visible on the run card, never swallowed.
        self._emit(run.conversation, "run_progress", {
            "run_id": run.run_id, "specialist": run.specialist,
            "event": {"type": "verify", "status": verdict["status"], "checks": verdict["checks"]},
        })
        if run.status != "ok" or run.failures >= RUN_FAILURE_LIMIT - 1 or result is None or run.cancel.is_set():
            self._verify_failed(run, verdict, retried=False)
            return
        run.failures += 1
        repair = drive([_repair_brief(verdict)], result.messages)
        self._fold(run, repair)
        if run.status != "ok":
            run.verify = verdict
            return
        verdict = self.verify_run(run)
        run.verify = verdict
        if verdict["status"] == "failed":
            self._verify_failed(run, verdict, retried=True)

    def _verify_failed(self, run: Run, verdict: dict, *, retried: bool) -> None:
        """The structured failure the foreman reports with an opinion — never
        a "done" (core-prompt law: no claim of done without the mandatory
        post-mutation validation)."""
        run.status = "failed"
        run.error = json.dumps(
            {
                "error": "verify_failed",
                "message": (
                    "the mandatory post-mutation validation still fails after the corrected retry"
                    if retried
                    else "the mandatory post-mutation validation failed and no corrected retry was left"
                ),
                "specialist": run.specialist,
                "retried": retried,
                "verify": verdict,
                "artifacts_touched": [a.get("id") for a in run.artifacts],
            },
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        )

    # -- stop ------------------------------------------------------------------------

    def stop_run(self, run_id: str, reason: str = "stopped by the user") -> dict:
        """⏹ on one run card: stop that run only (the conversation continues)."""
        run = self.runs.get(run_id)
        if run is None:
            raise KeyError(f"no run {run_id!r}")
        already = run.status != "running"
        if not already:
            run.cancel_reason = reason
            run.cancel.set()
            engine: PermissionEngine = self.registry.permissions
            for pending in engine.pending(run.conversation):
                # Key on the RUN, not the actor: two parallel runs of one
                # specialist share an actor string, and ⏹ on one card must
                # not reject the other's chip. Older chips carry no run_id
                # (nothing bound one) — fall back to the actor match.
                mine = pending.get("run_id") == run.run_id if pending.get("run_id") else (
                    pending.get("actor") == run.actor
                )
                if mine:
                    try:
                        engine.decide(pending["request_id"], "reject", reason, conversation=run.conversation)
                    except (KeyError, ValueError):
                        pass
        return {"run_id": run_id, "stopped": not already, "status": run.status, "reason": reason}

    def stop_conversation(self, conversation: str, reason: str = "stopped by the user") -> dict:
        """⏹ in the header / ``esc``: stop the reply and every run beneath it."""
        state = self.turn_state(conversation)
        if state is None:
            return {"conversation": conversation, "stopped": False, "reason": "no turn in flight"}
        state.cancel_reason = reason
        state.cancel.set()
        runs = [self.stop_run(run_id, reason) for run_id in sorted(state.runs)]
        engine: PermissionEngine = self.registry.permissions
        permissions = engine.cancel_pending(conversation, reason)
        plans = []
        for plan in list(self.plans.values()):
            if plan.conversation != conversation:
                continue
            if plan.status == "proposed":
                plan.status = "cancelled"
                plan.decided = {"decision": "cancelled", "reason": reason, "when": _now()}
                plan.decision.set()
                plans.append(plan.plan_id)
            elif plan.status == "halted":
                plan.status = "cancelled"
                plan.resume_action = "stop"
                plan.resume.set()
                plans.append(plan.plan_id)
        # A plan mid-execution is settled too — ⏹ starts nothing new, and the
        # unexecuted steps are reported skipped rather than left running.
        plans.extend(self.close_plans(conversation, reason=reason, status="stopped"))
        return {
            "conversation": conversation,
            "stopped": True,
            "reason": reason,
            "runs": runs,
            "permissions": permissions,
            "plans": plans,
        }

    # -- plans ---------------------------------------------------------------------

    def active_plan(self, conversation: str) -> Plan | None:
        with self._lock:
            for plan in self.plans.values():
                if plan.conversation == conversation and plan.status in ("running", "halted"):
                    return plan
        return None

    def _claim_step(self, conversation: str, name: str) -> tuple[Plan | None, dict | None]:
        if name in (PLAN_TOOL,):
            return None, None
        plan = self.active_plan(conversation)
        if plan is None:
            return None, None
        with plan.lock:
            if plan.status != "running":
                return plan, None
            step = plan.next_pending()
            if step is None:
                return plan, None
            step["status"] = "claimed"
            return plan, step

    def _plan_step(
        self, plan: Plan, step: dict, status: str, *, tool: str | None = None, seconds: float | None = None
    ) -> None:
        with plan.lock:
            step["status"] = status
            if tool is not None:
                step["tool"] = tool
            if seconds is not None:
                step["seconds"] = round(seconds, 3)
        payload = {"plan_id": plan.plan_id, "index": step["index"], "status": status, "tool": tool}
        if seconds is not None:
            payload["seconds"] = round(seconds, 3)
        self._emit(plan.conversation, "plan_step", payload, transcript=True)

    def _settle(self, plan: Plan, conversation: str) -> None:
        with plan.lock:
            if plan.status == "running" and plan.all_settled():
                plan.status = "done"
                done = True
            else:
                done = False
        if done:
            self._emit(conversation, "plan_done", {"plan_id": plan.plan_id, "steps": [dict(s) for s in plan.steps]},
                       transcript=True)

    def close_plans(self, conversation: str, *, reason: str, status: str = "stopped") -> list[str]:
        """Settle every plan of ``conversation`` still ``running`` — the turn
        ended (or was stopped) with steps the foreman never executed.

        Without this a plan stays ``running`` for the life of the service:
        ``_settle`` only closes a plan when a step COMPLETES, and nothing
        settles one the model simply stopped executing — after which
        ``propose_plan`` refuses every future plan in that conversation
        ("a plan is already running"). Unexecuted steps land as ``skipped``
        so A5's card settles honestly (doctrine 4: never hidden)."""
        closed: list[str] = []
        for plan in list(self.plans.values()):
            if plan.conversation != conversation:
                continue
            with plan.lock:
                if plan.status != "running":
                    continue
                unfinished = [s for s in plan.steps if s["status"] not in ("done", "skipped", "failed")]
                for step in unfinished:
                    step["status"] = "skipped"
                plan.status = status
                steps = [dict(s) for s in plan.steps]
            for step in unfinished:
                self._emit(
                    conversation, "plan_step",
                    {"plan_id": plan.plan_id, "index": step["index"], "status": "skipped", "tool": step.get("tool")},
                    transcript=True,
                )
            self._emit(
                conversation, "plan_done",
                {"plan_id": plan.plan_id, "status": status, "reason": reason, "steps": steps},
                transcript=True,
            )
            closed.append(plan.plan_id)
        return closed

    def _wait(self, event: threading.Event, conversation: str) -> bool:
        """Block on ``event`` while re-checking the turn's cancel flag; ``True``
        when the event fired, ``False`` when the turn was stopped first."""
        while not event.wait(_WAIT_TICK):
            state = self.turn_state(conversation)
            if state is None or state.cancel.is_set():
                return False
        return True

    def _halt(
        self, plan: Plan, step: dict, exc: Exception, conversation: str, *, tool: str, seconds: float
    ) -> Exception:
        """A step failed: halt the plan, wait for the user's way out, and
        return the exception the failed call surfaces to the foreman."""
        error = f"{type(exc).__name__}: {exc}"
        self._plan_step(plan, step, "failed", tool=tool, seconds=seconds)
        with plan.lock:
            plan.status = "halted"
            plan.halted = {"index": step["index"], "error": error, "options": list(HALT_OPTIONS), "when": _now()}
            plan.resume.clear()
            plan.resume_action = None
        self._emit(conversation, "plan_halted", {"plan_id": plan.plan_id, **plan.halted}, transcript=True)
        fired = self._wait(plan.resume, conversation)
        action = plan.resume_action if fired else "stop"
        with plan.lock:
            if not fired:
                plan.status = "cancelled"
        index = step["index"]
        prefix = f"plan {plan.plan_id} step {index} failed: {error}. "
        outcomes = {
            "continue": f"The user chose CONTINUE — retry step {index} now (it is pending again).",
            "skip": f"The user chose SKIP — step {index} is skipped; continue with the next step.",
            "undo": "The user chose UNDO — every completed step was reverted; do nothing further and report.",
            "stop": "The user chose STOP — do nothing further; report what completed and what did not.",
        }
        return ToolRefused(prefix + outcomes.get(action or "stop", outcomes["stop"]))

    def propose_plan(self, conversation: str, steps: Any) -> dict:
        """The ``propose_plan`` tool body: emit, block for the decision, answer."""
        state = self.turn_state(conversation)
        if state is None:
            raise LookupError(f"no turn in flight for conversation {conversation}")
        if self.active_plan(conversation) is not None:
            raise ValueError("a plan is already running in this conversation — finish or stop it first")
        plan = Plan(
            plan_id=PLAN_PREFIX + secrets.token_hex(4), conversation=conversation, steps=_normalize_steps(steps)
        )
        with self._lock:
            self.plans[plan.plan_id] = plan
        self._emit(conversation, "plan_proposed", {"plan_id": plan.plan_id, "steps": [dict(s) for s in plan.steps]},
                   transcript=True)
        if not self._wait(plan.decision, conversation):
            with plan.lock:
                plan.status = "cancelled"
            return {"plan_id": plan.plan_id, "decision": "cancelled", "reason": state.cancel_reason or "stopped"}
        decided = plan.decided or {}
        if plan.status == "rejected":
            return {
                "plan_id": plan.plan_id,
                "decision": "rejected",
                "reason": decided.get("reason"),
                "instructions": "The user rejected this plan. Revise it from their reason and propose again, or stop.",
            }
        if plan.status == "cancelled":
            return {"plan_id": plan.plan_id, "decision": "cancelled", "reason": decided.get("reason")}
        with plan.lock:
            plan.status = "running"
        return {
            "plan_id": plan.plan_id,
            "decision": "approved",
            "edited": decided.get("decision") == "edit",
            "steps": [dict(s) for s in plan.steps],
            "instructions": (
                f"Approved. Execute the {len(plan.steps)} steps now, in order, ONE tool call per step (a delegate call "
                "counts as one step) and nothing else in between. Every write is batched under this plan_id; paid "
                "steps still confirm when reached. A failing step halts the plan — the tool result then tells you "
                "the user's decision (continue / skip / undo / stop)."
            ),
        }

    def decide_plan(
        self, conversation: str, plan_id: str, decision: str, *, steps: Any = None, reason: str | None = None
    ) -> dict:
        """``POST /conversations/{id}/plans/{plan_id}`` — approve | reject | edit (with steps)."""
        plan = self.plans.get(plan_id)
        if plan is None or plan.conversation != conversation:
            raise KeyError(f"no plan {plan_id!r} in conversation {conversation}")
        if decision not in PLAN_DECISIONS:
            raise ValueError(f"decision must be one of {list(PLAN_DECISIONS)} (got {decision!r})")
        with plan.lock:
            if plan.status != "proposed":
                raise ValueError(f"plan {plan_id} is {plan.status}, not proposed")
            if decision == "edit":
                if steps is None:
                    raise ValueError("edit needs the edited steps")
                plan.steps = _normalize_steps(steps)
                plan.status = "approved"
            elif decision == "approve":
                plan.status = "approved"
            else:
                plan.status = "rejected"
            plan.decided = {
                "decision": decision, "reason": reason, "when": _now(), "steps": [dict(s) for s in plan.steps],
            }
        record = {"plan_id": plan_id, **plan.decided}
        self._emit(conversation, "plan_decision", record, transcript=True)
        plan.decision.set()
        return record

    def resume_plan(self, conversation: str, plan_id: str, action: str) -> dict:
        """The halted card's way out: continue | skip | stop (undo is ``undo_plan``)."""
        plan = self.plans.get(plan_id)
        if plan is None or plan.conversation != conversation:
            raise KeyError(f"no plan {plan_id!r} in conversation {conversation}")
        if action not in ("continue", "skip", "stop"):
            raise ValueError("action must be continue | skip | stop (undo is POST …/undo)")
        with plan.lock:
            if plan.status != "halted" or plan.halted is None:
                raise ValueError(f"plan {plan_id} is {plan.status}, not halted")
            index = plan.halted["index"]
            step = plan.steps[index - 1]
            if action == "continue":
                step["status"] = "pending"
                plan.status = "running"
            elif action == "skip":
                step["status"] = "skipped"
                plan.status = "running"
            else:
                plan.status = "stopped"
            plan.resume_action = action
            plan.halted = None
        if action == "skip":
            skipped = {"plan_id": plan_id, "index": index, "status": "skipped"}
            self._emit(conversation, "plan_step", skipped, transcript=True)
        record = {"plan_id": plan_id, "action": action, "index": index, "status": plan.status, "when": _now()}
        self._emit(conversation, "plan_resumed", record, transcript=True)
        plan.resume.set()
        if action == "skip":
            self._settle(plan, conversation)
        return record

    def plan_events(self, plan_id: str) -> list[dict]:
        """The journal events written under this plan's batch, in order."""
        return [e for e in provenance.all_events(self.pack) if e.get("batchId") == plan_id]

    def undo_plan(self, conversation: str, plan_id: str, *, actor: str | None = None) -> dict:
        """Restore every write of the plan to its ``before_hash``, reverse
        order, whoever wrote it (§5.5 rollback), via the ``restore`` tool
        under one ``undo:<plan_id>`` batch. Creates (no before) are kept —
        nothing is deleted. A halted plan resumes with ``undo``."""
        plan = self.plans.get(plan_id)
        if plan is None or plan.conversation != conversation:
            raise KeyError(f"no plan {plan_id!r} in conversation {conversation}")
        if plan.status == "proposed":
            raise ValueError("nothing to undo — the plan has not run")
        restore = self.registry.get("restore")
        who = actor or user_actor()
        restored: list[dict] = []
        skipped: list[dict] = []
        # The state to return to is each artifact's FIRST write in the plan —
        # a later write's before_hash is only the previous write's after_hash,
        # so an artifact the plan touched twice would come back half-undone.
        # Forward pass for the pre-plan hash, then restore in reverse order of
        # each artifact's LAST write (§5.5: reverse-order for plans).
        first_write: dict[str, dict] = {}
        for event in self.plan_events(plan_id):
            first_write.setdefault(str(event.get("artifact_id")), event)
        seen: set[str] = set()
        for event in reversed(self.plan_events(plan_id)):
            artifact = str(event.get("artifact_id"))
            if artifact in seen:
                continue
            seen.add(artifact)
            earliest = first_write[artifact]
            before = earliest.get("before_hash")
            if not before:
                skipped.append(
                    {"id": artifact, "reason": "created by the plan — nothing to restore to; nothing is deleted"}
                )
                continue
            if before == event.get("after_hash"):
                continue  # the plan's net effect on this artifact is nothing
            try:
                with self.gate.hold(artifact), bind_call(who, conversation), provenance.bind_batch(f"undo:{plan_id}"):
                    restore.run({"target": artifact, "version_hash": before})
                restored.append({"id": artifact, "to": before})
            except Exception as exc:  # noqa: BLE001 — report every miss; keep going
                skipped.append({"id": artifact, "reason": f"{type(exc).__name__}: {exc}"})
        with plan.lock:
            plan.undone = restored
            was_halted = plan.status == "halted"
            plan.status = "undone"
            plan.halted = None
            plan.resume_action = "undo"
        record = {"plan_id": plan_id, "restored": restored, "skipped": skipped, "when": _now()}
        self._emit(conversation, "plan_undone", record, transcript=True)
        if was_halted:
            plan.resume.set()
        return record

    # -- tool registration --------------------------------------------------------

    def register_tools(self, registry: ToolRegistry | None = None) -> list[str]:
        """Register ``delegate`` and ``propose_plan`` (tier ``auto`` — the
        approval they carry is the plan card / the delegated run's own
        chips; neither writes). Foreman-only: a call from a specialist
        actor is refused, and specialists' subsets never list them."""
        registry = registry if registry is not None else self.registry
        menu = delegate_menu(self.roster, [s.id for s in self.skills.routable()])

        def foreman_only(name: str) -> str:
            call = current_call()
            ref = parse_actor(call.actor)
            if ref.kind == "agent" and ref.specialist not in (None, FOREMAN):
                raise ToolRefused(f"{name} is the foreman's tool — a specialist never delegates or plans")
            return call.conversation

        def delegate(tool_input: dict) -> str:
            conversation = foreman_only(DELEGATE_TOOL)
            result = self.delegate(
                conversation=conversation,
                specialist=str(tool_input["specialist"]),
                task=str(tool_input["task"]),
                refs=list(tool_input.get("refs") or []),
                budget=tool_input.get("budget"),
            )
            return json.dumps(result, separators=(",", ":"), ensure_ascii=False, default=str)

        def propose(tool_input: dict) -> str:
            conversation = foreman_only(PLAN_TOOL)
            result = self.propose_plan(conversation, tool_input.get("steps"))
            return json.dumps(result, separators=(",", ":"), ensure_ascii=False, default=str)

        registry.register(
            Tool(
                spec=delegate_spec(menu),
                tier="auto",
                run=delegate,
                touches="starts a specialist run (its own tool calls ask through their own chips)",
            )
        )
        registry.register(
            Tool(
                spec=ToolSpec(
                    name=PLAN_TOOL,
                    description=(
                        "Propose a numbered plan for a multi-step request and wait for the user's decision (approve / "
                        "reject / edit). Each step is ONE tool call or delegation and names its tier (auto | ask | "
                        "paid) "
                        "and the specialist that acts. Once approved, execute the steps in order, one tool call each."
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "steps": {
                                "type": "array",
                                "minItems": 1,
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "text": {"type": "string"},
                                        "tier": {"type": "string", "description": "auto | ask | paid"},
                                        "specialist": {"type": "string"},
                                        "estimate": {
                                            "type": "object",
                                            "description": "Optional {low, high} USD for paid steps.",
                                        },
                                    },
                                    "required": ["text", "tier"],
                                    "additionalProperties": False,
                                },
                            }
                        },
                        "required": ["steps"],
                        "additionalProperties": False,
                    },
                ),
                tier="auto",
                run=propose,
                touches="renders the plan card; writes nothing",
            )
        )
        return [DELEGATE_TOOL, PLAN_TOOL]


__all__ = [
    "DELEGATE_TOOL",
    "HALT_OPTIONS",
    "PARALLEL_CAP",
    "PLAN_DECISIONS",
    "PLAN_STATUSES",
    "PLAN_TOOL",
    "RUN_FAILURE_LIMIT",
    "RUN_STATUSES",
    "VERIFY_LEVEL_TOOL",
    "VERIFY_STATUSES",
    "VLM_VERIFY_PROMPT",
    "VLM_VERIFY_TICKS",
    "Plan",
    "Run",
    "RunManager",
    "TurnState",
    "WriteGate",
    "delegate_menu",
    "delegate_spec",
    "write_target",
]
