"""The permission engine — row A2 shipped the shell, row A4 fills it (master §3.1 stage 4).

``PermissionEngine.check`` still answers ``ToolRegistry.execute``'s one
question — may this tool run now, for this actor, in this conversation? —
with the signature row A2 fixed (``tool, input, *, actor, conversation``;
``mode`` is an optional extra the service never needs to pass). What
changed is the answer for the ask and paid tiers: instead of a refusal
naming this row, ``check`` classifies (``classify``), and when the
classification is *ask* it opens a permission request, hands it to the
conversation's listener (the service's SSE + transcript), and BLOCKS the
worker thread until ``decide`` lands the user's answer — or the optional
timeout passes. The registry never learns which row is answering.

Tiers and modes are data (plain strings — Phase 1 §4, master doctrine 8):

- ``auto`` — allow. Reads never ask (D5).
- ``ask`` — allow when the project holds a grant for the TOOL NAME (grants
  govern actions, never agents — Phase 1 §5.4); otherwise ask.
- ``paid`` — always ask, in every mode, and "always" is refused ("paid is
  never Always-allowable"). The estimate + $-confirm card are row A6's;
  this row gates, it does not price.
- mode ``ask`` — ask; "Always allow in this project" is disabled with a
  reason (agent-panel README §6: grants are made in Allow mode).
- mode ``allow`` — ask unless granted; "always" writes a grant then runs.
  Allow mode is NOT "everything is allowed".
- mode ``plan`` — asks like Ask mode for any write outside an approved
  plan (row A4.5's ``propose_plan`` is the batch approval; paid steps
  still ask when reached); "always" is disabled with a reason — grants are
  made in Allow mode.
- any other tier or mode — refuse, fail closed.
- ``forbid_always(tool, reason)`` (row A4.5, master §3.0-F): a tool the
  recipe family registers is never Always-allow-eligible in ANY mode —
  bound/gate widening confirms per instance, like paid.

Decisions (``decide(request_id, decision, reason?)``): ``accept`` runs the
tool once; ``always`` writes a project grant (only when the request says
``always_allowed``; otherwise ``AlwaysNotAllowed`` carries the disabled
reason and the request stays pending) then runs; ``reject`` makes the tool
fail with ``rejected by the user: <reason>`` — an ``is_error`` tool result,
and the turn continues.

Grants live at ``<pack>/.canon/agent/permissions.json`` (master S17) —
``GrantStore``: schema ``cradle-agent-permissions/v1``, one atomic write
per change, read from disk on every lookup so a revoke lands at once.
The store is service-owned for WRITES, which is the claim Phase 1 §7.2
makes: no tool can write it — the registry registers nothing over it and
every write verb resolves to an artifact path, so a traversal in a verb's
``type`` / ``id`` / ``target`` fails before it reaches the file. It is
readable like any other pack file outside the object store:
``read_pack_file`` will show it (the grants are the user's own record —
``guard_path`` hides only ``.canon/objects``).

``cancel_pending(conversation, reason)`` (row A4.5) is what ⏹ Stop calls:
every open request of the conversation is decided ``cancelled`` with the
reason, so a turn blocked on a chip wakes and ends — the same
``permission_decision`` record, ``by: "service"``, and the tool is told
the turn was STOPPED, never that the user rejected it.

Deliberately absent, by row ownership: chip rendering (A5), the Settings
→ Permissions pane (A6 — the revoke endpoints it needs are in the
service), paid estimates (A6), the recipe tools themselves (W2.2 — only
the never-always edge exists here).
"""

from __future__ import annotations

import json
import os
import secrets
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from canon.agent.actors import FOREMAN, current_call, parse_actor

if TYPE_CHECKING:
    from canon.agent.registry import Tool

#: The tiers the engine gates — data, in the order the panel's chip copy lists them.
TIERS: tuple[str, ...] = ("auto", "ask", "paid")

#: The header's three modes (README §9) — data.
MODES: tuple[str, ...] = ("ask", "plan", "allow")

#: The chip's three buttons, as the API spells them.
DECISIONS: tuple[str, ...] = ("accept", "always", "reject")

#: ``Decision.outcome`` values.
OUTCOMES: tuple[str, ...] = ("allow", "ask", "refuse")

#: The grants file, relative to the pack root (master S17).
GRANTS_FILE = Path(".canon") / "agent" / "permissions.json"

#: The grants document's schema id.
GRANTS_SCHEMA = "cradle-agent-permissions/v1"

#: The one scope this row knows — grants are per project.
GRANT_SCOPE = "project"

