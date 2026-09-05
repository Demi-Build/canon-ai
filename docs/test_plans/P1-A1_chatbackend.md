# Human test plan — row P1-A1, ChatBackend + anthropic + streaming + fake + eval

**Row:** master §3.1 stage 1, `P1-A1 [B]` · **Gate:** scripted tool-use conversation green, $0 ·
**Ships no pricing data** (per-1M entries arrive with the §3.0-C module at P0-7).

All commands run from `~/Documents/projects/canon-ai`. Nothing in this row needs a key, a network,
or money except section E, which is optional and user-run.

---

## A. Integrity (what changed)

- [ ] **A1 — Exactly these files.** New: `src/canon/llm/chat.py`, `src/canon/backends/chat_anthropic.py`,
      `src/canon/agent/{__init__,loop,evals,eval}.py`, `tests/test_chat_backends.py`,
      `tests/test_agent_eval.py`. Extended: `src/canon/backends/{base,registry,testing,__init__}.py`,
      `src/canon/llm/__init__.py`. Nothing under `examples/` or `tests/fixtures/`:

```bash
git status --short src tests examples
```

- [ ] **A2 — Row files are ruff-clean** (the repo has 16 pre-existing findings in files this row
      did not touch; they are listed in the session summary, not fixed here):

```bash
uv run ruff check src/canon/llm/chat.py src/canon/backends/chat_anthropic.py src/canon/backends/base.py src/canon/backends/registry.py src/canon/backends/testing.py src/canon/backends/__init__.py src/canon/llm/__init__.py src/canon/agent/ tests/test_chat_backends.py tests/test_agent_eval.py
```

- [ ] **A3 — No pricing anywhere in the row.** A test enforces it; run it alone:

```bash
uv run python -m pytest tests/test_chat_backends.py -q -k "no_pricing or no_cost" -p no:cacheprovider
```

## B. The gate — scripted tool-use conversation green at $0

- [ ] **B1 — The eval corpus passes on the fake backend** (5 conversations, exit code 0, every
      cost note reads `$0 — fake backend, nothing measured`):

```bash
uv run python -m canon.agent.eval --backend fake
```

- [ ] **B2 — One conversation as JSON** (parseable; `usage` all zeros; `passed: 1`):

```bash
uv run python -m canon.agent.eval --only parallel-reads --json
```

- [ ] **B3 — Unknown backend id is a named usage error, exit 2**, listing the registered chat
      ids (empty until something calls `register()`) plus `fake`:

```bash
uv run python -m canon.agent.eval --backend nope
```

- [ ] **B4 — The row's tests** (183 tests: the two new files plus the existing backend/LLM
      tests they sit beside):

```bash
uv run python -m pytest tests/test_chat_backends.py tests/test_agent_eval.py tests/test_backends.py tests/test_anthropic_backend.py tests/test_llm_client.py -q -p no:cacheprovider
```

- [ ] **B5 — Suites green (platformer byte-identical).** Canon full suite: 2517 passed, 4
      skipped, 1 deselected on 2026-09-01 after the row landed (baseline before the row: 2401 passed;
      the difference is the 116 new tests in the two new files). Cradle: 201 passed. Rerun if you want your own numbers
      (~20 min for canon):

```bash
uv run python -m pytest tests/ -q -p no:cacheprovider --deselect tests/test_backend_lyria.py::TestLyriaMusicBackendRegister::test_register_adds_lyria_to_registry
```

```bash
cd ~/Documents/projects/cradle && npx vitest run --reporter=dot
```

## C. Read the contract (10 minutes)