#: The row that brought plan mode (kept as data for the older copy).
PLAN_ROW = "A4.5"

#: Why the middle button is disabled, per case (README §6: disabled-with-a-reason, never hidden).
PAID_NEVER_ALWAYS = "paid is never Always-allowable — a paid action confirms every time, in every mode"
ASK_MODE_NO_GRANTS = (
    "grants are made in Allow mode — switch the header to Allow to enable “Always allow in this project”"
)
PLAN_MODE_NO_GRANTS = (
    f"plan mode (row {PLAN_ROW}) approves a batch once (propose_plan); a write outside an approved plan asks "
    "like Ask mode, and grants are made in Allow mode"
)

#: The decision record ``cancel_pending`` lands (``by: "service"``).
CANCELLED_DECISION = "cancelled"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _current_run_id() -> str | None:
    """The delegated run the bound call belongs to, when a turn bound one
    (row A4's ``bind_call``) — ``None`` outside a turn or for the foreman."""
    try:
        return current_call().run_id
    except LookupError:
        return None


# ---------------------------------------------------------------------------
# Decision + request shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    """The engine's answer.

    Attributes:
        outcome: ``"allow"`` | ``"ask"`` | ``"refuse"`` (``OUTCOMES``).
            ``check`` never returns ``"ask"`` — it resolves it; ``classify``
            does.
        reason: Human-readable, surfaced verbatim as the tool result when
            refused and kept for the transcript when allowed.
        grant: The project grant that allowed the call, when one did.
    """

    outcome: str
    reason: str
    grant: dict | None = None

    @property
    def allowed(self) -> bool:
        """What ``ToolRegistry.execute`` reads — unchanged from row A2."""
        return self.outcome == "allow"


@dataclass
class PermissionRequest:
    """One open chip — the ``permission_request`` SSE event / transcript line.

    Attributes:
        request_id: ``perm_`` + 8 hex; what ``decide`` is keyed on.
        conversation: The conversation the turn belongs to.
        tool: The tool name (what a grant would cover).
        input: The tool input, verbatim.
        tier: The tool's tier (``ask`` | ``paid``).
        actor: ``agent:<conversation>/<specialist>``.
        specialist: The specialist parsed from ``actor`` (chip copy:
            "‹Specialist› wants to ‹verb› ‹target›").
        target: The human-readable "‹verb› ‹target›" for the chip.
        touches: The tool's ``touches`` line.
        mode: The mode the turn runs in.
        always_allowed: Whether "Always allow in this project" is enabled.
        always_reason: Why it is disabled, when it is (``None`` when enabled).
        pack: The project the grant would cover (the footnote names it).
        run_id: The delegated run the call belongs to, when one is bound
            (``None`` for the foreman's own calls). ⏹ on ONE run card
            keys on this: two parallel runs of the same specialist share
            an actor string, so an actor match would reject both their
            chips.
        created: When the request opened.
    """

    request_id: str
    conversation: str
    tool: str
    input: dict
    tier: str
    actor: str
    specialist: str
    target: str
    touches: str
    mode: str
    always_allowed: bool
    always_reason: str | None
    pack: str | None
    run_id: str | None = None
    #: Row P1-A6: the pre-spend estimate for a $-tier call —
    #: ``{low, high, backend, model, unitCount, unitLabel?}`` in USD, from
    #: canon's own estimator. ``None`` for every free tier, and for a paid
    #: call the estimator could not price (the chip still opens and says the
    #: price is unknown — doctrine 3: no estimate is NOT $0).
    estimate: dict | None = None
    created: str = field(default_factory=_now)

    def payload(self) -> dict:
        """The JSON the SSE event and the transcript line carry.

        Row P1-A6 adds ``estimate`` and, beside it, the ``paid`` view row A5's
        card already reads (``{state: "estimate", low, high, backend, model,
        unitCount, …}``) — one payload, so the chip renders
        ``Accept · spend up to $X`` with no client-side arithmetic.
        """
        out = asdict(self)
        if self.estimate:
            out["paid"] = {"state": "estimate", **self.estimate, "requestId": self.request_id}
        return out


@dataclass
class Listener:
    """Where a conversation's requests and decisions go (the service binds
    one per turn: SSE + transcript)."""

    on_request: Callable[[PermissionRequest], None]
    on_decision: Callable[[PermissionRequest, dict], None]


class AlwaysNotAllowed(PermissionError):
    """``decide(…, "always")`` on a request whose middle button is disabled;
    ``str(exc)`` is the reason the chip shows. The request stays pending."""


@dataclass
class _Pending:
    request: PermissionRequest
    event: threading.Event = field(default_factory=threading.Event)
    answer: dict | None = None


# ---------------------------------------------------------------------------
# The grants store
# ---------------------------------------------------------------------------


class GrantStore:
    """``<pack>/.canon/agent/permissions.json`` — read on every lookup,
    written atomically (tmp + ``os.replace``) under a lock.

    Document::

        {"schema": "cradle-agent-permissions/v1",
         "grants": [{"tool": str, "granted_by": <the agent actor — ``agent_actor(conversation, specialist)``>,
                     "when": iso, "scope": "project"}]}
    """

    def __init__(self, pack_dir: str | Path) -> None:
        self.pack_dir = Path(pack_dir)
        self.path = self.pack_dir / GRANTS_FILE
        self._lock = threading.Lock()

    def read(self) -> dict:
        """The document (an empty one when the file is absent). A file with
        another schema id is a ``ValueError`` — never silently reinterpreted."""
        if not self.path.is_file():
            return {"schema": GRANTS_SCHEMA, "grants": []}
        document = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or document.get("schema") != GRANTS_SCHEMA:
            raise ValueError(f"{self.path}: expected schema {GRANTS_SCHEMA!r}, got {document.get('schema')!r}")
        grants = document.get("grants")
        if not isinstance(grants, list):
            raise ValueError(f"{self.path}: 'grants' must be a list")
        return {"schema": GRANTS_SCHEMA, "grants": [dict(g) for g in grants]}

    def grants(self) -> list[dict]:
        return self.read()["grants"]

    def find(self, tool: str) -> dict | None:
        """The first project grant covering ``tool`` (by name — never by actor)."""
        for grant in self.grants():
            if grant.get("tool") == tool and grant.get("scope", GRANT_SCOPE) == GRANT_SCOPE:
                return grant
        return None

    def add(self, tool: str, granted_by: str) -> dict:
        """Append a grant (idempotent per tool: an existing grant is returned untouched)."""
        with self._lock:
            document = self.read()
            for grant in document["grants"]:
                if grant.get("tool") == tool and grant.get("scope", GRANT_SCOPE) == GRANT_SCOPE:
                    return grant
            grant = {"tool": tool, "granted_by": granted_by, "when": _now(), "scope": GRANT_SCOPE}
            document["grants"].append(grant)
            self._write(document)
            return grant

    def revoke(self, index: int) -> dict:
        """Remove the grant at ``index``; ``IndexError`` when there is none.
        Revoking undoes nothing already done (README §6)."""
        with self._lock:
            document = self.read()
            if not 0 <= index < len(document["grants"]):
                raise IndexError(f"no grant at index {index} (the project holds {len(document['grants'])})")
            removed = document["grants"].pop(index)
            self._write(document)
            return removed

    def revoke_all(self) -> int:
        """Remove every grant; returns how many were removed."""
        with self._lock:
            document = self.read()
            count = len(document["grants"])
            if count:
                self._write({"schema": GRANTS_SCHEMA, "grants": []})
            return count

    def _write(self, document: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


def always_allowance(tier: str, mode: str, forbidden: str | None = None) -> tuple[bool, str | None]:
    """Whether "Always allow in this project" is enabled for a ``tier`` tool
    in ``mode``, and the reason when it is not (README §6's four states).
    ``forbidden`` is a tool-level reason (``forbid_always``) that wins over
    every mode — the recipe family's never-always edge."""
    if forbidden:
        return False, forbidden
    if tier == "paid":
        return False, PAID_NEVER_ALWAYS
    if mode == "allow":
        return True, None
    if mode == "plan":
        return False, PLAN_MODE_NO_GRANTS
    return False, ASK_MODE_NO_GRANTS


class PermissionEngine:
    """Tier × mode × grants gating, plus the blocking ask round-trip.

    Args:
        pack_dir: The project whose grants file this engine reads and
            writes; ``None`` until ``attach`` (a bare engine — unit tests,
            the eval runner — has no grants and asks every time).
        timeout: Seconds a pending request waits for ``decide`` before it
            is rejected with "no decision"; ``None`` waits until A4.5's
            cancel (or the process) ends it. The sidecar's
            ``--permission-timeout`` is minutes over this.
        default_mode: The mode a conversation runs in until the service
            sets one per turn (data; ``"ask"`` is the safe default).
    """

    def __init__(
        self,
        pack_dir: str | Path | None = None,
        *,
        timeout: float | None = None,
        default_mode: str = "ask",
    ) -> None:
        self.grants: GrantStore | None = GrantStore(pack_dir) if pack_dir is not None else None
        self.timeout = timeout
        self.default_mode = default_mode
        self._modes: dict[str, str] = {}
        self._listeners: dict[str, Listener] = {}
        self._pending: dict[str, _Pending] = {}
        self._targets: dict[str, Callable[[dict], str]] = {}
        self._never_always: dict[str, str] = {}
        self._estimators: dict[str, Callable[[dict], dict | None]] = {}
        self._tiers: dict[str, Callable[[dict], str]] = {}
        self._lock = threading.Lock()

    # -- configuration -------------------------------------------------------

    def attach(self, pack_dir: str | Path) -> None:
        """Bind the grants store when the engine was built without a pack
        (the service does this so a test registry still persists grants)."""
        if self.grants is None:
            self.grants = GrantStore(pack_dir)

    def set_mode(self, conversation: str, mode: str) -> None:
        """The mode ``conversation``'s next checks run in (set per turn)."""
        self._modes[conversation] = mode

    def mode_for(self, conversation: str) -> str:
        return self._modes.get(conversation, self.default_mode)

    def describe(self, tool: str, describer: Callable[[dict], str]) -> None:
        """Register the "‹verb› ‹target›" describer for ``tool`` (the write
        tools register theirs; anything else falls back to ``run <tool>``)."""
        self._targets[tool] = describer

    def forbid_always(self, tool: str, reason: str) -> None:
        """Mark ``tool`` never Always-allow-eligible, in every mode, with
        the reason the chip shows (row A4.5, master §3.0-F: the recipe
        family's bound/gate widening confirms per instance). A standing
        grant for such a tool is ignored — the check asks anyway."""
        self._never_always[tool] = reason

    def never_always_reason(self, tool: str) -> str | None:
        return self._never_always.get(tool)

    def estimate_with(self, tool: str, estimator: Callable[[dict], dict | None]) -> None:
        """Register ``tool``'s pre-spend estimator (row P1-A6, mirroring
        :meth:`describe`): ``(input) -> {low, high, backend, model, unitCount}``
        in USD, or ``None`` when this input cannot be priced. It runs while the
        request is being BUILT — before the tool body — so the chip can say
        ``Accept · spend up to $X``. An estimator that raises is swallowed: a
        broken forecast must never stop the gate from opening."""
        self._estimators[tool] = estimator

    def estimate_for(self, tool: Tool, tool_input: dict) -> dict | None:
        """``tool``'s estimate for this input, or ``None`` (no estimator, no
        price, or the estimator failed). Never raises."""
        estimator = self._estimators.get(tool.spec.name)
        if estimator is None:
            return None
        try:
            estimate = estimator(tool_input)
        except Exception:  # noqa: BLE001 — an estimate never blocks the gate
            return None
        return estimate if isinstance(estimate, dict) and estimate else None

    def tier_with(self, tool: str, resolver: Callable[[dict], str]) -> None:
        """Register a per-INPUT tier for ``tool`` (row P1-A6; doctrine 3's
        "free never spend-confirms" / master §8 A-5). Tiers are data, and one
        tool can be $-tier or ask-tier depending on the backends the call
        selects: ``generate_asset`` on ``fake`` spends nothing, and a $0
        all-fake ``create_project`` is ask-tier, not paid. The resolver returns
        a tier string; anything it does not recognise falls back to the tool's
        registered ``tier``, and a raising resolver keeps the strict one."""
        self._tiers[tool] = resolver

    def effective_tier(self, tool: Tool, tool_input: dict) -> str:
        """The tier this CALL runs at: the resolver's answer when one is
        registered and returns a known tier, else ``tool.tier``. Fail-closed —
        a resolver that raises or answers nonsense keeps the strict tier."""
        resolver = self._tiers.get(tool.spec.name)
        if resolver is None:
            return tool.tier
        try:
            tier = resolver(tool_input)
        except Exception:  # noqa: BLE001 — fail closed to the registered tier
            return tool.tier
        return tier if isinstance(tier, str) and tier in TIERS else tool.tier

    def target_for(self, tool: Tool, tool_input: dict) -> str:
        describer = self._targets.get(tool.spec.name)
        if describer is not None:
            try:
                return describer(tool_input)
            except Exception:  # noqa: BLE001 — a describer must never break the gate
                pass
        return f"run {tool.spec.name}"

    @contextmanager
    def listen(
        self,
        conversation: str,
        *,
        on_request: Callable[[PermissionRequest], None],
        on_decision: Callable[[PermissionRequest, dict], None],
    ) -> Iterator[None]:
        """Route ``conversation``'s requests/decisions for the duration of a turn."""
        with self._lock:
            self._listeners[conversation] = Listener(on_request=on_request, on_decision=on_decision)
        try:
            yield
        finally:
            with self._lock:
                self._listeners.pop(conversation, None)

    # -- reads ---------------------------------------------------------------

    def pending(self, conversation: str | None = None) -> list[dict]:
        """Open requests (for one conversation, or all), oldest first."""
        with self._lock:
            entries = list(self._pending.values())
        return [e.request.payload() for e in entries if conversation is None or e.request.conversation == conversation]

    # -- the gate ------------------------------------------------------------

    def classify(
        self, tool: Tool, tool_input: dict, *, actor: str, conversation: str, mode: str | None = None
    ) -> Decision:
        """The pure tier × mode × grants answer — ``allow`` | ``ask`` | ``refuse``
        — without opening a request."""
        tier = self.effective_tier(tool, tool_input)
        name = tool.spec.name
        mode = mode if mode is not None else self.mode_for(conversation)
        # Both validations come FIRST: an unknown mode refuses every tier,
        # reads included, so "any other tier or mode — refuse, fail closed"
        # holds literally instead of quietly allowing reads under a mode
        # nothing understands.
        if tier not in TIERS:
            return Decision(
                "refuse", f"unknown tier {tier!r} on {name}; known tiers: {list(TIERS)} (refused, fail closed)"
            )
        if mode not in MODES:
            return Decision(
                "refuse", f"unknown permission mode {mode!r}; known modes: {list(MODES)} (refused, fail closed)"
            )
        if tier == "auto":
            return Decision("allow", f"auto-tier: {name} reads only, reads never ask")
        if tier == "paid":
            return Decision("ask", f"paid-tier: {name} confirms every time, in every mode")
        if name in self._never_always:
            return Decision("ask", f"{name} confirms per instance: {self._never_always[name]}")
        grant = self.grants.find(name) if self.grants is not None else None
        if grant is not None:
            return Decision(
                "allow",
                f"{name} is always allowed in this project "
                f"(granted by {grant.get('granted_by')} on {grant.get('when')})",
                grant=grant,
            )
        return Decision("ask", f"ask-tier: {name} needs the user's permission in {mode} mode")

    def check(self, tool: Tool, input: dict, *, actor: str, conversation: str, mode: str | None = None) -> Decision:  # noqa: A002
        """Decide whether ``tool`` may run with ``input`` for ``actor`` in
        ``conversation`` — blocking on the user when the answer is *ask*.

        ``mode`` defaults to the conversation's mode (``set_mode``), which
        is how the registry's unchanged call reaches the right answer.
        """
        mode = mode if mode is not None else self.mode_for(conversation)
        decision = self.classify(tool, input, actor=actor, conversation=conversation, mode=mode)
        if decision.outcome != "ask":
            return decision
        name = tool.spec.name
        with self._lock:
            listener = self._listeners.get(conversation)
        if listener is None:
            return Decision(
                "refuse",
                f"no one to ask: {name} is {self.effective_tier(tool, input)}-tier and no permission listener "
                f"is bound for conversation {conversation} (the service binds one per turn)",
            )
        tier = self.effective_tier(tool, input)
        always_allowed, always_reason = always_allowance(tier, mode, self._never_always.get(name))
        request = PermissionRequest(
            request_id="perm_" + secrets.token_hex(4),
            conversation=conversation,
            tool=name,
            input=dict(input),
            tier=tier,
            actor=actor,
            specialist=parse_actor(actor).specialist or FOREMAN,
            target=self.target_for(tool, input),
            touches=tool.touches,
            mode=mode,
            always_allowed=always_allowed,
            always_reason=always_reason,
            pack=str(self.grants.pack_dir) if self.grants is not None else None,
            run_id=_current_run_id(),
            estimate=self.estimate_for(tool, input) if tier == "paid" else None,
        )
        entry = _Pending(request)
        with self._lock:
            self._pending[request.request_id] = entry
        try:
            listener.on_request(request)
        except BaseException:
            with self._lock:  # a listener that cannot show the chip leaves nothing pending behind
                self._pending.pop(request.request_id, None)
            raise

        entry.event.wait(self.timeout)
        if entry.answer is None:
            with self._lock:
                timed_out = self._pending.pop(request.request_id, None) is not None
            if timed_out:
                seconds = self.timeout if self.timeout is not None else 0.0
                record = self._record(request, "timeout", f"no decision within {seconds:g} s", None)
                listener.on_decision(request, record)
                return Decision("refuse", f"no decision within {seconds:g} s — {name} was not run")
            entry.event.wait()  # decided between the timeout and the pop; the answer is landing
        answer = entry.answer or {}
        decision_name = answer.get("decision")
        if decision_name == "accept":
            return Decision("allow", f"accepted by the user: {name} runs once")
        if decision_name == "always":
            return Decision("allow", f"always allowed in this project from now on: {name}", grant=answer.get("grant"))
        reason = answer.get("reason") or "no reason given"
        if decision_name == CANCELLED_DECISION:
            # ⏹ Stop woke this chip — nobody clicked Reject. The record says
            # ``by: "service"``; the tool result must say the same thing.
            return Decision("refuse", f"stopped before {name} ran: {reason}")
        return Decision("refuse", f"rejected by the user: {reason}")

    # -- the user's answer ---------------------------------------------------

    def decide(
        self,
        request_id: str,
        decision: str,
        reason: str | None = None,
        *,
        conversation: str | None = None,
    ) -> dict:
        """Land the user's answer on a pending request and wake the turn.

        Returns the ``permission_decision`` record. ``KeyError`` for an
        unknown (or already decided) request — or one that belongs to
        another ``conversation`` when given; ``ValueError`` for a decision
        outside ``DECISIONS``; ``AlwaysNotAllowed`` when "always" is
        disabled for this request (it stays pending).
        """
        if decision not in DECISIONS:
            raise ValueError(f"decision must be one of {list(DECISIONS)} (got {decision!r})")
        with self._lock:
            entry = self._pending.get(request_id)
            if entry is None or (conversation is not None and entry.request.conversation != conversation):
                raise KeyError(f"no pending permission request {request_id!r}")
            request = entry.request
            if decision == "always" and not request.always_allowed:
                raise AlwaysNotAllowed(request.always_reason or "Always allow is not available for this request")
            grant = None
            if decision == "always":
                if self.grants is None:
                    raise AlwaysNotAllowed("no project is open — grants are per project")
                grant = self.grants.add(request.tool, granted_by=request.actor)
            record = self._record(request, decision, reason, grant)
            entry.answer = record
            del self._pending[request_id]
            listener = self._listeners.get(request.conversation)
        # The transcript line lands BEFORE the turn resumes (the tool result follows it).
        if listener is not None:
            listener.on_decision(request, record)
        entry.event.set()
        return record

    def cancel_pending(self, conversation: str, reason: str = "stopped by the user") -> list[str]:
        """⏹ Stop (row A4.5): reject every open request of ``conversation``
        with ``reason`` and wake the blocked turn. Returns the request ids
        decided. The record says ``by: "service"`` — nobody clicked."""
        with self._lock:
            entries = [e for e in self._pending.values() if e.request.conversation == conversation]
        decided: list[str] = []
        for entry in entries:
            with self._lock:
                if self._pending.pop(entry.request.request_id, None) is None:
                    continue  # decided meanwhile
                record = self._record(entry.request, CANCELLED_DECISION, reason, None)
                entry.answer = record
                listener = self._listeners.get(conversation)
            if listener is not None:
                listener.on_decision(entry.request, record)
            entry.event.set()
            decided.append(entry.request.request_id)
        return decided

    @staticmethod
    def _record(request: PermissionRequest, decision: str, reason: str | None, grant: dict | None) -> dict:
        return {
            "request_id": request.request_id,
            "conversation": request.conversation,
            "tool": request.tool,
            "decision": decision,
            "reason": reason,
            "grant": grant,
            "by": "user" if decision in DECISIONS else "service",
            "when": _now(),
        }


__all__ = [
    "ASK_MODE_NO_GRANTS",
    "CANCELLED_DECISION",
    "DECISIONS",
    "GRANTS_FILE",
    "GRANTS_SCHEMA",
    "GRANT_SCOPE",
    "MODES",
    "OUTCOMES",
    "PAID_NEVER_ALWAYS",
    "PLAN_MODE_NO_GRANTS",
    "PLAN_ROW",
    "TIERS",
    "AlwaysNotAllowed",
    "Decision",
    "GrantStore",
    "Listener",
    "PermissionEngine",
    "PermissionRequest",
    "always_allowance",
]