- [ ] **`src/canon/backends/base.py` — `ChatBackend`.** Sits beside `LLMBackend`, not inside it.
      The docstring states the four things later rows build on: the event-order contract
      (one `MessageStart` → per-block deltas + `ContentBlockDone` → one `MessageStop`), the
      cancel contract (`gen.close()` releases the provider connection — A4.5's ⏹ Stop), the
      provider-neutral stop-reason vocabulary (`tool_use` · `end_turn` · `max_tokens` ·
      `refusal`, others pass through), and "usage is measured tokens, not money".
- [ ] **`src/canon/backends/registry.py`.** `register_chat` / `chat` / `chat_ids` follow the
      existing `register_llm` / `llm` idiom exactly; `reset()` clears the chat namespace too.
- [ ] **`src/canon/llm/chat.py`.** Content blocks are plain dicts (JSON-serializable for A2's
      transcripts); thinking blocks pass through for replay; `Usage` has four token fields and
      no cost; `tool_result_message` returns ONE user message for all of a turn's results.
- [ ] **`src/canon/backends/chat_anthropic.py`.** Mirrors `anthropic.py`: explicit `register()`,
      never at import; `client` injection; `DEFAULT_CHAT_MODEL = "claude-sonnet-5"` (a data value —
      the code default; the effective default resolves project settings → cradle settings → this
      constant at P0-12/A5). Refusal fallbacks are ON by default (`fallbacks=True`): the scalar
      `fallbacks: "default"` form rides `extra_body` with the `anthropic-beta:
      server-side-fallback-2026-07-01` header in `extra_headers`, because SDK 0.98.x has no typed
      `fallbacks`/`betas` on `messages.stream`; `fallbacks=False` sends neither. An unknown
      `fallback` content block passes through as a dict with its `type`. `thinking` is omitted when
      off (never an explicit `disabled`); forced `tool_choice` raises `ValueError`;
      `request.metadata` is never forwarded; SDK errors become `ChatError(retryable=…)`; a missing
      credential is a named error, not a traceback.
- [ ] **`src/canon/backends/testing.py` — `FakeChatBackend`.** List mode and callable mode like
      `FakeLLMBackend`; streams the real event order (text in ≥2 deltas); `Usage()` zeros; a
      dict turn scripts `refusal`/`max_tokens`.
- [ ] **`src/canon/agent/loop.py`.** The single-actor loop A2/A4/A4.5 extend (its docstring
      names the hook points); tool results for one turn go back in one user message; the
      `max_tool_rounds` guard ends the turn with a resendable history.
- [ ] **`src/canon/agent/evals.py`.** Five scripted conversations named after the PRD traces
      (unbeatable-level, parallel-reads, just-talking, tool-error-recovers, refusal-surfaces);
      tools are stand-ins for A3's real read tools.
- [ ] **`src/canon/agent/eval.py`.** `fake` is the only backend it ever picks by itself; any other
      id is a user-run paid leg that frees the wording check and keeps tool order strict (A8's
      provider-swap rule).

## D. Break it (optional, 5 minutes)

- [ ] Edit `expected_tool_calls` of one conversation in `src/canon/agent/evals.py` to a wrong
      tool name, rerun B1, and confirm a named failure line plus exit 1; revert.
- [ ] In `tests/test_chat_backends.py`, find `test_cancel_closes_sdk_context` and read it: the
      generator is closed after the first `TextDelta` and the fake SDK context's `__exit__` must
      have run — that is the ⏹ Stop guarantee.

## E. Optional paid leg — USER-RUN, real money, NOT required by the gate

This is the first real network call of the row; the gate is met without it. If you want to see
the anthropic implementation stream once, pick the smallest conversation. Cost on
`claude-sonnet-5` (the default since 2026-09-01): a few thousand tokens, a few cents.

```bash
ANTHROPIC_API_KEY=... uv run python -m canon.agent.eval --backend anthropic --only just-talking
```

Expected: tool order strict (none for this conversation), wording not checked, and a cost note
of the form `measured tokens in=…/out=… (cache read=…, creation=…); priced by the §3.0-C module
from P0-7` — no dollar figure, by design. Without a key you get one named line
(`anthropic: no credential — set ANTHROPIC_API_KEY …`) and exit 1, never a traceback.

## F. Decisions to confirm (state them if you disagree)

- [x] **DECIDED 2026-09-01:** `DEFAULT_CHAT_MODEL = "claude-sonnet-5"` as the code default (ids
      are data; the effective default resolves project settings → cradle settings → code default
      at P0-12/A5; the panel header picks the conversation model at A5).
- [ ] New code says "conversation" for the transcript/thread id, never "session" (master §3.0-D).
- [ ] `ChatRequest.thinking=False` omits the thinking config, which on Opus 5 still runs adaptive
      thinking; `effort` is the knob that reduces it. Documented, not worked around.
- [x] **DECIDED 2026-09-01:** server-side refusal fallbacks ON by default
      (`AnthropicChatBackend(fallbacks=True)`): `fallbacks: "default"` via `extra_body` + the
      `server-side-fallback-2026-07-01` beta header via `extra_headers` (the SDK predates the
      typed parameter). On a policy decline the API re-runs the same request on a fallback model
      inside the same call; the served model is `MessageStart.model`; a final
      `stop_reason: refusal` means the whole chain refused and the loop still stops.

## G. On approval

- [ ] Tell me "A1 approved". I will flip A1 to ✅ in master §3.1 and Phase 1 §8 with a 2–5 line
      Built summary, and record it in memory. I do not run git — the new files are unstaged.
